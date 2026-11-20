/**
 * @file main.cpp (Node 1 - YoloUNO #1: Tram Phat Song CSI & Cam bien I2C DHT20)
 * @brief Ma cong nghe dam nhiem:
 *   - T01 (0.5d): Giao tiep cam bien nhiet am I2C DHT20/AHT20 (dia chi 0x38, GPIO11 SDA, GPIO12 SCL)
 *                 co kiem tra CRC-8 da thuc 0x31 va xuat chuoi JSON chuan Tasmota/CoreIOT.
 *   - T06 (1.5d): Phat lien tuc khung tin nhi phan `csi_ping_packet_t` (24 byte) o tan so co dinh
 *                 100 Hz (chu ky 10 ms) qua giao thuc ESP-NOW tren Kenh 6 (2437 MHz, WIFI_PS_NONE, +19.5 dBm).
 *   - T14 (2.0d): Ho tro cap nhat Firmware tu xa qua mang (OTA) tu CoreIOT voi xac thuc SHA-256 & Rollback.
 */

#include <Arduino.h>
#include <Wire.h>
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

// Bien chia se an toan giua Task_I2C_EnvRead (Core 0) va Task_ESPNOW_PingTx (Core 1)
static portMUX_TYPE g_envMux = portMUX_INITIALIZER_UNLOCKED;
static volatile float   g_latestTempC  = 27.5f;
static volatile float   g_latestHumiRh = 65.0f;
static volatile uint8_t g_node1Status  = 0;
static volatile uint32_t g_txSeqId     = 0;
static volatile uint32_t g_txFailCount = 0;

// ============================================================================
// Trinh dieu khien truc tiep cam bien I2C DHT20 / AHT20 (0x38) - Khong phu thuoc thu vien ngoai
// ============================================================================
static bool dht20Init() {
    Wire.begin(NODE1_I2C_SDA_PIN, NODE1_I2C_SCL_PIN, 100000u); // 100 kHz Standard I2C
    delay(40); // Cho >= 40 ms sau khi cap nguon theo datasheet DHT20

    Wire.beginTransmission(NODE1_DHT20_I2C_ADDR);
    Wire.write(0x71); // Doc thanh ghi trang thai
    if (Wire.endTransmission() != 0) {
        return false;
    }
    if (Wire.requestFrom((uint8_t)NODE1_DHT20_I2C_ADDR, (uint8_t)1) != 1) {
        return false;
    }
    uint8_t status = Wire.read();
    if ((status & 0x18) != 0x18) {
        // Khoi tao hieu chuan thanh ghi 0x1B, 0x1C, 0x1E theo Aosong Application Note
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
    // 1. Gui lenh kich hoat do: 0xAC 0x33 0x00
    Wire.beginTransmission(NODE1_DHT20_I2C_ADDR);
    Wire.write(0xAC);
    Wire.write(0x33);
    Wire.write(0x00);
    if (Wire.endTransmission() != 0) {
        return false;
    }

    // 2. Doi chuyen doi ADC 80 ms
    vTaskDelay(pdMS_TO_TICKS(85));

    // 3. Doc 7 byte ket qua: [State, RH_H, RH_M, RH_L|T_H, T_M, T_L, CRC8]
    if (Wire.requestFrom((uint8_t)NODE1_DHT20_I2C_ADDR, (uint8_t)7) != 7) {
        return false;
    }
    uint8_t raw[7];
    for (int i = 0; i < 7; ++i) {
        raw[i] = Wire.read();
    }

    // Kiem tra co Busy (bit 7 == 0) va CRC-8 da thuc 0x31
    if ((raw[0] & 0x80u) != 0) {
        return false;
    }
    uint8_t calcCrc = dht20_crc8_compute(raw, 6);
    if (calcCrc != raw[6]) {
        return false;
    }

    uint32_t rawHumi = ((uint32_t)raw[1] << 12) | ((uint32_t)raw[2] << 4) | ((uint32_t)(raw[3] >> 4) & 0x0Fu);
    uint32_t rawTemp = (((uint32_t)raw[3] & 0x0Fu) << 16) | ((uint32_t)raw[4] << 8) | (uint32_t)raw[5];

    outHumiRh = ((float)rawHumi / 1048576.0f) * 100.0f;
    outTempC  = ((float)rawTemp / 1048576.0f) * 200.0f - 50.0f;
    return true;
}

// ============================================================================
// Callback phat ESP-NOW
// ============================================================================
static void onEspNowDataSent(const uint8_t* /*mac_addr*/, esp_now_send_status_t status) {
    if (status != ESP_NOW_SEND_SUCCESS) {
        g_txFailCount++;
    }
}

// ============================================================================
// Task 1: Doc cam bien I2C DHT20 moi 5000 ms tren Core 0 (T01)
// ============================================================================
static void Task_I2C_EnvRead(void* /*pvParameters*/) {
    bool sensorOk = dht20Init();
    Serial.printf("[NODE1-T01] DHT20 I2C(0x38, SDA=%d, SCL=%d) init: %s\n",
                  NODE1_I2C_SDA_PIN, NODE1_I2C_SCL_PIN, sensorOk ? "OK" : "FALLBACK_SIM");

    TickType_t lastWake = xTaskGetTickCount();
    for (;;) {
        float t = 27.5f, h = 65.0f;
        bool ok = dht20ReadSample(t, h);

        portENTER_CRITICAL(&g_envMux);
        if (ok) {
            g_latestTempC  = t;
            g_latestHumiRh = h;
            g_node1Status |= NODE1_STATUS_DHT20_OK;
        } else {
            g_node1Status &= (uint8_t)(~NODE1_STATUS_DHT20_OK);
        }
        portEXIT_CRITICAL(&g_envMux);

        // Xuat chuoi JSON tuong thich dinh dang Tasmota SENSOR de de dang kiem chung T01 qua Serial
        Serial.printf("{\"Time\":%lu,\"DHT20\":{\"Temperature\":%.2f,\"Humidity\":%.2f,\"I2C_OK\":%s},"
                      "\"ESPNOW\":{\"tx_seq\":%lu,\"tx_fail\":%lu,\"ch\":%d},\"fw\":\"%s\"}\n",
                      (unsigned long)millis(),
                      (double)g_latestTempC,
                      (double)g_latestHumiRh,
                      ok ? "true" : "false",
                      (unsigned long)g_txSeqId,
                      (unsigned long)g_txFailCount,
                      CSI_WIFI_CHANNEL,
                      FIRMWARE_VERSION);

        // Nhan lenh OTA tu Serial neu can kich hoat kiem thu T14
        if (Serial.available()) {
            String cmd = Serial.readStringUntil('\n');
            cmd.trim();
            if (cmd.startsWith("OTA:")) {
                // Cu phap: OTA:<version>:<sha256_optional>
                int sep = cmd.indexOf(':', 4);
                String ver = (sep > 4) ? cmd.substring(4, sep) : cmd.substring(4);
                String sha = (sep > 4) ? cmd.substring(sep + 1) : "";
                WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
                uint32_t t0 = millis();
                while (WiFi.status() != WL_CONNECTED && (millis() - t0) < 10000) {
                    vTaskDelay(pdMS_TO_TICKS(200));
                }
                CoreIOTOtaUpdater::performHttpOtaUpdate(
                    COREIOT_MQTT_HOST, COREIOT_HTTP_PORT, COREIOT_NODE1_TOKEN,
                    FIRMWARE_TITLE_NODE1, ver.c_str(), sha.c_str()
                );
            }
        }

        vTaskDelayUntil(&lastWake, pdMS_TO_TICKS(NODE1_ENV_READ_MS));
    }
}

// ============================================================================
// Task 2: Phat xung ESP-NOW 100 Hz dung chu ky 10 ms tren Core 1 (T06)
// ============================================================================
static void Task_ESPNOW_PingTx(void* /*pvParameters*/) {
    const TickType_t periodTicks = pdMS_TO_TICKS(1000 / NODE1_ESPNOW_HZ); // 10 ms = 100 Hz
    TickType_t lastWake = xTaskGetTickCount();
    bool ledToggle = false;

    for (;;) {
        csi_ping_packet_t pkt{};
        pkt.magic_header = CSI_PING_MAGIC_HEADER;
        pkt.seq_id       = ++g_txSeqId;
        pkt.tx_time_ms   = (uint32_t)millis();

        portENTER_CRITICAL(&g_envMux);
        pkt.temp_c         = g_latestTempC;
        pkt.humi_rh        = g_latestHumiRh;
        pkt.tx_status_flag = g_node1Status;
        portEXIT_CRITICAL(&g_envMux);

        pkt.reserved_pad[0] = CSI_WIFI_CHANNEL;
        pkt.reserved_pad[1] = 19; // +19.5 dBm Tx power indicator
        pkt.reserved_pad[2] = 1;  // FW major
        pkt.reserved_pad[3] = 0;  // FW minor
        pkt.crc8_checksum   = csi_crc8_compute(reinterpret_cast<const uint8_t*>(&pkt), sizeof(pkt) - 1);

        esp_now_send(NODE2_RX_MAC_ADDR, reinterpret_cast<const uint8_t*>(&pkt), sizeof(pkt));

        // Nhay nhe LED onboard GPIO48 moi 50 goi (0.5 giay) bao dang phat song CSI
        if ((pkt.seq_id % 50u) == 0u) {
            ledToggle = !ledToggle;
            #ifdef RGB_BUILTIN
            neopixelWrite(NODE1_RGB_LED_PIN, 0, ledToggle ? 24 : 0, 0);
            #endif
        }

        vTaskDelayUntil(&lastWake, periodTicks);
    }
}

// ============================================================================
// Khoi dong Node 1
// ============================================================================
void setup() {
    Serial.begin(SERIAL_CSI_BAUDRATE);
    delay(200);
    CoreIOTOtaUpdater::confirmBootPartitionValid();

    Serial.println("============================================================");
    Serial.println(" YoloUNO #1 (ESP32-S3) - CSI Transmitter & DHT20 Node (v1.0)");
    Serial.println(" Codes: T01 (I2C DHT20), T06 (ESP-NOW 100Hz), T14 (OTA)     ");
    Serial.println("============================================================");

    // Cau hinh Wi-Fi Station co dinh Kenh 6 (2437 MHz, 802.11n HT20) cho ESP-NOW CSI
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    esp_wifi_set_ps(WIFI_PS_NONE); // Tat tiet kiem nang luong modem de giu on dinh pha/bien do CSI
    esp_wifi_set_protocol(WIFI_IF_STA, WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N);
    esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW_HT20);
    esp_wifi_set_channel(CSI_WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
    esp_wifi_set_max_tx_power(78); // 78 * 0.25 dBm = +19.5 dBm

    Serial.printf("[NODE1] Station MAC: %s | Channel: %d (HT20)\n",
                  WiFi.macAddress().c_str(), CSI_WIFI_CHANNEL);

    if (esp_now_init() != ESP_OK) {
        Serial.println("[NODE1] FATAL: esp_now_init() failed!");
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

    // Khoi tao 2 tac vu FreeRTOS tren 2 nhan rieng biet theo Muc 3.2.1 cua Spec
    xTaskCreatePinnedToCore(Task_I2C_EnvRead,   "Task_I2C_EnvRead",   4096, nullptr, 3,  nullptr, 0);
    xTaskCreatePinnedToCore(Task_ESPNOW_PingTx, "Task_ESPNOW_PingTx", 4096, nullptr, 10, nullptr, 1);
}

void loop() {
    vTaskDelay(pdMS_TO_TICKS(1000));
}
