/**
 * @file main.cpp (Node 1 - YoloUNO #1: I2C DHT20 Sensor & 20 Hz ESP-NOW Transmitter)
 * @brief Covered course components:
 *   - T01 (0.5 pt): Read I2C DHT20/AHT20 temperature & humidity sensor (address 0x38, GPIO11 SDA, GPIO12 SCL).
 *   - T06 (1.5 pt): Transmit 24-byte `csi_ping_packet_t` frames every 50 ms (20 Hz) via ESP-NOW to Node 2.
 *
 * Simple architecture: Single Arduino `loop()` using `millis()` timers — no FreeRTOS multi-core tasks or OTA.
 */

#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <esp_now.h>

#if __has_include("../include/config.h")
  #include "../include/config.h"
#endif
#ifndef CSI_WIFI_CHANNEL
  #include "config.example.h"
#endif

#include "csi_protocol.h"

static float    g_latestTempC  = 27.5f;
static float    g_latestHumiRh = 65.0f;
static uint8_t  g_node1Status  = 0;
static uint32_t g_txSeqId      = 0;
static volatile uint32_t g_txFailCount = 0;

static uint32_t g_lastEnvMs    = 0;
static uint32_t g_lastTxMs     = 0;
static bool     g_ledToggle    = false;

// ============================================================================
// 1. I2C DHT20 Temperature & Humidity Driver (Address 0x38) - T01
// ============================================================================
static bool dht20Init() {
    Wire.begin(NODE1_I2C_SDA_PIN, NODE1_I2C_SCL_PIN, 100000u);
    delay(40);

    Wire.beginTransmission(NODE1_DHT20_I2C_ADDR);
    Wire.write(0x71);
    if (Wire.endTransmission() != 0) {
        return false;
    }
    if (Wire.requestFrom((uint8_t)NODE1_DHT20_I2C_ADDR, (uint8_t)1) != 1) {
        return false;
    }
    uint8_t status = Wire.read();
    if ((status & 0x18) != 0x18) {
        const uint8_t initRegs[3] = {0x1B, 0x1C, 0x1E};
        for (uint8_t reg : initRegs) {
            Wire.beginTransmission(NODE1_DHT20_I2C_ADDR);
            Wire.write(reg);
            Wire.write(0x00);
            Wire.write(0x00);
            Wire.endTransmission();
            delay(5);
        }
    }
    return true;
}

static bool dht20ReadSample(float& outTempC, float& outHumiRh) {
    // Trigger measurement command: 0xAC 0x33 0x00
    Wire.beginTransmission(NODE1_DHT20_I2C_ADDR);
    Wire.write(0xAC);
    Wire.write(0x33);
    Wire.write(0x00);
    if (Wire.endTransmission() != 0) {
        return false;
    }

    delay(85); // Wait for ADC conversion (~80 ms)

    if (Wire.requestFrom((uint8_t)NODE1_DHT20_I2C_ADDR, (uint8_t)7) != 7) {
        return false;
    }
    uint8_t raw[7];
    for (int i = 0; i < 7; ++i) {
        raw[i] = Wire.read();
    }

    if ((raw[0] & 0x80u) != 0) {
        return false; // Sensor still busy
    }
    if (dht20_crc8_compute(raw, 6) != raw[6]) {
        return false; // CRC-8 mismatch
    }

    uint32_t rawHumi = ((uint32_t)raw[1] << 12) | ((uint32_t)raw[2] << 4) | ((uint32_t)(raw[3] >> 4) & 0x0Fu);
    uint32_t rawTemp = (((uint32_t)raw[3] & 0x0Fu) << 16) | ((uint32_t)raw[4] << 8) | (uint32_t)raw[5];

    outHumiRh = ((float)rawHumi / 1048576.0f) * 100.0f;
    outTempC  = ((float)rawTemp / 1048576.0f) * 200.0f - 50.0f;
    return true;
}

// ============================================================================
// 2. ESP-NOW Send Status Callback - T06
// ============================================================================
static void onEspNowDataSent(const uint8_t* /*mac_addr*/, esp_now_send_status_t status) {
    if (status != ESP_NOW_SEND_SUCCESS) {
        g_txFailCount++;
    }
}

// ============================================================================
// 3. Node 1 Setup
// ============================================================================
void setup() {
    Serial.begin(SERIAL_BAUDRATE);
    delay(200);

    Serial.println("============================================================");
    Serial.println(" YoloUNO #1 (Node 1) - DHT20 (T01) & ESP-NOW 20 Hz Tx (T06) ");
    Serial.println("============================================================");

    bool sensorOk = dht20Init();
    Serial.printf("[NODE1-T01] DHT20 I2C(0x38, SDA=%d, SCL=%d): %s\n",
                  NODE1_I2C_SDA_PIN, NODE1_I2C_SCL_PIN, sensorOk ? "OK" : "NOT_FOUND_USING_DEFAULT");

    // Enable Wi-Fi Station mode on fixed channel 6 for peer-to-peer ESP-NOW (no router needed)
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    esp_wifi_set_ps(WIFI_PS_NONE);
    esp_wifi_set_protocol(WIFI_IF_STA, WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N);
    esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW_HT20);
    esp_wifi_set_channel(CSI_WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);

    Serial.printf("[NODE1-T06] MAC=%s | Channel=%d | Rate=%d Hz\n",
                  WiFi.macAddress().c_str(), CSI_WIFI_CHANNEL, NODE1_ESPNOW_HZ);

    if (esp_now_init() != ESP_OK) {
        Serial.println("[NODE1] ERROR: Failed to initialize ESP-NOW!");
        return;
    }
    esp_now_register_send_cb(onEspNowDataSent);

    esp_now_peer_info_t peerInfo{};
    memcpy(peerInfo.peer_addr, NODE2_RX_MAC_ADDR, 6);
    peerInfo.channel = CSI_WIFI_CHANNEL;
    peerInfo.ifidx   = WIFI_IF_STA;
    peerInfo.encrypt = false;
    if (esp_now_add_peer(&peerInfo) != ESP_OK) {
        Serial.println("[NODE1] WARN: Failed to add Node 2 peer MAC (check config.h)");
    }
}

// ============================================================================
// 4. Main Loop
// ============================================================================
void loop() {
    const uint32_t now = millis();
    const uint32_t txPeriodMs = 1000u / NODE1_ESPNOW_HZ; // 50 ms = 20 Hz

    // (A) Read I2C DHT20 sensor every 5 seconds
    if ((now - g_lastEnvMs) >= NODE1_ENV_READ_MS) {
        g_lastEnvMs = now;
        float t = 27.5f, h = 65.0f;
        bool ok = dht20ReadSample(t, h);
        if (ok) {
            g_latestTempC  = t;
            g_latestHumiRh = h;
            g_node1Status |= NODE1_STATUS_DHT20_OK;
        } else {
            g_node1Status &= (uint8_t)(~NODE1_STATUS_DHT20_OK);
        }

        Serial.printf("{\"Time\":%lu,\"DHT20\":{\"Temperature\":%.2f,\"Humidity\":%.2f,\"I2C_OK\":%s},"
                      "\"ESPNOW\":{\"tx_seq\":%lu,\"tx_fail\":%lu,\"ch\":%d}}\n",
                      (unsigned long)now,
                      (double)g_latestTempC,
                      (double)g_latestHumiRh,
                      ok ? "true" : "false",
                      (unsigned long)g_txSeqId,
                      (unsigned long)g_txFailCount,
                      CSI_WIFI_CHANNEL);
    }

    // (B) Transmit 24-byte ESP-NOW packet every 50 ms (20 Hz)
    if ((now - g_lastTxMs) >= txPeriodMs) {
        g_lastTxMs = now;

        csi_ping_packet_t pkt{};
        pkt.magic_header    = CSI_PING_MAGIC_HEADER;
        pkt.seq_id          = ++g_txSeqId;
        pkt.tx_time_ms      = now;
        pkt.temp_c          = g_latestTempC;
        pkt.humi_rh         = g_latestHumiRh;
        pkt.tx_status_flag  = g_node1Status;
        pkt.reserved_pad[0] = CSI_WIFI_CHANNEL;
        pkt.reserved_pad[1] = NODE1_ESPNOW_HZ;
        pkt.crc8_checksum   = csi_crc8_compute(reinterpret_cast<const uint8_t*>(&pkt), sizeof(pkt) - 1);

        esp_now_send(NODE2_RX_MAC_ADDR, reinterpret_cast<const uint8_t*>(&pkt), sizeof(pkt));

        // Blink onboard RGB LED every 10 packets (0.5 s) as a heartbeat indicator
        if ((pkt.seq_id % 10u) == 0u) {
            g_ledToggle = !g_ledToggle;
            #ifdef RGB_BUILTIN
            neopixelWrite(NODE1_RGB_LED_PIN, 0, g_ledToggle ? 24 : 0, 0);
            #endif
        }
    }
}
