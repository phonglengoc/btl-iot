# Hệ thống Phát hiện Té ngã và Giám sát Phòng ở Thông minh (Wi-Fi CSI & CoreIOT)

**Smart Indoor Fall Detection & Room Monitoring System using Wi-Fi CSI (20 Hz), DHT20, PC Gateway Random Forest AI & CoreIOT**

- **Học phần:** Internet of Things (IoT) — Học kỳ 261 (Năm học 2026–2027)
- **Khoa:** Khoa Khoa học và Kỹ thuật Máy tính — Trường Đại học Bách Khoa, ĐHQG-HCM (HCMUT)
- **Giảng viên hướng dẫn:** TS. Nguyễn Hòa Hưng (`nguyenhoahung@hcmut.edu.vn`)
- **Phân công 3 thành viên (phiên bản tinh gọn, dễ hiểu & dễ bảo vệ):**
  1. **Thành viên 1 — Lê Ngọc Uy Phong (`2352905`)** (Mạch & Firmware cơ bản): Đọc cảm biến I2C DHT20 trên Node 1 (`T01`), kết nối còi Buzzer + đèn LED + nút nhấn SOS trên Node 2, truyền nhận gói tin `ESP-NOW` 20 Hz (`T06`) và xuất Serial `115200 bps`.
  2. **Thành viên 2 — Huỳnh Võ Quỳnh Anh (`2452043`)** (Dữ liệu & AI cơ bản trên Python): Thu thập dữ liệu 52 biên độ sóng CSI ra tệp CSV (`T05`), làm mượt bằng **Trung bình trượt (Moving Average)** + tính thống kê cơ bản (**Std, Max - Min**), và huấn luyện 1 mô hình **Random Forest (`scikit-learn`)** chạy CPU tại Gateway (`T09`).
  3. **Thành viên 3 — Trần Thị Bình (`2452157`)** (Gateway & Đám mây CoreIOT): Xây dựng luồng Python Local IoT Gateway (`T08`), giao diện Streamlit trực quan kèm tab tra cứu lịch sử sự kiện (`T10`, `N01`), đồng bộ CoreIOT qua MQTT (`T15`), cấu hình Dashboard (`T11`), RuleChain (`T12`) và cảnh báo Telegram (`T13`).

---

## 1. Bảng Đối chiếu Mã Công nghệ (10.5đ → 8.0/8.0 Bảng A + 1.0/1.0 Bảng B)

Kiến trúc đã cắt bỏ toàn bộ các thành phần quá phức tạp (TinyML INT8 C++, 2D-CNN GPU/CUDA, bộ lọc Hampel/Butterworth, quét BLE đa nhân, Web Server nhúng, Dual-OTA SHA-256 và ảnh chụp Snapshot STFT) nhưng vẫn dư **2.5 điểm an toàn** ở Bảng A và trọn **1.0 điểm** ở Bảng B:

| Mã | Thành phần Công nghệ | Điểm | Tệp Mã nguồn / Minh chứng |
|---|---|---:|---|
| **T01** | Cảm biến nhiệt độ, độ ẩm I2C `DHT20` (`0x38`) trên Node 1 | 0.5 | `firmware/node1_tx_env/src/main.cpp` |
| **T06** | Truyền nhận `ESP-NOW` (20 Hz) giữa Node 1 và Node 2 | 1.5 | `firmware/include/csi_protocol.h`, `node1_tx_env`, `node2_rx_csi` |
| **T05** | Thu thập dữ liệu lưu ra tệp CSV (Serial / Simulator) | 1.0 | `ai_training/csi_serial_collector.py`, `csi_data_generator.py` |
| **T09** | Chạy 1 mô hình AI nhẹ (`Random Forest` CPU) trên Gateway | 1.0 | `ai_training/train_gateway_ai.py`, `gateway/gateway_ai.py` |
| **T08** | Python Local IoT Gateway nhận Serial & gửi lệnh `RPC:ALARM` | 1.0 | `gateway/main_gateway.py` |
| **T10** | Giao diện theo dõi nội bộ bằng Streamlit | 1.0 | `gateway/streamlit_ui.py` |
| **T11** | Tạo Device Profile & Dashboard trên CoreIOT | 1.0 | `coreiot/dashboard_template.json` |
| **T12** | Xử lý điều kiện cảnh báo đơn giản trên CoreIOT RuleChain | 1.0 | `coreiot/rulechain_fall.json`, `gateway/coreiot_sync.py` |
| **T13** | Gửi tin nhắn cảnh báo khẩn cấp qua Telegram Bot | 0.5 | `gateway/coreiot_sync.py`, `coreiot/rulechain_fall.json` |
| **T15** | Đồng bộ dữ liệu từ PC Gateway lên CoreIOT qua MQTT | 2.0 | `gateway/coreiot_sync.py` (`v1/gateway/connect`, `telemetry`, `rpc`) |
| **Tổng A** | **Tổng điểm Bảng A** | **10.5 → 8.0/8.0** | Dư 2.5 điểm an toàn |
| **N01** | Tab tra cứu lịch sử cảnh báo (`event_log.jsonl`) trên Streamlit | **+1.0 → 1.0/1.0** | Tab 2 trong `gateway/streamlit_ui.py` |

---

## 2. Cấu trúc Thư mục Mã nguồn

```text
.
├── firmware/
│   ├── include/
│   │   ├── config.example.h         # Cấu hình MAC 2 mạch, chân GPIO, baud 115200, 20 Hz
│   │   └── csi_protocol.h           # Gói nhị phân 24-byte ESP-NOW, CRC-8 & tính 52 biên độ CSI
│   ├── node1_tx_env/                # Node 1 (YoloUNO #1): Đọc DHT20 + phát ESP-NOW 20 Hz (T01, T06)
│   └── node2_rx_csi/                # Node 2 (YoloUNO #2): Thu 52 biên độ CSI, xuất Serial, Buzzer/LED/SOS (T06, T08)
├── ai_training/
│   ├── csi_signal_features.py       # Trung bình trượt (Moving Average) + 12 đặc trưng thống kê (Std, Max-Min)
│   ├── csi_data_generator.py        # Tạo dữ liệu CSV 20 Hz mẫu cho 4 lớp: EMPTY, STATIONARY, WALKING, FALL (T05)
│   ├── csi_serial_collector.py      # Đọc cổng Serial 115200 bps hoặc Simulator lưu thành file CSV (T05)
│   └── train_gateway_ai.py          # Huấn luyện Random Forest (so sánh với MLP) & xuất `fall_rf_model.joblib` (T09)
├── gateway/
│   ├── config.example.yaml          # Cấu hình Serial/Simulator, AI model, CoreIOT & Telegram
│   ├── virtual_node_simulator.py    # Giả lập 2 mạch YoloUNO (chạy demo ngay không cần phần cứng!)
│   ├── gateway_ai.py                # Nạp `fall_rf_model.joblib` phân loại trực tiếp trên CPU (T09)
│   ├── coreiot_sync.py              # Đồng bộ MQTT CoreIOT Gateway, logic RuleChain & Telegram Bot (T12, T13, T15)
│   ├── main_gateway.py              # Dịch vụ PC Local IoT Gateway: đọc Serial, chạy AI, lưu event_log.jsonl (T08)
│   └── streamlit_ui.py              # Dashboard biểu đồ đường, nút bật/tắt còi & tra cứu lịch sử N01 (T10, N01)
├── coreiot/
│   ├── dashboard_template.json      # Mẫu nhập CoreIOT Dashboard (T11)
│   ├── rulechain_fall.json          # Mẫu nhập CoreIOT RuleChain đơn giản (T12, T13)
│   └── coreiot_setup_guide.md       # Hướng dẫn cấu hình CoreIOT & Telegram từng bước
└── tests/
    ├── cpp_protocol_unit_test.cpp   # Kiểm thử C++17 gói tin 24-byte ESP-NOW & CRC-8
    ├── run_quantitative_eval.py     # Đánh giá độ chính xác Random Forest theo khoảng cách & độ trễ CPU
    └── test_end_to_end.py           # Kiểm thử tự động toàn hệ thống (Pytest)
```

---

## 3. Hướng dẫn Chạy Nhanh Không Cần Mạch Phần Cứng (Pure CPU)

Không cần GPU, không cần cài CUDA hay PyTorch — chạy mượt trên mọi máy tính sinh viên:

```powershell
# 1. Tạo môi trường ảo Python và cài thư viện nhẹ (scikit-learn, streamlit, paho-mqtt...)
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# 2. Sinh dữ liệu CSV 20 Hz & Huấn luyện mô hình Random Forest (mất ~5 giây trên CPU)
.\.venv\Scripts\python ai_training\csi_data_generator.py --samples-per-class 300
.\.venv\Scripts\python ai_training\train_gateway_ai.py

# 3. Chạy kiểm thử tự động toàn hệ thống
.\.venv\Scripts\pytest -v tests/test_end_to_end.py

# 4. Mở Dashboard Streamlit (tự động chạy bộ giả lập YoloUNO ở chế độ nền!)
.\.venv\Scripts\streamlit run gateway\streamlit_ui.py
```

Hoặc chạy trực tiếp script một chạm: `.\run_demo.ps1`.
Mở trình duyệt tại **`http://localhost:8501`** để xem biểu đồ dao động sóng, thử nghiệm kịch bản **`FALL_EMERGENCY`** / **`SOS_BUTTON_HOLD`**, bấm bật/tắt còi báo động và hỏi đáp lịch sử cảnh báo ở tab **`N01`**.

---

## 4. Hướng dẫn Nạp Firmware Khi Đã Có 2 Mạch YoloUNO (ESP32-S3)

```powershell
# Bước 1: Tạo tệp cấu hình riêng từ bản mẫu (đã được ẩn trong .gitignore)
Copy-Item firmware/include/config.example.h firmware/include/config.h
Copy-Item gateway/config.example.yaml gateway/config.yaml
# -> Điền địa chi MAC thật của 2 mạch vào `config.h`, và Token CoreIOT/Telegram vào `config.yaml`.

# Bước 2: Nạp code trực tiếp qua cáp USB bằng PlatformIO (115200 bps, không cần OTA)
pio run -d firmware/node1_tx_env -e yolo_uno -t upload --upload-port COM3
pio run -d firmware/node2_rx_csi -e yolo_uno -t upload --upload-port COM4

# Bước 3: Chuyển `source_mode: "serial"` trong `gateway/config.yaml` và chạy Gateway thật
.\.venv\Scripts\python gateway\main_gateway.py --config gateway\config.yaml
.\.venv\Scripts\streamlit run gateway\streamlit_ui.py
```
