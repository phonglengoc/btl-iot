#ifndef IOT_BTL_CONFIG_EXAMPLE_H
#define IOT_BTL_CONFIG_EXAMPLE_H

/**
 * @file config.example.h
 * @brief Hardware configuration for two YoloUNO (ESP32-S3) boards:
 *        20 Hz ESP-NOW, I2C DHT20 sensor, and local alarm GPIO pins.
 *
 * PLUG-AND-PLAY SETUP FOR REAL BOARDS:
 *   - By default, NODE1_TX_MAC_ADDR and NODE2_RX_MAC_ADDR are set to Broadcast
 *     ({0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF}) so Node 1 and Node 2 auto-pair on
 *     Channel 6 immediately after flashing (Node 2 learns Node 1's MAC from the
 *     first valid 0xC51A + CRC-8 ESP-NOW packet).
 *   - If multiple teams test in the same classroom, copy this file to
 *     `firmware/include/config.h` and pin the exact Station MAC addresses printed
 *     on Serial when each board boots.
 */

#include <stdint.h>

// ============================================================================
// 1. ESP-NOW CHANNEL & USB SERIAL CONFIGURATION (T06, T08)
// ============================================================================
#define CSI_WIFI_CHANNEL        6           // Fixed Wi-Fi channel 6 (2437 MHz, 802.11n HT20)
#define SERIAL_BAUDRATE         115200      // Standard 115200 bps USB Serial baud rate

// Station MAC addresses of Node 1 (Tx) and Node 2 (Rx).
// Keep 0xFF (broadcast) for instant auto-pairing, or replace with exact board MACs.
static const uint8_t NODE1_TX_MAC_ADDR[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
static const uint8_t NODE2_RX_MAC_ADDR[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// ============================================================================
// 2. GPIO PIN MAPPING ON BOTH YOLOUNO ESP32-S3 BOARDS
// ============================================================================
// --- Node 1 (YoloUNO #1 - I2C DHT20 Sensor & 20 Hz ESP-NOW Transmitter) ---
#define NODE1_I2C_SDA_PIN       11          // GPIO11 -> DHT20 SDA (YoloUNO Grove I2C, 3.3V)
#define NODE1_I2C_SCL_PIN       12          // GPIO12 -> DHT20 SCL (YoloUNO Grove I2C, 100 kHz)
#define NODE1_DHT20_I2C_ADDR    0x38        // Default I2C address of DHT20 / AHT20
#define NODE1_RGB_LED_PIN       45          // GPIO45 -> Onboard WS2812B LED on YoloUNO (GPIO48 on DevKitC)
#define NODE1_ESPNOW_HZ         20          // ESP-NOW transmit rate: 20 packets/sec (50 ms)
#define NODE1_ENV_READ_MS       5000        // DHT20 temperature & humidity read interval: 5000 ms

// --- Node 2 (YoloUNO #2 - CSI Receiver, Serial Output & Hardware Visual Alert v1.2) ---
// Official OhStem YoloUNO ESP32-S3 pinout (variants/yolo_uno/pins_arduino.h v3.1.3):
//   I2C1: SDA=GPIO11, SCL=GPIO12 | D3=GPIO6, D4=GPIO7 | D5=GPIO8, D6=GPIO9 | RGB=GPIO45
#define NODE2_I2C_SDA_PIN       11          // I2C1 SDA -> OhStem LCD 16x2 I2C (0x21)
#define NODE2_I2C_SCL_PIN       12          // I2C1 SCL -> OhStem LCD 16x2 I2C (100 kHz)
#define NODE2_LCD_I2C_ADDR      0x21        // OhStem LCD 16x2 I2C default address (auto-probes 0x21/0x27/0x3F)
#define NODE2_TL_PIN_D5         8           // Grove D6-D5 -> D5 = GPIO8 (Traffic Light Bit 0 / IN1)
#define NODE2_TL_PIN_D6         9           // Grove D6-D5 -> D6 = GPIO9 (Traffic Light Bit 1 / IN2)
#define NODE2_BTN_PIN_D3        6           // Grove D4-D3 -> D3 = GPIO6 (SOS / Mute Button SIG1)
#define NODE2_BTN_PIN_D4        7           // Grove D4-D3 -> D4 = GPIO7 (SOS / Mute Button SIG2)
#define NODE2_ONBOARD_RGB_PIN   45          // GPIO45 -> Onboard WS2812B LED on YoloUNO
#define NODE2_ONBOARD_LED_PIN   48          // GPIO48 -> Onboard single LED (D13)
#define NODE2_SOS_BTN_PIN       6           // Primary SOS button pin (D3 = GPIO6)
#define NODE2_SOS_HOLD_MS       3000        // Hold 3 seconds for SOS alert; short press to mute alarm

#endif // IOT_BTL_CONFIG_EXAMPLE_H
