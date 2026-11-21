/**
 * @file main.cpp (Node 2 - YoloUNO #2: Wi-Fi CSI Receiver, USB Serial Bridge & Local Alarm)
 * @brief Covered course components:
 *   - T06 (1.5 pt): Receive 20 Hz ESP-NOW packets (DHT20 temperature/humidity) and extract
 *                   52 active Wi-Fi CSI subcarrier amplitudes from Node 1.
 *   - T08 (1.0 pt): Stream JSONL frames over 115200 bps USB Serial to PC Local Gateway
 *                   and execute downlink buzzer commands (`RPC:ALARM:1` / `RPC:ALARM:0`).
 *
 * Real-Board Highlights:
 *   - Plug-and-play Auto-Pair: When NODE1_TX_MAC_ADDR is {0xFF,...}, Node 2 automatically learns
 *     Node 1's MAC from the first valid 0xC51A + CRC-8 packet and locks CSI capture to that board.
 *   - Compatible with both Arduino-ESP32 v2.x and v3.x on YoloUNO (ESP32-S3).
 */

#include <Arduino.h>
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

// Latest CSI frame passed from ISR callback to loop() via a 1-slot queue (no manual spinlocks)
typedef struct {
    int8_t rssi;
    int8_t iq_raw[CSI_RAW_IQ_BYTES];
} csi_frame_t;

static QueueHandle_t g_csiQueue = nullptr;

// Auto-pair or pinned Node 1 MAC address
static uint8_t  g_targetNode1Mac[6] = {0};
static volatile bool g_targetMacLocked = false;
static bool     g_isBroadcastMode      = false;

// Latest DHT20 readings & packet stats received from Node 1
static volatile float    g_tempC         = 27.5f;
static volatile float    g_humiRh        = 65.0f;
static volatile uint32_t g_lastSeqId     = 0;
static volatile uint32_t g_rxPackets     = 0;
static volatile uint32_t g_missedPackets = 0;
static volatile uint32_t g_lastRxMs      = 0;

// Local buzzer alarm & SOS button state
static bool     g_alarmActive    = false;
static bool     g_sosTriggered   = false;
static bool     g_btnWasLow      = false;
static uint32_t g_btnPressStart  = 0;
static uint32_t g_lastDiagMs     = 0;

static bool isAllFF(const uint8_t mac[6]) {
    for (int i = 0; i < 6; ++i) {
        if (mac[i] != 0xFFu) return false;
    }
    return true;
}

static void setNode2Led(uint8_t r, uint8_t g, uint8_t b) {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
    rgbLedWrite(NODE2_NEOPIXEL_PIN, r, g, b);
#elif defined(RGB_BUILTIN)
    neopixelWrite(NODE2_NEOPIXEL_PIN, r, g, b);
#else
    (void)r; (void)g; (void)b;
#endif
}

// ============================================================================
// 1. Local Alarm Hardware Control: Buzzer (GPIO10) & WS2812B LED (GPIO18)
// ============================================================================
static void setAlarmHardware(bool active) {
    g_alarmActive = active;
    if (active) {
        tone(NODE2_BUZZER_PIN, NODE2_BUZZER_FREQ_HZ);
        setNode2Led(255, 0, 0); // Red: Emergency alarm active!
    } else {
        g_sosTriggered = false;
        noTone(NODE2_BUZZER_PIN);
        digitalWrite(NODE2_BUZZER_PIN, LOW);
        setNode2Led(0, 48, 12); // Green: Room safe
    }
}

// ============================================================================
// 2. ESP-NOW Receive Handler (Compatible with Arduino-ESP32 v2.x & v3.x) - T06
// ============================================================================
static void handleEspNowPacket(const uint8_t* mac, const uint8_t* incomingData, int len) {
    if (!mac || !incomingData || len < (int)sizeof(csi_ping_packet_t)) return;

    csi_ping_packet_t pkt{};
    memcpy(&pkt, incomingData, sizeof(pkt));
    if (pkt.magic_header != CSI_PING_MAGIC_HEADER) return;

    if (csi_crc8_compute(reinterpret_cast<const uint8_t*>(&pkt), sizeof(pkt) - 1) != pkt.crc8_checksum) {
        return;
    }

    if (!g_targetMacLocked) {
        if (g_isBroadcastMode) {
            memcpy(g_targetNode1Mac, mac, 6);
            g_targetMacLocked = true;
        }
    }
    if (g_targetMacLocked && memcmp(mac, g_targetNode1Mac, 6) != 0) {
        return;
    }

    if (g_lastSeqId > 0 && pkt.seq_id > g_lastSeqId + 1u) {
        g_missedPackets = g_missedPackets + (pkt.seq_id - g_lastSeqId - 1u);
    }
    g_lastSeqId = pkt.seq_id;
    g_rxPackets = g_rxPackets + 1u;
    g_tempC    = pkt.temp_c;
    g_humiRh   = pkt.humi_rh;
    g_lastRxMs = millis();
}

#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
static void onEspNowDataRecv(const esp_now_recv_info_t* info, const uint8_t* incomingData, int len) {
    if (!info) return;
    handleEspNowPacket(info->src_addr, incomingData, len);
}
#else
static void onEspNowDataRecv(const uint8_t* mac, const uint8_t* incomingData, int len) {
    handleEspNowPacket(mac, incomingData, len);
}
#endif

// ============================================================================
// 3. Wi-Fi PHY CSI Callback (Extracts 128 raw LLTF I/Q bytes from Node 1)
// ============================================================================
static void IRAM_ATTR wifi_csi_rx_cb(void* /*ctx*/, wifi_csi_info_t* info) {
    if (!info || !info->buf || info->len < (int)CSI_RAW_IQ_BYTES) return;
    if (!g_targetMacLocked) return;
    if (memcmp(info->mac, g_targetNode1Mac, 6) != 0) return;

    csi_frame_t frame;
    frame.rssi = info->rx_ctrl.rssi;
    memcpy(frame.iq_raw, info->buf, CSI_RAW_IQ_BYTES);

    BaseType_t higherWoken = pdFALSE;
    xQueueOverwriteFromISR(g_csiQueue, &frame, &higherWoken);
    if (higherWoken == pdTRUE) {
        portYIELD_FROM_ISR();
    }
}

// ============================================================================
// 4. SOS / Mute Button Handler (Hold 3s -> SOS Alarm; Short Press -> Mute)
// ============================================================================
static void pollSosButton() {
    const uint32_t now = millis();
    const bool isLow = (digitalRead(NODE2_SOS_BTN_PIN) == LOW);

    if (isLow && !g_btnWasLow) {
        g_btnPressStart = now;
        g_btnWasLow = true;
    } else if (isLow && g_btnWasLow) {
        if ((now - g_btnPressStart) >= NODE2_SOS_HOLD_MS && !g_sosTriggered) {
            g_sosTriggered = true;
            setAlarmHardware(true);
        }
    } else if (!isLow && g_btnWasLow) {
        uint32_t heldMs = now - g_btnPressStart;
        if (heldMs >= 40u && heldMs < NODE2_SOS_HOLD_MS) {
            setAlarmHardware(false); // Short press -> silence alarm locally
        }
        g_btnWasLow = false;
    }
}

// ============================================================================
// 5. Read Downlink RPC Commands from PC Local Gateway over USB Serial (T08)
// ============================================================================
static void pollSerialCommands() {
    while (Serial.available()) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();
        if (cmd.startsWith("RPC:ALARM:")) {
            bool on = cmd.endsWith("1") || cmd.endsWith("ON") || cmd.endsWith("true");
            setAlarmHardware(on);
        }
    }
}

static void printMacForConfig(const char* label) {
    uint8_t mac[6];
    WiFi.macAddress(mac);
    Serial.printf("[%s] MAC: %02X:%02X:%02X:%02X:%02X:%02X  (config.h: {0x%02X, 0x%02X, 0x%02X, 0x%02X, 0x%02X, 0x%02X})\n",
                  label,
                  mac[0], mac[1], mac[2], mac[3], mac[4], mac[5],
                  mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

// ============================================================================
// 6. Node 2 Setup
// ============================================================================
void setup() {
    Serial.begin(SERIAL_BAUDRATE);
    delay(300);

    pinMode(NODE2_BUZZER_PIN, OUTPUT);
    digitalWrite(NODE2_BUZZER_PIN, LOW);
    pinMode(NODE2_SOS_BTN_PIN, INPUT_PULLUP);
    setAlarmHardware(false);

    g_isBroadcastMode = isAllFF(NODE1_TX_MAC_ADDR);
    if (!g_isBroadcastMode) {
        memcpy(g_targetNode1Mac, NODE1_TX_MAC_ADDR, 6);
        g_targetMacLocked = true;
    }

    g_csiQueue = xQueueCreate(1, sizeof(csi_frame_t));

    // Configure Wi-Fi Station on fixed channel 6 for peer-to-peer ESP-NOW & CSI (no router needed)
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    esp_wifi_set_ps(WIFI_PS_NONE);
    esp_wifi_set_protocol(WIFI_IF_STA, WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N);
    esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW_HT20);
    esp_wifi_set_channel(CSI_WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
    esp_wifi_set_promiscuous(true); // Ensures CSI callback fires reliably for broadcast & unicast ESP-NOW

    // Enable 64-subcarrier Wi-Fi CSI capture
    wifi_csi_config_t csiCfg{};
    csiCfg.lltf_en           = true;
    csiCfg.htltf_en          = true;
    csiCfg.stbc_htltf2_en    = true;
    csiCfg.ltf_merge_en      = true;
    csiCfg.channel_filter_en = true;
    csiCfg.manu_scale        = false;
    esp_wifi_set_csi_config(&csiCfg);
    esp_wifi_set_csi_rx_cb(&wifi_csi_rx_cb, nullptr);
    esp_wifi_set_csi(true);

    if (esp_now_init() == ESP_OK) {
        esp_now_register_recv_cb(onEspNowDataRecv);
    }

    printMacForConfig("NODE2-RX");
    Serial.printf("[NODE2] Ready! Channel=%d | Baud=%d | Mode=%s\n",
                  CSI_WIFI_CHANNEL, SERIAL_BAUDRATE,
                  g_isBroadcastMode ? "AUTO_PAIR_BROADCAST" : "PINNED_MAC");
}

// ============================================================================
// 7. Main Loop: Poll SOS Button + Poll Serial RPC + Stream 52 CSI Amplitudes
// ============================================================================
void loop() {
    pollSosButton();
    pollSerialCommands();

    const uint32_t now = millis();
    if ((now - g_lastRxMs) > 5000u && (now - g_lastDiagMs) > 5000u) {
        g_lastDiagMs = now;
        Serial.printf("[NODE2] Waiting for Node 1 ESP-NOW packets on Ch %d (rx=%lu)...\n",
                      CSI_WIFI_CHANNEL, (unsigned long)g_rxPackets);
    }

    csi_frame_t frame;
    if (xQueueReceive(g_csiQueue, &frame, pdMS_TO_TICKS(10)) == pdTRUE) {
        float amps52[CSI_ACTIVE_SUBCARRIERS];
        csi_iq_to_amplitudes_52(frame.iq_raw, amps52);

        uint32_t totalExpected = g_rxPackets + g_missedPackets;
        float lossPct = (totalExpected > 0u)
                            ? (100.0f * (float)g_missedPackets / (float)totalExpected)
                            : 0.0f;

        // Emit one compact JSONL line over 115200 bps Serial (~360 bytes @ 20 Hz = ~7.2 KB/s)
        Serial.printf(
            "{\"ts_ms\":%lu,\"temp_c\":%.2f,\"humi_rh\":%.2f,"
            "\"alarm_active\":%s,\"sos\":%s,\"rssi\":%d,"
            "\"rx_packets\":%lu,\"pkt_loss_pct\":%.2f,\"amps_52\":[",
            (unsigned long)millis(),
            (double)g_tempC,
            (double)g_humiRh,
            g_alarmActive ? "true" : "false",
            g_sosTriggered ? "true" : "false",
            (int)frame.rssi,
            (unsigned long)g_rxPackets,
            (double)lossPct
        );
        for (size_t k = 0; k < CSI_ACTIVE_SUBCARRIERS; ++k) {
            Serial.printf(k + 1 < CSI_ACTIVE_SUBCARRIERS ? "%.1f," : "%.1f]}\n", (double)amps52[k]);
        }
    }
}
