#ifndef IOT_BTL_CONFIG_EXAMPLE_H
#define IOT_BTL_CONFIG_EXAMPLE_H

/**
 * @file config.example.h
 * @brief Lightweight configuration template for two YoloUNO (ESP32-S3) nodes:
 *        20 Hz ESP-NOW, I2C DHT20 sensor, and local alarm GPIO pins.
 *
 * SETUP INSTRUCTIONS:
 * 1. Copy this file to `firmware/include/config.h`:
 *      cp firmware/include/config.example.h firmware/include/config.h
 * 2. Fill in the real Station MAC addresses of both YoloUNO boards (printed on Serial at boot).
 * 3. Flash directly over USB using PlatformIO (no Wi-Fi router credentials, BLE, or OTA needed).
 */

#include <stdint.h>

// ============================================================================
// 1. ESP-NOW CHANNEL & USB SERIAL CONFIGURATION (T06, T08)
// ============================================================================
#define CSI_WIFI_CHANNEL        6           // Fixed Wi-Fi channel 6 (2437 MHz, 802.11n HT20)
#define SERIAL_BAUDRATE         115200      // Standard 115200 bps USB Serial baud rate

// Station MAC addresses of Node 1 (Tx) and Node 2 (Rx)
// Obtain from `WiFi.macAddress()` printed on Serial when each YoloUNO boots
static const uint8_t NODE1_TX_MAC_ADDR[6] = {0x34, 0x85, 0x18, 0xAA, 0xBB, 0x01};
static const uint8_t NODE2_RX_MAC_ADDR[6] = {0x34, 0x85, 0x18, 0xAA, 0xBB, 0x02};

// ============================================================================
// 2. GPIO PIN MAPPING ON BOTH YOLOUNO ESP32-S3 BOARDS
// ============================================================================
// --- Node 1 (YoloUNO #1 - I2C DHT20 Sensor & 20 Hz ESP-NOW Transmitter) ---
#define NODE1_I2C_SDA_PIN       11          // GPIO11 -> DHT20 SDA (3.3V)
#define NODE1_I2C_SCL_PIN       12          // GPIO12 -> DHT20 SCL (100 kHz)
#define NODE1_DHT20_I2C_ADDR    0x38        // Default I2C address of DHT20 / AHT20
#define NODE1_RGB_LED_PIN       48          // GPIO48 -> Onboard WS2812B LED on YoloUNO
#define NODE1_ESPNOW_HZ         20          // ESP-NOW transmit rate: 20 packets/sec (50 ms)
#define NODE1_ENV_READ_MS       5000        // DHT20 temperature & humidity read interval: 5000 ms

// --- Node 2 (YoloUNO #2 - CSI Receiver, Serial Output & Buzzer/LED/SOS) ---
#define NODE2_BUZZER_PIN        10          // GPIO10 -> Active/PWM Buzzer (2700 Hz)
#define NODE2_BUZZER_FREQ_HZ    2700        // Emergency alarm tone frequency (Hz)
#define NODE2_NEOPIXEL_PIN      18          // GPIO18 -> WS2812B RGB status LED strip
#define NODE2_NEOPIXEL_COUNT    4           // Number of LEDs on the alert module
#define NODE2_SOS_BTN_PIN       14          // GPIO14 -> SOS / Mute Alarm button (INPUT_PULLUP)
#define NODE2_SOS_HOLD_MS       3000        // Hold 3 seconds for SOS alert; short press to mute alarm

#endif // IOT_BTL_CONFIG_EXAMPLE_H
