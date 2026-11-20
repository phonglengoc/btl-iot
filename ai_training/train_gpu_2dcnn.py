"""
Gateway GPU STFT Spectrogram 2D-CNN Training, ONNX Export & CUDA Latency Benchmark (`T09`, `N04`).

Architecture (matches Table 3.3 of `HK261_Group01_IoTReport.tex`):
  Input S_gw: (Batch, 3, 64, 64) STFT Spectrogram
    1. Conv2D(3 -> 32, 3x3, pad=1) + BatchNorm2D + ReLU + MaxPool2D(2)   -> (32, 32, 32)
    2. Conv2D(32 -> 64, 3x3, pad=1) + BatchNorm2D + ReLU + MaxPool2D(2)  -> (64, 16, 16)
    3. Conv2D(64 -> 128, 3x3, pad=1) + BatchNorm2D + ReLU                -> (128, 16, 16)
    4. AdaptiveAvgPool2D((2, 4)) -> preserves frequency band x 4 temporal quadrants!
    5. Dropout(0.2) + Linear(1024 -> 4)

Trains directly on NVIDIA GeForce RTX 5070 Laptop GPU (`cuda:0`), exports static/dynamic ONNX graph,
runs ONNX Runtime `CUDAExecutionProvider` vs `CPUExecutionProvider` latency benchmarks (`N04`),
and evaluates Two-Stage Collaborative AI Fusion (`0.4 * Edge_INT8 + 0.6 * GPU_2DCNN`).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn as nn

_torch_lib = Path(torch.__file__).parent / "lib"
if _torch_lib.exists() and hasattr(os, "add_dll_directory"):
    os.add_dll_directory(str(_torch_lib))
    os.environ["PATH"] = str(_torch_lib) + os.pathsep + os.environ.get("PATH", "")

import onnx
import onnxruntime as ort
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader, TensorDataset

from csi_dsp_common import (
    CLASS_NAMES,
    NUM_CLASSES,
    extract_edge_1dcnn_tensor,
    extract_gateway_stft_spectrogram,
)
from csi_physics_generator import generate_full_dataset
from train_tinyml_1dcnn import run_int8_1dcnn_reference


class GatewaySpectrogram2DCNN(nn.Module):
    """Exact 2D-CNN Spectrogram classifier specified in Table 3.3 for PC Local Gateway GPU (T09, N04)."""

    def __init__(self) -> None:
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 32, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),
            nn.Conv2d(32, 64, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),
            nn.Conv2d(64, 128, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(128),
            nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool2d((2, 4)),
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Dropout(p=0.20),
            nn.Linear(128 * 2 * 4, NUM_CLASSES),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.classifier(self.features(x))


def benchmark_onnx_providers(onnx_path: Path, sample_batch_1: np.ndarray, sample_batch_16: np.ndarray) -> dict:
    """Benchmark CPUExecutionProvider vs CUDAExecutionProvider on RTX 5070 (`N04` proof)."""
    results: dict = {"available_providers": ort.get_available_providers()}

    for prov_label, providers in [
        ("cpu", ["CPUExecutionProvider"]),
        ("cuda_rtx5070", ["CUDAExecutionProvider", "CPUExecutionProvider"]),
    ]:
        if prov_label == "cuda_rtx5070" and "CUDAExecutionProvider" not in ort.get_available_providers():
            continue

        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess = ort.InferenceSession(str(onnx_path), sess_options=sess_opts, providers=providers)
        active_prov = sess.get_providers()[0]
        in_name = sess.get_inputs()[0].name

        # Warmup
        for _ in range(25):
            sess.run(None, {in_name: sample_batch_1})
            sess.run(None, {in_name: sample_batch_16})

        # Single-frame latency (real-time streaming)
        lat_b1 = []
        for _ in range(200):
            t0 = time.perf_counter()
            sess.run(None, {in_name: sample_batch_1})
            lat_b1.append((time.perf_counter() - t0) * 1000.0)

        # Multi-room Gateway batch=16 throughput latency (ms per spectrogram)
        lat_b16 = []
        for _ in range(150):
            t0 = time.perf_counter()
            sess.run(None, {in_name: sample_batch_16})
            lat_b16.append(((time.perf_counter() - t0) * 1000.0) / 16.0)

        results[prov_label] = {
            "active_provider": active_prov,
            "single_mean_ms": round(float(np.mean(lat_b1)), 3),
            "single_p95_ms": round(float(np.percentile(lat_b1, 95)), 3),
            "batch16_per_sample_ms": round(float(np.mean(lat_b16)), 3),
        }

    if "cpu" in results and "cuda_rtx5070" in results:
        results["speedup_single_x"] = round(
            results["cpu"]["single_mean_ms"] / max(results["cuda_rtx5070"]["single_mean_ms"], 1e-4), 2
        )
        results["speedup_batch16_x"] = round(
            results["cpu"]["batch16_per_sample_ms"] / max(results["cuda_rtx5070"]["batch16_per_sample_ms"], 1e-4), 2
        )
    return results


def train_and_export_gpu_2dcnn(
    dataset_dir: Path,
    models_dir: Path,
    artifacts_dir: Path,
    epochs: int = 45,
    seed: int = 2026,
) -> dict:
    torch.manual_seed(seed)
    np.random.seed(seed)

    npz_path = dataset_dir / "csi_multimodal_dataset.npz"
    if not npz_path.exists():
        generate_full_dataset(dataset_dir, samples_per_class=350, seed=seed)

    data = np.load(npz_path, allow_pickle=True)
    amps_52 = data["amps_52"]
    labels = data["labels"]
    temps = data["temps"]
    humis = data["humis"]

    print(f"[Gateway GPU T09/N04] Computing 64x64x3 STFT Spectrograms for {len(labels)} windows...")
    specs_all = np.stack(
        [extract_gateway_stft_spectrogram(amps_52[i], float(temps[i]), float(humis[i])) for i in range(len(labels))],
        axis=0,
    )

    idx_all = np.arange(len(labels))
    idx_train, idx_test = train_test_split(idx_all, test_size=0.22, stratify=labels, random_state=seed)
    idx_train, idx_val = train_test_split(idx_train, test_size=0.15, stratify=labels[idx_train], random_state=seed)

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    gpu_name = torch.cuda.get_device_name(0) if device.type == "cuda" else "CPU"
    print(f"[Gateway GPU T09/N04] Training Spectrogram 2D-CNN on {device} ({gpu_name})...")

    train_loader = DataLoader(
        TensorDataset(torch.from_numpy(specs_all[idx_train]), torch.from_numpy(labels[idx_train])),
        batch_size=32,
        shuffle=True,
    )

    model = GatewaySpectrogram2DCNN().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1.5e-3, weight_decay=2e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    criterion = nn.CrossEntropyLoss()

    best_val_f1 = 0.0
    best_state = None
    hist_loss, hist_val_f1 = [], []

    t0 = time.perf_counter()
    for ep in range(1, epochs + 1):
        model.train()
        ep_loss = 0.0
        for bx, by in train_loader:
            bx, by = bx.to(device), by.to(device)
            bx_aug = torch.clamp(bx + 0.03 * torch.randn_like(bx), 0.0, 3.0)
            optimizer.zero_grad()
            logits = model(bx_aug)
            loss = criterion(logits, by)
            loss.backward()
            optimizer.step()
            ep_loss += float(loss.item()) * len(by)
        scheduler.step()

        model.eval()
        with torch.no_grad():
            val_logits = model(torch.from_numpy(specs_all[idx_val]).to(device))
            val_preds = val_logits.argmax(dim=1).cpu().numpy()
            val_f1 = float(f1_score(labels[idx_val], val_preds, average="macro"))

        hist_loss.append(ep_loss / len(idx_train))
        hist_val_f1.append(val_f1)
        if val_f1 >= best_val_f1:
            best_val_f1 = val_f1
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    train_time_s = time.perf_counter() - t0
    assert best_state is not None
    model.load_state_dict(best_state)
    model.eval()

    with torch.no_grad():
        test_logits = model(torch.from_numpy(specs_all[idx_test]).to(device))
        gpu_probs = torch.softmax(test_logits, dim=1).cpu().numpy()
    gpu_preds = np.argmax(gpu_probs, axis=1)

    gpu_acc = float(accuracy_score(labels[idx_test], gpu_preds))
    gpu_macro_f1 = float(f1_score(labels[idx_test], gpu_preds, average="macro"))
    gpu_fall_f1 = float(f1_score(labels[idx_test] == 3, gpu_probs[:, 3] >= 0.5))
    cm_gpu = confusion_matrix(labels[idx_test], gpu_preds)

    # Export PyTorch checkpoint + ONNX graph
    models_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    pt_path = models_dir / "csi_spectrogram_2dcnn.pt"
    onnx_path = models_dir / "csi_spectrogram_2dcnn.onnx"
    torch.save(best_state, pt_path)

    model_cpu = GatewaySpectrogram2DCNN()
    model_cpu.load_state_dict(best_state)
    model_cpu.eval()
    dummy_in = torch.randn(1, 3, 64, 64, dtype=torch.float32)
    torch.onnx.export(
        model_cpu,
        dummy_in,
        str(onnx_path),
        input_names=["spectrogram"],
        output_names=["logits"],
        dynamic_axes={"spectrogram": {0: "batch_size"}, "logits": {0: "batch_size"}},
        opset_version=17,
        dynamo=False,
    )
    onnx_model = onnx.load(str(onnx_path))
    onnx.checker.check_model(onnx_model)

    # Evaluate Two-Stage Collaborative Fusion (0.4 * Edge_INT8 + 0.6 * GPU_2DCNN) matching RuleChain Listing 3.2!
    tinyml_npz = models_dir / "tinyml_1dcnn_int8.npz"
    fused_metrics = {}
    if tinyml_npz.exists():
        q_params = dict(np.load(tinyml_npz))
        x_edge_test = np.stack(
            [extract_edge_1dcnn_tensor(amps_52[i], float(temps[i]), float(humis[i])) for i in idx_test],
            axis=0,
        )
        edge_probs = run_int8_1dcnn_reference(x_edge_test, q_params)
        fused_probs = 0.4 * edge_probs + 0.6 * gpu_probs
        fused_preds = np.argmax(fused_probs, axis=1)
        fused_metrics = {
            "fused_accuracy": round(float(accuracy_score(labels[idx_test], fused_preds)), 4),
            "fused_macro_f1": round(float(f1_score(labels[idx_test], fused_preds, average="macro")), 4),
            "fused_fall_f1": round(float(f1_score(labels[idx_test] == 3, fused_preds == 3)), 4),
        }

    # Benchmark CPU vs RTX 5070 CUDA ONNX Runtime (`N04`)
    b1 = specs_all[idx_test[:1]].astype(np.float32)
    b16 = specs_all[idx_test[:16]].astype(np.float32)
    bench = benchmark_onnx_providers(onnx_path, b1, b16)

    metrics = {
        "gpu_device": gpu_name,
        "pytorch_version": torch.__version__,
        "onnxruntime_version": ort.__version__,
        "train_time_sec": round(train_time_s, 3),
        "onnx_size_kb": round(onnx_path.stat().st_size / 1024.0, 1),
        "gpu_accuracy": round(gpu_acc, 4),
        "gpu_macro_f1": round(gpu_macro_f1, 4),
        "gpu_fall_f1": round(gpu_fall_f1, 4),
        "two_stage_fusion": fused_metrics,
        "latency_benchmark": bench,
        "confusion_matrix": cm_gpu.tolist(),
        "classification_report": classification_report(labels[idx_test], gpu_preds, target_names=CLASS_NAMES, output_dict=True),
    }
    (artifacts_dir / "gpu_2dcnn_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    # Save visual figures for the report & README
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.6), dpi=160)
    axes[0].plot(hist_loss, label="Train Loss", color="#d62728", lw=2)
    ax2 = axes[0].twinx()
    ax2.plot([f * 100 for f in hist_val_f1], label="Val Macro-F1 (%)", color="#1f77b4", lw=2)
    axes[0].set_title(f"2D-CNN Training on {gpu_name}")
    axes[0].set_xlabel("Epoch")
    axes[0].set_ylabel("Cross-Entropy Loss")
    ax2.set_ylabel("Macro F1-Score (%)")
    axes[0].grid(True, alpha=0.3)

    im = axes[1].imshow(cm_gpu, cmap="Greens")
    axes[1].set_xticks(range(NUM_CLASSES), CLASS_NAMES, rotation=20)
    axes[1].set_yticks(range(NUM_CLASSES), CLASS_NAMES)
    axes[1].set_xlabel("Predicted Class (RTX 5070 ONNX)")
    axes[1].set_ylabel("Ground Truth")
    axes[1].set_title(f"2D-CNN Confusion Matrix (F1={gpu_macro_f1*100:.1f}%)")
    for r in range(NUM_CLASSES):
        for c in range(NUM_CLASSES):
            axes[1].text(c, r, str(cm_gpu[r, c]), ha="center", va="center", color="white" if cm_gpu[r, c] > cm_gpu.max() / 2 else "black", fontweight="bold")
    fig.colorbar(im, ax=axes[1], fraction=0.046)

    if "cpu" in bench and "cuda_rtx5070" in bench:
        labels_b = ["Single Frame (B=1)", "Multi-Stream (B=16 / frame)"]
        cpu_vals = [bench["cpu"]["single_mean_ms"], bench["cpu"]["batch16_per_sample_ms"]]
        gpu_vals = [bench["cuda_rtx5070"]["single_mean_ms"], bench["cuda_rtx5070"]["batch16_per_sample_ms"]]
        x_pos = np.arange(len(labels_b))
        axes[2].bar(x_pos - 0.18, cpu_vals, width=0.34, label="CPUExecutionProvider", color="#7f7f7f")
        axes[2].bar(x_pos + 0.18, gpu_vals, width=0.34, label="CUDAExecutionProvider (RTX 5070)", color="#2ca02c")
        axes[2].set_xticks(x_pos, labels_b)
        axes[2].set_ylim(0, max(cpu_vals + gpu_vals) * 1.25)
        axes[2].set_ylabel("Inference Latency (ms)")
        axes[2].set_title("ONNX Runtime Latency Benchmark (N04)")
        axes[2].legend(fontsize=8)
        axes[2].grid(True, axis="y", alpha=0.3)
        for i, (cv, gv) in enumerate(zip(cpu_vals, gpu_vals)):
            axes[2].text(i - 0.18, cv + 0.02, f"{cv:.2f}ms", ha="center", fontsize=8)
            axes[2].text(i + 0.18, gv + 0.02, f"{gv:.2f}ms", ha="center", fontsize=8, fontweight="bold")

    fig.tight_layout()
    fig.savefig(artifacts_dir / "gpu_2dcnn_evaluation.png")
    plt.close(fig)

    # Also save a visual comparison of the 4 classes' STFT Spectrograms!
    fig_s, axes_s = plt.subplots(1, 4, figsize=(15, 3.6), dpi=160)
    for cls_i, cls_name in enumerate(CLASS_NAMES):
        idx_sample = int(np.where(labels[idx_test] == cls_i)[0][0])
        spec_rgb = specs_all[idx_test[idx_sample]]  # (3, 64, 64)
        axes_s[cls_i].imshow(spec_rgb[0], origin="lower", aspect="auto", cmap="inferno")
        axes_s[cls_i].set_title(f"{cls_name} (STFT Ch.0)")
        axes_s[cls_i].set_xlabel("Time Window (0 -> 2.0s)")
        axes_s[cls_i].set_ylabel("Doppler Freq Bin")
    fig_s.tight_layout()
    fig_s.savefig(artifacts_dir / "csi_class_spectrograms.png")
    plt.close(fig_s)

    print(
        f"[Gateway GPU T09/N04] Done in {train_time_s:.2f}s | GPU Acc={gpu_acc*100:.2f}% | "
        f"Macro F1={gpu_macro_f1*100:.2f}% | Fall F1={gpu_fall_f1*100:.2f}% | "
        f"Benchmark: {bench}"
    )
    return metrics


if __name__ == "__main__":
    base = Path(__file__).parent
    parser = argparse.ArgumentParser(description="Train & Export Gateway GPU 2D-CNN ONNX (T09, N04)")
    parser.add_argument("--epochs", type=int, default=45)
    args = parser.parse_args()

    train_and_export_gpu_2dcnn(
        dataset_dir=base / "dataset",
        models_dir=base / "models",
        artifacts_dir=base / "artifacts",
        epochs=args.epochs,
    )
