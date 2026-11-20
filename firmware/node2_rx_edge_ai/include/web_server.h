#ifndef IOT_BTL_NODE2_WEB_SERVER_H
#define IOT_BTL_NODE2_WEB_SERVER_H

/**
 * @file web_server.h
 * @brief Embedded HTTP Web Server tu lap trinh tren Node 2 (Ma cong nghe T07, 1.0 diem).
 *
 * Cung cap giao dien HTML5/CSS/JS hien dai va 3 diem cuoi RESTful API (Muc 3.2.3 Spec):
 *   - GET  /            : Giao dien dieu khien va giam sat truc quan tai cho
 *   - GET  /api/status  : JSON trang thai CSI, TinyML, DHT20, BLE, IP, Packet Loss
 *   - POST /api/config  : Cap nhat `fall_threshold`, `ble_rssi_min`, `security_armed`
 *   - POST /api/alarm   : Kiem thu bat/tat coi Buzzer (GPIO10) va den WS2812B RGB (GPIO18)
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>

#include "csi_protocol.h"

typedef struct {
    uint8_t  csi_state;
    float    edge_conf;
    float    motion_energy;
    float    temp_c;
    float    humi_rh;
    bool     ble_authorized;
    char     ble_mac[18];
    int      ble_rssi;
    float    fall_threshold;
    int      ble_rssi_min;
    bool     security_armed;
    bool     alarm_active;
    uint32_t rx_packets;
    float    pkt_loss_pct;
} node2_shared_status_t;

static const char NODE2_INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>YoloUNO Node 2 - Wi-Fi CSI Radar & Edge AI (T07)</title>
<style>
  body{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:18px}
  .wrap{max-width:860px;margin:0 auto}
  .hdr{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #334155;padding-bottom:12px;margin-bottom:16px}
  .badge{padding:6px 12px;border-radius:999px;font-weight:700;font-size:13px}
  .ok{background:#065f46;color:#6ee7b7}.warn{background:#92400e;color:#fde68a}.crit{background:#991b1b;color:#fca5a5}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin-bottom:16px}
  .card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:16px}
  .lbl{font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em}
  .val{font-size:24px;font-weight:700;margin-top:6px}
  button{background:#2563eb;color:#fff;border:none;padding:9px 14px;border-radius:8px;cursor:pointer;font-weight:600;margin-right:8px}
  button.red{background:#dc2626}button.gray{background:#475569}
  input[type=number]{background:#0f172a;border:1px solid #475569;color:#fff;padding:6px 8px;border-radius:6px;width:90px}
</style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <div>
      <h2 style="margin:0">YoloUNO #2 — Wi-Fi CSI Radar &amp; TinyML</h2>
      <small style="color:#94a3b8">HCMUT IoT HK261 | Embedded HTTP Web Server (T07)</small>
    </div>
    <span id="stateBadge" class="badge ok">CONNECTING...</span>
  </div>
  <div class="grid">
    <div class="card"><div class="lbl">TinyML Edge State (T04)</div><div class="val" id="stVal">--</div><small id="confVal">TinyML Conf: --%</small></div>
    <div class="card"><div class="lbl">DHT20 Environment (T01)</div><div class="val" id="envVal">-- °C / -- %</div><small id="pktVal">ESP-NOW Rx: 0</small></div>
    <div class="card"><div class="lbl">BLE Identity (T03)</div><div class="val" id="bleVal">--</div><small id="macVal">MAC: --</small></div>
  </div>
  <div class="card" style="margin-bottom:14px">
    <h3 style="margin-top:0">Sensitivity Threshold &amp; Security Configuration (/api/config)</h3>
    <label>Fall threshold (0.50–0.95): <input id="thFall" type="number" step="0.05" min="0.50" max="0.95" value="0.80"></label>
    &nbsp;&nbsp;
    <label>Min BLE RSSI (dBm): <input id="thBle" type="number" step="1" min="-95" max="-40" value="-75"></label>
    &nbsp;&nbsp;
    <label><input id="chkSec" type="checkbox" checked> Arm Intrusion Guard</label>
    <div style="margin-top:12px"><button onclick="saveCfg()">Save Configuration</button></div>
  </div>
  <div class="card">
    <h3 style="margin-top:0">Local Piezo Buzzer &amp; WS2812B Siren Control (/api/alarm)</h3>
    <button class="red" onclick="setAlarm(1)">Trigger Siren &amp; Red LED</button>
    <button class="gray" onclick="setAlarm(0)">Mute Local Alarm</button>
  </div>
</div>
<script>
async function poll(){
  try{
    const r = await fetch('/api/status'); const d = await r.json();
    document.getElementById('stVal').textContent = d.csi_state;
    document.getElementById('confVal').textContent = 'TinyML Conf: ' + Math.round(d.edge_conf*100) + '% | Energy: ' + d.motion_energy.toFixed(1);
    document.getElementById('envVal').textContent = d.temp_c.toFixed(1) + ' °C / ' + d.humi_rh.toFixed(1) + ' %';
    document.getElementById('pktVal').textContent = 'ESP-NOW Rx: ' + d.rx_packets + ' (Loss: ' + d.pkt_loss_pct.toFixed(2) + '%)';
    document.getElementById('bleVal').textContent = d.ble_authorized ? 'Authorized (Resident)' : 'No BLE Tag';
    document.getElementById('macVal').textContent = d.ble_mac + ' (' + d.ble_rssi + ' dBm)';
    const b = document.getElementById('stateBadge');
    b.textContent = d.alarm_active ? 'ALARM ACTIVE' : d.csi_state;
    b.className = 'badge ' + (d.csi_state==='FALL'||d.alarm_active ? 'crit' : (d.csi_state==='WALKING' ? 'warn' : 'ok'));
  }catch(e){}
}
async function saveCfg(){
  const body = new URLSearchParams({
    fall_threshold: document.getElementById('thFall').value,
    ble_rssi_min: document.getElementById('thBle').value,
    security_armed: document.getElementById('chkSec').checked ? '1' : '0'
  });
  await fetch('/api/config', {method:'POST', body});
}
async function setAlarm(on){
  await fetch('/api/alarm', {method:'POST', body: new URLSearchParams({active: on})});
  poll();
}
setInterval(poll, 750); poll();
</script>
</body>
</html>
)rawliteral";

typedef void (*ConfigUpdateCallback)(float fallThreshold, int bleRssiMin, bool securityArmed);
typedef void (*AlarmControlCallback)(bool active);

class Node2EmbeddedWebServer {
public:
    void begin(
        const node2_shared_status_t* statusPtr,
        ConfigUpdateCallback onConfig,
        AlarmControlCallback onAlarm
    ) {
        m_statusPtr = statusPtr;
        m_onConfig  = onConfig;
        m_onAlarm   = onAlarm;

        m_server.on("/", HTTP_GET, [this]() {
            m_server.send_P(200, "text/html", NODE2_INDEX_HTML);
        });

        m_server.on("/api/status", HTTP_GET, [this]() {
            if (!m_statusPtr) {
                m_server.send(500, "application/json", "{\"error\":\"no_status\"}");
                return;
            }
            const node2_shared_status_t s = *m_statusPtr;
            char json[512];
            snprintf(
                json, sizeof(json),
                "{\"csi_state\":\"%s\",\"edge_conf\":%.3f,\"motion_energy\":%.2f,"
                "\"temp_c\":%.2f,\"humi_rh\":%.2f,\"ble_authorized\":%s,"
                "\"ble_mac\":\"%s\",\"ble_rssi\":%d,\"fall_threshold\":%.2f,"
                "\"ble_rssi_min\":%d,\"security_armed\":%s,\"alarm_active\":%s,"
                "\"rx_packets\":%lu,\"pkt_loss_pct\":%.2f,\"ip\":\"%s\"}",
                csi_state_to_str(s.csi_state),
                (double)s.edge_conf,
                (double)s.motion_energy,
                (double)s.temp_c,
                (double)s.humi_rh,
                s.ble_authorized ? "true" : "false",
                s.ble_mac,
                s.ble_rssi,
                (double)s.fall_threshold,
                s.ble_rssi_min,
                s.security_armed ? "true" : "false",
                s.alarm_active ? "true" : "false",
                (unsigned long)s.rx_packets,
                (double)s.pkt_loss_pct,
                WiFi.localIP().toString().c_str()
            );
            m_server.send(200, "application/json", json);
        });

        m_server.on("/api/config", HTTP_POST, [this]() {
            float ft = m_statusPtr ? m_statusPtr->fall_threshold : 0.80f;
            int br   = m_statusPtr ? m_statusPtr->ble_rssi_min   : -75;
            bool sec = m_statusPtr ? m_statusPtr->security_armed : true;

            if (m_server.hasArg("fall_threshold")) {
                ft = m_server.arg("fall_threshold").toFloat();
                if (ft < 0.50f) ft = 0.50f;
                if (ft > 0.95f) ft = 0.95f;
            }
            if (m_server.hasArg("ble_rssi_min")) {
                br = m_server.arg("ble_rssi_min").toInt();
            }
            if (m_server.hasArg("security_armed")) {
                String v = m_server.arg("security_armed");
                sec = (v == "1" || v.equalsIgnoreCase("true"));
            }
            if (m_onConfig) {
                m_onConfig(ft, br, sec);
            }
            m_server.send(200, "application/json", "{\"ok\":true}");
        });

        m_server.on("/api/alarm", HTTP_POST, [this]() {
            bool act = false;
            if (m_server.hasArg("active")) {
                String v = m_server.arg("active");
                act = (v == "1" || v.equalsIgnoreCase("true"));
            }
            if (m_onAlarm) {
                m_onAlarm(act);
            }
            m_server.send(200, "application/json", "{\"ok\":true}");
        });

        m_server.begin();
    }

    void handleClient() {
        m_server.handleClient();
    }

private:
    WebServer                    m_server{80};
    const node2_shared_status_t* m_statusPtr = nullptr;
    ConfigUpdateCallback         m_onConfig  = nullptr;
    AlarmControlCallback         m_onAlarm   = nullptr;
};

#endif // IOT_BTL_NODE2_WEB_SERVER_H
