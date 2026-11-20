"""
PC Local IoT Gateway Main Orchestrator (`T08`, `T09`, `T13`, `T15`, `N04`, `N06`).

Responsibilities (Sections 2.3.3 & 3.4 of `spec_btl_iot_fall_detection.tex`):
  1. Ingests 100 Hz CSI + BLE + DHT20 telemetry from YoloUNO #2 via USB-CDC (`921600 bps`)
     OR from the Hardware-Free `VirtualYoloUnoCluster` (`simulator` mode).
  2. Runs real-time STFT Spectrogram 2D-CNN cross-verification on the NVIDIA GeForce RTX 5070 GPU
     (`CUDAExecutionProvider` - `T09`, `N04`) and fuses with Edge TinyML INT8 (`0.4*Edge + 0.6*GPU`).
  3. Automatically captures CSI Heatmap + STFT Spectrogram Snapshot PNGs & event records (`N06`)
     whenever a `CRITICAL_FALL_EMERGENCY`, `MAJOR_INTRUSION_ALERT`, or `CRITICAL_SOS_EMERGENCY` occurs.
  4. Synchronizes multi-device telemetry & shared attributes with CoreIOT Gateway (`T15`),
     evaluates/triggers Telegram Bot alerts (`T13`), and distributes downlink RPC commands (`T08`).
"""

from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import serial
import yaml

from coreiot_sync import CoreIOTGatewaySync, TelegramAlertClient, evaluate_coreiot_rulechain_logic
from gpu_spectrogram_ai import GatewayGpuSpectrogramEngine
from virtual_node_simulator import VirtualYoloUnoCluster

ROOT_DIR = Path(__file__).resolve().parents[1]


def load_gateway_config(config_path: Path | str | None = None) -> dict:
    if config_path is None:
        cfg_real = ROOT_DIR / "gateway" / "config.yaml"
        cfg_ex = ROOT_DIR / "gateway" / "config.example.yaml"
        target = cfg_real if cfg_real.exists() else cfg_ex
    else:
        target = Path(config_path)
    return yaml.safe_load(target.read_text(encoding="utf-8"))


class LocalIoTGatewayService:
    """End-to-end PC Local IoT Gateway service (`T08`, `T09`, `T13`, `T15`, `N04`, `N06`)."""

    def __init__(self, config: dict | None = None) -> None:
        self.cfg = config or load_gateway_config()
        gw_cfg = self.cfg.get("gateway", {})
        ai_cfg = self.cfg.get("ai_engine", {})
        ci_cfg = self.cfg.get("coreiot", {})
        tg_cfg = self.cfg.get("telegram_alert", {})

        self.source_mode: str = gw_cfg.get("source_mode", "simulator")
        self.serial_port: str = gw_cfg.get("serial_port", "COM4")
        self.serial_baud: int = int(gw_cfg.get("serial_baudrate", 921600))
        self.sim_host: str = gw_cfg.get("simulator_tcp_host", "127.0.0.1")
        self.sim_port: int = int(gw_cfg.get("simulator_tcp_port", 19550))
        self.sim_http_port: int = int(gw_cfg.get("virtual_node2_http_port", 8082))
        self.telemetry_interval: float = float(gw_cfg.get("telemetry_interval_sec", 1.0))

        self.runtime_dir = ROOT_DIR / gw_cfg.get("runtime_dir", "gateway/runtime")
        self.snapshots_dir = self.runtime_dir / "snapshots"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

        self.fall_fused_threshold = float(ai_cfg.get("fall_fused_threshold", 0.85))
        self.security_armed = bool(ai_cfg.get("security_armed_default", True))
        self.ble_rssi_min = int(ai_cfg.get("ble_rssi_min_default", -75))

        self.gpu_ai = GatewayGpuSpectrogramEngine(
            onnx_path=ROOT_DIR / ai_cfg.get("onnx_model_path", "ai_training/models/csi_spectrogram_2dcnn.onnx"),
            tinyml_npz_path=ROOT_DIR / ai_cfg.get("tinyml_weights_path", "ai_training/models/tinyml_1dcnn_int8.npz"),
            prefer_gpu=bool(ai_cfg.get("prefer_gpu_cuda", True)),
            edge_weight=float(ai_cfg.get("edge_fusion_weight", 0.40)),
            gpu_weight=float(ai_cfg.get("gpu_fusion_weight", 0.60)),
        )

        self.telegram = TelegramAlertClient(
            bot_token=tg_cfg.get("bot_token", ""),
            chat_id=tg_cfg.get("chat_id", ""),
            cooldown_sec=float(tg_cfg.get("cooldown_seconds", 15.0)),
            journal_path=self.runtime_dir / "telegram_alerts.jsonl",
        )

        self.coreiot = CoreIOTGatewaySync(
            host=ci_cfg.get("host", "app.coreiot.io"),
            port=int(ci_cfg.get("port", 1883)),
            gateway_token=ci_cfg.get("gateway_token", ""),
            node1_name=ci_cfg.get("node1_device_name", "YoloUNO_Node1_Tx"),
            node2_name=ci_cfg.get("node2_device_name", "YoloUNO_Node2_Rx"),
            device_profile=ci_cfg.get("device_profile", "CSI_Radar_Profile"),
            runtime_dir=self.runtime_dir,
            on_rpc_callback=self.handle_downlink_rpc,
        )

        self._csi_ring: deque[np.ndarray] = deque(maxlen=200)
        self._downlink_queue: deque[str] = deque(maxlen=64)
        self._events_log_path = self.runtime_dir / "event_snapshots_log.jsonl"
        self._live_state_path = self.runtime_dir / "live_state.json"
        self._last_snapshot_ts: float = 0.0
        self._stop_evt = threading.Event()
        self._virtual_sim: VirtualYoloUnoCluster | None = None

    def handle_downlink_rpc(self, device: str, method: str, params: dict) -> dict:
        """
        Receive RPC from CoreIOT Cloud OR Local UI (`T08`, `T15`) and forward to YoloUNO Node 2!
        """
        val = params.get("value", params) if isinstance(params, dict) else params
        cmd_line = ""
        if method in ("setAlarm", "alarm"):
            on = bool(val if not isinstance(val, dict) else val.get("active", False))
            cmd_line = f"RPC:ALARM:{1 if on else 0}"
        elif method in ("setSecurityArmed", "security_armed"):
            arm = bool(val if not isinstance(val, dict) else val.get("armed", True))
            self.security_armed = arm
            cmd_line = f"RPC:ARM:{1 if arm else 0}"
        elif method in ("setFallThreshold", "fall_threshold"):
            th = float(val if not isinstance(val, dict) else val.get("threshold", 0.85))
            self.fall_fused_threshold = float(np.clip(th, 0.50, 0.95))
            cmd_line = f"RPC:THRESHOLD:{self.fall_fused_threshold:.2f}"
        elif method in ("triggerOtaUpdate", "ota"):
            ver = str(params.get("version", "1.1.0")) if isinstance(params, dict) else "1.1.0"
            sha = str(params.get("sha256", "")) if isinstance(params, dict) else ""
            cmd_line = f"RPC:OTA:{ver}:{sha}"
        elif method == "sharedAttributesUpdate" and isinstance(params, dict):
            if "fall_threshold" in params:
                self.fall_fused_threshold = float(params["fall_threshold"])
                cmd_line = f"RPC:THRESHOLD:{self.fall_fused_threshold:.2f}"
            if "security_armed" in params:
                self.security_armed = bool(params["security_armed"])
                cmd_line = f"RPC:ARM:{1 if self.security_armed else 0}"

        if cmd_line:
            self._downlink_queue.append(cmd_line + "\n")
            if self._virtual_sim is not None:
                self._virtual_sim.apply_rpc_line(cmd_line)
        return {"ok": True, "device": device, "executed_rpc": cmd_line}

    def _save_event_snapshot(
        self,
        alarm_type: str,
        amps_200x52: np.ndarray,
        spectrogram_64x64: np.ndarray,
        ai_result: dict,
        rule_result: dict,
        node_pkt: dict,
    ) -> str | None:
        """
        Advanced Feature `N06`: Automatically highlight sudden CSI amplitude/Doppler drop,
        render dual-panel CSI Heatmap + STFT Spectrogram PNG snapshot, and append to event log!
        """
        now = time.time()
        if (now - self._last_snapshot_ts) < 4.0:
            return None
        self._last_snapshot_ts = now

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        img_name = f"snapshot_{alarm_type.lower()}_{stamp}.png"
        img_path = self.snapshots_dir / img_name

        # Locate peak physical motion burst (after causal Hampel despiking) for event highlight (`N06`)
        from csi_dsp_common import hampel_filter_causal
        clean_amps = hampel_filter_causal(amps_200x52, half_win=2, n_sigmas=3.0)
        temporal_diff = np.mean(np.abs(np.diff(clean_amps, axis=0, prepend=clean_amps[:1])), axis=1)
        peak_frame = int(np.argmax(temporal_diff))

        fig, axes = plt.subplots(1, 2, figsize=(11.4, 4.0), dpi=130)
        im0 = axes[0].imshow(clean_amps.T, aspect="auto", origin="lower", cmap="viridis")
        axes[0].axvline(peak_frame, color="#ff3b30", linestyle="--", lw=2.0, label=f"Peak @ {peak_frame*10}ms")
        axes[0].axvspan(max(0, peak_frame - 18), min(199, peak_frame + 18), color="#ff3b30", alpha=0.18)
        axes[0].set_title(f"52-Subcarrier Wi-Fi CSI Heatmap ({alarm_type})")
        axes[0].set_xlabel("Frame Index (100 Hz -> 2.0s)")
        axes[0].set_ylabel("Active OFDM Subcarrier (0..51)")
        axes[0].legend(loc="upper right", fontsize=8)
        fig.colorbar(im0, ax=axes[0], fraction=0.046)

        im1 = axes[1].imshow(spectrogram_64x64, aspect="auto", origin="lower", cmap="inferno")
        axes[1].set_title(
            f"STFT Spectrogram | Fused={rule_result['fused_conf']*100:.1f}% ({ai_result['active_provider']})"
        )
        axes[1].set_xlabel("Time Bin (0..63)")
        axes[1].set_ylabel("Doppler Frequency Bin (0..63)")
        fig.colorbar(im1, ax=axes[1], fraction=0.046)
        fig.tight_layout()
        fig.savefig(img_path)
        plt.close(fig)

        event_entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "alarm_type": alarm_type,
            "fused_state": ai_result["fused_state"],
            "edge_conf": ai_result["edge_conf"],
            "gpu_conf": ai_result["gpu_conf"],
            "fused_conf": rule_result["fused_conf"],
            "ble_authorized": bool(node_pkt.get("ble_authorized", False)),
            "ble_mac": node_pkt.get("ble_mac", "NONE"),
            "temp_c": node_pkt.get("temp_c", 27.5),
            "humi_rh": node_pkt.get("humi_rh", 65.0),
            "snapshot_png": str(img_path),
            "telegram_text": rule_result["telegram_text"],
        }
        with self._events_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event_entry, ensure_ascii=False) + "\n")
        return str(img_path)

    def process_incoming_packet(self, pkt: dict, force_publish: bool = False) -> dict | None:
        """
        Process one JSONL frame from Node 2 (either single-frame `[52]` or 200-frame `[[52]...]`),
        run GPU AI + RuleChain + CoreIOT Sync, and persist live state for `streamlit_ui.py`.
        """
        amps_raw = np.asarray(pkt["amps_52"], dtype=np.float32)
        if amps_raw.ndim == 2 and amps_raw.shape == (200, 52):
            amps_200x52 = amps_raw
        elif amps_raw.ndim == 1 and amps_raw.shape[0] == 52:
            self._csi_ring.append(amps_raw)
            if len(self._csi_ring) < 200:
                return None
            amps_200x52 = np.stack(list(self._csi_ring), axis=0)
        else:
            return None

        temp_c = float(pkt.get("temp_c", 27.5))
        humi_rh = float(pkt.get("humi_rh", 65.0))
        edge_probs = pkt.get("edge_probs")

        ai_res = self.gpu_ai.infer_window(
            amps_200x52=amps_200x52,
            temp_c=temp_c,
            humi_rh=humi_rh,
            edge_probs_from_node2=edge_probs,
        )

        node1_telemetry = {
            "room_temp_c": temp_c,
            "room_humi_rh": humi_rh,
            "espnow_channel": 6,
            "pkt_loss_pct": float(pkt.get("pkt_loss_pct", 0.0)),
            "fw_version": pkt.get("fw_version", "1.0.0"),
        }

        node2_telemetry = {
            "csi_state": ai_res["fused_state"],
            "edge_state": ai_res["edge_state"],
            "gpu_state": ai_res["gpu_state"],
            "edge_conf": ai_res["edge_conf"],
            "gpu_conf": ai_res["gpu_conf"],
            "fused_conf": ai_res["fused_conf"],
            "csi_motion_energy": ai_res["motion_energy"],
            "ble_authorized": bool(pkt.get("ble_authorized", False)),
            "ble_mac": str(pkt.get("ble_mac", "NONE")),
            "ble_rssi": int(pkt.get("ble_rssi", -100)),
            "room_temp_c": temp_c,
            "room_humi_rh": humi_rh,
            "fall_threshold": self.fall_fused_threshold,
            "sos_triggered": bool(pkt.get("sos", False)),
            "gpu_infer_ms": ai_res["gpu_infer_ms"],
        }

        rule_res = evaluate_coreiot_rulechain_logic(node2_telemetry, security_armed=self.security_armed)
        snapshot_file = None
        if rule_res["triggered"]:
            snapshot_file = self._save_event_snapshot(
                alarm_type=rule_res["alarm_type"],
                amps_200x52=amps_200x52,
                spectrogram_64x64=ai_res["spectrogram_ch0"],
                ai_result=ai_res,
                rule_result=rule_res,
                node_pkt=pkt,
            )
            self.telegram.send_alert(rule_res["alarm_type"], rule_res["telegram_text"])
            # Send latch RPC back down to Node 2 (`T08`)
            self.handle_downlink_rpc("YoloUNO_Node2_Rx", "setAlarm", {"active": True})

        now = time.time()
        last_pub = getattr(self, "_last_pub_ts", 0.0)
        if force_publish or rule_res["triggered"] or (now - last_pub) >= self.telemetry_interval:
            self._last_pub_ts = now
            self.coreiot.publish_gateway_telemetry(node1_telemetry, node2_telemetry)

        # Persist compact live state JSON (atomic write) for Streamlit UI (`T10`)
        live_obj = {
            "updated_ts": round(now, 3),
            "source_mode": self.source_mode,
            "scenario": pkt.get("scenario_ground_truth", "LIVE_SERIAL"),
            "node1": node1_telemetry,
            "node2": node2_telemetry,
            "ai": {k: v for k, v in ai_res.items() if k != "spectrogram_ch0"},
            "rulechain": rule_res,
            "security_armed": self.security_armed,
            "alarm_active": bool(pkt.get("alarm_active", False)) or rule_res["triggered"],
            "latest_snapshot": snapshot_file,
            "coreiot_published": self.coreiot.published_count,
            "coreiot_mode": "OFFLINE_MOCK_SYNC" if self.coreiot.is_mock_mode else "LIVE_CLOUD",
            # Downsample CSI heatmap to (50, 52) & spectrogram (64, 64) for ultra-smooth UI rendering
            "heatmap_50x52": np.round(amps_200x52[::4, :], 2).tolist(),
            "spectrogram_64x64": np.round(ai_res["spectrogram_ch0"], 3).tolist(),
        }
        tmp_path = self._live_state_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(live_obj), encoding="utf-8")
        tmp_path.replace(self._live_state_path)

        return live_obj

    def run_forever(self, max_steps: int | None = None) -> None:
        self.coreiot.start()
        self.coreiot.publish_gateway_attributes(
            {
                "fall_threshold": self.fall_fused_threshold,
                "security_armed": self.security_armed,
                "gpu_provider": self.gpu_ai.active_provider,
                "gpu_device": self.gpu_ai.gpu_device_name,
            }
        )

        steps = 0
        if self.source_mode == "serial":
            print(f"[Gateway T08] Connecting to YoloUNO #2 on {self.serial_port} @ {self.serial_baud} bps...")
            with serial.Serial(self.serial_port, self.serial_baud, timeout=1.0) as ser:
                while not self._stop_evt.is_set():
                    while self._downlink_queue:
                        ser.write(self._downlink_queue.popleft().encode("utf-8"))
                    line = ser.readline().decode("utf-8", errors="ignore").strip()
                    if line.startswith("{"):
                        try:
                            self.process_incoming_packet(json.loads(line))
                            steps += 1
                            if max_steps and steps >= max_steps:
                                break
                        except Exception:
                            continue
        else:
            # Auto-launch Virtual YoloUNO Simulator if nothing is listening on sim_port yet
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(1.0)
                sock.connect((self.sim_host, self.sim_port))
            except OSError:
                sock.close()
                self._virtual_sim = VirtualYoloUnoCluster(
                    tcp_host=self.sim_host,
                    tcp_port=self.sim_port,
                    http_port=self.sim_http_port,
                    control_file=self.runtime_dir / "simulator_control.json",
                )
                self._virtual_sim.start_background(step_interval_sec=0.30)
                time.sleep(0.15)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((self.sim_host, self.sim_port))

            print(
                f"[Gateway T08/T09/N04] Connected to Virtual YoloUNO Stream | "
                f"GPU Provider={self.gpu_ai.active_provider} ({self.gpu_ai.gpu_device_name}) | "
                f"Node 2 Web UI: http://127.0.0.1:{self.sim_http_port}"
            )
            sock.settimeout(2.0)
            f_sock = sock.makefile("r", encoding="utf-8")
            try:
                while not self._stop_evt.is_set():
                    while self._downlink_queue:
                        sock.sendall(self._downlink_queue.popleft().encode("utf-8"))
                    line = f_sock.readline()
                    if not line:
                        break
                    line = line.strip()
                    if line.startswith("{"):
                        self.process_incoming_packet(json.loads(line))
                        steps += 1
                        if max_steps and steps >= max_steps:
                            break
            finally:
                sock.close()
                if self._virtual_sim is not None:
                    self._virtual_sim.stop()
                self.coreiot.stop()

    def stop(self) -> None:
        self._stop_evt.set()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PC Local IoT Gateway (T08, T09, T13, T15, N04, N06)")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    args = parser.parse_args()

    cfg = load_gateway_config(args.config)
    service = LocalIoTGatewayService(cfg)
    try:
        service.run_forever(max_steps=args.max_steps)
    except KeyboardInterrupt:
        service.stop()
