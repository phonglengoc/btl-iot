"""
Train Lightweight PC Gateway AI Model using scikit-learn (`T05`, `T09`).

Pipeline (pure CPU, no GPU/CUDA, no PyTorch, no ONNX, no C++ TinyML):
  1. Load labeled 20 Hz CSI CSV files (`empty.csv`, `stationary.csv`, `walking.csv`, `fall.csv`).
  2. Smooth with Moving Average & extract 12 basic statistical features (Std, Max-Min, Peak-to-Tail).
  3. Train & evaluate a `RandomForestClassifier` (primary Gateway AI model) and compare with `MLPClassifier`.
  4. Save the trained model to `ai_training/models/fall_rf_model.joblib` + confusion matrix & metrics JSON.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import (
    ConfusionMatrixDisplay,
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from csi_signal_features import (
    CLASS_NAMES,
    CLASS_TO_IDX,
    FEATURE_NAMES,
    FS_CSI_HZ,
    NUM_SUBCARRIERS,
    WINDOW_SAMPLES,
    extract_statistical_features,
)

SC_COLS = [f"sc_{k:02d}" for k in range(NUM_SUBCARRIERS)]


def load_features_from_csv_dir(
    dataset_dir: Path,
    augment_copies: int = 0,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """Read labeled CSV files and compute the 18 statistical features for every 2-second window."""
    features_list: list[np.ndarray] = []
    labels_list: list[int] = []
    rng = np.random.default_rng(seed)

    for label in CLASS_NAMES:
        csv_path = dataset_dir / f"{label.lower()}.csv"
        if not csv_path.exists():
            raise FileNotFoundError(
                f"Missing {csv_path}! Run `python ai_training/csi_data_generator.py` first."
            )
        df = pd.read_csv(csv_path)
        for _, grp in df.groupby("sample_id", sort=True):
            grp_sorted = grp.sort_values("frame_idx")
            amps = grp_sorted[SC_COLS].to_numpy(dtype=np.float32)
            if amps.shape[0] < WINDOW_SAMPLES:
                continue
            win = amps[:WINDOW_SAMPLES]
            features_list.append(extract_statistical_features(win))
            labels_list.append(CLASS_TO_IDX[label])

            # Optional small-jitter augmentation when blending limited real-room recordings
            for _ in range(max(0, int(augment_copies))):
                scale_j = float(rng.uniform(0.92, 1.08))
                noise_j = rng.normal(0.0, 0.08, size=win.shape).astype(np.float32)
                aug = np.clip(win * scale_j + noise_j, 0.5, 120.0)
                if label in ("EMPTY", "STATIONARY", "WALKING"):
                    shift = int(rng.integers(-4, 5))
                    aug = np.roll(aug, shift=shift, axis=0)
                features_list.append(extract_statistical_features(aug))
                labels_list.append(CLASS_TO_IDX[label])

    return np.stack(features_list, axis=0), np.array(labels_list, dtype=np.int64)


def train_and_export(
    dataset_dir: Path,
    models_dir: Path,
    artifacts_dir: Path,
    seed: int = 42,
    extra_real_dir: Path | None = None,
    real_weight_copies: int = 6,
) -> dict:
    models_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    print(f"[T09 Training - Pure CPU] Loading CSV dataset from {dataset_dir}...")
    X, y = load_features_from_csv_dir(dataset_dir)
    real_windows = 0
    if extra_real_dir is not None and extra_real_dir.exists():
        all_real_present = all((extra_real_dir / f"{c.lower()}.csv").exists() for c in CLASS_NAMES)
        if all_real_present:
            X_real, y_real = load_features_from_csv_dir(
                extra_real_dir, augment_copies=real_weight_copies, seed=seed + 7
            )
            real_windows = len(y_real)
            X = np.vstack([X, X_real])
            y = np.concatenate([y, y_real])
            print(f"  -> Blended {real_windows} real-room (augmented) windows from {extra_real_dir}")
    print(f"  -> Total dataset: {len(y)} windows x {X.shape[1]} scale-invariant statistical features.")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=seed, stratify=y
    )

    # 1. Primary Model: Tuned Random Forest (100% CPU, decisive probabilities, ~2-4ms infer)
    t0 = time.perf_counter()
    rf_model = RandomForestClassifier(
        n_estimators=220,
        max_depth=18,
        min_samples_leaf=1,
        max_features="sqrt",
        class_weight="balanced_subsample",
        random_state=seed,
        n_jobs=-1,
    )
    rf_model.fit(X_train, y_train)
    rf_train_sec = time.perf_counter() - t0

    y_pred_rf = rf_model.predict(X_test)
    rf_acc = float(accuracy_score(y_test, y_pred_rf))
    rf_macro_f1 = float(f1_score(y_test, y_pred_rf, average="macro"))
    rf_fall_f1 = float(f1_score(y_test == CLASS_TO_IDX["FALL"], y_pred_rf == CLASS_TO_IDX["FALL"]))

    # 5-fold Stratified Cross-Validation on pure CPU
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    cv_scores = cross_val_score(
        RandomForestClassifier(
            n_estimators=160,
            max_depth=18,
            min_samples_leaf=1,
            max_features="sqrt",
            class_weight="balanced_subsample",
            random_state=seed,
            n_jobs=-1,
        ),
        X,
        y,
        cv=cv,
        scoring="f1_macro",
        n_jobs=-1,
    )
    rf_cv_macro_f1 = float(np.mean(cv_scores))

    # Benchmark single-sample CPU latency (with n_jobs=1 as deployed on Gateway)
    rf_model.n_jobs = 1
    t_lat0 = time.perf_counter()
    n_bench = min(80, len(X_test))
    for i in range(n_bench):
        rf_model.predict_proba(X_test[i : i + 1])
    rf_infer_ms = (time.perf_counter() - t_lat0) * 1000.0 / max(1, n_bench)

    # 2. Secondary Comparison Model A: HistGradientBoosting (pure CPU)
    hgb_model = HistGradientBoostingClassifier(
        max_iter=180,
        max_depth=8,
        learning_rate=0.08,
        random_state=seed,
    )
    hgb_model.fit(X_train, y_train)
    y_pred_hgb = hgb_model.predict(X_test)
    hgb_acc = float(accuracy_score(y_test, y_pred_hgb))
    hgb_macro_f1 = float(f1_score(y_test, y_pred_hgb, average="macro"))

    # 3. Secondary Comparison Model B: MLP (Multi-Layer Perceptron, pure CPU)
    mlp_model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("mlp", MLPClassifier(hidden_layer_sizes=(48, 24), max_iter=400, random_state=seed)),
        ]
    )
    mlp_model.fit(X_train, y_train)
    y_pred_mlp = mlp_model.predict(X_test)
    mlp_acc = float(accuracy_score(y_test, y_pred_mlp))
    mlp_macro_f1 = float(f1_score(y_test, y_pred_mlp, average="macro"))

    # Save trained Random Forest model bundle for Gateway (`T09`)
    model_path = models_dir / "fall_rf_model.joblib"
    bundle = {
        "model": rf_model,
        "model_name": "RandomForestClassifier (scikit-learn CPU, 18 features)",
        "class_names": CLASS_NAMES,
        "feature_names": FEATURE_NAMES,
        "fs_hz": FS_CSI_HZ,
        "window_samples": WINDOW_SAMPLES,
        "test_accuracy": round(rf_acc, 4),
        "test_macro_f1": round(rf_macro_f1, 4),
        "cv5_macro_f1": round(rf_cv_macro_f1, 4),
        "real_blended_windows": int(real_windows),
    }
    joblib.dump(bundle, model_path)

    # Save Confusion Matrix PNG for easy inspection / presentation
    cm = confusion_matrix(y_test, y_pred_rf)
    fig, ax = plt.subplots(figsize=(6.2, 5.2), dpi=130)
    disp = ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=CLASS_NAMES)
    disp.plot(ax=ax, cmap="Blues", colorbar=False)
    ax.set_title(
        f"Gateway AI (Random Forest CPU) Confusion Matrix\n"
        f"Acc={rf_acc*100:.2f}% | Macro F1={rf_macro_f1*100:.2f}% | 5-Fold CV={rf_cv_macro_f1*100:.2f}%"
    )
    fig.tight_layout()
    cm_path = artifacts_dir / "confusion_matrix.png"
    fig.savefig(cm_path)
    plt.close(fig)

    # Save Feature Importance bar chart
    importances = rf_model.feature_importances_
    order = np.argsort(importances)
    fig2, ax2 = plt.subplots(figsize=(7.6, 5.4), dpi=130)
    ax2.barh([FEATURE_NAMES[i] for i in order], importances[order], color="#2563eb")
    ax2.set_title("Random Forest Feature Importance (18 Scale-Invariant CSI Features)")
    ax2.set_xlabel("Importance")
    fig2.tight_layout()
    fi_path = artifacts_dir / "feature_importance.png"
    fig2.savefig(fi_path)
    plt.close(fig2)

    metrics = {
        "primary_model": "RandomForestClassifier (scikit-learn CPU, 18 features)",
        "model_path": str(model_path),
        "total_windows": int(len(y)),
        "real_blended_windows": int(real_windows),
        "train_windows": int(len(y_train)),
        "test_windows": int(len(y_test)),
        "rf_accuracy": round(rf_acc, 4),
        "rf_macro_f1": round(rf_macro_f1, 4),
        "rf_fall_f1": round(rf_fall_f1, 4),
        "rf_cv5_macro_f1": round(rf_cv_macro_f1, 4),
        "rf_train_seconds": round(rf_train_sec, 3),
        "rf_single_infer_ms": round(rf_infer_ms, 3),
        "hgb_comparison_accuracy": round(hgb_acc, 4),
        "hgb_comparison_macro_f1": round(hgb_macro_f1, 4),
        "mlp_comparison_accuracy": round(mlp_acc, 4),
        "mlp_comparison_macro_f1": round(mlp_macro_f1, 4),
        "feature_importance": {
            name: round(float(val), 4) for name, val in zip(FEATURE_NAMES, importances)
        },
        "classification_report": classification_report(
            y_test, y_pred_rf, target_names=CLASS_NAMES, output_dict=True
        ),
    }
    metrics_path = artifacts_dir / "gateway_ai_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    print(
        f"[T09 Training - Pure CPU] Done in {rf_train_sec:.2f}s! "
        f"RF Acc={rf_acc*100:.2f}%, Macro F1={rf_macro_f1*100:.2f}%, Fall F1={rf_fall_f1*100:.2f}%, "
        f"5-Fold CV={rf_cv_macro_f1*100:.2f}% | Infer={rf_infer_ms:.2f} ms/win | "
        f"HGB={hgb_acc*100:.2f}% | MLP={mlp_acc*100:.2f}% | Saved -> {model_path}"
    )
    return metrics


if __name__ == "__main__":
    base = Path(__file__).parent
    parser = argparse.ArgumentParser(description="Train pure-CPU scikit-learn Gateway Fall Detection AI (T09)")
    parser.add_argument("--dataset-dir", type=Path, default=base / "dataset")
    parser.add_argument("--extra-real-dir", type=Path, default=None, help="Optional real room CSV folder to blend")
    parser.add_argument("--real-weight-copies", type=int, default=6, help="Augmentation multiplier for real room CSVs")
    parser.add_argument("--models-dir", type=Path, default=base / "models")
    parser.add_argument("--artifacts-dir", type=Path, default=base / "artifacts")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    train_and_export(
        args.dataset_dir,
        args.models_dir,
        args.artifacts_dir,
        seed=args.seed,
        extra_real_dir=args.extra_real_dir,
        real_weight_copies=args.real_weight_copies,
    )
