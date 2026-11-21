"""
Quantitative Evaluation Runner for the Simplified Gateway Random Forest AI (`T09`).

Evaluates across distances (`1.0m`, `2.0m`, `3.0m`, `NLOS` behind wall),
hard-negative `sit_fast` anti-false-alarm specificity, and pure-CPU inference latency.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
import numpy as np
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

ROOT_DIR = Path(__file__).resolve().parents[1]
for p in (ROOT_DIR / "ai_training", ROOT_DIR / "gateway"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from coreiot_sync import evaluate_coreiot_rulechain_logic  # noqa: E402
from csi_data_generator import synthesize_csi_window  # noqa: E402
from csi_signal_features import CLASS_NAMES  # noqa: E402
from gateway_ai import GatewayFallAI  # noqa: E402


def run_evaluation(trials_per_condition: int = 50, seed: int = 202611) -> dict:
    rng = np.random.default_rng(seed)
    ai = GatewayFallAI()

    conditions = [
        ("LOS_1.0m", 1.0, False),
        ("LOS_2.0m", 2.0, False),
        ("LOS_3.0m", 3.0, False),
        ("NLOS_2.5m_Wall", 2.5, True),
    ]

    by_condition = {}
    all_true, all_pred = [], []
    latencies_ms = []

    for cond_name, dist_m, is_nlos in conditions:
        y_t, y_p = [], []
        for cls_idx, label in enumerate(CLASS_NAMES):
            for _ in range(trials_per_condition):
                win = synthesize_csi_window(label=label, rng=rng, distance_m=dist_m, nlos=is_nlos)
                out = ai.predict_window(win["amps_52"])
                y_t.append(cls_idx)
                y_p.append(CLASS_NAMES.index(out["state"]))
                latencies_ms.append(out["infer_ms"])

        by_condition[cond_name] = {
            "trials_total": len(y_t),
            "accuracy_pct": round(float(accuracy_score(y_t, y_p)) * 100, 2),
            "fall_f1_pct": round(float(f1_score(np.array(y_t) == 3, np.array(y_p) == 3)) * 100, 2),
        }
        all_true.extend(y_t)
        all_pred.extend(y_p)

    # Hard-negative `sit_fast` false-alarm test
    sit_fast_false_alarms = 0
    for _ in range(trials_per_condition):
        win = synthesize_csi_window(label="STATIONARY", rng=rng, distance_m=2.0, hard_negative="sit_fast")
        out = ai.predict_window(win["amps_52"])
        rc = evaluate_coreiot_rulechain_logic(
            {"csi_state": out["state"], "ai_conf": out["confidence"], "fall_threshold": 0.80}
        )
        if rc["alarm_type"] == "CRITICAL_FALL_EMERGENCY":
            sit_fast_false_alarms += 1

    y_true_arr = np.array(all_true)
    y_pred_arr = np.array(all_pred)
    summary = {
        "model": ai.model_name,
        "total_windows_evaluated": int(len(all_true)),
        "overall_accuracy_pct": round(float(accuracy_score(y_true_arr, y_pred_arr)) * 100, 2),
        "overall_fall_precision_pct": round(float(precision_score(y_true_arr == 3, y_pred_arr == 3)) * 100, 2),
        "overall_fall_recall_pct": round(float(recall_score(y_true_arr == 3, y_pred_arr == 3)) * 100, 2),
        "overall_fall_f1_pct": round(float(f1_score(y_true_arr == 3, y_pred_arr == 3)) * 100, 2),
        "by_distance": by_condition,
        "sit_fast_specificity_pct": round((1.0 - sit_fast_false_alarms / trials_per_condition) * 100, 2),
        "cpu_infer_mean_ms": round(float(np.mean(latencies_ms)), 3),
    }

    out_json = ROOT_DIR / "tests" / "quantitative_eval_results.json"
    out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Quantitative Eval] Evaluated {len(all_true)} windows -> {out_json}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


if __name__ == "__main__":
    run_evaluation(trials_per_condition=50)
