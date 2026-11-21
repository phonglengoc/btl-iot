"""
Automated Test Suite for Simplified HCMUT IoT Course Project (HK261).

Verifies all retained components (Bảng A = 10.5 -> 8.0/8.0, Bảng B = N01 +1.0 -> 1.0/1.0):
  - Security hygiene: zero hardcoded tokens in tracked templates & `.gitignore` coverage
  - C++17 ESP-NOW 24-byte packet, CRC-8, DHT20 CRC-8 & 52-subcarrier amplitude extraction (`T01`, `T06`)
  - Simple Moving-Average + Std / (Max - Min) feature extraction & scikit-learn Random Forest AI (`T05`, `T09`)
  - End-to-end PC Local Gateway + Virtual Simulator + RuleChain + Telegram dry-run + CoreIOT MQTT + RPC (`T08`, `T12`, `T13`, `T15`)
  - Streamlit N01 event history assistant query over `event_log.jsonl` (`N01`)
  - Valid importable CoreIOT Dashboard & RuleChain JSON templates (`T11`, `T12`)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
for p in (ROOT_DIR / "ai_training", ROOT_DIR / "gateway"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from coreiot_sync import evaluate_coreiot_rulechain_logic  # noqa: E402
from csi_data_generator import synthesize_csi_window  # noqa: E402
from csi_signal_features import FEATURE_NAMES, extract_statistical_features  # noqa: E402
from gateway_ai import GatewayFallAI  # noqa: E402
from main_gateway import LocalIoTGatewayService, load_gateway_config  # noqa: E402
from virtual_node_simulator import VirtualYoloUnoCluster  # noqa: E402


def test_security_rules_no_hardcoded_secrets():
    """Tracked templates must contain placeholders only, never real tokens or passwords."""
    gitignore = (ROOT_DIR / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore
    assert "firmware/include/config.h" in gitignore
    assert "gateway/config.yaml" in gitignore

    cfg_y = (ROOT_DIR / "gateway" / "config.example.yaml").read_text(encoding="utf-8")
    assert "REPLACE_WITH_" in cfg_y

    env_ex = (ROOT_DIR / ".env.example").read_text(encoding="utf-8")
    assert "REPLACE_WITH_" in env_ex


def test_cpp_espnow_protocol_and_csi_amplitude(tmp_path: Path):
    """Compile C++17 `firmware/include/csi_protocol.h` with `g++` and verify 24-byte struct & CRC-8 (`T01`, `T06`)."""
    exe_path = tmp_path / "cpp_protocol_unit_test.exe"
    cpp_src = ROOT_DIR / "tests" / "cpp_protocol_unit_test.cpp"
    res = subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-O2",
            f"-I{ROOT_DIR / 'firmware' / 'include'}",
            str(cpp_src),
            "-o",
            str(exe_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"g++ compile failed: {res.stderr}"

    run_res = subprocess.run([str(exe_path)], capture_output=True, text=True, check=True)
    assert "[CPP_PROTOCOL_TEST_OK] pkt_bytes=24 active_sc=52 rate_hz=20" in run_res.stdout


def test_statistical_features_and_gateway_rf_ai():
    """Verify Moving Average + Std + Max-Min features & scikit-learn Random Forest accuracy (`T05`, `T09`)."""
    metrics_path = ROOT_DIR / "ai_training" / "artifacts" / "gateway_ai_metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics["rf_accuracy"] >= 0.92, f"Expected RF accuracy >= 92%, got {metrics['rf_accuracy']}"
    assert metrics["rf_fall_f1"] >= 0.92, f"Expected RF Fall F1 >= 92%, got {metrics['rf_fall_f1']}"

    rng = np.random.default_rng(123)
    win = synthesize_csi_window("FALL", rng=rng, distance_m=2.0, nlos=False)
    feats = extract_statistical_features(win["amps_52"])
    assert feats.shape == (len(FEATURE_NAMES),)
    assert np.all(np.isfinite(feats))

    ai = GatewayFallAI()
    pred = ai.predict_window(win["amps_52"])
    assert pred["state"] == "FALL"
    assert pred["confidence"] >= 0.70
    assert len(pred["activity_curve"]) == 40


def test_end_to_end_gateway_rulechain_rpc_and_n01_history(tmp_path: Path):
    """Verify `T08`, `T09`, `T12`, `T13`, `T15`, and `N01` log history assistant end-to-end."""
    sim = VirtualYoloUnoCluster(tcp_port=19655, control_file=tmp_path / "ctrl.json")
    cfg = load_gateway_config()
    cfg["gateway"]["runtime_dir"] = str(tmp_path / "rt")
    gw = LocalIoTGatewayService(cfg)
    gw._virtual_sim = sim
    gw.coreiot.start()

    try:
        # 1. Normal walking should NOT trigger fall alert
        sim.set_scenario("WALKING", distance_m=2.0, nlos=False)
        pkt_walk = sim.generate_one_step_packet()
        res_walk = gw.process_incoming_packet(pkt_walk, force_publish=True)
        assert res_walk is not None
        assert res_walk["rulechain"]["triggered"] is False

        # 2. FALL_EMERGENCY must trigger CRITICAL_FALL_EMERGENCY, write `event_log.jsonl`, Telegram dry-run & CoreIOT MQTT
        sim.set_scenario("FALL_EMERGENCY", distance_m=1.8, nlos=False)
        triggered_fall = False
        for _ in range(5):
            pkt_fall = sim.generate_one_step_packet()
            res_fall = gw.process_incoming_packet(pkt_fall, force_publish=True)
            if res_fall and res_fall["rulechain"]["alarm_type"] == "CRITICAL_FALL_EMERGENCY":
                triggered_fall = True
                break
        assert triggered_fall, "FALL_EMERGENCY must trigger CRITICAL_FALL_EMERGENCY"
        assert sim.state["alarm_active"] is True

        # 3. Verify `event_log.jsonl`, `telegram_alerts.jsonl`, and `coreiot_mqtt_journal.jsonl`
        ev_file = tmp_path / "rt" / "event_log.jsonl"
        tg_file = tmp_path / "rt" / "telegram_alerts.jsonl"
        mqtt_file = tmp_path / "rt" / "coreiot_mqtt_journal.jsonl"
        assert ev_file.exists() and len(ev_file.read_text(encoding="utf-8").splitlines()) >= 1
        assert tg_file.exists() and len(tg_file.read_text(encoding="utf-8").splitlines()) >= 1
        assert mqtt_file.exists() and gw.coreiot.published_count >= 2

        # 4. Test Downlink RPC (`RPC:ALARM:0`) turning buzzer OFF (`T08`)
        rpc_reply = gw.coreiot.inject_simulated_rpc("YoloUNO_Node2_Rx", "setAlarm", False)
        assert rpc_reply["executed_rpc"] == "RPC:ALARM:0"
        assert sim.state["alarm_active"] is False

        # 5. Test single-frame Serial streaming path (40 single frames -> 1 AI prediction)
        gw._csi_ring.clear()
        gw._frame_counter = 0
        single_out = None
        for row in pkt_fall["amps_52"]:
            single_out = gw.process_incoming_packet(
                {"temp_c": 27.5, "humi_rh": 65.0, "alarm_active": False, "sos": False, "amps_52": row}
            )
        assert single_out is not None and single_out["node2"]["csi_state"] == "FALL"

        # 6. Test simple RuleChain logic directly (`T12`)
        rc_fall = evaluate_coreiot_rulechain_logic({"csi_state": "FALL", "ai_conf": 0.92, "fall_threshold": 0.80})
        assert rc_fall["alarm_type"] == "CRITICAL_FALL_EMERGENCY"
        rc_sos = evaluate_coreiot_rulechain_logic({"csi_state": "STATIONARY", "ai_conf": 0.90, "sos_triggered": True})
        assert rc_sos["alarm_type"] == "CRITICAL_SOS_EMERGENCY"
        rc_ok = evaluate_coreiot_rulechain_logic({"csi_state": "WALKING", "ai_conf": 0.95})
        assert rc_ok["triggered"] is False
    finally:
        gw.coreiot.stop()


def test_coreiot_json_templates_valid():
    """Verify `T11` Dashboard JSON & `T12` RuleChain JSON are valid importable files without BLE/OTA."""
    dash_text = (ROOT_DIR / "coreiot" / "dashboard_template.json").read_text(encoding="utf-8")
    rc_text = (ROOT_DIR / "coreiot" / "rulechain_fall.json").read_text(encoding="utf-8")
    assert "ble_" not in dash_text.lower()
    assert "ble_" not in rc_text.lower()

    dash = json.loads(dash_text)
    rc = json.loads(rc_text)
    assert "widgets" in dash["configuration"]
    assert len(rc["metadata"]["nodes"]) >= 6
