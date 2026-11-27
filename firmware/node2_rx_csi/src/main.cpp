/**
 * @file main.cpp (Node 2 - YoloUNO #2: Wi-Fi CSI Receiver, USB Serial Bridge &
 * Hardware Visual Alerts v1.2)
 * @brief Physical OhStem YoloUNO ESP32-S3 wiring:
 *   - I2C1  (GPIO11 SDA / GPIO12 SCL): OhStem LCD 16x2 I2C (addr 0x21, Lab 3 LiquidCrystal_I2C)
 *   - D6-D5 (GPIO9 D6  / GPIO8 D5)   : Traffic Light LED Module (Red/Yellow/Green)
 *   - D4-D3 (GPIO7 D4  / GPIO6 D3)   : SOS / Mute Push Button (Hold 3s = SOS, Tap = Mute/Refresh LCD)
 *   - GPIO45 / GPIO48                : Onboard RGB LED + LED_BUILTIN mirror
 */

#include <Arduino.h>
#include <WiFi.h>
#include <Wire.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <stdio.h>
#include <string.h>

#if __has_include("../include/config.h")
#include "../include/config.h"
#endif
#ifndef CSI_WIFI_CHANNEL
#include "config.example.h"
#endif

#ifndef NODE2_I2C_SDA_PIN
#define NODE2_I2C_SDA_PIN 11
#endif
#ifndef NODE2_I2C_SCL_PIN
#define NODE2_I2C_SCL_PIN 12
#endif
#ifndef NODE2_LCD_I2C_ADDR
#define NODE2_LCD_I2C_ADDR 0x21
#endif
#ifndef NODE2_TL_PIN_D5
#define NODE2_TL_PIN_D5 8
#endif
#ifndef NODE2_TL_PIN_D6
#define NODE2_TL_PIN_D6 9
#endif
#ifndef NODE2_BTN_PIN_D3
#define NODE2_BTN_PIN_D3 6
#endif
#ifndef NODE2_BTN_PIN_D4
#define NODE2_BTN_PIN_D4 7
#endif
#ifndef NODE2_ONBOARD_RGB_PIN
#define NODE2_ONBOARD_RGB_PIN 45
#endif
#ifndef NODE2_ONBOARD_LED_PIN
#define NODE2_ONBOARD_LED_PIN 48
#endif

#include "LiquidCrystal_I2C.h"
#include "csi_protocol.h"

typedef struct {
  int8_t rssi;
  int8_t iq_raw[CSI_RAW_IQ_BYTES];
} csi_frame_t;

static QueueHandle_t g_csiQueue = nullptr;

// Auto-pair or pinned Node 1 MAC address
static uint8_t g_targetNode1Mac[6] = {0};
static volatile bool g_targetMacLocked = false;
static bool g_isBroadcastMode = false;

// Latest DHT20 readings & packet stats received from Node 1
static volatile float g_tempC = 27.5f;
static volatile float g_humiRh = 65.0f;
static volatile uint32_t g_lastSeqId = 0;
static volatile uint32_t g_rxPackets = 0;
static volatile uint32_t g_missedPackets = 0;
static volatile uint32_t g_lastRxMs = 0;
static volatile uint32_t g_csiFrames = 0;
static volatile uint32_t g_csiCbAny = 0;
static volatile int g_csiLastLen = 0;

// Visual alert, AI state & SOS button state
static bool g_alarmActive = false;
static bool g_sosTriggered = false;
static int g_btnD3Idle = HIGH;
static int g_btnD4Idle = HIGH;
static bool g_btnWasPressed = false;
static uint32_t g_btnPressStart = 0;
static uint32_t g_lastDiagMs = 0;

static char g_aiState[16] = "WAIT_N1";
static LiquidCrystal_I2C g_lcd(NODE2_LCD_I2C_ADDR, 16, 2);
static uint8_t g_lcdAddr = 0;
static bool g_lcdBacklightOn = true;
static char g_lcdLine0Cache[17] = "";
static char g_lcdLine1Cache[17] = "";
static uint32_t g_lastLcdRefreshMs = 0;
static uint32_t g_lastBlinkMs = 0;

static bool isAllFF(const uint8_t mac[6]) {
  for (int i = 0; i < 6; ++i) {
    if (mac[i] != 0xFFu)
      return false;
  }
  return true;
}

static bool i2cProbe(uint8_t addr) {
  Wire.beginTransmission(addr);
  return (Wire.endTransmission() == 0);
}

// ============================================================================
// 1. Official HCMUT Lab 3 LiquidCrystal_I2C Wrapper (OhStem 0x21 @ 50 kHz)
// ============================================================================
static bool lcdInitAuto() {
  const uint8_t candidates[3] = {NODE2_LCD_I2C_ADDR, 0x27u, 0x3Fu};
  g_lcdAddr = 0;
  for (uint8_t a : candidates) {
    if (i2cProbe(a)) {
      g_lcdAddr = a;
      break;
    }
  }
  if (g_lcdAddr == 0)
    return false;

  g_lcd.setAddress(g_lcdAddr);
  g_lcd.resetErrorCount();
  g_lcd.begin();
  g_lcd.backlight();
  g_lcdBacklightOn = true;
  g_lcdLine0Cache[0] = '\0';
  g_lcdLine1Cache[0] = '\0';
  return true;
}

static void lcdWriteLineIfChanged(uint8_t row, const char *text) {
  if (g_lcdAddr == 0)
    return;

  // Auto-recover from cable wiggle / I2C glitch that could desync 4-bit nibbles
  if (g_lcd.getErrorCount() > 0u) {
    if (i2cProbe(g_lcdAddr)) {
      g_lcd.resetErrorCount();
      g_lcd.begin();
      if (g_lcdBacklightOn)
        g_lcd.backlight();
      else
        g_lcd.noBacklight();
      g_lcdLine0Cache[0] = '\0';
      g_lcdLine1Cache[0] = '\0';
    } else {
      return;
    }
  }

  char buf[17];
  size_t n = strlen(text);
  for (size_t i = 0; i < 16; ++i) {
    buf[i] = (i < n) ? text[i] : ' ';
  }
  buf[16] = '\0';

  char *cache = (row == 0) ? g_lcdLine0Cache : g_lcdLine1Cache;
  if (strcmp(cache, buf) == 0)
    return;
  memcpy(cache, buf, 17);

  g_lcd.setCursor(0, row);
  g_lcd.print(buf);
}

// ============================================================================
// 2. Traffic Light LED Driver on Grove D6-D5 (D5=GPIO8, D6=GPIO9) + Onboard LED
//    OFF=(0,0) | GREEN=(1,0) | YELLOW=(0,1) | RED=(1,1)
// ============================================================================
enum TrafficColor : uint8_t {
  TL_OFF = 0,
  TL_GREEN = 1,
  TL_YELLOW = 2,
  TL_RED = 3,
};

static void setTrafficPinsRaw(bool d5High, bool d6High) {
  digitalWrite(NODE2_TL_PIN_D5, d5High ? HIGH : LOW);
  digitalWrite(NODE2_TL_PIN_D6, d6High ? HIGH : LOW);
}

static void setTrafficLight(TrafficColor color) {
  switch (color) {
  case TL_GREEN:
    setTrafficPinsRaw(true, false);
    break;
  case TL_YELLOW:
    setTrafficPinsRaw(false, true);
    break;
  case TL_RED:
    setTrafficPinsRaw(true, true);
    break;
  case TL_OFF:
  default:
    setTrafficPinsRaw(false, false);
    break;
  }
}

static void setNode2Rgb(uint8_t r, uint8_t g, uint8_t b) {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
  rgbLedWrite(NODE2_ONBOARD_RGB_PIN, r, g, b);
#elif defined(RGB_BUILTIN)
  neopixelWrite(NODE2_ONBOARD_RGB_PIN, r, g, b);
#else
  (void)r;
  (void)g;
  (void)b;
#endif
}

// ============================================================================
// 3. Visual Alert Output Update (LCD 16x2 + D6-D5 Traffic Light + Onboard LED)
// ============================================================================
static void updateVisualOutputs(bool forceNow) {
  const uint32_t now = millis();

  if (g_alarmActive || g_sosTriggered) {
    if (forceNow || (now - g_lastBlinkMs) >= 350u) {
      g_lastBlinkMs = now;
      g_lcdBacklightOn = !g_lcdBacklightOn;
      if (g_lcdAddr != 0) {
        if (g_lcdBacklightOn)
          g_lcd.backlight();
        else
          g_lcd.noBacklight();
      }
      setTrafficLight(g_lcdBacklightOn ? TL_RED : TL_OFF);
      setNode2Rgb(g_lcdBacklightOn ? 180 : 0, 0, 0);
      digitalWrite(NODE2_ONBOARD_LED_PIN, g_lcdBacklightOn ? HIGH : LOW);
    }
  } else {
    if (!g_lcdBacklightOn) {
      g_lcdBacklightOn = true;
      if (g_lcdAddr != 0)
        g_lcd.backlight();
    }
    digitalWrite(NODE2_ONBOARD_LED_PIN, LOW);
    if (strcmp(g_aiState, "WALKING") == 0) {
      setTrafficLight(TL_YELLOW);
      setNode2Rgb(80, 55, 0);
    } else if (g_rxPackets == 0u) {
      setTrafficLight(TL_YELLOW);
      setNode2Rgb(0, 20, 60);
    } else {
      setTrafficLight(TL_GREEN);
      setNode2Rgb(0, 60, 10);
    }
  }

  if (!forceNow && (now - g_lastLcdRefreshMs) < 1000u)
    return;
  g_lastLcdRefreshMs = now;

  char line0[17];
  char line1[17];

  if (g_sosTriggered) {
    snprintf(line0, sizeof(line0), "!! SOS MANUAL !!");
    snprintf(line1, sizeof(line1), "TAP BTN TO MUTE ");
  } else if (g_alarmActive || strcmp(g_aiState, "FALL") == 0) {
    snprintf(line0, sizeof(line0), "!! FALL ALERT !!");
    snprintf(line1, sizeof(line1), "CHECK ROOM NOW! ");
  } else if (g_rxPackets == 0u) {
    snprintf(line0, sizeof(line0), "NODE2 READY Ch%d ", CSI_WIFI_CHANNEL);
    snprintf(line1, sizeof(line1), "WAIT NODE1 CSI..");
  } else if (strcmp(g_aiState, "WALKING") == 0) {
    snprintf(line0, sizeof(line0), "STATE: WALKING  ");
    snprintf(line1, sizeof(line1), "T:%.1fC H:%.0f%%", (double)g_tempC,
             (double)g_humiRh);
  } else if (strcmp(g_aiState, "EMPTY") == 0) {
    snprintf(line0, sizeof(line0), "STATE: SAFE     ");
    snprintf(line1, sizeof(line1), "T:%.1fC H:%.0f%%", (double)g_tempC,
             (double)g_humiRh);
  } else {
    snprintf(line0, sizeof(line0), "STATE: SAFE     ");
    snprintf(line1, sizeof(line1), "T:%.1fC H:%.0f%%", (double)g_tempC,
             (double)g_humiRh);
  }

  lcdWriteLineIfChanged(0, line0);
  lcdWriteLineIfChanged(1, line1);
}

static void setAlarmHardware(bool active) {
  g_alarmActive = active;
  if (!active) {
    g_sosTriggered = false;
    g_lcdBacklightOn = true;
    if (g_lcdAddr != 0)
      g_lcd.backlight();
  }
  updateVisualOutputs(true);
}

// ============================================================================
// 4. ESP-NOW Receive Handler (Compatible with Arduino-ESP32 v2.x & v3.x) - T06
// ============================================================================
static void handleEspNowPacket(const uint8_t *mac, const uint8_t *incomingData,
                               int len) {
  if (!mac || !incomingData || len < (int)sizeof(csi_ping_packet_t))
    return;

  csi_ping_packet_t pkt{};
  memcpy(&pkt, incomingData, sizeof(pkt));
  if (pkt.magic_header != CSI_PING_MAGIC_HEADER)
    return;

  if (csi_crc8_compute(reinterpret_cast<const uint8_t *>(&pkt),
                       sizeof(pkt) - 1) != pkt.crc8_checksum) {
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
  g_tempC = pkt.temp_c;
  g_humiRh = pkt.humi_rh;
  g_lastRxMs = millis();
}

#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
static void onEspNowDataRecv(const esp_now_recv_info_t *info,
                             const uint8_t *incomingData, int len) {
  if (!info)
    return;
  handleEspNowPacket(info->src_addr, incomingData, len);
}
#else
static void onEspNowDataRecv(const uint8_t *mac, const uint8_t *incomingData,
                             int len) {
  handleEspNowPacket(mac, incomingData, len);
}
#endif

// ============================================================================
// 5. Wi-Fi PHY CSI Callback (Extracts 128 raw LLTF I/Q bytes from Node 1)
// ============================================================================
static void IRAM_ATTR wifi_csi_rx_cb(void * /*ctx*/, wifi_csi_info_t *info) {
  g_csiCbAny = g_csiCbAny + 1u;
  if (!info || !info->buf)
    return;
  if (!g_targetMacLocked)
    return;
  if (memcmp(info->mac, g_targetNode1Mac, 6) != 0)
    return;
  g_csiLastLen = info->len;
  if (info->len < (int)CSI_RAW_IQ_BYTES)
    return;

  csi_frame_t frame;
  frame.rssi = info->rx_ctrl.rssi;
  memcpy(frame.iq_raw, info->buf, CSI_RAW_IQ_BYTES);
  g_csiFrames = g_csiFrames + 1u;

  BaseType_t higherWoken = pdFALSE;
  xQueueOverwriteFromISR(g_csiQueue, &frame, &higherWoken);
  if (higherWoken == pdTRUE) {
    portYIELD_FROM_ISR();
  }
}

// ============================================================================
// 6. SOS / Mute Button Handler on Grove D4-D3 (GPIO6 / GPIO7)
//    Hold >= 3s -> SOS Emergency Alert; Short Tap -> Mute / Re-sync LCD
// ============================================================================
static void pollSosButton() {
  const uint32_t now = millis();
  const bool pressed = (digitalRead(NODE2_BTN_PIN_D3) != g_btnD3Idle) ||
                       (digitalRead(NODE2_BTN_PIN_D4) != g_btnD4Idle);

  if (pressed && !g_btnWasPressed) {
    g_btnPressStart = now;
    g_btnWasPressed = true;
  } else if (pressed && g_btnWasPressed) {
    if ((now - g_btnPressStart) >= NODE2_SOS_HOLD_MS && !g_sosTriggered) {
      g_sosTriggered = true;
      setAlarmHardware(true);
    }
  } else if (!pressed && g_btnWasPressed) {
    uint32_t heldMs = now - g_btnPressStart;
    if (heldMs >= 40u && heldMs < NODE2_SOS_HOLD_MS) {
      if (g_alarmActive || g_sosTriggered) {
        setAlarmHardware(false); // Short tap -> silence/clear alert locally
      } else {
        // Short tap while already safe -> re-sync LCD cleanly (fixes any cable wiggle!)
        lcdInitAuto();
        updateVisualOutputs(true);
      }
    }
    g_btnWasPressed = false;
  }
}

// ============================================================================
// 7. Downlink RPC Commands & Diagnostics from PC Local Gateway (T08)
// ============================================================================
static void scanI2cBus() {
  uint8_t found = 0;
  Serial.printf("[I2C_SCAN] SDA=%d SCL=%d addrs=[", NODE2_I2C_SDA_PIN,
                NODE2_I2C_SCL_PIN);
  for (uint8_t addr = 0x08; addr <= 0x77; ++addr) {
    if (i2cProbe(addr)) {
      if (found > 0)
        Serial.print(",");
      Serial.printf("0x%02X", addr);
      ++found;
    }
  }
  Serial.printf("] count=%u lcd=0x%02X i2c_errs=%lu btn_idle=(D3:%d,D4:%d)\n",
                (unsigned)found, (unsigned)g_lcdAddr,
                (unsigned long)g_lcd.getErrorCount(), g_btnD3Idle, g_btnD4Idle);
}

static void pollSerialCommands() {
  while (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.startsWith("RPC:ALARM:")) {
      bool on = cmd.endsWith("1") || cmd.endsWith("ON") || cmd.endsWith("true");
      setAlarmHardware(on);
    } else if (cmd.startsWith("RPC:STATE:")) {
      String st = cmd.substring(10);
      st.trim();
      st.toUpperCase();
      if (st.length() > 0) {
        snprintf(g_aiState, sizeof(g_aiState), "%s", st.c_str());
        updateVisualOutputs(true);
      }
    } else if (cmd == "RPC:I2CSCAN") {
      scanI2cBus();
    } else if (cmd == "RPC:LCDINIT") {
      bool ok = lcdInitAuto();
      updateVisualOutputs(true);
      Serial.printf("[LCD] Re-init addr=0x%02X ok=%s errs=%lu\n",
                    (unsigned)g_lcdAddr, ok ? "YES" : "NO",
                    (unsigned long)g_lcd.getErrorCount());
    }
  }
}

static void printMacForConfig(const char *label) {
  uint8_t mac[6];
  WiFi.macAddress(mac);
  Serial.printf("[%s] MAC: %02X:%02X:%02X:%02X:%02X:%02X  (config.h: {0x%02X, "
                "0x%02X, 0x%02X, 0x%02X, 0x%02X, 0x%02X})\n",
                label, mac[0], mac[1], mac[2], mac[3], mac[4], mac[5], mac[0],
                mac[1], mac[2], mac[3], mac[4], mac[5]);
}

// ============================================================================
// 8. Node 2 Setup
// ============================================================================
void setup() {
  Serial.begin(SERIAL_BAUDRATE);
  delay(300);

  pinMode(NODE2_TL_PIN_D5, OUTPUT);
  pinMode(NODE2_TL_PIN_D6, OUTPUT);
  pinMode(NODE2_ONBOARD_LED_PIN, OUTPUT);
  setTrafficLight(TL_OFF);

  pinMode(NODE2_BTN_PIN_D3, INPUT_PULLUP);
  pinMode(NODE2_BTN_PIN_D4, INPUT_PULLUP);
  delay(20);
  g_btnD3Idle = digitalRead(NODE2_BTN_PIN_D3);
  g_btnD4Idle = digitalRead(NODE2_BTN_PIN_D4);

  g_isBroadcastMode = isAllFF(NODE1_TX_MAC_ADDR);
  if (!g_isBroadcastMode) {
    memcpy(g_targetNode1Mac, NODE1_TX_MAC_ADDR, 6);
    g_targetMacLocked = true;
  }

  g_csiQueue = xQueueCreate(1, sizeof(csi_frame_t));

  // Configure Wi-Fi Station FIRST so 3.3V rail is settled before LCD init
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_ps(WIFI_PS_NONE);
  esp_wifi_set_protocol(WIFI_IF_STA, WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G |
                                         WIFI_PROTOCOL_11N);
  esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW_HT20);
  esp_wifi_set_channel(CSI_WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(true);

  wifi_csi_config_t csiCfg{};
  csiCfg.lltf_en = true;
  csiCfg.htltf_en = true;
  csiCfg.stbc_htltf2_en = true;
  csiCfg.ltf_merge_en = true;
  csiCfg.channel_filter_en = true;
  csiCfg.manu_scale = false;
  esp_wifi_set_csi_config(&csiCfg);
  esp_wifi_set_csi_rx_cb(&wifi_csi_rx_cb, nullptr);
  esp_wifi_set_csi(true);

  if (esp_now_init() == ESP_OK) {
    esp_now_register_recv_cb(onEspNowDataRecv);
  }

  // Start I2C1 at rock-solid 50 kHz for OhStem LCD 16x2 (0x21)
  Wire.begin(NODE2_I2C_SDA_PIN, NODE2_I2C_SCL_PIN, 50000u);
  bool lcdOk = lcdInitAuto();
  if (lcdOk) {
    lcdWriteLineIfChanged(0, "SMART FALL v1.2 ");
    lcdWriteLineIfChanged(1, "CSI RX READY!   ");
  }
  scanI2cBus();
  setAlarmHardware(false);

  printMacForConfig("NODE2-RX");
  Serial.printf(
      "[NODE2] Ready! Ch=%d | LCD=0x%02X(%s,errs=%lu) | TL=D5(G%d)/D6(G%d) | BTN=D3(G%d)/D4(G%d)\n",
      CSI_WIFI_CHANNEL, (unsigned)g_lcdAddr, lcdOk ? "OK" : "NONE",
      (unsigned long)g_lcd.getErrorCount(), NODE2_TL_PIN_D5, NODE2_TL_PIN_D6,
      NODE2_BTN_PIN_D3, NODE2_BTN_PIN_D4);
}

// ============================================================================
// 9. Main Loop: Poll SOS + Serial RPC + Update LCD/TL + Stream 52 CSI Amps
// ============================================================================
void loop() {
  pollSosButton();
  pollSerialCommands();
  updateVisualOutputs(false);

  const uint32_t now = millis();
  if ((now - g_lastRxMs) > 5000u && (now - g_lastDiagMs) > 5000u) {
    g_lastDiagMs = now;
    Serial.printf(
        "[NODE2] Waiting for Node 1 ESP-NOW packets on Ch %d (rx=%lu, csi_any=%lu)...\n",
        CSI_WIFI_CHANNEL, (unsigned long)g_rxPackets,
        (unsigned long)g_csiCbAny);
  } else if (g_rxPackets > 0u && g_csiFrames == 0u &&
             (now - g_lastDiagMs) > 3000u) {
    g_lastDiagMs = now;
    Serial.printf(
        "[NODE2-DIAG] ESP-NOW rx=%lu (locked %02X:%02X:%02X:%02X:%02X:%02X) | csi_any=%lu | last_len=%d\n",
        (unsigned long)g_rxPackets, g_targetNode1Mac[0], g_targetNode1Mac[1],
        g_targetNode1Mac[2], g_targetNode1Mac[3], g_targetNode1Mac[4],
        g_targetNode1Mac[5], (unsigned long)g_csiCbAny, (int)g_csiLastLen);
  }

  csi_frame_t frame;
  if (xQueueReceive(g_csiQueue, &frame, pdMS_TO_TICKS(10)) == pdTRUE) {
    float amps52[CSI_ACTIVE_SUBCARRIERS];
    csi_iq_to_amplitudes_52(frame.iq_raw, amps52);

    uint32_t totalExpected = g_rxPackets + g_missedPackets;
    float lossPct =
        (totalExpected > 0u)
            ? (100.0f * (float)g_missedPackets / (float)totalExpected)
            : 0.0f;

    Serial.printf("{\"ts_ms\":%lu,\"temp_c\":%.2f,\"humi_rh\":%.2f,"
                  "\"alarm_active\":%s,\"sos\":%s,\"rssi\":%d,"
                  "\"rx_packets\":%lu,\"pkt_loss_pct\":%.2f,\"amps_52\":[",
                  (unsigned long)millis(), (double)g_tempC, (double)g_humiRh,
                  g_alarmActive ? "true" : "false",
                  g_sosTriggered ? "true" : "false", (int)frame.rssi,
                  (unsigned long)g_rxPackets, (double)lossPct);
    for (size_t k = 0; k < CSI_ACTIVE_SUBCARRIERS; ++k) {
      Serial.printf(k + 1 < CSI_ACTIVE_SUBCARRIERS ? "%.1f," : "%.1f]}\n",
                    (double)amps52[k]);
    }
  }
}
