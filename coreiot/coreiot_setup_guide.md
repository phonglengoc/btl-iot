# Hướng dẫn Cấu hình CoreIOT Gateway, Dashboard, RuleChain & Telegram (`T11`, `T12`, `T13`, `T15`)

Tài liệu này hướng dẫn từng bước thiết lập nền tảng **`https://app.coreiot.io`** (dựa trên ThingsBoard) cho hệ thống **Phát hiện Té ngã & Giám sát Phòng ở Thông minh**.

---

## 1. Tạo Device Profile & Thiết bị Gateway trên CoreIOT (`T11`, `T15`)

1. Đăng nhập vào `https://app.coreiot.io`.
2. Vào **Profiles -> Device profiles -> + Add device profile**:
   - Đặt tên: **`Smart_Room_Fall_Profile`**.
   - Trong tab **Rule chain**, chọn `Smart_Room_Fall_Alert_RuleChain` (sau khi nhập ở Bước 2).
3. Vào **Entities -> Devices -> + Add new device**:
   - Tạo thiết bị **`PC_Local_Fall_Gateway`**, đánh dấu chọn ô **`Is gateway` (`true`)**.
   - Nhấn **Copy access token** và dán vào `COREIOT_GATEWAY_TOKEN` trong tệp `.env` hoặc `gateway/config.yaml` (không commit lên GitHub!).
   - Khi chạy `python gateway/main_gateway.py`, hai thiết bị con **`YoloUNO_Node1_Tx`** và **`YoloUNO_Node2_Rx`** sẽ tự động được tạo và đồng bộ dữ liệu qua MQTT (`v1/gateway/connect`, `v1/gateway/telemetry`).

---

## 2. Nhập RuleChain & Kết nối Telegram Bot (`T12`, `T13`)

1. Vào **Rule chains -> + Add rule chain -> Import rule chain** và chọn tệp `coreiot/rulechain_fall.json`.
2. Luồng xử lý rất đơn giản:
   - Lưu dữ liệu thời gian thực -> Kiểm tra điều kiện `if (msg.csi_state === 'FALL' && msg.ai_conf >= 0.80 || msg.sos_triggered === true)` -> Tạo Alarm trên Dashboard + Gửi tin nhắn Telegram + Gửi lệnh RPC bật còi.
3. Mở thiết bị `YoloUNO_Node2_Rx` -> tab **Attributes -> Server attributes**, thêm 2 thuộc tính bảo mật:
   - `telegramBotToken`: Token lấy từ `@BotFather` trên Telegram.
   - `telegramChatId`: ID nhóm chat hoặc người thân nhận cảnh báo.
4. Nhấn **Apply changes** để lưu RuleChain.

---

## 3. Nhập Giao diện Dashboard Giám sát & Điều khiển Còi (`T11`)

1. Vào **Dashboards -> + Add dashboard -> Import dashboard** và chọn tệp `coreiot/dashboard_template.json`.
2. Mở Dashboard vừa nhập -> nhấn **Edit mode -> Entity aliases**:
   - Gán `alias_node1_tx` vào thiết bị `YoloUNO_Node1_Tx`.
   - Gán `alias_node2_rx` vào thiết bị `YoloUNO_Node2_Rx`.
3. Lưu Dashboard để theo dõi nhiệt độ/độ ẩm DHT20, trạng thái AI, đồ thị sóng CSI và nút bấm RPC bật/tắt còi báo động từ xa.
