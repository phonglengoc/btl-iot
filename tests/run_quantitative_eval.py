"""
Chapter 4 Quantitative Evaluation Protocol Runner (`50 trials` per scenario & distance).

Evaluates the entire Edge TinyML + RTX 5070 GPU Spectrogram 2D-CNN + BLE + RuleChain pipeline
against every quantitative target in Table 4.1 of `HK261_Group01_IoTReport.tex`:
  - Scenario S1: Distance & Obstacle sweep (`1.0m`, `2.0m`, `3.0m`, `NLOS` behind thin wall, 50 trials/class)
  - Scenario S2: BLE Authorization & Anti-False-Alarm (`WALKING` with BLE vs without BLE, plus `sit_fast` hard negatives)
  - Scenario S3/S4: Latency benchmarks (Edge TinyML inference, GPU ONNX inference, RPC round-trip)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
import numpy as np
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

ROOT_DIR = Path(__file__).resolve().parents[1]
for p in (ROOT_DIR / "ai_training", ROOT_DIR / "gateway"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from csi_dsp_common import CLASS_NAMES, iq_bytes_to_active_amplitudes  # noqa: E402
from csi_physics_generator import synthesize_csi_iq_window  # noqa: E402
from coreiot_sync import evaluate_coreiot_rulechain_logic  # noqa: E402
from gpu_spectrogram_ai import GatewayGpuSpectrogramEngine  # noqa: E402


def run_chapter4_evaluation(trials_per_condition: int = 50, seed: int = 202611) -> dict:
    rng = np.random.default_rng(seed)
    engine = GatewayGpuSpectrogramEngine(prefer_gpu=True)

    # 1. Scenario S1: Distance & NLOS sweep (50 trials x 4 classes per condition)
    conditions = [
        ("LOS_1.0m", 1.0, False),
        ("LOS_2.0m", 2.0, False),
        ("LOS_3.0m", 3.0, False),
        ("NLOS_2.5m_Wall", 2.5, True),
    ]

    s1_results = {}
    all_y_true, all_edge_pred, all_gpu_pred, all_fused_pred = [], [], [], []
    gpu_latencies_ms = []
    dsp_latencies_ms = []

    for cond_name, dist_m, is_nlos in conditions:
        y_t, y_e, y_g, y_f = [], [], [], []
        for cls_idx, label in enumerate(CLASS_NAMES):
            for _ in range(trials_per_condition):
                win = synthesize_csi_iq_window(label=label, rng=rng, distance_m=dist_m, nlos=is_nlos)
                amps = iq_bytes_to_active_amplitudes(win["iq_raw"])
                out = engine.infer_window(amps, temp_c=win["temp_c"], humi_rh=win["humi_rh"])

                y_t.append(cls_idx)
                y_e.append(CLASS_NAMES.index(out["edge_state"]))
                y_g.append(CLASS_NAMES.index(out["gpu_state"]))
                y_f.append(CLASS_NAMES.index(out["fused_state"]))
                gpu_latencies_ms.append(out["gpu_infer_ms"])
                dsp_latencies_ms.append(out["dsp_ms"])

        s1_results[cond_name] = {
            "trials_total": len(y_t),
            "edge_acc_pct": round(float(accuracy_score(y_t, y_e)) * 100, 2),
            "gpu_acc_pct": round(float(accuracy_score(y_t, y_g)) * 100, 2),
            "fused_acc_pct": round(float(accuracy_score(y_t, y_f)) * 100, 2),
            "fused_fall_f1_pct": round(float(f1_score(np.array(y_t) == 3, np.array(y_f) == 3)) * 100, 2),
        }
        all_y_true.extend(y_t)
        all_edge_pred.extend(y_e)
        all_gpu_pred.extend(y_g)
        all_fused_pred.extend(y_f)

    # 2. Scenario S2: Hard-Negative `sit_fast` False Positive Test & BLE Intrusion Branching (50 trials each)
    sit_fast_false_alarms = 0
    for _ in range(trials_per_condition):
        win = synthesize_csi_iq_window(label="STATIONARY", rng=rng, distance_m=2.0, hard_negative="sit_fast")
        amps = iq_bytes_to_active_amplitudes(win["iq_raw"])
        out = engine.infer_window(amps, temp_c=win["temp_c"], humi_rh=win["humi_rh"])
        rc = evaluate_coreiot_rulechain_logic(
            {
                "csi_state": out["fused_state"],
                "edge_conf": out["edge_conf"],
                "gpu_conf": out["gpu_conf"],
                "ble_authorized": True,
                "room_temp_c": 27.5,
                "fall_threshold": 0.85,
            }
        )
        if rc["alarm_type"] == "CRITICAL_FALL_EMERGENCY":
            sit_fast_false_alarms += 1

    ble_family_false_intrusion = 0
    ble_intruder_detected = 0
    for _ in range(trials_per_condition):
        win = synthesize_csi_iq_window(label="WALKING", rng=rng, distance_m=2.0)
        amps = iq_bytes_to_active_amplitudes(win["iq_raw"])
        out = engine.infer_window(amps, temp_c=win["temp_c"], humi_rh=win["humi_rh"])

        # Family member carrying BLE tag
        rc_fam = evaluate_coreiot_rulechain_logic(
            {"csi_state": out["fused_state"], "edge_conf": out["edge_conf"], "gpu_conf": out["gpu_conf"], "ble_authorized": True}
        )
        if rc_fam["alarm_type"] == "MAJOR_INTRUSION_ALERT":
            ble_family_false_intrusion += 1

        # Intruder WITHOUT BLE tag
        rc_int = evaluate_coreiot_rulechain_logic(
            {"csi_state": out["fused_state"], "edge_conf": out["edge_conf"], "gpu_conf": out["gpu_conf"], "ble_authorized": False}
        )
        if rc_int["alarm_type"] == "MAJOR_INTRUSION_ALERT":
            ble_intruder_detected += 1

    y_true_arr = np.array(all_y_true)
    fused_arr = np.array(all_fused_pred)
    edge_arr = np.array(all_edge_pred)

    summary = {
        "gpu_provider": engine.active_provider,
        "gpu_device": engine.gpu_device_name,
        "total_s1_windows_evaluated": int(len(all_y_true)),
        "overall_edge_tinyml_accuracy_pct": round(float(accuracy_score(y_true_arr, edge_arr)) * 100, 2),
        "overall_fused_accuracy_pct": round(float(accuracy_score(y_true_arr, fused_arr)) * 100, 2),
        "overall_fall_precision_pct": round(float(precision_score(y_true_arr == 3, fused_arr == 3)) * 100, 2),
        "overall_fall_recall_pct": round(float(recall_score(y_true_arr == 3, fused_arr == 3)) * 100, 2),
        "overall_fall_f1_pct": round(float(f1_score(y_true_arr == 3, fused_arr == 3)) * 100, 2),
        "scenario_s1_by_distance": s1_results,
        "scenario_s2_sit_fast_specificity_pct": round((1.0 - sit_fast_false_alarms / trials_per_condition) * 100, 2),
        "scenario_s2_ble_family_safe_rate_pct": round((1.0 - ble_family_false_intrusion / trials_per_condition) * 100, 2),
        "scenario_s2_ble_intruder_detection_pct": round((ble_intruder_detected / trials_per_condition) * 100, 2),
        "gateway_dsp_mean_ms": round(float(np.mean(dsp_latencies_ms)), 3),
        "gateway_gpu_infer_mean_ms": round(float(np.mean(gpu_latencies_ms)), 3),
        "targets_check": {
            "tinyml_acc_gt_88pct": float(accuracy_score(y_true_arr, edge_arr)) > 0.88,
            "fused_fall_f1_gt_92pct": float(f1_score(y_true_arr == 3, fused_arr == 3)) > 0.92,
            "gpu_infer_lt_10ms": float(np.mean(gpu_latencies_ms)) < 10.0,
        },
    }

    out_json = ROOT_DIR / "tests" / "quantitative_eval_results.json"
    out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Chapter 4 Eval] Completed {len(all_y_true)} S1 windows + 150 S2 windows -> {out_json}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


if __name__ == "__main__":
    run_chapter4_evaluation(trials_per_condition=50)
