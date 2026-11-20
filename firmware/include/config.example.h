#ifndef IOT_BTL_CONFIG_EXAMPLE_H
#define IOT_BTL_CONFIG_EXAMPLE_H

/**
 * @file config.example.h
 * @brief Mau cau hinh mang Wi-Fi, MAC ESP-NOW, BLE Whitelist, Nguong AI va CoreIOT OTA.
 *
 * HUONG DAN BAO MAT (Tuan thu Quy dinh BTL IoT HK261):
 * 1. Sao chep file nay thanh `firmware/include/config.h`:
 *      cp firmware/include/config.example.h firmware/include/config.h
 * 2. Dien thong so Wi-Fi va Token that vao `config.h` (da duoc chan trong .gitignore).
 * 3. TUYET DOI KHONG commit mat khau that hoac Access Token len GitHub!
 */

#include <stdint.h>

// ============================================================================
// 1. CAU HINH WI-FI NOI BO & KENH VO TUYEN CSI (T06, T07, T14)
// ============================================================================
#define WIFI_SSID               "YOUR_WIFI_SSID"
#define WIFI_PASSWORD           "YOUR_WIFI_PASSWORD"
#define CSI_WIFI_CHANNEL        6           // Kenh co dinh 2437 MHz (802.11n HT20)
#define SERIAL_CSI_BAUDRATE     921600      // Toc do USB-CDC giua Node 2 va PC Gateway

// Dia chi MAC Station cua Node 1 (Tx) va Node 2 (Rx)
// Lay bang lenh `WiFi.macAddress()` khi khoi dong mach YoloUNO
static const uint8_t NODE1_TX_MAC_ADDR[6] = {0x34, 0x85, 0x18, 0xAA, 0xBB, 0x01};
static const uint8_t NODE2_RX_MAC_ADDR[6] = {0x34, 0x85, 0x18, 0xAA, 0xBB, 0x02};

// ============================================================================
// 2. SO DO CHAN GPIO TREN 2 MACH YOLOUNO ESP32-S3 (Khop 100% Bang 3.1 & 3.2)
// ============================================================================
// --- Cum Node 1 (YoloUNO #1 - Tram Phat CSI & Cam bien I2C DHT20) ---
#define NODE1_I2C_SDA_PIN       11          // GPIO11 -> DHT20 SDA (3.3V)
#define NODE1_I2C_SCL_PIN       12          // GPIO12 -> DHT20 SCL (100 kHz)
#define NODE1_DHT20_I2C_ADDR    0x38        // Dia chi I2C mac dinh cua DHT20 / AHT20
#define NODE1_RGB_LED_PIN       48          // GPIO48 -> Onboard WS2812B LED tren YoloUNO
#define NODE1_ESPNOW_HZ         100         // Tan so phat xung CSI: 100 goi/giay (10 ms)
#define NODE1_ENV_READ_MS       5000        // Chu ky doc nhiet am I2C: 5000 ms

// --- Cum Node 2 (YoloUNO #2 - Tram Thu CSI, BLE, TinyML & Web Server) ---
#define NODE2_BUZZER_PIN        10          // GPIO10 -> KY-012 Buzzer PWM (2700 Hz)
#define NODE2_BUZZER_FREQ_HZ    2700        // Tan so am thanh canh bao khan cap
#define NODE2_NEOPIXEL_PIN      18          // GPIO18 -> Cum 4 bong WS2812B RGB
#define NODE2_NEOPIXEL_COUNT    4           // So bong LED tren cum canh bao
#define NODE2_SOS_BTN_PIN       14          // GPIO14 -> Nut nhan SOS / Huy bao dong (INPUT_PULLUP)
#define NODE2_SOS_HOLD_MS       3000        // Giu 3 giay de kich hoat SOS khan cap

// ============================================================================
// 3. DANH SACH TIN CAY BLE BEACON NGUOI NHA (T03)
// ============================================================================
#define BLE_SCAN_INTERVAL_MS    1000        // Quet thu dong moi 1 giay
#define BLE_SCAN_WINDOW_MS      80          // Cua so quet 80 ms tranh xung dot Wi-Fi CSI
#define DEFAULT_BLE_RSSI_MIN    (-75)       // Nguong RSSI xac nhan nguoi nha trong phong (dBm)

static const char* const BLE_AUTHORIZED_MACS[] = {
    "A4:C1:38:9B:12:F0",    // The khoa BLE Tag cua Nguoi cao tuoi #1
    "D8:3A:DD:41:7C:22",    // Smartphone cua Thanh vien gia dinh #2
    "F0:9E:4A:11:55:89"     // Vong deo BLE du phong #3
};
static const size_t BLE_AUTHORIZED_COUNT = sizeof(BLE_AUTHORIZED_MACS) / sizeof(BLE_AUTHORIZED_MACS[0]);

// ============================================================================
// 4. NGUONG MAC DINH TINYML & BAO DONG AN NINH (T04, T07)
// ============================================================================
#define DEFAULT_FALL_THRESHOLD  0.80f       // Xac suat TinyML kich hoat bao dong tai bien [0.50..0.95]
#define DEFAULT_SECURITY_ARMED  true        // Bat che do phat hien dot nhap khi khong co BLE hop le

// ============================================================================
// 5. CAU HINH COREIOT SERVER & CAP NHAT FIRMWARE TU XA OTA (T14)
// ============================================================================
#define COREIOT_MQTT_HOST       "app.coreiot.io"
#define COREIOT_MQTT_PORT       1883
#define COREIOT_HTTP_PORT       80
#define COREIOT_NODE1_TOKEN     "REPLACE_WITH_NODE1_COREIOT_TOKEN"
#define COREIOT_NODE2_TOKEN     "REPLACE_WITH_NODE2_COREIOT_TOKEN"
#define FIRMWARE_TITLE_NODE1    "yolo_uno_node1_tx_env"
#define FIRMWARE_TITLE_NODE2    "yolo_uno_node2_rx_edge_ai"
#define FIRMWARE_VERSION        "1.0.0"

#endif // IOT_BTL_CONFIG_EXAMPLE_H
