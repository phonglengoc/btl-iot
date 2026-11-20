# Cấu hình Tasmota ESP32-S3 cho Cảm biến I2C DHT20/AHT20 trên YoloUNO (`T01`)

Tài liệu này cung cấp cấu hình chuẩn cho chế độ **Tasmota ESP32-S3** (phục vụ minh chứng trực tiếp mã **`T01: Sensor using the I2C interface on Tasmota`** song song với chế độ Firmware tích hợp ESP-NOW `src/main.cpp`).

## 1. Sơ đồ Đấu nối Phần cứng (Khớp Bảng 3.1 Đặc tả)
| Chân DHT20 | Chân YoloUNO (ESP32-S3) | Chức năng |
|---|---|---|
| `VDD` (Chân 1) | `3V3` | Nguồn 3.3V ổn định |
| `SDA` (Chân 2) | `GPIO11` | Tasmota `I2C SDA (640)` |
| `GND` (Chân 3) | `GND` | Nối đất chung |
| `SCL` (Chân 4) | `GPIO12` | Tasmota `I2C SCL (608)` |

## 2. Tasmota Template JSON cho Mạch YoloUNO (ESP32-S3)
Dán chuỗi Template dưới đây vào mục **Configuration -> Configure Other -> Template** trên giao diện Tasmota (`tasmota32s3-sensors.bin`):

```json
{"NAME":"YoloUNO-S3-DHT20","GPIO":[0,0,0,0,0,0,0,640,608,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1376,0,0,0,0,0,0,0],"FLAG":0,"BASE":1}
```
*(Ghi chú: Trên Tasmota32-S3, gán `GPIO11 = I2C SDA (1)` và `GPIO12 = I2C SCL (1)`, `GPIO48 = WS2812 (1)`).*

## 3. Lệnh Console Kiểm tra Bus I2C (`0x38`) & Đẩy Telemetry lên CoreIOT
Chạy các lệnh sau trên **Tasmota Console** (ẩn `<NODE1_COREIOT_TOKEN>` thật khi chụp báo cáo):

```text
I2CScan
I2CDriver43 1
TelePeriod 10
Backlog MqttHost app.coreiot.io; MqttPort 1883; MqttUser <NODE1_COREIOT_TOKEN>; Topic yolo_node1_dht20
Rule1 ON Tele-AHT2X#Temperature DO Publish v1/devices/me/telemetry {"room_temp_c":%value%} ENDON
Rule1 + ON Tele-AHT2X#Humidity DO Publish v1/devices/me/telemetry {"room_humi_rh":%value%} ENDON
Rule1 1
```
Kết quả phản hồi trên Console khi cảm biến hoạt động tốt:
```json
{"I2CScan":"Device(s) found at 0x38"}
{"Time":"2026-11-15T10:30:00","AHT2X":{"Temperature":27.4,"Humidity":68.2,"DewPoint":21.0},"TempUnit":"C"}
```
