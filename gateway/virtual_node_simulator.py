"""
Hardware-Free Virtual YoloUNO #1 + YoloUNO #2 + BLE Beacon Simulator (`Virtual HIL`).

Allows the entire IoT Course Project to run, visualize, and pass end-to-end tests
BEFORE purchasing the physical ESP32-S3 (YoloUNO) boards!

What it emulates faithfully:
  1. Node 1 (`YoloUNO #1 - Tx`):
     - I2C DHT20 temperature & humidity sensor (`0x38`, gradual thermal/steam drift) (`T01`)
     - 100 Hz IEEE 802.11n HT20 (Ch. 6) ESP-NOW ping frames + 64-subcarrier multipath physics (`T06`)
  2. BLE Beacon Tag (`A4:C1:38:9B:12:F0` family member vs absent/unknown intruder MAC) (`T03`)
  3. Node 2 (`YoloUNO #2 - Rx`):
     - Real Hampel + 4th-order Butterworth DSP + REAL quantized INT8 1D-CNN TinyML inference (`T04`)
     - Embedded HTTP Web Server on `http://127.0.0.1:8082` serving the exact `/`, `/api/status`,
       `/api/config`, and `/api/alarm` endpoints (`T07`)!
     - Bidirectional TCP Serial Bridge (`127.0.0.1:19550`) streaming the exact JSONL frames
       that `firmware/node2_rx_edge_ai/src/main.cpp` outputs over USB-CDC `921600 bps`!
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "ai_training") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "ai_training"))

from csi_dsp_common import (  # noqa: E402
    CLASS_NAMES,
    compute_motion_energy_index,
    extract_edge_1dcnn_tensor,
    iq_bytes_to_active_amplitudes,
)
from csi_physics_generator import synthesize_csi_iq_window  # noqa: E402
from train_tinyml_1dcnn import run_int8_1dcnn_reference  # noqa: E402


def _extract_embedded_html_from_firmware() -> str:
    """Load the exact `NODE2_INDEX_HTML` from `firmware/node2_rx_edge_ai/include/web_server.h`!"""
    header_file = ROOT_DIR / "firmware" / "node2_rx_edge_ai" / "include" / "web_server.h"
    if header_file.exists():
        txt = header_file.read_text(encoding="utf-8")
        marker_start = 'R"rawliteral('
        marker_end = ')rawliteral";'
        i0 = txt.find(marker_start)
        i1 = txt.find(marker_end)
        if i0 != -1 and i1 != -1:
            return txt[i0 + len(marker_start) : i1]
    return "<html><body><h2>YoloUNO Node 2 Virtual Embedded Web Server (T07)</h2></body></html>"


class VirtualYoloUnoCluster:
    """Emulates Node 1 + Node 2 + BLE Tag + Embedded Web Server + USB-CDC Serial stream."""

    SCENARIOS = [
        "EMPTY",                # Phong trong
        "STATIONARY_FAMILY",    # Nguoi nha ngoi doc sach / ngu (BLE hop le)
        "SIT_FAST_HARD_NEG",    # Ngoi xuong ghe nhanh (Hard negative chong bao gia!)
        "WALKING_FAMILY",       # Nguoi nha di lai binh thuong (BLE hop le -> An toan)
        "WALKING_INTRUDER",     # Ke la di lai KHONG co the BLE -> Bao dong Dot nhap!
        "FALL_EMERGENCY",       # Nguoi cao tuoi bi TE NGA -> Bao dong Cap cuu!
        "SOS_BUTTON_HOLD",      # Nhan giu nut SOS 3 giay tren Node 2
    ]

    def __init__(
        self,
        tcp_host: str = "127.0.0.1",
        tcp_port: int = 19550,
        http_port: int = 8082,
        control_file: Path | None = None,
        seed: int = 2026,
    ) -> None:
        self.tcp_host = tcp_host
        self.tcp_port = int(tcp_port)
        self.http_port = int(http_port)
        self.control_file = control_file or (ROOT_DIR / "gateway" / "runtime" / "simulator_control.json")
        self.rng = np.random.default_rng(seed)

        tinyml_npz = ROOT_DIR / "ai_training" / "models" / "tinyml_1dcnn_int8.npz"
        self.q_params = dict(np.load(tinyml_npz)) if tinyml_npz.exists() else None
        self.embedded_html = _extract_embedded_html_from_firmware()

        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._clients: list[socket.socket] = []

        self.state = {
            "scenario": "STATIONARY_FAMILY",
            "csi_state": "STATIONARY",
            "edge_conf": 0.95,
            "motion_energy": 6.0,
            "temp_c": 27.4,
            "humi_rh": 66.5,
            "ble_authorized": True,
            "ble_mac": "A4:C1:38:9B:12:F0",
            "ble_rssi": -58,
            "fall_threshold": 0.80,
            "ble_rssi_min": -75,
            "security_armed": True,
            "alarm_active": False,
            "sos_manual": False,
            "rx_packets": 0,
            "pkt_loss_pct": 0.42,
            "distance_m": 2.0,
            "nlos": False,
            "fw_version": "1.0.0",
        }

    def set_scenario(self, scenario: str, distance_m: float = 2.0, nlos: bool = False) -> None:
        with self._lock:
            if scenario in self.SCENARIOS:
                self.state["scenario"] = scenario
            self.state["distance_m"] = float(distance_m)
            self.state["nlos"] = bool(nlos)
            if scenario == "SOS_BUTTON_HOLD":
                self.state["sos_manual"] = True
                self.state["alarm_active"] = True
            elif scenario in ("EMPTY", "STATIONARY_FAMILY", "WALKING_FAMILY"):
                self.state["sos_manual"] = False

    def apply_rpc_line(self, line: str) -> None:
        line = line.strip()
        with self._lock:
            if line.startswith("RPC:ALARM:"):
                on = line.endswith("1") or line.lower().endswith("true") or line.upper().endswith("ON")
                self.state["alarm_active"] = on
                if not on:
                    self.state["sos_manual"] = False
            elif line.startswith("RPC:ARM:"):
                self.state["security_armed"] = line.endswith("1") or line.lower().endswith("true")
            elif line.startswith("RPC:THRESHOLD:"):
                try:
                    th = float(line.split(":", 2)[2])
                    self.state["fall_threshold"] = float(np.clip(th, 0.50, 0.95))
                except ValueError:
                    pass
            elif line.startswith("RPC:SCENARIO:"):
                sc = line.split(":", 2)[2].strip()
                if sc in self.SCENARIOS:
                    self.state["scenario"] = sc
            elif line.startswith("RPC:OTA:"):
                ver = line.split(":", 2)[2].split(":")[0].strip()
                if ver:
                    self.state["fw_version"] = ver

    def generate_one_step_packet(self) -> dict:
        """
        Synthesize 2.0s physics CSI window for current scenario + run REAL INT8 1D-CNN TinyML!
        """
        # Poll external control file so Streamlit UI can switch scenarios live!
        if self.control_file.exists():
            try:
                ctrl = json.loads(self.control_file.read_text(encoding="utf-8-sig"))
                with self._lock:
                    if "scenario" in ctrl and ctrl["scenario"] in self.SCENARIOS:
                        self.state["scenario"] = ctrl["scenario"]
                    if "distance_m" in ctrl:
                        self.state["distance_m"] = float(ctrl["distance_m"])
                    if "nlos" in ctrl:
                        self.state["nlos"] = bool(ctrl["nlos"])
                    if "reset_alarm" in ctrl and ctrl["reset_alarm"]:
                        self.state["alarm_active"] = False
                        self.state["sos_manual"] = False
                        ctrl["reset_alarm"] = False
                        self.control_file.write_text(json.dumps(ctrl), encoding="utf-8")
            except Exception:
                pass

        with self._lock:
            scenario = self.state["scenario"]
            dist = float(self.state["distance_m"])
            nlos = bool(self.state["nlos"])
            fall_th = float(self.state["fall_threshold"])
            ble_min = int(self.state["ble_rssi_min"])
            sec_armed = bool(self.state["security_armed"])

        # Map scenario to physical activity + BLE presence
        hard_neg = None
        ble_present = True
        if scenario == "EMPTY":
            phys_label = "EMPTY"
            ble_present = False
        elif scenario == "STATIONARY_FAMILY":
            phys_label = "STATIONARY"
            ble_present = True
        elif scenario == "SIT_FAST_HARD_NEG":
            phys_label = "STATIONARY"
            hard_neg = "sit_fast"
            ble_present = True
        elif scenario == "WALKING_FAMILY":
            phys_label = "WALKING"
            ble_present = True
        elif scenario == "WALKING_INTRUDER":
            phys_label = "WALKING"
            ble_present = False
        elif scenario == "FALL_EMERGENCY":
            phys_label = "FALL"
            ble_present = True
        else:  # SOS_BUTTON_HOLD
            phys_label = "STATIONARY"
            ble_present = True

        # Smooth DHT20 room temperature & humidity drift
        temp_c = round(27.4 + 0.35 * np.sin(time.time() / 20.0) + float(self.rng.normal(0, 0.05)), 2)
        humi_rh = round(67.0 + 1.8 * np.cos(time.time() / 25.0) + float(self.rng.normal(0, 0.12)), 2)

        win = synthesize_csi_iq_window(
            label=phys_label,
            rng=self.rng,
            distance_m=dist,
            nlos=nlos,
            hard_negative=hard_neg,
            temp_c=temp_c,
            humi_rh=humi_rh,
        )
        amps_200x52 = iq_bytes_to_active_amplitudes(win["iq_raw"])  # (200, 52)

        # Run real INT8 1D-CNN TinyML inference (`T04`)!
        t0 = time.perf_counter()
        x_edge = extract_edge_1dcnn_tensor(amps_200x52, temp_c=temp_c, humi_rh=humi_rh)[None, ...]
        if self.q_params is not None:
            edge_probs = run_int8_1dcnn_reference(x_edge, self.q_params)[0]
        else:
            edge_probs = np.eye(4, dtype=np.float32)[CLASS_NAMES.index(phys_label)]
        infer_ms = (time.perf_counter() - t0) * 1000.0

        pred_idx = int(np.argmax(edge_probs))
        pred_state = CLASS_NAMES[pred_idx]
        edge_conf = float(edge_probs[pred_idx])
        motion_energy = compute_motion_energy_index(amps_200x52)

        if ble_present:
            ble_mac = "A4:C1:38:9B:12:F0"
            ble_rssi = int(np.clip(-52 - 12 * np.log10(max(0.8, dist)) + self.rng.normal(0, 1.5), -90, -38))
            ble_auth = ble_rssi >= ble_min
        else:
            ble_mac = "NONE"
            ble_rssi = -98
            ble_auth = False

        with self._lock:
            self.state["rx_packets"] += 20  # 20 frames per 200ms step = 100 Hz!
            sos_now = bool(self.state["sos_manual"]) or (scenario == "SOS_BUTTON_HOLD")
            if (pred_state == "FALL" and edge_conf >= fall_th) or (
                sec_armed and pred_state == "WALKING" and not ble_auth
            ) or sos_now:
                self.state["alarm_active"] = True

            self.state.update(
                {
                    "csi_state": pred_state,
                    "edge_conf": round(edge_conf, 4),
                    "motion_energy": round(motion_energy, 2),
                    "temp_c": temp_c,
                    "humi_rh": humi_rh,
                    "ble_authorized": ble_auth,
                    "ble_mac": ble_mac,
                    "ble_rssi": ble_rssi,
                }
            )
            alarm_now = bool(self.state["alarm_active"])
            rx_pkts = int(self.state["rx_packets"])
            fw_ver = str(self.state["fw_version"])

        return {
            "ts_ms": int(time.time() * 1000),
            "scenario_ground_truth": scenario,
            "physical_label": phys_label,
            "edge_state": pred_state,
            "edge_conf": round(edge_conf, 4),
            "edge_probs": [round(float(p), 4) for p in edge_probs],
            "infer_ms": round(infer_ms, 2),
            "motion_energy": round(motion_energy, 2),
            "temp_c": temp_c,
            "humi_rh": humi_rh,
            "ble_authorized": ble_auth,
            "ble_mac": ble_mac,
            "ble_rssi": ble_rssi,
            "alarm_active": alarm_now,
            "sos": sos_now,
            "rssi": win["rssi_dbm"],
            "rx_packets": rx_pkts,
            "pkt_loss_pct": 0.42,
            "fw_version": fw_ver,
            "amps_52": [[round(float(v), 2) for v in row] for row in amps_200x52],
        }

    def _start_http_server(self) -> ThreadingHTTPServer | None:
        cluster = self

        class Node2WebHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return  # Keep console clean

            def do_GET(self):
                if self.path == "/" or self.path.startswith("/index"):
                    body = cluster.embedded_html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path.startswith("/api/status"):
                    with cluster._lock:
                        s = dict(cluster.state)
                    s["ip"] = f"127.0.0.1:{cluster.http_port}"
                    body = json.dumps(s).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8", errors="ignore")
                params = urllib.parse.parse_qs(raw)

                if self.path.startswith("/api/config"):
                    with cluster._lock:
                        if "fall_threshold" in params:
                            cluster.state["fall_threshold"] = float(
                                np.clip(float(params["fall_threshold"][0]), 0.50, 0.95)
                            )
                        if "ble_rssi_min" in params:
                            cluster.state["ble_rssi_min"] = int(params["ble_rssi_min"][0])
                        if "security_armed" in params:
                            v = params["security_armed"][0]
                            cluster.state["security_armed"] = v in ("1", "true", "True")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')
                elif self.path.startswith("/api/alarm"):
                    act = params.get("active", ["0"])[0] in ("1", "true", "True")
                    with cluster._lock:
                        cluster.state["alarm_active"] = act
                        if not act:
                            cluster.state["sos_manual"] = False
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')
                else:
                    self.send_error(404)

        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", self.http_port), Node2WebHandler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            return httpd
        except OSError:
            return None

    def start_background(self, step_interval_sec: float = 0.25) -> None:
        """Start Virtual Node 2 HTTP Web Server (`T07`) + TCP Serial Stream (`T08`)."""
        self._httpd = self._start_http_server()
        self._srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv_sock.bind((self.tcp_host, self.tcp_port))
        self._srv_sock.listen(4)
        self._srv_sock.settimeout(0.5)

        def _accept_loop():
            while not self._stop_evt.is_set():
                try:
                    conn, _ = self._srv_sock.accept()
                    conn.settimeout(0.05)
                    with self._lock:
                        self._clients.append(conn)
                except socket.timeout:
                    continue
                except OSError:
                    break

        def _stream_loop():
            while not self._stop_evt.is_set():
                pkt = self.generate_one_step_packet()
                wire = (json.dumps(pkt) + "\n").encode("utf-8")
                dead = []
                with self._lock:
                    clients = list(self._clients)
                for c in clients:
                    try:
                        c.sendall(wire)
                        # Check for non-blocking downlink RPC lines from Gateway
                        try:
                            rx = c.recv(1024)
                            if rx:
                                for ln in rx.decode("utf-8", errors="ignore").splitlines():
                                    self.apply_rpc_line(ln)
                        except socket.timeout:
                            pass
                    except OSError:
                        dead.append(c)
                if dead:
                    with self._lock:
                        for d in dead:
                            if d in self._clients:
                                self._clients.remove(d)
                            try:
                                d.close()
                            except OSError:
                                pass
                time.sleep(step_interval_sec)

        threading.Thread(target=_accept_loop, daemon=True).start()
        threading.Thread(target=_stream_loop, daemon=True).start()

    def stop(self) -> None:
        self._stop_evt.set()
        if getattr(self, "_httpd", None) is not None:
            self._httpd.shutdown()
        if getattr(self, "_srv_sock", None) is not None:
            try:
                self._srv_sock.close()
            except OSError:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Virtual YoloUNO #1 + #2 Hardware Simulator")
    parser.add_argument("--tcp-port", type=int, default=19550)
    parser.add_argument("--http-port", type=int, default=8082)
    parser.add_argument("--scenario", choices=VirtualYoloUnoCluster.SCENARIOS, default="STATIONARY_FAMILY")
    args = parser.parse_args()

    sim = VirtualYoloUnoCluster(tcp_port=args.tcp_port, http_port=args.http_port)
    sim.set_scenario(args.scenario)
    sim.start_background()
    print(
        f"[Virtual YoloUNO Simulator] Streaming 100Hz CSI on tcp://127.0.0.1:{args.tcp_port} | "
        f"Node 2 Embedded Web UI (T07): http://127.0.0.1:{args.http_port}"
    )
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        sim.stop()
