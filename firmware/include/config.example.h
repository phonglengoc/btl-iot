#ifndef IOT_BTL_CONFIG_EXAMPLE_H
#define IOT_BTL_CONFIG_EXAMPLE_H

/**
 * @file config.example.h
 * @brief Cau hinh gon nhe cho 2 mach YoloUNO (ESP32-S3): ESP-NOW 20 Hz, I2C DHT20 va GPIO bao dong.
 *
 * HUONG DAN SU DUNG:
 * 1. Sao chep file nay thanh `firmware/include/config.h`:
 *      cp firmware/include/config.example.h firmware/include/config.h
 * 2. Dien dung dia chi MAC that cua 2 mach YoloUNO (xem tren Serial Monitor khi khoi dong).
 * 3. Nap code truc tiep qua cap USB bang PlatformIO (khong can cau hinh Wi-Fi router, BLE hay OTA).
 */

#include <stdint.h>

// ============================================================================
// 1. CAU HINH KENH ESP-NOW & SERIAL USB (T06, T08)
// ============================================================================
#define CSI_WIFI_CHANNEL        6           // Kenh Wi-Fi co dinh 2437 MHz (802.11n HT20)
#define SERIAL_BAUDRATE         115200      // Toc do Serial tieu chuan 115200 bps (on dinh, de doc)

// Dia chi MAC Station cua Node 1 (Tx) va Node 2 (Rx)
// Lay bang lenh `WiFi.macAddress()` in ra Serial khi khoi dong moi mach YoloUNO
static const uint8_t NODE1_TX_MAC_ADDR[6] = {0x34, 0x85, 0x18, 0xAA, 0xBB, 0x01};
static const uint8_t NODE2_RX_MAC_ADDR[6] = {0x34, 0x85, 0x18, 0xAA, 0xBB, 0x02};

// ============================================================================
// 2. SO DO CHAN GPIO TREN 2 MACH YOLOUNO ESP32-S3
// ============================================================================
// --- Node 1 (YoloUNO #1 - Cam bien I2C DHT20 & Phat ESP-NOW 20 Hz) ---
#define NODE1_I2C_SDA_PIN       11          // GPIO11 -> DHT20 SDA (3.3V)
#define NODE1_I2C_SCL_PIN       12          // GPIO12 -> DHT20 SCL (100 kHz)
#define NODE1_DHT20_I2C_ADDR    0x38        // Dia chi I2C mac dinh cua DHT20 / AHT20
#define NODE1_RGB_LED_PIN       48          // GPIO48 -> LED onboard WS2812B tren YoloUNO
#define NODE1_ESPNOW_HZ         20          // Tan so phat goi tin ESP-NOW: 20 goi/giay (50 ms)
#define NODE1_ENV_READ_MS       5000        // Chu ky doc nhiet do, do am DHT20: 5000 ms

// --- Node 2 (YoloUNO #2 - Thu CSI, Xuat Serial & Coi/LED/Nut SOS) ---
#define NODE2_BUZZER_PIN        10          // GPIO10 -> Coi Buzzer (PWM 2700 Hz)
#define NODE2_BUZZER_FREQ_HZ    2700        // Tan so am thanh canh bao khan cap
#define NODE2_NEOPIXEL_PIN      18          // GPIO18 -> Den LED canh bao WS2812B
#define NODE2_NEOPIXEL_COUNT    4           // So bong LED tren cum canh bao
#define NODE2_SOS_BTN_PIN       14          // GPIO14 -> Nut nhan SOS / Tat coi (INPUT_PULLUP)
#define NODE2_SOS_HOLD_MS       3000        // Giu 3 giay -> kich hoat SOS khan cap; nhan nhanh -> tat coi

#endif // IOT_BTL_CONFIG_EXAMPLE_H
