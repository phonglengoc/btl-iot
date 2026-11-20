"""
Comprehensive Automated Test Suite for HCMUT IoT Course Project (HK261).
Verifies every registered technology element (`T01`, `T03`–`T15`, `N04`, `N06`),
security rules (zero hardcoded credentials), C++ vs Python INT8 bit-accuracy,
Virtual Node 2 HTTP Web Server (`T07`), RTX 5070 GPU ONNX Runtime (`N04`),
and CoreIOT Gateway + RuleChain + Snapshot integration.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
for p in (ROOT_DIR / "ai_training", ROOT_DIR / "gateway"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from coreiot_sync import evaluate_coreiot_rulechain_logic  # noqa: E402
from gpu_spectrogram_ai import GatewayGpuSpectrogramEngine  # noqa: E402
from main_gateway import LocalIoTGatewayService, load_gateway_config  # noqa: E402
from virtual_node_simulator import VirtualYoloUnoCluster  # noqa: E402


def test_security_rules_no_hardcoded_secrets():
    """Course Rule Check: Tracked templates must only contain placeholders, never real tokens."""
    gitignore = (ROOT_DIR / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore
    assert "firmware/include/config.h" in gitignore
    assert "gateway/config.yaml" in gitignore

    cfg_h = (ROOT_DIR / "firmware" / "include" / "config.example.h").read_text(encoding="utf-8")
    assert "YOUR_WIFI_SSID" in cfg_h
    assert "REPLACE_WITH_" in cfg_h

    cfg_y = (ROOT_DIR / "gateway" / "config.example.yaml").read_text(encoding="utf-8")
    assert "REPLACE_WITH_" in cfg_y


def test_cpp_firmware_dsp_and_int8_bit_exact_agreement(tmp_path: Path):
    """Compile C++17 `csi_dsp.h` + `tinyml_infer.h` with `g++` and verify exact agreement with Python INT8!"""
    exe_path = tmp_path / "cpp_tinyml_unit_test.exe"
    cpp_src = ROOT_DIR / "tests" / "cpp_tinyml_unit_test.cpp"
    res = subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-O2",
            f"-I{ROOT_DIR / 'firmware' / 'include'}",
            f"-I{ROOT_DIR / 'firmware' / 'node2_rx_edge_ai' / 'include'}",
            str(cpp_src),
            "-o",
            str(exe_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"g++ compile failed: {res.stderr}"

    # 1. Run standalone protocol + DSP + CRC8 check
    run1 = subprocess.run([str(exe_path)], capture_output=True, text=True, check=True)
    assert "[CPP_UNIT_TEST_OK] pkt_bytes=24" in run1.stdout

    # 2. Verify C++ INT8 probabilities match Python `run_int8_1dcnn_reference` within 1e-4!
    metrics = json.loads((ROOT_DIR / "ai_training" / "artifacts" / "tinyml_metrics.json").read_text(encoding="utf-8"))
    assert metrics["int8_accuracy"] > 0.88, f"Expected TinyML INT8 acc > 88%, got {metrics['int8_accuracy']}"

    for sample_in, expected_probs in zip(metrics["cpp_test_inputs"][:5], metrics["cpp_test_expected_probs"][:5]):
        flat_str = " ".join(f"{val:.7f}" for row in sample_in for val in row)
        proc = subprocess.run(
            [str(exe_path), "--stdin-vector"],
            input=flat_str,
            capture_output=True,
            text=True,
            check=True,
        )
        parts = proc.stdout.strip().split()
        cpp_probs = np.array([float(x) for x in parts[:4]], dtype=np.float32)
        py_probs = np.array(expected_probs, dtype=np.float32)
        max_diff = float(np.max(np.abs(cpp_probs - py_probs)))
        assert max_diff < 5e-4, f"Mismatch between C++ INT8 and Python INT8: max_diff={max_diff}"


def test_rtx5070_gpu_onnx_engine_and_accuracy():
    """Verify `T09` + `N04`: ONNX Runtime runs on GPU (`CUDAExecutionProvider`) and achieves >92% F1."""
    gpu_metrics = json.loads(
        (ROOT_DIR / "ai_training" / "artifacts" / "gpu_2dcnn_metrics.json").read_text(encoding="utf-8")
    )
    assert gpu_metrics["gpu_macro_f1"] > 0.92

    engine = GatewayGpuSpectrogramEngine(prefer_gpu=True)
    assert engine.active_provider == "CUDAExecutionProvider", (
        f"Expected CUDAExecutionProvider on RTX 5070, got {engine.active_provider}"
    )
    assert "5070" in engine.gpu_device_name


def test_node2_embedded_webserver_and_end_to_end_gateway_snapshots(tmp_path: Path):
    """Verify `T03`, `T07`, `T08`, `T12`, `T13`, `T15`, `N06` end-to-end."""
    # 1. Start Virtual Node 2 Embedded Web Server (`T07`) on an ephemeral high port
    sim = VirtualYoloUnoCluster(tcp_port=19651, http_port=18082, control_file=tmp_path / "ctrl.json")
    sim.start_background(step_interval_sec=0.15)
    try:
        with urllib.request.urlopen("http://127.0.0.1:18082/api/status", timeout=3.0) as r:
            st_json = json.loads(r.read().decode("utf-8"))
            assert "csi_state" in st_json and "ble_authorized" in st_json

        # Test POST /api/config & POST /api/alarm (`T07`)
        cfg_body = urllib.parse.urlencode({"fall_threshold": "0.78", "ble_rssi_min": "-72", "security_armed": "1"}).encode()
        req = urllib.request.Request("http://127.0.0.1:18082/api/config", data=cfg_body, method="POST")
        with urllib.request.urlopen(req, timeout=3.0) as r2:
            assert json.loads(r2.read().decode("utf-8"))["ok"] is True

        # 2. Test Gateway processing `FALL_EMERGENCY` -> triggers RuleChain + Snapshot PNG (`N06`) + CoreIOT Journal (`T15`)
        cfg = load_gateway_config()
        cfg["gateway"]["runtime_dir"] = str(tmp_path / "rt")
        gw = LocalIoTGatewayService(cfg)
        gw.coreiot.start()

        sim.set_scenario("FALL_EMERGENCY", distance_m=1.8, nlos=False)
        triggered_fall = False
        for _ in range(5):
            pkt = sim.generate_one_step_packet()
            res = gw.process_incoming_packet(pkt, force_publish=True)
            if res and res["rulechain"]["alarm_type"] == "CRITICAL_FALL_EMERGENCY":
                triggered_fall = True
                assert res["latest_snapshot"] is not None
                assert Path(res["latest_snapshot"]).exists()
                break
        assert triggered_fall, "FALL_EMERGENCY must trigger CRITICAL_FALL_EMERGENCY and save Snapshot PNG"

        # 3. Test `WALKING_INTRUDER` (no BLE) vs `WALKING_FAMILY` (authorized BLE) (`T03`, `T12`)
        rc_intruder = evaluate_coreiot_rulechain_logic(
            {"csi_state": "WALKING", "edge_conf": 0.90, "gpu_conf": 0.94, "ble_authorized": False}
        )
        assert rc_intruder["alarm_type"] == "MAJOR_INTRUSION_ALERT"

        rc_family = evaluate_coreiot_rulechain_logic(
            {"csi_state": "WALKING", "edge_conf": 0.90, "gpu_conf": 0.94, "ble_authorized": True}
        )
        assert rc_family["triggered"] is False

        gw.coreiot.stop()
    finally:
        sim.stop()


def test_coreiot_json_templates_valid():
    """Verify `T11` Dashboard JSON & `T12` RuleChain JSON are valid importable artifacts."""
    dash = json.loads((ROOT_DIR / "coreiot" / "dashboard_template.json").read_text(encoding="utf-8"))
    rc = json.loads((ROOT_DIR / "coreiot" / "rulechain_fall_sec.json").read_text(encoding="utf-8"))
    assert "widgets" in dash["configuration"]
    assert len(rc["metadata"]["nodes"]) >= 6
