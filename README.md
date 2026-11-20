# Camera-Free Wi-Fi CSI & BLE Radar System for Indoor Fall and Intrusion Detection with Collaborative Edge–GPU AI

**Privacy-Preserving Device-Free Indoor Fall & Intrusion Detection System using Wi-Fi CSI, BLE, and Collaborative Edge–GPU AI**

- **Course:** Internet of Things (IoT) — Semester HK261 (Academic Year 2026–2027)
- **Faculty:** Faculty of Computer Science and Engineering — Ho Chi Minh City University of Technology (HCMUT), VNU-HCM
- **Instructor:** Dr. Nguyễn Hòa Hưng (`nguyenhoahung@hcmut.edu.vn`)
- **Project Group (Group 01):**
  1. **Lê Ngọc Uy Phong** (Student ID: `2352905` — `phong.le9999@hcmut.edu.vn`) — Hardware architecture, dual YoloUNO ESP32-S3 firmware, ESP-NOW 100 Hz CSI (`T06`), I2C DHT20 (`T01`), BLE 5.0 scanner (`T03`), Embedded Web Server (`T07`)
  2. **Huỳnh Võ Quỳnh Anh** (Student ID: `2452043` — `anh.huynhvo02@hcmut.edu.vn`) — CSV dataset collector (`T05`), causal Hampel/Butterworth DSP, INT8 TinyML 1D-CNN (`T04`), GPU 2D-CNN Spectrogram (`T09`, `N04`), quantitative evaluation
  3. **Trần Thị Bình** (Student ID: `2452157` — `binh.tranbinh2856@hcmut.edu.vn`) — PC Local IoT Gateway (`T08`), Streamlit real-time heatmap & snapshot dashboard (`T10`, `N06`), CoreIOT Gateway sync (`T15`), Dashboard (`T11`), RuleChain (`T12`), Telegram alerting (`T13`) & OTA updates (`T14`)

---

## 1. System Architecture & Technology Coverage Matrix (14.0 T-Points + 2.0 N-Points)

The system exploits amplitude and phase perturbations across **64 OFDM subcarriers (Wi-Fi CSI)** transmitted at **100 Hz** over **ESP-NOW** between two **YoloUNO (ESP32-S3)** boards to detect indoor falls in total darkness **without cameras** and **without requiring elderly residents to wear sensors**. Passive **BLE 5.0** scanning is fused with Wi-Fi CSI to distinguish authorized residents from unauthorized intruders.

| Code | Technology Component | Score | Source Files / Implementation Artifacts |
|---|---|---:|---|
| **T01** | I2C environmental sensor (`DHT20`, `0x38`) on Tasmota / ESP32-S3 | 0.5 | `firmware/node1_tx_env/src/main.cpp`, `tasmota_yolo_uno_dht20.md` |
| **T03** | Passive Bluetooth Low Energy identification (`MAC` + `RSSI`) | 1.5 | `firmware/node2_rx_edge_ai/include/ble_scanner.h` |
| **T04** | On-device TinyML inference (`INT8 1D-CNN`) | 1.0 | `firmware/node2_rx_edge_ai/include/tinyml_infer.h`, `model_data.h` |
| **T05** | Labeled CSV dataset collector, training & TinyML export toolchain | 1.0 | `ai_training/csi_serial_collector.py`, `train_tinyml_1dcnn.py` |
| **T06** | High-rate 100 Hz wireless transport over `ESP-NOW` | 1.5 | `firmware/include/csi_protocol.h`, `node1_tx_env`, `node2_rx_edge_ai` |
| **T07** | Custom Embedded Web Server on microcontroller (`Port 80`) | 1.0 | `firmware/node2_rx_edge_ai/include/web_server.h` |
| **T08** | PC Local IoT Gateway for telemetry aggregation & RPC dispatch | 1.0 | `gateway/main_gateway.py` |
| **T09** | Local Gateway AI engine analyzing `STFT 2D-CNN` spectrograms | 1.0 | `gateway/gpu_spectrogram_ai.py`, `ai_training/train_gpu_2dcnn.py` |
| **T10** | Real-time monitoring & control UI at the Local Gateway | 1.0 | `gateway/streamlit_ui.py` |
| **T11** | CoreIOT Device Profile, Template & Remote Dashboard | 1.0 | `coreiot/dashboard_template.json` |
| **T12** | Smart event processing workflow on CoreIOT RuleChain | 1.0 | `coreiot/rulechain_fall_sec.json`, `gateway/coreiot_sync.py` |
| **T13** | Emergency alert notifications via Telegram Bot API | 0.5 | `gateway/coreiot_sync.py`, `coreiot/rulechain_fall_sec.json` |
| **T14** | Over-the-Air firmware updates (`OTA` Dual-Partition + SHA-256) | 2.0 | `firmware/include/ota_updater.h`, `firmware/partitions_16mb_ota.csv` |
| **T15** | CoreIOT Gateway synchronization with PC Local IoT Gateway | 2.0 | `gateway/coreiot_sync.py` (`v1/gateway/connect`, `telemetry`, `rpc`) |
| **N04** | GPU-accelerated AI inference (`ONNX Runtime CUDA`) | +1.0 | `gateway/gpu_spectrogram_ai.py` (`NVIDIA GeForce RTX 5070`) |
| **N06** | Custom dashboard with automatic incident detection & snapshot archive | +1.0 | `gateway/main_gateway.py`, `gateway/streamlit_ui.py` |
| **N01** | Natural-language telemetry & event log assistant | Bonus | Tab 4 in `gateway/streamlit_ui.py` |

---

## 2. Repository Structure

```text
.
├── firmware/
│   ├── include/
│   │   ├── config.example.h         # Wi-Fi, ESP-NOW MAC, BLE, and CoreIOT template (NO real secrets)
│   │   ├── csi_protocol.h           # 24-byte ESP-NOW binary frame layout & CRC-8
│   │   └── ota_updater.h            # CoreIOT OTA updater + SHA-256 verification + Rollback (T14)
│   ├── node1_tx_env/                # YoloUNO #1 Firmware: I2C DHT20 + 100 Hz ESP-NOW Tx (T01, T06)
│   ├── node2_rx_edge_ai/            # YoloUNO #2 Firmware: CSI Rx, BLE, INT8 TinyML, Web Server (T03, T04, T07)
│   └── partitions_16mb_ota.csv      # Symmetric ota_0 / ota_1 partition table for 16 MB Flash
├── ai_training/
│   ├── csi_dsp_common.py            # Hampel + 4th-order Butterworth + STFT Spectrogram DSP
│   ├── csi_physics_generator.py     # 64-subcarrier OFDM multipath physics simulator (T05)
│   ├── csi_serial_collector.py      # Labeled CSV recorder for USB-CDC / HIL Simulator (T05)
│   ├── train_tinyml_1dcnn.py        # 1D-CNN training, INT8 quantization & C++ model_data.h export (T04)
│   ├── train_gpu_2dcnn.py           # 2D-CNN training on RTX 5070 GPU & ONNX export (T09, N04)
│   ├── train_1dcnn_tinyml.ipynb     # TinyML training Jupyter Notebook
│   └── train_2dcnn_gpu.ipynb        # GPU 2D-CNN Spectrogram training Jupyter Notebook
├── gateway/
│   ├── config.example.yaml          # Serial/Simulator, CoreIOT & Telegram configuration template
│   ├── virtual_node_simulator.py    # Physics HIL cluster simulator for YoloUNO #1 + #2 + BLE + Web UI
│   ├── gpu_spectrogram_ai.py        # ONNX Runtime CUDA inference engine on RTX 5070 (T09, N04)
│   ├── coreiot_sync.py              # CoreIOT Gateway MQTT sync, RuleChain & Telegram alerts (T12, T13, T15)
│   ├── main_gateway.py              # PC Local IoT Gateway central service & snapshot recorder (T08, N06)
│   └── streamlit_ui.py              # Real-time CSI Heatmap, STFT & Control Dashboard (T10, N06, N01)
├── coreiot/
│   ├── dashboard_template.json      # Importable CoreIOT Dashboard template (T11)
│   ├── rulechain_fall_sec.json      # Importable CoreIOT RuleChain JSON (T12, T13)
│   └── ota_guide.md                 # Step-by-step CoreIOT & OTA deployment guide (T14)
├── tests/
│   ├── cpp_tinyml_unit_test.cpp     # C++17 unit test verifying INT8 bit-exactness against Python
│   ├── run_quantitative_eval.py     # Chapter 4 quantitative evaluation suite (50 trials/scenario)
│   └── test_end_to_end.py           # End-to-end automated integration test suite (Pytest)
├── diagrams_en/                     # Editable Draw.io architecture & workflow diagrams (.drawio)
├── graphics/                        # Rendered figures, charts, and UI screenshots
└── HK261_Group01_IoTReport.pdf      # Complete English technical report (LaTeX)
```

---

## 3. Quick Start Without Physical Hardware (Physics HIL Simulator + RTX 5070 GPU)

The repository includes a full **Physics Hardware-in-the-Loop Simulator (`VirtualYoloUnoCluster`)** that synthesizes 64-subcarrier OFDM multipath CSI streams, runs the real **INT8 TinyML** model, serves the **Node 2 Embedded Web UI (`T07`)** at `http://127.0.0.1:8082`, and executes second-tier verification on **NVIDIA RTX 5070 (`CUDAExecutionProvider`)**:

```powershell
# 1. Activate Python 3.11 virtual environment (CUDA 12.8 sm_120 support for RTX 5070)
.\.venv\Scripts\Activate.ps1

# 2. Generate CSI dataset & train both AI models on RTX 5070 GPU
python ai_training/csi_physics_generator.py --samples-per-class 350
python ai_training/train_tinyml_1dcnn.py --epochs 65
python ai_training/train_gpu_2dcnn.py --epochs 45

# 3. Launch the PC Local IoT Gateway Dashboard (auto-starts the YoloUNO HIL simulator)
streamlit run gateway/streamlit_ui.py
```

- Open **`http://localhost:8501`** to view the live 52-subcarrier CSI heatmap, 64×64 STFT spectrogram, trigger **`FALL_EMERGENCY`** or **`WALKING_INTRUDER`** scenarios, and inspect automatic incident snapshots (`N06`).
- Open **`http://127.0.0.1:8082`** to interact directly with the **Node 2 Embedded Web Server (`T07`)**.

---

## 4. Building & Flashing Firmware onto Physical YoloUNO (ESP32-S3) Boards

```powershell
# Step 1: Create local configuration files from templates (ignored by .gitignore)
Copy-Item firmware/include/config.example.h firmware/include/config.h
Copy-Item gateway/config.example.yaml gateway/config.yaml
# -> Fill in your real WIFI_SSID, WIFI_PASSWORD, YoloUNO MAC addresses, and COREIOT_GATEWAY_TOKEN.

# Step 2: Build and flash Node 1 (Tx + DHT20) & Node 2 (Rx + BLE + TinyML)
pio run -d firmware/node1_tx_env -e yolo_uno -t upload --upload-port COM3
pio run -d firmware/node2_rx_edge_ai -e yolo_uno -t upload --upload-port COM4

# Step 3: Set `source_mode: "serial"` in `gateway/config.yaml` and run the Gateway
python gateway/main_gateway.py --config gateway/config.yaml
streamlit run gateway/streamlit_ui.py
```

---

## 5. Automated Testing & Quantitative Evaluation

```powershell
# Run full integration test suite (C++ INT8 vs Python INT8, RTX 5070 CUDA, Web Server, CoreIOT, RuleChain)
.\.venv\Scripts\pytest.exe -v

# Run Chapter 4 quantitative evaluation (50 trials per scenario: 1m, 2m, 3m, NLOS, BLE, Fast Sit)
.\.venv\Scripts\python.exe tests/run_quantitative_eval.py
```

---

## 6. Security Policy & AI Tool Disclosure

- **Credential Hygiene:** This repository strictly excludes real Wi-Fi credentials, CoreIOT Access Tokens, and Telegram Bot Tokens via `.gitignore` (only sanitized templates `.env.example`, `config.example.h`, and `config.example.yaml` are committed).
- **AI & Reference Disclosure:** Generative AI tools were used to assist with code architecture review, LaTeX formatting, and simulation test-pipeline refinement prior to physical hardware deployment. ESP32-S3 CSI data structures follow the official `espressif/esp-csi` documentation and the `CoreIOT / ThingsBoard Gateway MQTT API`.
