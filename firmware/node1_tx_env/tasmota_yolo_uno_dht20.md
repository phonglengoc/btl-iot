# Tasmota ESP32-S3 Configuration for I2C DHT20/AHT20 on YoloUNO (`T01`)

This guide provides a standard **Tasmota ESP32-S3** configuration to demonstrate **`T01: Sensor using the I2C interface on Tasmota`** alongside the custom ESP-NOW firmware in `src/main.cpp`.

## 1. Hardware Wiring
| DHT20 Pin | YoloUNO (ESP32-S3) Pin | Function |
|---|---|---|
| `VDD` (Pin 1) | `3V3` | Regulated 3.3V power |
| `SDA` (Pin 2) | `GPIO11` | Tasmota `I2C SDA (640)` |
| `GND` (Pin 3) | `GND` | Common ground |
| `SCL` (Pin 4) | `GPIO12` | Tasmota `I2C SCL (608)` |

## 2. Tasmota Template JSON for YoloUNO (ESP32-S3)
Paste the following template under **Configuration -> Configure Other -> Template** in Tasmota (`tasmota32s3-sensors.bin`):

```json
{"NAME":"YoloUNO-S3-DHT20","GPIO":[0,0,0,0,0,0,0,640,608,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1376,0,0,0,0,0,0,0],"FLAG":0,"BASE":1}
```
*(Note: On Tasmota32-S3, assign `GPIO11 = I2C SDA (1)`, `GPIO12 = I2C SCL (1)`, and `GPIO48 = WS2812 (1)`.)*

## 3. Console Commands to Verify I2C (`0x38`) & Publish Telemetry to CoreIOT
Run these commands in the **Tasmota Console** (redact `<NODE1_COREIOT_TOKEN>` before taking report screenshots):

```text
I2CScan
I2CDriver43 1
TelePeriod 10
Backlog MqttHost app.coreiot.io; MqttPort 1883; MqttUser <NODE1_COREIOT_TOKEN>; Topic yolo_node1_dht20
Rule1 ON Tele-AHT2X#Temperature DO Publish v1/devices/me/telemetry {"room_temp_c":%value%} ENDON
Rule1 + ON Tele-AHT2X#Humidity DO Publish v1/devices/me/telemetry {"room_humi_rh":%value%} ENDON
Rule1 1
```
Expected console output when the sensor is wired properly:
```json
{"I2CScan":"Device(s) found at 0x38"}
{"Time":"2026-11-15T10:30:00","AHT2X":{"Temperature":27.4,"Humidity":68.2,"DewPoint":21.0},"TempUnit":"C"}
```
