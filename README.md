# Hệ thống Radar Không Camera (Wi-Fi CSI & BLE) Phát hiện Té ngã và Xâm nhập Tích hợp AI tại Biên

**Privacy-Preserving Device-Free Indoor Fall & Intrusion Detection System using Wi-Fi CSI, BLE, and Collaborative Edge-GPU AI**

- **Học phần:** Internet of Things (IoT) — Học kỳ 261 (Năm học 2026–2027)
- **Khoa:** Khoa Khoa học và Kỹ thuật Máy tính — Trường Đại học Bách Khoa, ĐHQG-HCM (HCMUT)
- **Giảng viên hướng dẫn:** TS. Nguyễn Hòa Hưng (`nguyenhoahung@hcmut.edu.vn`)
- **Nhóm sinh viên thực hiện:**
  1. **Lê Ngọc Uy Phong** (MSSV: `2352905` — `phong.le9999@hcmut.edu.vn`) — Phần cứng, Firmware 2 Node YoloUNO, ESP-NOW CSI (`T06`), I2C DHT20 (`T01`), BLE (`T03`), Embedded Web Server (`T07`)
  2. **Huỳnh Võ Quỳnh Anh** (MSSV: `2452043` — `anh.huynhvo02@hcmut.edu.vn`) — Thu thập CSV (`T05`), Lọc số Hampel/Butterworth, TinyML 1D-CNN INT8 (`T04`), 2D-CNN Spectrogram trên GPU (`T09`, `N04`), Đo kiểm định lượng
  3. **Trần Thị Bình** (MSSV: `2452157` — `binh.tranbinh2856@hcmut.edu.vn`) — PC Local IoT Gateway (`T08`), Giao diện Streamlit Heatmap (`T10`, `N06`), Đồng bộ CoreIOT Gateway (`T15`), Dashboard (`T11`), RuleChain (`T12`), Telegram (`T13`) & OTA (`T14`)

---

## 1. Tổng quan Kiến trúc & Bảng Mã Công nghệ Đạt được (14.0đ T + 2.0đ N)

Hệ thống tận dụng sự biến dạng biên độ/pha của **64 sóng mang phụ OFDM (Wi-Fi CSI)** truyền ở tốc độ **100 Hz** qua giao thức **ESP-NOW** giữa hai mạch **YoloUNO (ESP32-S3)** để phát hiện té ngã xuyên bóng tối mà **không dùng camera** và **không bắt người cao tuổi đeo thiết bị**, kết hợp quét **BLE 5.0** để phân biệt người nhà với kẻ lạ đột nhập.

| Mã | Thành phần Công nghệ | Điểm | Tệp Mã nguồn / Minh chứng Hiện thực |
|---|---|---:|---|
| **T01** | Cảm biến I2C (`DHT20`, `0x38`) trên Tasmota / ESP32-S3 | 0.5 | `firmware/node1_tx_env/src/main.cpp`, `tasmota_yolo_uno_dht20.md` |
| **T03** | Quét định danh Bluetooth Low Energy (`MAC` + `RSSI`) | 1.5 | `firmware/node2_rx_edge_ai/include/ble_scanner.h` |
| **T04** | Suy luận TinyML chạy trực tiếp trên thiết bị (`INT8 1D-CNN`) | 1.0 | `firmware/node2_rx_edge_ai/include/tinyml_infer.h`, `model_data.h` |
| **T05** | Bộ công cụ thu thập CSV, huấn luyện & xuất mô hình TinyML | 1.0 | `ai_training/csi_serial_collector.py`, `train_tinyml_1dcnn.py` |
| **T06** | Truyền thông không dây tốc độ cao 100 Hz qua `ESP-NOW` | 1.5 | `firmware/include/csi_protocol.h`, `node1_tx_env`, `node2_rx_edge_ai` |
| **T07** | Embedded Web Server tự lập trình trên mạch (`Port 80`) | 1.0 | `firmware/node2_rx_edge_ai/include/web_server.h` |
| **T08** | PC Local IoT Gateway tổng hợp telemetry & phân phối lệnh RPC | 1.0 | `gateway/main_gateway.py` |
| **T09** | Hàm AI chạy trên Local Gateway phân tích phổ `STFT 2D-CNN` | 1.0 | `gateway/gpu_spectrogram_ai.py`, `ai_training/train_gpu_2dcnn.py` |
| **T10** | Giao diện giám sát và điều khiển trực quan tại Local Gateway | 1.0 | `gateway/streamlit_ui.py` |
| **T11** | CoreIOT Device Profile, Template & Remote Dashboard | 1.0 | `coreiot/dashboard_template.json` |
| **T12** | Kịch bản xử lý thông minh trên CoreIOT RuleChain | 1.0 | `coreiot/rulechain_fall_sec.json`, `gateway/coreiot_sync.py` |
| **T13** | Gửi thông báo cảnh báo khẩn cấp qua Telegram Bot API | 0.5 | `gateway/coreiot_sync.py`, `coreiot/rulechain_fall_sec.json` |
| **T14** | Cập nhật Firmware từ xa qua mạng (`OTA` Dual-Partition + SHA-256) | 2.0 | `firmware/include/ota_updater.h`, `firmware/partitions_16mb_ota.csv` |
| **T15** | Đồng bộ hóa CoreIOT Gateway với PC Local IoT Gateway | 2.0 | `gateway/coreiot_sync.py` (`v1/gateway/connect`, `telemetry`, `rpc`) |
| **N04** | Tăng tốc mô hình AI chạy trên GPU (`ONNX Runtime CUDA`) | +1.0 | `gateway/gpu_spectrogram_ai.py` (`NVIDIA GeForce RTX 5070`) |
| **N06** | Dashboard tự thiết kế tự động phát hiện & chụp Snapshot sự cố | +1.0 | `gateway/main_gateway.py`, `gateway/streamlit_ui.py` |
| **N01** | Trợ lý ảo hỏi đáp nhật ký sự kiện bằng tiếng Việt tự nhiên | Dự phòng | Tab 4 trong `gateway/streamlit_ui.py` |

---

## 2. Cấu trúc Thư mục Kho Mã nguồn

```text
.
├── firmware/
│   ├── include/
│   │   ├── config.example.h         # Mẫu cấu hình Wi-Fi, MAC, BLE, Token (KHÔNG chứa mật khẩu thật)
│   │   ├── csi_protocol.h           # Cấu trúc gói nhị phân 24-byte ESP-NOW & CRC-8
│   │   └── ota_updater.h            # Trình cập nhật OTA CoreIOT + SHA-256 + Rollback (T14)
│   ├── node1_tx_env/                # Firmware YoloUNO #1: Đọc I2C DHT20 + Phát ESP-NOW 100Hz (T01, T06)
│   ├── node2_rx_edge_ai/            # Firmware YoloUNO #2: Thu CSI, BLE, INT8 TinyML, Web Server (T03, T04, T07)
│   └── partitions_16mb_ota.csv      # Bảng phân vùng đối xứng ota_0 / ota_1 trên Flash 16MB
├── ai_training/
│   ├── csi_dsp_common.py            # Tiền xử lý Hampel + Butterworth bậc 4 + STFT Spectrogram
│   ├── csi_physics_generator.py     # Bộ tạo dữ liệu vật lý đa đường OFDM 64 sóng mang (T05)
│   ├── csi_serial_collector.py      # Công cụ thu thập & gắn nhãn CSV từ cổng COM / Simulator (T05)
│   ├── train_tinyml_1dcnn.py        # Huấn luyện 1D-CNN, lượng tử hóa INT8 & xuất C++ model_data.h (T04)
│   ├── train_gpu_2dcnn.py           # Huấn luyện 2D-CNN trên GPU RTX 5070 & xuất ONNX (T09, N04)
│   ├── train_1dcnn_tinyml.ipynb     # Jupyter Notebook huấn luyện TinyML
│   └── train_2dcnn_gpu.ipynb        # Jupyter Notebook huấn luyện GPU Spectrogram 2D-CNN
├── gateway/
│   ├── config.example.yaml          # Mẫu cấu hình cổng Serial/Simulator, CoreIOT & Telegram
│   ├── virtual_node_simulator.py    # Bộ giả lập phần cứng YoloUNO #1 + #2 + BLE + Web Server (Chạy không cần mạch!)
│   ├── gpu_spectrogram_ai.py        # Engine suy luận ONNX Runtime CUDA trên GPU RTX 5070 (T09, N04)
│   ├── coreiot_sync.py              # Đồng bộ MQTT CoreIOT Gateway, RuleChain & Telegram Alert (T12, T13, T15)
│   ├── main_gateway.py              # Dịch vụ trung tâm PC Local IoT Gateway & lưu Snapshot (T08, N06)
│   └── streamlit_ui.py              # Giao diện Dashboard thời gian thực CSI Heatmap & STFT (T10, N06, N01)
├── coreiot/
│   ├── dashboard_template.json      # File nhập giao diện CoreIOT Dashboard (T11)
│   ├── rulechain_fall_sec.json      # File nhập kịch bản CoreIOT RuleChain (T12, T13)
│   └── ota_guide.md                 # Hướng dẫn cấu hình CoreIOT & cập nhật OTA chi tiết (T14)
├── tests/
│   ├── cpp_tinyml_unit_test.cpp     # Kiểm thử đơn vị C++17 đối chiếu xác suất INT8 với Python
│   ├── run_quantitative_eval.py     # Chạy kịch bản đo kiểm định lượng Chương 4 (50 lần thử/cự ly)
│   └── test_end_to_end.py           # Bộ kiểm thử tích hợp tự động toàn hệ thống (Pytest)
├── diagrams/                        # Các sơ đồ gốc Draw.io (.drawio)
└── spec_btl_iot_fall_detection.pdf  # Bản đặc tả kỹ thuật chi tiết LaTeX (39 trang)
```

---

## 3. Hướng dẫn Chạy Ngay Không Cần Phần Cứng (Chế độ Mô phỏng Vật lý + GPU RTX 5070)

Toàn bộ hệ thống đã tích hợp sẵn **Bộ Giả lập Phần cứng Vật lý (`VirtualYoloUnoCluster`)** phát sóng đa đường 64 kênh CSI, chạy mô hình **INT8 TinyML** thật, mở **Embedded Web Server (`T07`)** tại `http://127.0.0.1:8082` và suy luận xác thực chéo trên **GPU NVIDIA RTX 5070 (`CUDAExecutionProvider`)**:

```powershell
# 1. Kích hoạt môi trường ảo Python 3.11 (hỗ trợ CUDA 12.8 sm_120 cho RTX 5070)
.\.venv\Scripts\Activate.ps1

# 2. Sinh dữ liệu sóng CSI & Huấn luyện cả 2 mô hình AI trên GPU RTX 5070
python ai_training/csi_physics_generator.py --samples-per-class 350
python ai_training/train_tinyml_1dcnn.py --epochs 65
python ai_training/train_gpu_2dcnn.py --epochs 45

# 3. Khởi chạy ngay Giao diện PC Local IoT Gateway Dashboard (tự động bật bộ giả lập YoloUNO!)
streamlit run gateway/streamlit_ui.py
```
- Mở trình duyệt tại **`http://localhost:8501`** để xem bản đồ nhiệt 52 sóng mang CSI, ảnh phổ STFT Spectrogram, thử nghiệm kịch bản **`FALL_EMERGENCY`** (Té ngã) hoặc **`WALKING_INTRUDER`** (Đột nhập không có BLE) và xem ảnh chụp Snapshot tự động (`N06`).
- Mở **`http://127.0.0.1:8082`** để trải nghiệm trực tiếp giao diện **Embedded Web Server của Node 2 (`T07`)**.

---

## 4. Hướng dẫn Biên dịch & Nạp Firmware Khi Đã Mua 2 Mạch YoloUNO (ESP32-S3)

```powershell
# Bước 1: Tạo tệp cấu hình riêng từ bản mẫu (đã được bảo vệ bởi .gitignore)
Copy-Item firmware/include/config.example.h firmware/include/config.h
Copy-Item gateway/config.example.yaml gateway/config.yaml
# -> Điền WIFI_SSID, WIFI_PASSWORD, MAC 2 mạch YoloUNO và COREIOT_GATEWAY_TOKEN thật.

# Bước 2: Biên dịch và nạp Firmware cho Node 1 (Tx + DHT20) & Node 2 (Rx + BLE + TinyML)
pio run -d firmware/node1_tx_env -e yolo_uno -t upload --upload-port COM3
pio run -d firmware/node2_rx_edge_ai -e yolo_uno -t upload --upload-port COM4

# Bước 3: Đổi `source_mode: "serial"` trong `gateway/config.yaml` và chạy Gateway thật
python gateway/main_gateway.py --config gateway/config.yaml
streamlit run gateway/streamlit_ui.py
```

---

## 5. Kiểm thử Tự động & Đo kiểm Định lượng Chương 4

```powershell
# Chạy toàn bộ kiểm thử tích hợp (C++ INT8 vs Python INT8, RTX 5070 CUDA, Web Server, CoreIOT, RuleChain)
.\.venv\Scripts\pytest.exe -v

# Chạy kịch bản đo kiểm định lượng 50 lần thử/kịch bản (1m, 2m, 3m, NLOS, BLE, Sit-Fast)
.\.venv\Scripts\python.exe tests/run_quantitative_eval.py
```

---

## 6. Quy tắc Bảo mật & Khai báo Sử dụng Công cụ AI
- **Bảo mật thông tin nhạy cảm:** Kho mã nguồn tuân thủ nghiêm ngặt quy định không lưu trữ mật khẩu Wi-Fi, CoreIOT Access Token hay Telegram Bot Token thật trên GitHub (chỉ cung cấp `.env.example`, `config.example.h`, `config.example.yaml`).
- **Khai báo sử dụng AI & Tài nguyên tham khảo:** Nhóm có sử dụng công cụ AI tạo sinh hỗ trợ rà soát kiến trúc mã nguồn, định dạng tài liệu LaTeX và tối ưu hóa pipeline kiểm thử mô phỏng trước khi có mạch thực tế; chuẩn cấu trúc CSI trên ESP32-S3 tham khảo từ tài liệu chính thức `espressif/esp-csi` và `CoreIOT/ThingsBoard Gateway MQTT API`.
