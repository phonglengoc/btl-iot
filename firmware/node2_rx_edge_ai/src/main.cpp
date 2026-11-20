/**
 * @file main.cpp (Node 2 - YoloUNO #2: Tram Thu Radar CSI, BLE, TinyML & Web Server)
 * @brief Ma cong nghe dam nhiem:
 *   - T06 (1.5d): Thu khung tin ESP-NOW 100 Hz tu Node 1 va trich xuat ma tran 64 song mang
 *                 Wi-Fi CSI o tang vat ly (PHY) qua `wifi_csi_rx_cb()` trong IRAM.
 *   - T03 (1.5d): Quet thu dong Bluetooth Low Energy (BLE) tren Core 0 dinh danh chu nha.
 *   - T04 (1.0d): Chay bo loc Hampel + Butterworth bac 4 va suy luan TinyML 1D-CNN INT8
 *                 truc tiep tren ESP32-S3 Core 1 (< 5 ms) phan loai EMPTY/STATIONARY/WALKING/FALL.
 *   - T07 (1.0d): Embedded HTTP Web Server cong 80 (/api/status, /api/config, /api/alarm)
 *                 kem dieu khien coi Buzzer PWM 2.7 kHz (GPIO10), den WS2812B (GPIO18) va nut SOS (GPIO14).
 *   - T14 (2.0d): Ho tro cap nhat Firmware OTA qua CoreIOT voi kiem tra ma bam SHA-256 & Rollback.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <esp_now.h>

#if __has_include("../include/config.h")
  #include "../include/config.h"
#endif
#ifndef WIFI_SSID
  #include "config.example.h"
#endif

#include "csi_protocol.h"
#include "ota_updater.h"
#include "csi_dsp.h"
#include "tinyml_infer.h"
#include "ble_scanner.h"
#include "web_server.h"

// Khung du lieu CSI day tu ngat IRAM sang Task_CSI_TinyML tren Core 1
typedef struct {
    int64_t timestamp_us;
    int8_t  rssi;
    int8_t  noise_floor;
    int8_t  iq_raw[CSI_RAW_IQ_BYTES];
} csi_frame_item_t;

static QueueHandle_t         xQueueCSI = nullptr;
static BlePresenceScanner    g_bleScanner;
static Node2EmbeddedWebServer g_webServer;
static node2_shared_status_t g_sharedStatus{};
static portMUX_TYPE          g_stateMux = portMUX_INITIALIZER_UNLOCKED;

static volatile uint32_t g_lastSeqId      = 0;
static volatile uint32_t g_rxPackets      = 0;
static volatile uint32_t g_missedPackets  = 0;
static volatile bool     g_sosManualAlarm = false;

// Bo dem cua so truot 200 khung x 52 song mang trong SRAM/PSRAM
static float g_csiRingWindow[200][CSI_ACTIVE_SUBCARRIERS];
static float g_edgeInputTensor[TINYML_WINDOW_LEN][TINYML_NUM_SUBCARRIERS];

// ============================================================================
// Dieu khien phan cung Coi Buzzer (GPIO10 PWM 2.7 kHz) & Den WS2812B (GPIO18)
// ============================================================================
static void setLocalAlarmHardware(bool alarmOn, uint8_t csiState) {
    if (alarmOn) {
        tone(NODE2_BUZZER_PIN, NODE2_BUZZER_FREQ_HZ);
        #ifdef RGB_BUILTIN
        neopixelWrite(NODE2_NEOPIXEL_PIN, 255, 0, 0); // Do ruc: Bao dong khan cap!
        #endif
    } else {
        noTone(NODE2_BUZZER_PIN);
        digitalWrite(NODE2_BUZZER_PIN, LOW);
        #ifdef RGB_BUILTIN
        if (csiState == CSI_STATE_WALKING) {
            neopixelWrite(NODE2_NEOPIXEL_PIN, 180, 140, 0); // Vang: Co chuyen dong
        } else {
            neopixelWrite(NODE2_NEOPIXEL_PIN, 0, 48, 12);   // Xanh la: An toan
        }
        #endif
    }
}

static void onWebConfigUpdated(float fallThreshold, int bleRssiMin, bool securityArmed) {
    portENTER_CRITICAL(&g_stateMux);
    g_sharedStatus.fall_threshold = fallThreshold;
    g_sharedStatus.ble_rssi_min   = bleRssiMin;
    g_sharedStatus.security_armed = securityArmed;
    portEXIT_CRITICAL(&g_stateMux);
}

static void onWebAlarmControl(bool active) {
    portENTER_CRITICAL(&g_stateMux);
    g_sharedStatus.alarm_active = active;
    if (!active) {
        g_sosManualAlarm = false;
    }
    uint8_t st = g_sharedStatus.csi_state;
    portEXIT_CRITICAL(&g_stateMux);
    setLocalAlarmHardware(active, st);
}

// ============================================================================
// Callback thu khung tin ESP-NOW (giai ma nhiet do/do am DHT20 tu Node 1)
// ============================================================================
static void onEspNowDataRecv(const uint8_t* mac, const uint8_t* incomingData, int len) {
    if (!mac || !incomingData || len < (int)sizeof(csi_ping_packet_t)) return;
    if (memcmp(mac, NODE1_TX_MAC_ADDR, 6) != 0) return;

    csi_ping_packet_t pkt{};
    memcpy(&pkt, incomingData, sizeof(pkt));
    if (pkt.magic_header != CSI_PING_MAGIC_HEADER) return;

    uint8_t calcCrc = csi_crc8_compute(reinterpret_cast<const uint8_t*>(&pkt), sizeof(pkt) - 1);
    if (calcCrc != pkt.crc8_checksum) return;

    portENTER_CRITICAL(&g_stateMux);
    if (g_lastSeqId > 0 && pkt.seq_id > g_lastSeqId + 1u) {
        g_missedPackets += (pkt.seq_id - g_lastSeqId - 1u);
    }
    g_lastSeqId = pkt.seq_id;
    g_rxPackets++;
    uint32_t totalExpected = g_rxPackets + g_missedPackets;
    g_sharedStatus.rx_packets   = g_rxPackets;
    g_sharedStatus.pkt_loss_pct = (totalExpected > 0)
                                ? (100.0f * (float)g_missedPackets / (float)totalExpected)
                                : 0.0f;
    g_sharedStatus.temp_c  = pkt.temp_c;
    g_sharedStatus.humi_rh = pkt.humi_rh;
    portEXIT_CRITICAL(&g_stateMux);
}

// ============================================================================
// Callback tang vat ly (IRAM) trich xuat 128 byte I/Q cua 64 song mang CSI (Doan ma 3.1)
// ============================================================================
static void IRAM_ATTR wifi_csi_rx_cb(void* /*ctx*/, wifi_csi_info_t* info) {
    if (!info || !info->buf || info->len < (int)CSI_RAW_IQ_BYTES) return;
    if (memcmp(info->mac, NODE1_TX_MAC_ADDR, 6) != 0) return;

    csi_frame_item_t item;
    item.timestamp_us = esp_timer_get_time();
    item.rssi         = info->rx_ctrl.rssi;
    item.noise_floor  = info->rx_ctrl.noise_floor;
    memcpy(item.iq_raw, info->buf, CSI_RAW_IQ_BYTES);

    BaseType_t higherWoken = pdFALSE;
    xQueueSendFromISR(xQueueCSI, &item, &higherWoken);
    if (higherWoken == pdTRUE) {
        portYIELD_FROM_ISR();
    }
}

// ============================================================================
// Task_CSI_TinyML tren Core 1: Loc tin hieu + Chay INT8 1D-CNN + Xuat USB-CDC (T04, T08)
// ============================================================================
static void Task_CSI_TinyML(void* /*pvParameters*/) {
    size_t framesCollected = 0;
    size_t stepCounter     = 0;

    for (;;) {
        csi_frame_item_t frame;
        if (xQueueReceive(xQueueCSI, &frame, pdMS_TO_TICKS(50)) == pdTRUE) {
            float amps52[CSI_ACTIVE_SUBCARRIERS];
            CsiDspProcessor::iqToActiveAmplitudes(frame.iq_raw, amps52);

            // Dich cua so truot 200 mau
            if (framesCollected < 200u) {
                memcpy(g_csiRingWindow[framesCollected], amps52, sizeof(amps52));
                framesCollected++;
            } else {
                memmove(&g_csiRingWindow[0], &g_csiRingWindow[1], 199u * sizeof(amps52));
                memcpy(g_csiRingWindow[199], amps52, sizeof(amps52));
            }
            stepCounter++;

            // Moi 20 khung (200 ms @ 100 Hz) khi da du cua so 200 khung: Chay TinyML INT8!
            if (framesCollected >= 200u && (stepCounter % 20u) == 0u) {
                float tempC, humiRh, fallTh;
                bool secArmed, bleAuth;
                char bleMac[18];
                int bleRssi;

                portENTER_CRITICAL(&g_stateMux);
                tempC    = g_sharedStatus.temp_c;
                humiRh   = g_sharedStatus.humi_rh;
                fallTh   = g_sharedStatus.fall_threshold;
                secArmed = g_sharedStatus.security_armed;
                bleAuth  = g_sharedStatus.ble_authorized;
                bleRssi  = g_sharedStatus.ble_rssi;
                memcpy(bleMac, g_sharedStatus.ble_mac, sizeof(bleMac));
                portEXIT_CRITICAL(&g_stateMux);

                uint32_t tInferStartUs = (uint32_t)esp_timer_get_time();
                CsiDspProcessor::processWindowToEdgeTensor(g_csiRingWindow, tempC, humiRh, g_edgeInputTensor);
                tinyml_result_t aiRes = EdgeTinyMLClassifier::predict(g_edgeInputTensor);
                float inferMs = (float)((uint32_t)esp_timer_get_time() - tInferStartUs) * 0.001f;
                float motionIdx = CsiDspProcessor::computeMotionEnergy(g_csiRingWindow);

                bool fallDetected = (aiRes.predicted_class == CSI_STATE_FALL && aiRes.confidence >= fallTh);
                bool intrusionDetected = (secArmed && aiRes.predicted_class == CSI_STATE_WALKING && !bleAuth);
                bool triggerAlarm = fallDetected || intrusionDetected || g_sosManualAlarm;

                portENTER_CRITICAL(&g_stateMux);
                g_sharedStatus.csi_state     = aiRes.predicted_class;
                g_sharedStatus.edge_conf     = aiRes.confidence;
                g_sharedStatus.motion_energy = motionIdx;
                if (triggerAlarm) {
                    g_sharedStatus.alarm_active = true;
                }
                bool currentAlarm = g_sharedStatus.alarm_active;
                portEXIT_CRITICAL(&g_stateMux);

                setLocalAlarmHardware(currentAlarm, aiRes.predicted_class);

                // Xuat khung JSONL toc do cao qua USB-CDC 921600 bps ve PC Local Gateway (T08, T10)
                Serial.printf(
                    "{\"ts_ms\":%lu,\"edge_state\":\"%s\",\"edge_conf\":%.4f,"
                    "\"edge_probs\":[%.3f,%.3f,%.3f,%.3f],\"infer_ms\":%.2f,"
                    "\"motion_energy\":%.2f,\"temp_c\":%.2f,\"humi_rh\":%.2f,"
                    "\"ble_authorized\":%s,\"ble_mac\":\"%s\",\"ble_rssi\":%d,"
                    "\"alarm_active\":%s,\"sos\":%s,\"rssi\":%d,\"amps_52\":[",
                    (unsigned long)millis(),
                    csi_state_to_str(aiRes.predicted_class),
                    (double)aiRes.confidence,
                    (double)aiRes.probs[0], (double)aiRes.probs[1],
                    (double)aiRes.probs[2], (double)aiRes.probs[3],
                    (double)inferMs,
                    (double)motionIdx,
                    (double)tempC,
                    (double)humiRh,
                    bleAuth ? "true" : "false",
                    bleMac,
                    bleRssi,
                    currentAlarm ? "true" : "false",
                    g_sosManualAlarm ? "true" : "false",
                    (int)frame.rssi
                );
                for (size_t k = 0; k < CSI_ACTIVE_SUBCARRIERS; ++k) {
                    Serial.printf(k + 1 < CSI_ACTIVE_SUBCARRIERS ? "%.2f," : "%.2f]}\n", (double)amps52[k]);
                }
            }
        }
    }
}

// ============================================================================
// Task_BLE_Scanner & Nut nhan SOS / RPC tren Core 0 (T03, T07, T08, T14)
// ============================================================================
static void Task_BLE_And_IO(void* /*pvParameters*/) {
    g_bleScanner.begin();
    uint32_t lastBleMs = 0;
    uint32_t btnPressStartMs = 0;
    bool btnWasLow = false;

    for (;;) {
        uint32_t now = millis();

        // 1. Xu ly nut nhan SOS / Reset bao dong tren GPIO14 (INPUT_PULLUP)
        bool isLow = (digitalRead(NODE2_SOS_BTN_PIN) == LOW);
        if (isLow && !btnWasLow) {
            btnPressStartMs = now;
            btnWasLow = true;
        } else if (isLow && btnWasLow) {
            if ((now - btnPressStartMs) >= NODE2_SOS_HOLD_MS && !g_sosManualAlarm) {
                g_sosManualAlarm = true;
                onWebAlarmControl(true);
            }
        } else if (!isLow && btnWasLow) {
            if ((now - btnPressStartMs) >= 40u && (now - btnPressStartMs) < NODE2_SOS_HOLD_MS) {
                // Nhan don (< 3 giay) -> Huy bao dong gia tai cho
                onWebAlarmControl(false);
            }
            btnWasLow = false;
        }

        // 2. Xu ly HTTP Embedded Web Server (T07)
        g_webServer.handleClient();

        // 3. Nhan lenh RPC chieu ve tu PC Local Gateway qua USB-CDC (T08, T14)
        while (Serial.available()) {
            String line = Serial.readStringUntil('\n');
            line.trim();
            if (line.startsWith("RPC:ALARM:")) {
                bool on = line.endsWith("1") || line.endsWith("ON") || line.endsWith("true");
                onWebAlarmControl(on);
            } else if (line.startsWith("RPC:ARM:")) {
                bool arm = line.endsWith("1") || line.endsWith("true");
                portENTER_CRITICAL(&g_stateMux);
                g_sharedStatus.security_armed = arm;
                portEXIT_CRITICAL(&g_stateMux);
            } else if (line.startsWith("RPC:THRESHOLD:")) {
                float th = line.substring(14).toFloat();
                if (th >= 0.50f && th <= 0.95f) {
                    portENTER_CRITICAL(&g_stateMux);
                    g_sharedStatus.fall_threshold = th;
                    portEXIT_CRITICAL(&g_stateMux);
                }
            } else if (line.startsWith("RPC:OTA:")) {
                int sep = line.indexOf(':', 8);
                String ver = (sep > 8) ? line.substring(8, sep) : line.substring(8);
                String sha = (sep > 8) ? line.substring(sep + 1) : "";
                CoreIOTOtaUpdater::performHttpOtaUpdate(
                    COREIOT_MQTT_HOST, COREIOT_HTTP_PORT, COREIOT_NODE2_TOKEN,
                    FIRMWARE_TITLE_NODE2, ver.c_str(), sha.c_str()
                );
            }
        }

        // 4. Quet thu dong BLE moi 1000 ms (T03)
        if ((now - lastBleMs) >= BLE_SCAN_INTERVAL_MS) {
            lastBleMs = now;
            int rssiMin;
            portENTER_CRITICAL(&g_stateMux);
            rssiMin = g_sharedStatus.ble_rssi_min;
            portEXIT_CRITICAL(&g_stateMux);

            g_bleScanner.scanOnce(rssiMin);
            ble_presence_state_t bs = g_bleScanner.getState();

            portENTER_CRITICAL(&g_stateMux);
            g_sharedStatus.ble_authorized = bs.authorized_present;
            g_sharedStatus.ble_rssi       = bs.best_rssi;
            memcpy(g_sharedStatus.ble_mac, bs.best_mac, sizeof(g_sharedStatus.ble_mac));
            portEXIT_CRITICAL(&g_stateMux);
        }

        vTaskDelay(pdMS_TO_TICKS(15));
    }
}

// ============================================================================
// Khoi dong Node 2
// ============================================================================
void setup() {
    Serial.begin(SERIAL_CSI_BAUDRATE);
    delay(200);
    CoreIOTOtaUpdater::confirmBootPartitionValid();

    pinMode(NODE2_BUZZER_PIN, OUTPUT);
    digitalWrite(NODE2_BUZZER_PIN, LOW);
    pinMode(NODE2_SOS_BTN_PIN, INPUT_PULLUP);

    // Khoi tao trang thai chia se mac dinh
    g_sharedStatus.csi_state      = CSI_STATE_EMPTY;
    g_sharedStatus.temp_c         = 27.5f;
    g_sharedStatus.humi_rh        = 65.0f;
    g_sharedStatus.fall_threshold = DEFAULT_FALL_THRESHOLD;
    g_sharedStatus.ble_rssi_min   = DEFAULT_BLE_RSSI_MIN;
    g_sharedStatus.security_armed = DEFAULT_SECURITY_ARMED;
    strncpy(g_sharedStatus.ble_mac, "NONE", sizeof(g_sharedStatus.ble_mac) - 1);
    g_sharedStatus.ble_rssi       = -100;

    xQueueCSI = xQueueCreate(64, sizeof(csi_frame_item_t));

    // Khoi tao Wi-Fi Station + SoftAP du phong de Embedded Web Server (T07) luon truy cap duoc!
    WiFi.mode(WIFI_AP_STA);
    WiFi.softAP("YoloUNO_CSI_Node2", nullptr, CSI_WIFI_CHANNEL);
    if (strlen(WIFI_SSID) > 0 && strcmp(WIFI_SSID, "YOUR_WIFI_SSID") != 0) {
        WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    }

    esp_wifi_set_ps(WIFI_PS_NONE);
    esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW_HT20);
    esp_wifi_set_channel(CSI_WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);

    // Kich hoat bo thu Wi-Fi CSI tang vat ly (LLTF + HT-LTF) tren ESP32-S3
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

    g_webServer.begin(&g_sharedStatus, onWebConfigUpdated, onWebAlarmControl);

    Serial.printf("[NODE2] Ready! STA MAC=%s | AP IP=%s | Ch=%d\n",
                  WiFi.macAddress().c_str(), WiFi.softAPIP().toString().c_str(), CSI_WIFI_CHANNEL);

    xTaskCreatePinnedToCore(Task_CSI_TinyML, "Task_CSI_TinyML", 16384, nullptr, 12, nullptr, 1);
    xTaskCreatePinnedToCore(Task_BLE_And_IO, "Task_BLE_And_IO",  8192, nullptr,  5, nullptr, 0);
}

void loop() {
    vTaskDelay(pdMS_TO_TICKS(1000));
}
