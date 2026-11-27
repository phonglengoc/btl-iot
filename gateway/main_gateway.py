"""
PC Local IoT Gateway Main Service (`T08`, `T09`, `T12`, `T13`, `T15`, `N01`).

Clean & lightweight workflow:
  1. Receives 20 Hz CSI (52 amplitudes) + DHT20 temperature/humidity from YoloUNO #2
     via USB Serial (`115200 bps`) OR from `VirtualYoloUnoCluster` (`simulator` mode).
  2. Buffers 40 frames (2.0 seconds) and classifies activity using 1 scikit-learn Random Forest
     model (`GatewayFallAI` - `T09`, CPU only).
  3. Evaluates alert condition (`FALL` or `SOS`), logs incident records into `event_log.jsonl`
     (for Streamlit history assistant `N01`), triggers Telegram Bot alert (`T13`),
     and sends `RPC:ALARM:1` / `RPC:ALARM:0` back down to Node 2 (`T08`).
  4. Publishes synchronized telemetry to CoreIOT Cloud via MQTT (`T15`).
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np
import serial
import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "ai_training") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "ai_training"))

from csi_signal_features import CLASS_NAMES, NUM_SUBCARRIERS, WINDOW_SAMPLES  # noqa: E402
from coreiot_sync import CoreIOTGatewaySync, TelegramAlertClient, evaluate_coreiot_rulechain_logic  # noqa: E402
from gateway_ai import GatewayFallAI  # noqa: E402
from virtual_node_simulator import VirtualYoloUnoCluster  # noqa: E402


def load_gateway_config(config_path: Path | str | None = None) -> dict:
    if config_path is None:
        cfg_real = ROOT_DIR / "gateway" / "config.yaml"
        cfg_ex = ROOT_DIR / "gateway" / "config.example.yaml"
        target = cfg_real if cfg_real.exists() else cfg_ex
    else:
        target = Path(config_path)
    return yaml.safe_load(target.read_text(encoding="utf-8"))


class LocalIoTGatewayService:
    """End-to-end PC Local IoT Gateway service (`T08`, `T09`, `T13`, `T15`, `N01`)."""

    def __init__(self, config: dict | None = None) -> None:
        self.cfg = config or load_gateway_config()
        gw_cfg = self.cfg.get("gateway", {})
        ai_cfg = self.cfg.get("ai_engine", {})
        ci_cfg = self.cfg.get("coreiot", {})
        tg_cfg = self.cfg.get("telegram_alert", {})

        self.source_mode: str = gw_cfg.get("source_mode", "simulator")
        self.serial_port: str = gw_cfg.get("serial_port", "COM4")
        self.serial_baud: int = int(gw_cfg.get("serial_baudrate", 115200))
        self.sim_host: str = gw_cfg.get("simulator_tcp_host", "127.0.0.1")
        self.sim_port: int = int(gw_cfg.get("simulator_tcp_port", 19550))
        self.telemetry_interval: float = float(gw_cfg.get("telemetry_interval_sec", 1.0))

        self.runtime_dir = ROOT_DIR / gw_cfg.get("runtime_dir", "gateway/runtime")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        self.fall_threshold = float(ai_cfg.get("fall_threshold", 0.80))
        self.ai_engine = GatewayFallAI(
            model_path=ROOT_DIR / ai_cfg.get("model_path", "ai_training/models/fall_rf_model.joblib")
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
            device_profile=ci_cfg.get("device_profile", "Smart_Room_Fall_Profile"),
            runtime_dir=self.runtime_dir,
            on_rpc_callback=self.handle_downlink_rpc,
        )

        self._csi_ring: deque[np.ndarray] = deque(maxlen=WINDOW_SAMPLES)
        self._history_ring: deque[dict] = deque(maxlen=60)
        self._downlink_queue: deque[str] = deque(maxlen=64)
        self._events_log_path = self.runtime_dir / "event_log.jsonl"
        self._live_state_path = self.runtime_dir / "live_state.json"
        self._ui_cmd_path = self.runtime_dir / "ui_commands.jsonl"
        self._last_event_ts: float = 0.0
        self._last_pub_ts: float = 0.0
        self._last_sent_state: str = ""
        self._frame_counter: int = 0
        self._ema_probs: np.ndarray | None = None
        self._stable_state: str = "EMPTY"
        self._pending_state: str = ""
        self._pending_count: int = 0
        self._fall_hold_until: float = 0.0
        self._stop_evt = threading.Event()
        self._virtual_sim: VirtualYoloUnoCluster | None = None

    def poll_ui_commands(self) -> None:
        """Read pending UI RPC commands (`ui_commands.jsonl`) so Alarm ON/OFF works in BOTH serial & simulator modes."""
        if not self._ui_cmd_path.exists():
            return
        try:
            raw_lines = self._ui_cmd_path.read_text(encoding="utf-8").splitlines()
            self._ui_cmd_path.unlink(missing_ok=True)
        except OSError:
            return
        for ln in raw_lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                cmd = json.loads(ln)
                method = str(cmd.get("method", ""))
                params = cmd.get("params", {})
                if method:
                    self.handle_downlink_rpc(self.coreiot.node2_name, method, params)
            except Exception:
                continue

    def handle_downlink_rpc(self, device: str, method: str, params: dict) -> dict:
        """Forward RPC commands (`setAlarm`, `setFallThreshold`) from CoreIOT / UI to Node 2 (`T08`)."""
        val = params.get("value", params) if isinstance(params, dict) else params
        cmd_line = ""
        if method in ("setAlarm", "alarm"):
            on = bool(val if not isinstance(val, dict) else val.get("active", False))
            cmd_line = f"RPC:ALARM:{1 if on else 0}"
            if not on:
                self._fall_hold_until = 0.0
        elif method in ("setFallThreshold", "fall_threshold"):
            th = float(val if not isinstance(val, dict) else val.get("threshold", 0.80))
            self.fall_threshold = float(np.clip(th, 0.50, 0.95))
        elif method == "sharedAttributesUpdate" and isinstance(params, dict):
            if "fall_threshold" in params:
                self.fall_threshold = float(np.clip(float(params["fall_threshold"]), 0.50, 0.95))

        if cmd_line:
            self._downlink_queue.append(cmd_line + "\n")
            if self._virtual_sim is not None:
                self._virtual_sim.apply_rpc_line(cmd_line)
        return {"ok": True, "device": device, "executed_rpc": cmd_line}

    def _record_alert_event(self, alarm_type: str, ai_res: dict, rule_res: dict, node_pkt: dict) -> dict | None:
        """Append incident entry to `event_log.jsonl` for Streamlit history assistant (`N01`)."""
        now = time.time()
        if (now - self._last_event_ts) < 4.0:
            return None
        self._last_event_ts = now

        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "alarm_type": alarm_type,
            "state": ai_res["state"],
            "ai_conf": ai_res["confidence"],
            "signal_std": ai_res["signal_std"],
            "signal_range": ai_res["signal_range"],
            "temp_c": round(float(node_pkt.get("temp_c", 27.5)), 2),
            "humi_rh": round(float(node_pkt.get("humi_rh", 65.0)), 2),
            "telegram_text": rule_res["telegram_text"],
        }
        with self._events_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def _stabilize_stream_prediction(self, ai_res: dict, now: float) -> dict:
        """
        Fast-Attack EMA + Hysteresis + 3.5s FALL Latch for live 20 Hz serial streaming.
        Eliminates 3-way state flicker between EMPTY/STATIONARY/WALKING while keeping
        instant attack for real FALL surges.
        """
        raw_probs = np.asarray(ai_res.get("probs", [1.0, 0.0, 0.0, 0.0]), dtype=np.float64)
        if raw_probs.shape[0] != len(CLASS_NAMES):
            return ai_res

        if self._ema_probs is None or self._frame_counter <= WINDOW_SAMPLES:
            self._ema_probs = raw_probs.copy()
            self._stable_state = str(ai_res["state"])
            self._pending_state = ""
            self._pending_count = 0
            self._fall_hold_until = (now + 3.5) if (self._stable_state == "FALL" and raw_probs[3] >= 0.70) else 0.0
            return ai_res

        # Asymmetric EMA: instant fast-attack for FALL, decisive smoothing for non-fall jitter
        if raw_probs[3] >= 0.55:
            alpha = 0.85
        elif float(np.max(raw_probs)) >= 0.80:
            alpha = 0.65
        else:
            alpha = 0.35

        self._ema_probs = alpha * raw_probs + (1.0 - alpha) * self._ema_probs
        ema = self._ema_probs / (float(np.sum(self._ema_probs)) + 1e-9)

        top_idx = int(np.argmax(ema))
        top_state = CLASS_NAMES[top_idx]
        top_conf = float(ema[top_idx])

        # 1. Immediate FALL attack & 3.5-second hold latch
        if float(ema[3]) >= 0.65 or float(raw_probs[3]) >= 0.78:
            self._stable_state = "FALL"
            self._pending_state = ""
            self._pending_count = 0
            self._fall_hold_until = now + 3.5
            disp_conf = max(float(ema[3]), float(raw_probs[3]))
        elif now < self._fall_hold_until:
            self._stable_state = "FALL"
            disp_conf = max(float(ema[3]), self.fall_threshold)
        else:
            # 2. Hysteresis for non-FALL states (prevents 49%/51% coin-flip flicker)
            if self._stable_state == "FALL":
                # Exited FALL hold window -> settle cleanly to current top non-fall state
                self._stable_state = top_state
                self._pending_state = ""
                self._pending_count = 0
            elif top_state == self._stable_state:
                self._pending_state = ""
                self._pending_count = 0
            else:
                cur_idx = CLASS_NAMES.index(self._stable_state)
                cur_prob = float(ema[cur_idx])
                margin = top_conf - cur_prob
                if top_conf >= 0.68 and margin >= 0.15:
                    self._stable_state = top_state
                    self._pending_state = ""
                    self._pending_count = 0
                elif margin >= 0.08:
                    if self._pending_state == top_state:
                        self._pending_count += 1
                    else:
                        self._pending_state = top_state
                        self._pending_count = 1
                    if self._pending_count >= 2:
                        self._stable_state = top_state
                        self._pending_state = ""
                        self._pending_count = 0
                else:
                    self._pending_state = ""
                    self._pending_count = 0
            disp_conf = float(ema[CLASS_NAMES.index(self._stable_state)])

        stabilized = dict(ai_res)
        stabilized["state"] = self._stable_state
        stabilized["confidence"] = round(disp_conf, 4)
        stabilized["probs"] = [round(float(p), 4) for p in ema]
        return stabilized

    def process_incoming_packet(self, pkt: dict, force_publish: bool = False) -> dict | None:
        """
        Process a JSON packet from Node 2 (either 1 frame `[52]` from USB Serial
        or a 40-frame window `[[52]...]` from Simulator), run scikit-learn AI (`T09`),
        evaluate alerts (`T12`, `T13`), sync CoreIOT (`T15`), and write `live_state.json`.
        """
        amps_raw = np.asarray(pkt.get("amps_52", []), dtype=np.float32)
        is_streaming_frame = False
        if amps_raw.ndim == 2 and amps_raw.shape == (WINDOW_SAMPLES, NUM_SUBCARRIERS):
            amps_40x52 = amps_raw
        elif amps_raw.ndim == 1 and amps_raw.shape[0] == NUM_SUBCARRIERS:
            is_streaming_frame = True
            self._csi_ring.append(amps_raw)
            self._frame_counter += 1
            # Stride = 2 frames (~0.10s @ 20 Hz) for fast, responsive real-time detection
            if len(self._csi_ring) < WINDOW_SAMPLES or (self._frame_counter % 2) != 0:
                return None
            amps_40x52 = np.stack(list(self._csi_ring), axis=0)
        else:
            return None

        temp_c = float(pkt.get("temp_c", 27.5))
        humi_rh = float(pkt.get("humi_rh", 65.0))

        # Run single lightweight scikit-learn Random Forest model (`T09`)
        ai_res = self.ai_engine.predict_window(amps_40x52)
        if is_streaming_frame:
            ai_res = self._stabilize_stream_prediction(ai_res, time.time())

        node1_telemetry = {
            "room_temp_c": temp_c,
            "room_humi_rh": humi_rh,
            "espnow_channel": 6,
            "pkt_loss_pct": float(pkt.get("pkt_loss_pct", 0.0)),
        }

        node2_telemetry = {
            "csi_state": ai_res["state"],
            "ai_conf": ai_res["confidence"],
            "signal_std": ai_res["signal_std"],
            "signal_range": ai_res["signal_range"],
            "room_temp_c": temp_c,
            "room_humi_rh": humi_rh,
            "fall_threshold": self.fall_threshold,
            "sos_triggered": bool(pkt.get("sos", False)),
            "ai_infer_ms": ai_res["infer_ms"],
        }

        rule_res = evaluate_coreiot_rulechain_logic(node2_telemetry)
        if self.source_mode == "serial" and ai_res["state"] != self._last_sent_state:
            self._last_sent_state = str(ai_res["state"])
            self._downlink_queue.append(f"RPC:STATE:{self._last_sent_state}\n")

        if rule_res["triggered"]:
            self._record_alert_event(rule_res["alarm_type"], ai_res, rule_res, pkt)
            self.telegram.send_alert(rule_res["alarm_type"], rule_res["telegram_text"])
            self.handle_downlink_rpc(self.coreiot.node2_name, "setAlarm", {"active": True})

        now = time.time()
        if force_publish or rule_res["triggered"] or (now - self._last_pub_ts) >= self.telemetry_interval:
            self._last_pub_ts = now
            self.coreiot.publish_gateway_telemetry(node1_telemetry, node2_telemetry)

        self._history_ring.append(
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "signal_std": ai_res["signal_std"],
                "signal_range": ai_res["signal_range"],
                "temp_c": temp_c,
                "humi_rh": humi_rh,
                "state": ai_res["state"],
            }
        )

        live_obj = {
            "updated_ts": round(now, 3),
            "source_mode": self.source_mode,
            "scenario": pkt.get("scenario_ground_truth", "LIVE_SERIAL"),
            "node1": node1_telemetry,
            "node2": node2_telemetry,
            "ai": ai_res,
            "rulechain": rule_res,
            "alarm_active": bool(pkt.get("alarm_active", False)) or rule_res["triggered"],
            "coreiot_published": self.coreiot.published_count,
            "coreiot_mode": "OFFLINE_MOCK_SYNC" if self.coreiot.is_mock_mode else "LIVE_CLOUD",
            "history": list(self._history_ring),
        }
        payload_str = json.dumps(live_obj, ensure_ascii=False)
        tmp_path = self._live_state_path.with_suffix(".tmp")
        tmp_path.write_text(payload_str, encoding="utf-8")
        # Windows raises PermissionError when Streamlit reads live_state.json concurrently
        replaced = False
        for _ in range(5):
            try:
                tmp_path.replace(self._live_state_path)
                replaced = True
                break
            except PermissionError:
                time.sleep(0.02)
        if not replaced:
            try:
                self._live_state_path.write_text(payload_str, encoding="utf-8")
            except OSError:
                pass
        return live_obj

    def run_forever(self, max_steps: int | None = None) -> None:
        self.coreiot.start()
        self.coreiot.publish_gateway_attributes(
            {
                "fall_threshold": self.fall_threshold,
                "ai_model": self.ai_engine.model_name,
            }
        )

        steps = 0
        if self.source_mode == "serial":
            print(f"[Gateway T08] Connecting to physical YoloUNO #2 on {self.serial_port} @ {self.serial_baud} bps...")
            try:
                with serial.Serial(self.serial_port, self.serial_baud, timeout=0.5) as ser:
                    print(f"[Gateway T08/T09] Serial connected! Waiting for 20 Hz JSON frames (Model={self.ai_engine.model_name})")
                    while not self._stop_evt.is_set():
                        self.poll_ui_commands()
                        while self._downlink_queue:
                            ser.write(self._downlink_queue.popleft().encode("utf-8"))
                            ser.flush()
                        line = ser.readline().decode("utf-8", errors="ignore").strip()
                        if line.startswith("{"):
                            try:
                                res = self.process_incoming_packet(json.loads(line))
                                while self._downlink_queue:
                                    ser.write(self._downlink_queue.popleft().encode("utf-8"))
                                    ser.flush()
                                if res is not None:
                                    steps += 1
                                    if max_steps and steps >= max_steps:
                                        break
                            except Exception:
                                continue
            finally:
                self.coreiot.stop()
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(1.0)
                sock.connect((self.sim_host, self.sim_port))
            except OSError:
                sock.close()
                self._virtual_sim = VirtualYoloUnoCluster(
                    tcp_host=self.sim_host,
                    tcp_port=self.sim_port,
                    control_file=self.runtime_dir / "simulator_control.json",
                )
                self._virtual_sim.start_background(step_interval_sec=0.40)
                time.sleep(0.15)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((self.sim_host, self.sim_port))

            print(
                f"[Gateway T08/T09] Connected to Virtual YoloUNO Stream | "
                f"Model={self.ai_engine.model_name}"
            )
            sock.settimeout(2.0)
            f_sock = sock.makefile("r", encoding="utf-8")
            try:
                while not self._stop_evt.is_set():
                    self.poll_ui_commands()
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
    parser = argparse.ArgumentParser(description="PC Local IoT Gateway (T08, T09, T13, T15, N01)")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    args = parser.parse_args()

    cfg = load_gateway_config(args.config)
    service = LocalIoTGatewayService(cfg)
    try:
        service.run_forever(max_steps=args.max_steps)
    except KeyboardInterrupt:
        service.stop()
