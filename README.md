# Smart Indoor Fall Detection & Room Monitoring System (Wi-Fi CSI & CoreIOT)

**Lightweight Camera-Free Fall Detection & Environmental Monitoring using 20 Hz Wi-Fi CSI, I2C DHT20, CPU Random Forest AI & CoreIOT**

- **Course:** Internet of Things (IoT) — Semester HK261 (Academic Year 2026–2027)
- **Institution:** Faculty of Computer Science & Engineering — Ho Chi Minh City University of Technology (HCMUT)
- **Instructor:** Dr. Nguyen Hoa Hung (`nguyenhoahung@hcmut.edu.vn`)
- **Team Roles (3 Members — Streamlined Architecture):**
  1. **Member 1 — Le Ngoc Uy Phong (`2352905`)** (Hardware & Basic Firmware): Wire I2C `DHT20` sensor on Node 1 (`T01`), wire Buzzer + RGB LED + SOS button on Node 2, implement 20 Hz `ESP-NOW` transmission (`T06`) and `115200 bps` USB Serial output.
  2. **Member 2 — Huynh Vo Quynh Anh (`2452043`)** (Data & Python CPU AI): Record 52 Wi-Fi CSI subcarrier amplitudes to CSV (`T05`), apply **Moving Average** smoothing + basic statistical features (**Std, Max - Min**), and train a lightweight **Random Forest (`scikit-learn`)** classifier for the PC Gateway (`T09`).
  3. **Member 3 — Tran Thi Binh (`2452157`)** (Local Gateway & CoreIOT Cloud): Build the Python Local IoT Gateway (`T08`), Streamlit live dashboard & alert history assistant (`T10`, `N01`), CoreIOT MQTT Gateway sync (`T15`), Dashboard (`T11`), RuleChain (`T12`), and Telegram emergency alerts (`T13`).

---

## 1. Course Technology Score Mapping (10.5 → 8.0/8.0 Table A + 1.0/1.0 Table B)

All high-complexity components (INT8 C++ TinyML, GPU 2D-CNN/CUDA, Hampel/Butterworth DSP, BLE scanning, Embedded Web Server, Dual-OTA SHA-256, and STFT snapshots) have been removed while keeping a **+2.5-point safety margin** in Table A and full credit in Table B:

| Code | Technology Component | Points | Source Files / Evidence |
|---|---|---:|---|
| **T01** | I2C Temperature & Humidity Sensor (`DHT20`, `0x38`) on Node 1 | 0.5 | `firmware/node1_tx_env/src/main.cpp` |
| **T06** | 20 Hz `ESP-NOW` Wireless Link between Node 1 and Node 2 | 1.5 | `firmware/include/csi_protocol.h`, `node1_tx_env`, `node2_rx_csi` |
| **T05** | Labeled CSV Data Collection Tool (Serial / Simulator) | 1.0 | `ai_training/csi_serial_collector.py`, `csi_data_generator.py` |
| **T09** | Lightweight Gateway AI (`scikit-learn` Random Forest on CPU) | 1.0 | `ai_training/train_gateway_ai.py`, `gateway/gateway_ai.py` |
| **T08** | Python Local IoT Gateway (Serial Ingestion & `RPC:ALARM` Control) | 1.0 | `gateway/main_gateway.py` |
| **T10** | Real-Time Local Monitoring Dashboard using Streamlit | 1.0 | `gateway/streamlit_ui.py` |
| **T11** | CoreIOT Device Profile & Remote Monitoring Dashboard | 1.0 | `coreiot/dashboard_template.json` |
| **T12** | Simple Fall / SOS Alert Condition on CoreIOT RuleChain | 1.0 | `coreiot/rulechain_fall.json`, `gateway/coreiot_sync.py` |
| **T13** | Instant Emergency Notification via Telegram Bot API | 0.5 | `gateway/coreiot_sync.py`, `coreiot/rulechain_fall.json` |
| **T15** | PC Local Gateway to CoreIOT Cloud Synchronization via MQTT | 2.0 | `gateway/coreiot_sync.py` (`v1/gateway/connect`, `telemetry`, `rpc`) |
| **Total A** | **Table A Total** | **10.5 → 8.0/8.0** | +2.5 pts safety margin |
| **N01** | Alert History Query Assistant (`event_log.jsonl`) on Streamlit | **+1.0 → 1.0/1.0** | Tab 2 in `gateway/streamlit_ui.py` |

---

## 2. Repository Structure
## 2. Repository Structure

```text
.
├── firmware/
│   ├── include/
│   │   ├── config.example.h         # Node MAC addresses, GPIO pins, 115200 baud, 20 Hz config
│   │   └── csi_protocol.h           # 24-byte ESP-NOW packet, CRC-8 & 52-subcarrier amplitude helper
│   ├── node1_tx_env/                # Node 1 (YoloUNO #1): I2C DHT20 + 20 Hz ESP-NOW Tx (T01, T06)
│   └── node2_rx_csi/                # Node 2 (YoloUNO #2): 52 CSI Rx, USB Serial, Buzzer/LED/SOS (T06, T08)
├── ai_training/
│   ├── csi_signal_features.py       # Moving Average smoothing + 12 basic statistical features (Std, Max-Min)
│   ├── csi_data_generator.py        # 20 Hz labeled CSV generator for EMPTY, STATIONARY, WALKING, FALL (T05)
│   ├── csi_serial_collector.py      # Record 115200 bps Serial or Simulator stream to CSV (T05)
│   └── train_gateway_ai.py          # Train Random Forest (with MLP benchmark) & export `fall_rf_model.joblib` (T09)
├── gateway/
│   ├── config.example.yaml          # Serial/Simulator, AI model, CoreIOT & Telegram configuration template
│   ├── virtual_node_simulator.py    # Hardware-free virtual YoloUNO #1 + #2 simulator for instant demos
│   ├── gateway_ai.py                # CPU inference engine loading `fall_rf_model.joblib` (T09)
│   ├── coreiot_sync.py              # CoreIOT MQTT Gateway sync, RuleChain logic & Telegram Bot (T12, T13, T15)
│   ├── main_gateway.py              # Main PC Local Gateway service: Serial -> AI -> event_log.jsonl -> MQTT (T08)
│   └── streamlit_ui.py              # Local web dashboard with line charts, alarm buttons & N01 assistant (T10, N01)
├── coreiot/
│   ├── dashboard_template.json      # Importable CoreIOT Dashboard JSON (T11)
│   ├── rulechain_fall.json          # Importable CoreIOT Fall/SOS RuleChain JSON (T12, T13)
│   └── coreiot_setup_guide.md       # Step-by-step CoreIOT & Telegram setup instructions
└── tests/
    ├── cpp_protocol_unit_test.cpp   # C++17 unit test for 24-byte ESP-NOW struct & CRC-8
    ├── run_quantitative_eval.py     # Distance/NLOS accuracy & CPU latency benchmark
    └── test_end_to_end.py           # End-to-end automated Pytest suite
```

---

## 3. Quick Start (Hardware-Free Mode, Pure CPU)

No GPU, CUDA, or PyTorch required — runs smoothly on any standard laptop:

```powershell
# 1. Create Python virtual environment and install lightweight CPU packages
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# 2. Generate 20 Hz CSV dataset & train the scikit-learn Random Forest model (~5 seconds on CPU)
.\.venv\Scripts\python ai_training\csi_data_generator.py --samples-per-class 300
.\.venv\Scripts\python ai_training\train_gateway_ai.py

# 3. Run the automated end-to-end test suite
.\.venv\Scripts\pytest -v tests/test_end_to_end.py

# 4. Launch the Streamlit Dashboard (auto-starts the virtual YoloUNO simulator in background!)
.\.venv\Scripts\streamlit run gateway\streamlit_ui.py
```

Or simply run the one-click launcher: `.\run_demo.ps1`.
Open **`http://localhost:8501`** in your browser to view the live Moving-Average CSI disturbance charts, trigger `FALL_EMERGENCY` / `SOS_BUTTON_HOLD` scenarios, toggle the buzzer alarm via RPC, and query incident statistics in the **`N01`** tab.

---

## 4. Running on Physical YoloUNO (ESP32-S3) Boards

### Hardware Wiring
- **Node 1 (`YoloUNO #1` — Transmitter):** Connect I2C `DHT20` sensor (`VDD -> 3V3`, `GND -> GND`, `SDA -> GPIO11`, `SCL -> GPIO12`).
- **Node 2 (`YoloUNO #2` — Receiver):** Connect Buzzer (`GPIO10` + `GND`), RGB WS2812B LED (`GPIO18` + `3V3/5V` + `GND`), and SOS/Mute push-button (`GPIO14` <-> `GND`, internal `INPUT_PULLUP`). Connect Node 2 to the PC via USB-C.

### Step 1: Flash Both Boards via PlatformIO (USB, 115200 bps)
Node 1 and Node 2 **auto-pair on Wi-Fi Channel 6** out of the box (Broadcast MAC `{0xFF,...}` with `0xC51A` + CRC-8 verification). If multiple teams test in the same room, copy `firmware/include/config.example.h` to `firmware/include/config.h` and pin the exact board MAC addresses printed on Serial at boot.

```powershell
pio run -d firmware/node1_tx_env -e yolo_uno -t upload --upload-port COM3
pio run -d firmware/node2_rx_csi -e yolo_uno -t upload --upload-port COM4
```

### Step 2: Collect Real Room Data & Retrain Random Forest (`T05`, `T09`)
Place Node 1 and Node 2 ~2–3 meters apart across the monitored room, then record 20–30 real windows per class directly from Node 2 over USB Serial (`COM4`):

```powershell
.\.venv\Scripts\python ai_training\csi_serial_collector.py --mode serial --port COM4 --label EMPTY --windows 25 --replace
.\.venv\Scripts\python ai_training\csi_serial_collector.py --mode serial --port COM4 --label STATIONARY --windows 25 --replace
.\.venv\Scripts\python ai_training\csi_serial_collector.py --mode serial --port COM4 --label WALKING --windows 25 --replace
.\.venv\Scripts\python ai_training\csi_serial_collector.py --mode serial --port COM4 --label FALL --windows 25 --replace

# Retrain the Random Forest model on your real room dataset (~2 seconds on CPU)
.\.venv\Scripts\python ai_training\train_gateway_ai.py --dataset-dir ai_training\dataset\real
```

### Step 3: Switch Gateway to Live USB Serial & Launch Dashboard (`T08`, `T10`, `T15`)
```powershell
Copy-Item gateway/config.example.yaml gateway/config.yaml
# Edit gateway/config.yaml:
#   source_mode: "serial"
#   serial_port: "COM4"
#   gateway_token: "<YOUR_COREIOT_GATEWAY_TOKEN>"
#   bot_token / chat_id: "<YOUR_TELEGRAM_CREDENTIALS>"

.\.venv\Scripts\python gateway\main_gateway.py --config gateway\config.yaml
.\.venv\Scripts\streamlit run gateway\streamlit_ui.py
```
