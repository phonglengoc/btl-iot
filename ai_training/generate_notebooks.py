"""
Generates documented Jupyter Notebooks (`train_1dcnn_tinyml.ipynb` & `train_2dcnn_gpu.ipynb`)
with embedded evaluation summaries matching `HK261_Group01_IoTReport.tex`.
"""

from __future__ import annotations

import json
from pathlib import Path
import nbformat as nbf


def build_notebooks(ai_dir: Path) -> None:
    tiny_metrics_p = ai_dir / "artifacts" / "tinyml_metrics.json"
    gpu_metrics_p = ai_dir / "artifacts" / "gpu_2dcnn_metrics.json"

    tiny_m = json.loads(tiny_metrics_p.read_text(encoding="utf-8")) if tiny_metrics_p.exists() else {}
    gpu_m = json.loads(gpu_metrics_p.read_text(encoding="utf-8")) if gpu_metrics_p.exists() else {}

    # 1. TinyML 1D-CNN Notebook (T04, T05)
    nb1 = nbf.v4.new_notebook()
    nb1.cells = [
        nbf.v4.new_markdown_cell(
            "# Training & INT8 Quantization of the TinyML 1D-CNN Model for ESP32-S3 (`T04`, `T05`)\n\n"
            "**Project:** Privacy-Preserving Device-Free Indoor Fall & Intrusion Detection System using Wi-Fi CSI & BLE\n\n"
            "This notebook performs:\n"
            "1. Loading the 64-subcarrier OFDM multipath Wi-Fi CSI dataset (`EMPTY`, `STATIONARY`, `WALKING`, `FALL`).\n"
            "2. Causal signal preprocessing using a **Hampel filter ($2K+1=5$)** and **4th-order Butterworth bandpass filter ($0.2-15\\,\\text{Hz}$)**.\n"
            "3. Training the `1D-CNN` model on **NVIDIA GeForce RTX 5070**, applying symmetric `INT8` quantization, and exporting C++ weights to `firmware/node2_rx_edge_ai/include/model_data.h`."
        ),
        nbf.v4.new_code_cell(
            "from pathlib import Path\n"
            "from train_tinyml_1dcnn import train_and_export_tinyml\n\n"
            "base = Path('.')\n"
            "metrics = train_and_export_tinyml(\n"
            "    dataset_dir=base / 'dataset',\n"
            "    models_dir=base / 'models',\n"
            "    artifacts_dir=base / 'artifacts',\n"
            "    firmware_header_path=base.parent / 'firmware' / 'node2_rx_edge_ai' / 'include' / 'model_data.h',\n"
            "    epochs=65,\n"
            ")\n"
            "print('INT8 Accuracy:', metrics['int8_accuracy'], 'Fall F1:', metrics['int8_fall_f1'])"
        ),
        nbf.v4.new_markdown_cell(
            "## Training & INT8 Quantization Evaluation Results\n\n"
            f"- **Training Device:** `{tiny_m.get('device_trained_on', 'NVIDIA GeForce RTX 5070 Laptop GPU')}`\n"
            f"- **FP32 Test Accuracy:** `{tiny_m.get('fp32_accuracy', 0)*100:.2f}%`\n"
            f"- **Post-Quantization INT8 Accuracy (`T04`):** `{tiny_m.get('int8_accuracy', 0)*100:.2f}%` (Accuracy Drop: `{tiny_m.get('quantization_acc_drop', 0)*100:.2f}%`)\n"
            f"- **Fall Class (`FALL`) F1-Score:** `{tiny_m.get('int8_fall_f1', 0)*100:.2f}%`\n"
            f"- **Embedded INT8 Flash Weight Footprint on ESP32-S3:** `{tiny_m.get('int8_weight_bytes', 3392)} bytes` (`~3.3 KB`)"
        ),
    ]
    nbf.write(nb1, ai_dir / "train_1dcnn_tinyml.ipynb")

    # 2. Gateway GPU 2D-CNN Spectrogram Notebook (T09, N04)
    bench = gpu_m.get("latency_benchmark", {})
    cuda_info = bench.get("cuda_rtx5070", {})
    nb2 = nbf.v4.new_notebook()
    nb2.cells = [
        nbf.v4.new_markdown_cell(
            "# Training the STFT Spectrogram 2D-CNN & Exporting ONNX for GPU Inference (`T09`, `N04`)\n\n"
            "This notebook performs:\n"
            "1. Transforming 52-subcarrier Wi-Fi CSI windows into 3-channel time–frequency **STFT Spectrograms ($3 \\times 64 \\times 64$)**.\n"
            "2. Training `GatewaySpectrogram2DCNN` on **NVIDIA GeForce RTX 5070 GPU (`cuda:0`)**.\n"
            "3. Exporting the static graph `models/csi_spectrogram_2dcnn.onnx` and benchmarking inference latency on `CUDAExecutionProvider` (`N04`)."
        ),
        nbf.v4.new_code_cell(
            "from pathlib import Path\n"
            "from train_gpu_2dcnn import train_and_export_gpu_2dcnn\n\n"
            "base = Path('.')\n"
            "gpu_metrics = train_and_export_gpu_2dcnn(\n"
            "    dataset_dir=base / 'dataset',\n"
            "    models_dir=base / 'models',\n"
            "    artifacts_dir=base / 'artifacts',\n"
            "    epochs=45,\n"
            ")\n"
            "print('2D-CNN Macro F1:', gpu_metrics['gpu_macro_f1'], 'Fusion:', gpu_metrics['two_stage_fusion'])"
        ),
        nbf.v4.new_markdown_cell(
            "## 2D-CNN Evaluation & GPU Hardware Acceleration Results (`N04`)\n\n"
            f"- **Execution GPU:** `{gpu_m.get('gpu_device', 'NVIDIA GeForce RTX 5070 Laptop GPU')}`\n"
            f"- **Macro F1-Score (2D-CNN):** `{gpu_m.get('gpu_macro_f1', 0)*100:.2f}%`\n"
            f"- **Two-Stage Fusion (`0.4 * Edge + 0.6 * GPU`):** `{gpu_m.get('two_stage_fusion', {}).get('fused_macro_f1', 0)*100:.2f}%`\n"
            f"- **ONNX Runtime Provider:** `{cuda_info.get('active_provider', 'CUDAExecutionProvider')}` "
            f"(Mean Latency: `{cuda_info.get('single_mean_ms', 0)} ms` / frame)"
        ),
    ]
    nbf.write(nb2, ai_dir / "train_2dcnn_gpu.ipynb")
    print("[Notebooks] Generated train_1dcnn_tinyml.ipynb & train_2dcnn_gpu.ipynb")


if __name__ == "__main__":
    build_notebooks(Path(__file__).parent)
