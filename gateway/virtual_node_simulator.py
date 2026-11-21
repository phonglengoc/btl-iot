"""
Hardware-Free Virtual YoloUNO #1 + YoloUNO #2 Simulator.

Allows running, demoing, and testing the full Smart Fall Detection & Room Monitoring system
BEFORE purchasing the physical ESP32-S3 (YoloUNO) boards:
  - Node 1 (`YoloUNO #1 - Tx`): I2C DHT20 temperature & humidity (`T01`) + ESP-NOW 20 Hz (`T06`)
  - Node 2 (`YoloUNO #2 - Rx`): 52 Wi-Fi CSI amplitudes over TCP Serial Bridge (`127.0.0.1:19550`),
    SOS button simulation, and Buzzer/LED alarm controlled via `RPC:ALARM:1` / `RPC:ALARM:0` (`T08`).
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path
import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "ai_training") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "ai_training"))

from csi_data_generator import synthesize_csi_window  # noqa: E402
from csi_signal_features import WINDOW_SAMPLES  # noqa: E402


class VirtualYoloUnoCluster:
    """Emulates Node 1 (DHT20 + ESP-NOW 20 Hz) and Node 2 (CSI Serial + Buzzer/SOS)."""

    SCENARIOS = [
        "EMPTY",           # Phong trong
        "STATIONARY",      # Nguoi ngoi doc sach / nghi ngoi
        "SIT_FAST",        # Ngoi xuong ghe nhanh (kiem tra chong bao gia)
        "WALKING",         # Nguoi di lai binh thuong trong phong
        "FALL_EMERGENCY",  # Te nga khan cap!
        "SOS_BUTTON_HOLD", # Nhan giu nut SOS 3 giay tren Node 2
    ]

    def __init__(
        self,
        tcp_host: str = "127.0.0.1",
        tcp_port: int = 19550,
        control_file: Path | None = None,
        seed: int = 2026,
    ) -> None:
        self.tcp_host = tcp_host
        self.tcp_port = int(tcp_port)
        self.control_file = control_file or (ROOT_DIR / "gateway" / "runtime" / "simulator_control.json")
        self.rng = np.random.default_rng(seed)

        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._clients: list[socket.socket] = []

        self.state = {
            "scenario": "STATIONARY",
            "temp_c": 27.4,
            "humi_rh": 66.5,
            "alarm_active": False,
            "sos_manual": False,
            "rx_packets": 0,
            "pkt_loss_pct": 0.25,
            "distance_m": 2.0,
            "nlos": False,
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
            else:
                self.state["sos_manual"] = False

    def apply_rpc_line(self, line: str) -> None:
        """Handle downlink RPC commands from PC Local Gateway (`T08`)."""
        line = line.strip()
        with self._lock:
            if line.startswith("RPC:ALARM:"):
                on = line.endswith("1") or line.lower().endswith("true") or line.upper().endswith("ON")
                self.state["alarm_active"] = on
                if not on:
                    self.state["sos_manual"] = False
            elif line.startswith("RPC:SCENARIO:"):
                sc = line.split(":", 2)[2].strip()
                if sc in self.SCENARIOS:
                    self.state["scenario"] = sc

    def generate_one_step_packet(self) -> dict:
        """Generate one 2.0-second simulated window (40 x 52 CSI amplitudes + DHT20 temp/humi)."""
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
                    if "alarm_cmd" in ctrl and ctrl["alarm_cmd"] is not None:
                        act = bool(ctrl["alarm_cmd"])
                        self.state["alarm_active"] = act
                        if not act:
                            self.state["sos_manual"] = False
                        ctrl["alarm_cmd"] = None
                        self.control_file.write_text(json.dumps(ctrl), encoding="utf-8")
            except Exception:
                pass

        with self._lock:
            scenario = str(self.state["scenario"])
            dist = float(self.state["distance_m"])
            nlos = bool(self.state["nlos"])

        hard_neg = None
        if scenario == "EMPTY":
            phys_label = "EMPTY"
        elif scenario == "STATIONARY":
            phys_label = "STATIONARY"
        elif scenario == "SIT_FAST":
            phys_label = "STATIONARY"
            hard_neg = "sit_fast"
        elif scenario == "WALKING":
            phys_label = "WALKING"
        elif scenario == "FALL_EMERGENCY":
            phys_label = "FALL"
        else:  # SOS_BUTTON_HOLD
            phys_label = "STATIONARY"

        temp_c = round(27.4 + 0.35 * np.sin(time.time() / 20.0) + float(self.rng.normal(0, 0.05)), 2)
        humi_rh = round(67.0 + 1.8 * np.cos(time.time() / 25.0) + float(self.rng.normal(0, 0.12)), 2)

        win = synthesize_csi_window(
            label=phys_label,
            rng=self.rng,
            distance_m=dist,
            nlos=nlos,
            hard_negative=hard_neg,
            temp_c=temp_c,
            humi_rh=humi_rh,
        )

        with self._lock:
            self.state["rx_packets"] += WINDOW_SAMPLES // 4  # ~10 packets per 0.5s step (20 Hz)
            sos_now = bool(self.state["sos_manual"]) or (scenario == "SOS_BUTTON_HOLD")
            if sos_now:
                self.state["alarm_active"] = True
            self.state["temp_c"] = temp_c
            self.state["humi_rh"] = humi_rh
            alarm_now = bool(self.state["alarm_active"])
            rx_pkts = int(self.state["rx_packets"])

        return {
            "ts_ms": int(time.time() * 1000),
            "scenario_ground_truth": scenario,
            "physical_label": phys_label,
            "temp_c": temp_c,
            "humi_rh": humi_rh,
            "alarm_active": alarm_now,
            "sos": sos_now,
            "rssi": win["rssi_dbm"],
            "rx_packets": rx_pkts,
            "pkt_loss_pct": 0.25,
            "amps_52": [[round(float(v), 1) for v in row] for row in win["amps_52"]],
        }

    def start_background(self, step_interval_sec: float = 0.40) -> None:
        """Start TCP Serial Stream emulating Node 2 USB Serial (`T08`)."""
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
        if getattr(self, "_srv_sock", None) is not None:
            try:
                self._srv_sock.close()
            except OSError:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Virtual YoloUNO #1 + #2 Hardware Simulator")
    parser.add_argument("--tcp-port", type=int, default=19550)
    parser.add_argument("--scenario", choices=VirtualYoloUnoCluster.SCENARIOS, default="STATIONARY")
    args = parser.parse_args()

    sim = VirtualYoloUnoCluster(tcp_port=args.tcp_port)
    sim.set_scenario(args.scenario)
    sim.start_background()
    print(f"[Virtual YoloUNO Simulator] Streaming 20 Hz CSI on tcp://127.0.0.1:{args.tcp_port}")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        sim.stop()
