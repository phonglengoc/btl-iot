# Hướng dẫn Cấu hình CoreIOT Gateway, RuleChain, Telegram & Cập nhật OTA (`T11–T15`)

Tài liệu này hướng dẫn từng bước nhập các tệp cấu hình sẵn trong thư mục `coreiot/` lên máy chủ **`https://app.coreiot.io`** (dựa trên nền tảng ThingsBoard) và thực hiện kiểm thử cập nhật Firmware từ xa qua mạng (**OTA — `T14`**, 2.0 điểm).

---

## 1. Khởi tạo Device Profile & Thiết bị Gateway trên CoreIOT (`T11`, `T15`)

1. Đăng nhập vào `https://app.coreiot.io`.
2. Vào **Profiles -> Device profiles -> + Add device profile**:
   - Đặt tên: **`CSI_Radar_Profile`**.
   - Trong tab **Rule chain**, trỏ về RuleChain `CSI_Fall_And_Intrusion_Smart_RuleChain` (sau khi nhập ở Bước 2).
3. Vào **Entities -> Devices -> + Add new device**:
   - Tạo thiết bị **`PC_Local_CSI_Gateway`**, đánh dấu chọn ô **`Is gateway` (`true`)**.
   - Nhấn **Copy access token** và dán vào biến `COREIOT_GATEWAY_TOKEN` trong tệp `.env` hoặc `gateway/config.yaml` (không commit lên GitHub!).
   - Khi khởi chạy `python gateway/main_gateway.py`, hai thiết bị con **`YoloUNO_Node1_Tx`** và **`YoloUNO_Node2_Rx`** sẽ tự động được đăng ký và đồng bộ qua topic `v1/gateway/connect` (`T15`).

---

## 2. Nhập Kịch bản RuleChain & Kết nối Telegram Bot (`T12`, `T13`)

1. Vào **Rule chains -> + Add rule chain -> Import rule chain** và tải lên tệp `coreiot/rulechain_fall_sec.json`.
2. Mở thiết bị `YoloUNO_Node2_Rx` -> tab **Attributes -> Server attributes**, thêm 2 thuộc tính bảo mật (không nằm trong mã nguồn GitHub):
   - `telegramBotToken`: Token lấy từ `@BotFather` trên Telegram.
   - `telegramChatId`: ID nhóm chat gia đình nhận cảnh báo khẩn cấp.
3. Nhấn tích chọn **Apply changes** (biểu tượng dấu tích góc dưới phải RuleChain).

---

## 3. Nhập Giao diện Dashboard Giám sát & Điều khiển Từ xa (`T11`)

1. Vào **Dashboards -> + Add dashboard -> Import dashboard** và chọn tệp `coreiot/dashboard_template.json`.
2. Mở Dashboard vừa nhập -> nhấn **Edit mode -> Entity aliases** (biểu tượng bộ lọc góc trên phải):
   - Gán `alias_node1_tx` vào thiết bị `YoloUNO_Node1_Tx`.
   - Gán `alias_node2_rx` vào thiết bị `YoloUNO_Node2_Rx`.
   - Lưu Dashboard để hiển thị biểu đồ thời gian thực và bảng nút nhấn RPC.

---

## 4. Quy trình Cập nhật Firmware OTA cho Mạch YoloUNO ESP32-S3 (`T14`)

Bảng phân vùng `firmware/partitions_16mb_ota.csv` chia sẵn hai phân vùng đối xứng `ota_0` (`4 MB`) và `ota_1` (`4 MB`) hỗ trợ kiểm tra băm **SHA-256** và tự động **Rollback** nếu bản mới khởi động lỗi.

### Bước 1: Biên dịch bản Firmware nâng cấp (`v1.1.0`) và lấy mã băm SHA-256
```powershell
# Doi FIRMWARE_VERSION thanh "1.1.0" trong firmware/include/config.h, sau do bien dich:
pio run -d firmware/node2_rx_edge_ai -e yolo_uno

# Tinh ma bam SHA-256 cua tep nhi phan firmware.bin:
Get-FileHash firmware\node2_rx_edge_ai\.pio\build\yolo_uno\firmware.bin -Algorithm SHA256
```

### Bước 2: Tải gói Firmware lên CoreIOT OTA Repository
1. Vào mục **OTA updates -> + Add package** trên `app.coreiot.io`:
   - **Title**: `yolo_uno_node2_rx_edge_ai` (khớp với `FIRMWARE_TITLE_NODE2`).
   - **Version**: `1.1.0`.
   - **Package type**: `Firmware`.
   - Tải lên tệp `firmware.bin` vừa biên dịch, chọn thuật toán kiểm tra toàn vẹn **`SHA-256`**.
2. Mở chi tiết thiết bị `YoloUNO_Node2_Rx` -> chọn gói Firmware `1.1.0` vừa tải hoặc gửi lệnh RPC từ Dashboard:
   - Method: `triggerOtaUpdate`
   - Params: `{"version": "1.1.0", "sha256": "<64-ky-tu-sha256>"}`
3. Theo dõi trên Serial Monitor: mạch tải từng phân đoạn `4096 byte`, in thông báo `[OTA-T14] SHA-256 verified OK`, tự khởi động lại sang phân vùng `ota_1` và báo cáo `[OTA-T14] Boot partition verified OK -> Rollback cancelled`.
