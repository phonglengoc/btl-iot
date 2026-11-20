"""
Edge TinyML 1D-CNN Training, INT8 Quantization & ESP32-S3 C++ Header Exporter (`T04`, `T05`).

Architecture (matches Table 3.3 of `spec_btl_iot_fall_detection.tex`):
  Input X_edge: (Batch, 100, 16) -> transposed to (Batch, 16, 100) for Conv1d
    1. Conv1D(16 -> 16, kernel_size=5, padding=2) + ReLU
    2. MaxPool1D(kernel_size=2, stride=2) -> length 50
    3. Conv1D(16 -> 32, kernel_size=3, padding=1) + ReLU
    4. GlobalAvgPool1D -> (Batch, 32)
    5. Dense(32 -> 16) + ReLU
    6. Dense(16 -> 4)  + Softmax

Trains on NVIDIA GeForce RTX 5070 GPU (`cuda:0`), performs symmetric INT8 weight & activation
quantization, verifies FP32 vs INT8 accuracy/F1 on held-out test data, and exports:
  - `firmware/node2_rx_edge_ai/include/model_data.h` (C++ INT8 weights + SOS filter coeffs)
  - `ai_training/models/tinyml_1dcnn_int8.npz` (used by Hardware-Free Virtual Node Simulator)
  - `ai_training/artifacts/tinyml_confusion_matrix.png` & `tinyml_metrics.json`
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn as nn
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader, TensorDataset

from csi_dsp_common import (
    BUTTER_SOS,
    CLASS_NAMES,
    EDGE_WINDOW_SAMPLES,
    NUM_CLASSES,
    NUM_EDGE_CHANNELS,
    extract_edge_1dcnn_tensor,
)
from csi_physics_generator import generate_full_dataset


class EdgeTinyML1DCNN(nn.Module):
    """Exact 1D-CNN architecture specified in Table 3.3 for ESP32-S3 TinyML (T04)."""

    def __init__(self) -> None:
        super().__init__()
        self.conv1 = nn.Conv1d(NUM_EDGE_CHANNELS, 16, kernel_size=5, stride=1, padding=2)
        self.relu1 = nn.ReLU()
        self.pool1 = nn.MaxPool1d(kernel_size=2, stride=2)
        self.conv2 = nn.Conv1d(16, 32, kernel_size=3, stride=1, padding=1)
        self.relu2 = nn.ReLU()
        self.fc1 = nn.Linear(32, 16)
        self.relu3 = nn.ReLU()
        self.fc2 = nn.Linear(16, NUM_CLASSES)

    def forward(self, x_time_ch: torch.Tensor) -> torch.Tensor:
        # x_time_ch: (B, 100, 16) -> (B, 16, 100)
        x = x_time_ch.permute(0, 2, 1)
        x = self.pool1(self.relu1(self.conv1(x)))  # (B, 16, 50)
        x = self.relu2(self.conv2(x))              # (B, 32, 50)
        x = x.mean(dim=-1)                         # GlobalAvgPool1D -> (B, 32)
        x = self.relu3(self.fc1(x))                # (B, 16)
        logits = self.fc2(x)                       # (B, 4)
        return logits


def quantize_tensor_int8(arr: np.ndarray) -> tuple[np.ndarray, float]:
    """Symmetric per-tensor INT8 quantization: w_float ~= w_int8 * scale."""
    max_abs = float(np.max(np.abs(arr)))
    scale = max(max_abs / 127.0, 1e-8)
    q = np.clip(np.rint(arr / scale), -127, 127).astype(np.int8)
    return q, scale


def run_int8_1dcnn_reference(x_batch: np.ndarray, q_params: dict) -> np.ndarray:
    """
    Exact bit-accurate NumPy simulation of the C++ INT8 inference kernel (`tinyml_infer.h`)
    running on ESP32-S3: quantizes activations to INT8, accumulates MACs in INT32, rescales + bias + ReLU!
    x_batch: (N, 100, 16) float32
    Returns probabilities: (N, 4) float32
    """
    probs_all = np.zeros((len(x_batch), NUM_CLASSES), dtype=np.float32)

    in_scale = float(q_params["input_scale"])
    w1, s_w1, b1 = q_params["conv1_w"], float(q_params["conv1_s"]), q_params["conv1_b"]
    s_a1 = float(q_params["act1_s"])
    w2, s_w2, b2 = q_params["conv2_w"], float(q_params["conv2_s"]), q_params["conv2_b"]
    s_a2 = float(q_params["act2_s"])
    w3, s_w3, b3 = q_params["fc1_w"], float(q_params["fc1_s"]), q_params["fc1_b"]
    s_a3 = float(q_params["act3_s"])
    w4, s_w4, b4 = q_params["fc2_w"], float(q_params["fc2_s"]), q_params["fc2_b"]

    for n in range(len(x_batch)):
        # 0. Quantize input (100, 16) to int8
        x_q = np.clip(np.rint(x_batch[n] / in_scale), -127, 127).astype(np.int32)  # (100, 16)

        # 1. Conv1D(16->16, k=5, pad=2) in INT32 accumulator
        # w1 shape: (16_out, 16_in, 5_k)
        c1_out = np.zeros((100, 16), dtype=np.float32)
        for t in range(100):
            acc = np.zeros(16, dtype=np.int32)
            for k in range(5):
                ti = t + k - 2
                if 0 <= ti < 100:
                    acc += w1[:, :, k].astype(np.int32) @ x_q[ti]
            val = acc.astype(np.float32) * (in_scale * s_w1) + b1
            c1_out[t] = np.maximum(val, 0.0)

        # MaxPool1D(2) -> (50, 16) & quantize activation to INT8
        p1 = np.maximum(c1_out[0::2], c1_out[1::2])
        p1_q = np.clip(np.rint(p1 / s_a1), 0, 127).astype(np.int32)

        # 2. Conv1D(16->32, k=3, pad=1) in INT32 accumulator
        # w2 shape: (32_out, 16_in, 3_k)
        c2_out = np.zeros((50, 32), dtype=np.float32)
        for t in range(50):
            acc = np.zeros(32, dtype=np.int32)
            for k in range(3):
                ti = t + k - 1
                if 0 <= ti < 50:
                    acc += w2[:, :, k].astype(np.int32) @ p1_q[ti]
            val = acc.astype(np.float32) * (s_a1 * s_w2) + b2
            c2_out[t] = np.maximum(val, 0.0)

        # GlobalAvgPool1D -> (32,) & quantize to INT8
        gap = np.mean(c2_out, axis=0)
        gap_q = np.clip(np.rint(gap / s_a2), 0, 127).astype(np.int32)

        # 3. Dense(32->16) + ReLU & quantize to INT8
        acc3 = w3.astype(np.int32) @ gap_q
        fc1 = np.maximum(acc3.astype(np.float32) * (s_a2 * s_w3) + b3, 0.0)
        fc1_q = np.clip(np.rint(fc1 / s_a3), 0, 127).astype(np.int32)

        # 4. Dense(16->4) + Softmax
        acc4 = w4.astype(np.int32) @ fc1_q
        logits = acc4.astype(np.float32) * (s_a3 * s_w4) + b4
        logits -= np.max(logits)
        exps = np.exp(logits)
        probs_all[n] = exps / np.sum(exps)

    return probs_all


def _format_c_array_int8(name: str, arr: np.ndarray) -> str:
    flat = arr.flatten()
    vals = ", ".join(str(int(v)) for v in flat)
    return f"alignas(16) static const int8_t {name}[{len(flat)}] = {{\n    {vals}\n}};\n"


def _format_c_array_float(name: str, arr: np.ndarray) -> str:
    flat = arr.flatten()
    vals = ", ".join(f"{float(v):.8e}f" for v in flat)
    return f"alignas(16) static const float {name}[{len(flat)}] = {{\n    {vals}\n}};\n"


def export_cpp_model_header(header_path: Path, q_params: dict, metrics: dict) -> None:
    """Export standalone INT8 weights + Butterworth SOS coefficients to C++ header (`T04`, `T05`)."""
    header_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "#ifndef IOT_BTL_TINYML_MODEL_DATA_H",
        "#define IOT_BTL_TINYML_MODEL_DATA_H",
        "",
        "/**",
        " * @file model_data.h",
        " * @brief Auto-generated INT8 Quantized 1D-CNN TinyML Weights & Butterworth SOS Coefficients",
        " *        Trained on NVIDIA GeForce RTX 5070 GPU via `ai_training/train_tinyml_1dcnn.py` (T04, T05).",
        f" *        Test Accuracy (INT8): {metrics['int8_accuracy']*100:.2f}% | Macro F1: {metrics['int8_macro_f1']*100:.2f}%",
        " */",
        "",
        "#include <stdint.h>",
        "",
        f"#define TINYML_INPUT_SCALE   ({q_params['input_scale']:.8e}f)",
        f"#define TINYML_CONV1_W_SCALE ({q_params['conv1_s']:.8e}f)",
        f"#define TINYML_ACT1_SCALE    ({q_params['act1_s']:.8e}f)",
        f"#define TINYML_CONV2_W_SCALE ({q_params['conv2_s']:.8e}f)",
        f"#define TINYML_ACT2_SCALE    ({q_params['act2_s']:.8e}f)",
        f"#define TINYML_FC1_W_SCALE   ({q_params['fc1_s']:.8e}f)",
        f"#define TINYML_ACT3_SCALE    ({q_params['act3_s']:.8e}f)",
        f"#define TINYML_FC2_W_SCALE   ({q_params['fc2_s']:.8e}f)",
        "",
        "// 4th-Order Butterworth Bandpass (0.2 - 15.0 Hz @ 100 Hz) -> 4 Cascaded Biquad Sections [b0,b1,b2,a0,a1,a2]",
        _format_c_array_float("CSI_BUTTER_SOS", BUTTER_SOS),
        "// Layer 1: Conv1D(in=16, out=16, k=5, pad=2) INT8 weights [16][16][5] = 1280 bytes",
        _format_c_array_int8("TINYML_CONV1_W", q_params["conv1_w"]),
        _format_c_array_float("TINYML_CONV1_B", q_params["conv1_b"]),
        "// Layer 2: Conv1D(in=16, out=32, k=3, pad=1) INT8 weights [32][16][3] = 1536 bytes",
        _format_c_array_int8("TINYML_CONV2_W", q_params["conv2_w"]),
        _format_c_array_float("TINYML_CONV2_B", q_params["conv2_b"]),
        "// Layer 3: Dense(in=32, out=16) INT8 weights [16][32] = 512 bytes",
        _format_c_array_int8("TINYML_FC1_W", q_params["fc1_w"]),
        _format_c_array_float("TINYML_FC1_B", q_params["fc1_b"]),
        "// Layer 4: Dense(in=16, out=4) INT8 weights [4][16] = 64 bytes",
        _format_c_array_int8("TINYML_FC2_W", q_params["fc2_w"]),
        _format_c_array_float("TINYML_FC2_B", q_params["fc2_b"]),
        "#endif // IOT_BTL_TINYML_MODEL_DATA_H",
        "",
    ]
    header_path.write_text("\n".join(lines), encoding="utf-8")


def train_and_export_tinyml(
    dataset_dir: Path,
    models_dir: Path,
    artifacts_dir: Path,
    firmware_header_path: Path,
    epochs: int = 65,
    seed: int = 2026,
) -> dict:
    torch.manual_seed(seed)
    np.random.seed(seed)

    npz_path = dataset_dir / "csi_multimodal_dataset.npz"
    if not npz_path.exists():
        generate_full_dataset(dataset_dir, samples_per_class=350, seed=seed)

    data = np.load(npz_path, allow_pickle=True)
    amps_52 = data["amps_52"]  # (N, 200, 52)
    labels = data["labels"]    # (N,)
    temps = data["temps"]
    humis = data["humis"]

    print(f"[TinyML T04/T05] Extracting DSP tensors ({EDGE_WINDOW_SAMPLES}x{NUM_EDGE_CHANNELS}) for {len(labels)} windows...")
    x_all = np.stack(
        [extract_edge_1dcnn_tensor(amps_52[i], float(temps[i]), float(humis[i])) for i in range(len(labels))],
        axis=0,
    )

    idx_all = np.arange(len(labels))
    idx_train, idx_test = train_test_split(idx_all, test_size=0.22, stratify=labels, random_state=seed)
    idx_train, idx_val = train_test_split(idx_train, test_size=0.15, stratify=labels[idx_train], random_state=seed)

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    gpu_name = torch.cuda.get_device_name(0) if device.type == "cuda" else "CPU"
    print(f"[TinyML T04/T05] Training Edge 1D-CNN on {device} ({gpu_name})...")

    train_loader = DataLoader(
        TensorDataset(torch.from_numpy(x_all[idx_train]), torch.from_numpy(labels[idx_train])),
        batch_size=32,
        shuffle=True,
    )

    model = EdgeTinyML1DCNN().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-3, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    criterion = nn.CrossEntropyLoss()

    best_val_acc = 0.0
    best_state = None
    history_loss, history_val_acc = [], []

    t0 = time.perf_counter()
    for ep in range(1, epochs + 1):
        model.train()
        ep_loss = 0.0
        for bx, by in train_loader:
            bx, by = bx.to(device), by.to(device)
            # Subtle amplitude jitter augmentation during training
            bx_aug = bx * (1.0 + 0.05 * torch.randn((bx.size(0), 1, 1), device=device))
            optimizer.zero_grad()
            logits = model(bx_aug)
            loss = criterion(logits, by)
            loss.backward()
            optimizer.step()
            ep_loss += float(loss.item()) * len(by)
        scheduler.step()

        model.eval()
        with torch.no_grad():
            val_logits = model(torch.from_numpy(x_all[idx_val]).to(device))
            val_preds = val_logits.argmax(dim=1).cpu().numpy()
            val_acc = float(accuracy_score(labels[idx_val], val_preds))

        history_loss.append(ep_loss / len(idx_train))
        history_val_acc.append(val_acc)
        if val_acc >= best_val_acc:
            best_val_acc = val_acc
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    train_time_s = time.perf_counter() - t0
    assert best_state is not None
    model.load_state_dict(best_state)
    model.eval()

    # Evaluate FP32 on Test Split
    with torch.no_grad():
        fp32_logits = model(torch.from_numpy(x_all[idx_test]).to(device)).cpu().numpy()
        fp32_preds = np.argmax(fp32_logits, axis=1)
    fp32_acc = float(accuracy_score(labels[idx_test], fp32_preds))
    fp32_f1 = float(f1_score(labels[idx_test], fp32_preds, average="macro"))

    # Calibrate activation ranges on training set for symmetric INT8 post-training quantization
    with torch.no_grad():
        cal_x = torch.from_numpy(x_all[idx_train]).to(device).permute(0, 2, 1)
        a1 = model.pool1(model.relu1(model.conv1(cal_x)))
        a2 = model.relu2(model.conv2(a1)).mean(dim=-1)
        a3 = model.relu3(model.fc1(a2))

        p999_in = float(np.percentile(np.abs(x_all[idx_train]), 99.95))
        p999_a1 = float(torch.quantile(a1.flatten(), 0.9995).item())
        p999_a2 = float(torch.quantile(a2.flatten(), 0.9995).item())
        p999_a3 = float(torch.quantile(a3.flatten(), 0.9995).item())

    conv1_w_q, conv1_s = quantize_tensor_int8(model.conv1.weight.detach().cpu().numpy())
    conv2_w_q, conv2_s = quantize_tensor_int8(model.conv2.weight.detach().cpu().numpy())
    fc1_w_q, fc1_s = quantize_tensor_int8(model.fc1.weight.detach().cpu().numpy())
    fc2_w_q, fc2_s = quantize_tensor_int8(model.fc2.weight.detach().cpu().numpy())

    q_params = {
        "input_scale": max(p999_in / 127.0, 1e-6),
        "conv1_w": conv1_w_q,
        "conv1_s": conv1_s,
        "conv1_b": model.conv1.bias.detach().cpu().numpy().astype(np.float32),
        "act1_s": max(p999_a1 / 127.0, 1e-6),
        "conv2_w": conv2_w_q,
        "conv2_s": conv2_s,
        "conv2_b": model.conv2.bias.detach().cpu().numpy().astype(np.float32),
        "act2_s": max(p999_a2 / 127.0, 1e-6),
        "fc1_w": fc1_w_q,
        "fc1_s": fc1_s,
        "fc1_b": model.fc1.bias.detach().cpu().numpy().astype(np.float32),
        "act3_s": max(p999_a3 / 127.0, 1e-6),
        "fc2_w": fc2_w_q,
        "fc2_s": fc2_s,
        "fc2_b": model.fc2.bias.detach().cpu().numpy().astype(np.float32),
    }

    # Evaluate exact INT8 quantized inference on Test Split
    int8_probs = run_int8_1dcnn_reference(x_all[idx_test], q_params)
    int8_preds = np.argmax(int8_probs, axis=1)
    int8_acc = float(accuracy_score(labels[idx_test], int8_preds))
    int8_macro_f1 = float(f1_score(labels[idx_test], int8_preds, average="macro"))
    int8_fall_f1 = float(f1_score(labels[idx_test] == 3, int8_preds == 3))
    cm = confusion_matrix(labels[idx_test], int8_preds)

    models_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(models_dir / "tinyml_1dcnn_int8.npz", **q_params)
    torch.save(best_state, models_dir / "tinyml_1dcnn_fp32.pt")

    metrics = {
        "device_trained_on": gpu_name,
        "train_time_sec": round(train_time_s, 3),
        "fp32_accuracy": round(fp32_acc, 4),
        "fp32_macro_f1": round(fp32_f1, 4),
        "int8_accuracy": round(int8_acc, 4),
        "int8_macro_f1": round(int8_macro_f1, 4),
        "int8_fall_f1": round(int8_fall_f1, 4),
        "quantization_acc_drop": round(fp32_acc - int8_acc, 4),
        "int8_weight_bytes": int(conv1_w_q.nbytes + conv2_w_q.nbytes + fc1_w_q.nbytes + fc2_w_q.nbytes),
        "confusion_matrix": cm.tolist(),
        "classification_report": classification_report(labels[idx_test], int8_preds, target_names=CLASS_NAMES, output_dict=True),
        # Save 8 test vectors + INT8 probabilities so C++ `g++` unit tests verify exact agreement!
        "cpp_test_inputs": x_all[idx_test[:8]].tolist(),
        "cpp_test_expected_probs": int8_probs[:8].tolist(),
        "cpp_test_labels": labels[idx_test[:8]].tolist(),
    }
    (artifacts_dir / "tinyml_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    export_cpp_model_header(firmware_header_path, q_params, metrics)

    # Plot Confusion Matrix & Training Curve
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), dpi=160)
    axes[0].plot(history_loss, label="Train Cross-Entropy", color="#1f77b4", lw=2)
    ax_twin = axes[0].twinx()
    ax_twin.plot([a * 100 for a in history_val_acc], label="Val Accuracy (%)", color="#2ca02c", lw=2)
    axes[0].set_title("Edge TinyML 1D-CNN Training Curve")
    axes[0].set_xlabel("Epoch")
    axes[0].set_ylabel("Loss")
    ax_twin.set_ylabel("Val Accuracy (%)")
    axes[0].grid(True, alpha=0.3)

    im = axes[1].imshow(cm, cmap="Blues")
    axes[1].set_xticks(range(NUM_CLASSES), CLASS_NAMES, rotation=20)
    axes[1].set_yticks(range(NUM_CLASSES), CLASS_NAMES)
    axes[1].set_xlabel("Predicted Class (INT8 TinyML)")
    axes[1].set_ylabel("Ground Truth")
    axes[1].set_title(f"INT8 Confusion Matrix (Acc={int8_acc*100:.1f}%, Fall F1={int8_fall_f1*100:.1f}%)")
    for r in range(NUM_CLASSES):
        for c in range(NUM_CLASSES):
            axes[1].text(c, r, str(cm[r, c]), ha="center", va="center", color="white" if cm[r, c] > cm.max() / 2 else "black", fontweight="bold")
    fig.colorbar(im, ax=axes[1], fraction=0.046)
    fig.tight_layout()
    fig.savefig(artifacts_dir / "tinyml_confusion_matrix.png")
    plt.close(fig)

    print(
        f"[TinyML T04/T05] Done in {train_time_s:.2f}s | FP32 Acc={fp32_acc*100:.2f}% | "
        f"INT8 Acc={int8_acc*100:.2f}% | Fall F1={int8_fall_f1*100:.2f}% | "
        f"Exported -> {firmware_header_path}"
    )
    return metrics


if __name__ == "__main__":
    base = Path(__file__).parent
    parser = argparse.ArgumentParser(description="Train & Quantize Edge TinyML 1D-CNN (T04, T05)")
    parser.add_argument("--epochs", type=int, default=65)
    args = parser.parse_args()

    train_and_export_tinyml(
        dataset_dir=base / "dataset",
        models_dir=base / "models",
        artifacts_dir=base / "artifacts",
        firmware_header_path=base.parent / "firmware" / "node2_rx_edge_ai" / "include" / "model_data.h",
        epochs=args.epochs,
    )
