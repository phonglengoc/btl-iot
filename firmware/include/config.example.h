#ifndef IOT_BTL_CONFIG_EXAMPLE_H
#define IOT_BTL_CONFIG_EXAMPLE_H

/**
 * @file config.example.h
 * @brief Template configuration for Wi-Fi, ESP-NOW MACs, BLE Whitelist, AI Thresholds, and CoreIOT OTA.
 *
 * SECURITY GUIDELINES (HCMUT IoT Project HK261):
 * 1. Copy this file to `firmware/include/config.h`:
 *      cp firmware/include/config.example.h firmware/include/config.h
 * 2. Fill in your real Wi-Fi credentials and tokens in `config.h` (ignored by .gitignore).
 * 3. NEVER commit real passwords or Access Tokens to GitHub!
 */

#include <stdint.h>

// ============================================================================
// 1. LOCAL WI-FI & CSI RADIO CHANNEL CONFIGURATION (T06, T07, T14)
// ============================================================================
#define WIFI_SSID               "YOUR_WIFI_SSID"
#define WIFI_PASSWORD           "YOUR_WIFI_PASSWORD"
#define CSI_WIFI_CHANNEL        6           // Fixed 2437 MHz channel (802.11n HT20)
#define SERIAL_CSI_BAUDRATE     921600      // USB-CDC baudrate between Node 2 and PC Gateway

// Station MAC addresses of Node 1 (Tx) and Node 2 (Rx)
// Obtain via `WiFi.macAddress()` on boot of each YoloUNO board
static const uint8_t NODE1_TX_MAC_ADDR[6] = {0x34, 0x85, 0x18, 0xAA, 0xBB, 0x01};
static const uint8_t NODE2_RX_MAC_ADDR[6] = {0x34, 0x85, 0x18, 0xAA, 0xBB, 0x02};

// ============================================================================
// 2. GPIO PIN MAP FOR DUAL YOLOUNO ESP32-S3 BOARDS (Matches Report Tables 3.1 & 3.2)
// ============================================================================
// --- Node 1 (YoloUNO #1 - CSI Transmitter & I2C DHT20 Sensor) ---
#define NODE1_I2C_SDA_PIN       11          // GPIO11 -> DHT20 SDA (3.3V)
#define NODE1_I2C_SCL_PIN       12          // GPIO12 -> DHT20 SCL (100 kHz)
#define NODE1_DHT20_I2C_ADDR    0x38        // Default I2C address of DHT20 / AHT20
#define NODE1_RGB_LED_PIN       48          // GPIO48 -> Onboard WS2812B LED on YoloUNO
#define NODE1_ESPNOW_HZ         100         // CSI ping transmission rate: 100 packets/sec (10 ms)
#define NODE1_ENV_READ_MS       5000        // I2C temperature/humidity polling interval: 5000 ms

// --- Node 2 (YoloUNO #2 - CSI Receiver, BLE, TinyML & Embedded Web Server) ---
#define NODE2_BUZZER_PIN        10          // GPIO10 -> KY-012 Buzzer PWM (2700 Hz)
#define NODE2_BUZZER_FREQ_HZ    2700        // Emergency siren acoustic frequency
#define NODE2_NEOPIXEL_PIN      18          // GPIO18 -> 4-LED WS2812B RGB alert strip
#define NODE2_NEOPIXEL_COUNT    4           // Number of RGB LEDs in alert strip
#define NODE2_SOS_BTN_PIN       14          // GPIO14 -> Physical SOS / Alarm Reset button (INPUT_PULLUP)
#define NODE2_SOS_HOLD_MS       3000        // Hold for 3 seconds to trigger emergency SOS

// ============================================================================
// 3. AUTHORIZED RESIDENT BLE BEACON WHITELIST (T03)
// ============================================================================
#define BLE_SCAN_INTERVAL_MS    1000        // Passive scan every 1 second
#define BLE_SCAN_WINDOW_MS      80          // 80 ms scan window to avoid Wi-Fi CSI radio contention
#define DEFAULT_BLE_RSSI_MIN    (-75)       // Minimum RSSI threshold confirming resident presence (dBm)

static const char* const BLE_AUTHORIZED_MACS[] = {
    "A4:C1:38:9B:12:F0",    // Resident #1 BLE key fob tag
    "D8:3A:DD:41:7C:22",    // Family member #2 smartphone
    "F0:9E:4A:11:55:89"     // Backup BLE wristband #3
};
static const size_t BLE_AUTHORIZED_COUNT = sizeof(BLE_AUTHORIZED_MACS) / sizeof(BLE_AUTHORIZED_MACS[0]);

// ============================================================================
// 4. DEFAULT TINYML & INTRUSION SECURITY THRESHOLDS (T04, T07)
// ============================================================================
#define DEFAULT_FALL_THRESHOLD  0.80f       // On-device TinyML fall alert threshold [0.50..0.95]
#define DEFAULT_SECURITY_ARMED  true        // Enable intrusion alerts when no valid BLE tag is present

// ============================================================================
// 5. COREIOT SERVER & OVER-THE-AIR (OTA) FIRMWARE CONFIGURATION (T14)
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
