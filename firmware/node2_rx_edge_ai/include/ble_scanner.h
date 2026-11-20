#ifndef IOT_BTL_BLE_SCANNER_H
#define IOT_BTL_BLE_SCANNER_H

/**
 * @file ble_scanner.h
 * @brief Bo quet thu dong Bluetooth Low Energy (BLE 5.0 Passive Scanner - Ma cong nghe T03, 1.5 diem).
 *
 * Chuc nang (Muc 2.3.1 & 3.2.2 trong `spec_btl_iot_fall_detection.tex`):
 *   - Mo cua so quet thu dong ngan (80 ms moi 1000 ms tren Core 0) de khong gay xung dot
 *     bo chia se ang-ten Wi-Fi/BLE (Coexistence Arbitrator) voi luong thu CSI 100 Hz.
 *   - Doi chieu dia chi MAC quang ba voi danh sach tin cay `BLE_AUTHORIZED_MACS`.
 *   - Neu ton tai the BLE hop le co `RSSI >= bleRssiMin` (mac dinh -75 dBm), xac nhan
 *     nguoi trong phong la thanh vien gia dinh (`ble_authorized = true`).
 */

#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>

#if __has_include("../../include/config.h")
  #include "../../include/config.h"
#endif
#ifndef WIFI_SSID
  #include "config.example.h"
#endif

typedef struct {
    bool     authorized_present;
    char     best_mac[18];
    int      best_rssi;
    uint32_t last_seen_ms;
    uint32_t scan_cycles;
} ble_presence_state_t;

class BlePresenceScanner {
public:
    void begin() {
        BLEDevice::init("YoloUNO_CSI_Radar_Node2");
        m_pScan = BLEDevice::getScan();
        if (m_pScan) {
            m_pScan->setActiveScan(false); // Passive scan tiet kiem thoi gian chia se ang-ten RF
            m_pScan->setInterval(160);     // 160 * 0.625 ms = 100 ms
            m_pScan->setWindow(80);        //  80 * 0.625 ms =  50 ms
        }
        memset(&m_state, 0, sizeof(m_state));
        strncpy(m_state.best_mac, "NONE", sizeof(m_state.best_mac) - 1);
        m_state.best_rssi = -100;
    }

    /**
     * @brief Quet nhanh cac goi BLE Advertising va cap nhat trang thai dinh danh chu nha
     */
    void scanOnce(int rssiMinThresholdDbm) {
        if (!m_pScan) return;

        // Quet nhanh 1 giay (blocking tren Task_BLE_Scanner rieng o Core 0, khong anh huong Core 1)
        BLEScanResults foundDevices = m_pScan->start(1, false);
        int count = foundDevices.getCount();

        bool foundAuthorized = false;
        int strongestRssi = -127;
        String strongestMac = "NONE";

        for (int i = 0; i < count; ++i) {
            BLEAdvertisedDevice dev = foundDevices.getDevice(i);
            String macStr = dev.getAddress().toString().c_str();
            macStr.toUpperCase();
            int rssi = dev.getRSSI();

            for (size_t k = 0; k < BLE_AUTHORIZED_COUNT; ++k) {
                if (macStr.equalsIgnoreCase(BLE_AUTHORIZED_MACS[k])) {
                    if (rssi > strongestRssi) {
                        strongestRssi = rssi;
                        strongestMac  = macStr;
                    }
                    if (rssi >= rssiMinThresholdDbm) {
                        foundAuthorized = true;
                    }
                }
            }
        }
        m_pScan->clearResults();

        uint32_t now = millis();
        m_state.scan_cycles++;
        if (foundAuthorized) {
            m_state.authorized_present = true;
            m_state.best_rssi = strongestRssi;
            strncpy(m_state.best_mac, strongestMac.c_str(), sizeof(m_state.best_mac) - 1);
            m_state.best_mac[sizeof(m_state.best_mac) - 1] = '\0';
            m_state.last_seen_ms = now;
        } else {
            // Duy tri trang thai Grace Period 5 giay tranh nhap nhay RSSI tuc thoi
            if ((now - m_state.last_seen_ms) > 5000u) {
                m_state.authorized_present = false;
                m_state.best_rssi = strongestRssi;
                strncpy(m_state.best_mac, strongestMac.c_str(), sizeof(m_state.best_mac) - 1);
                m_state.best_mac[sizeof(m_state.best_mac) - 1] = '\0';
            }
        }
    }

    ble_presence_state_t getState() const {
        return m_state;
    }

private:
    BLEScan*             m_pScan = nullptr;
    ble_presence_state_t m_state{};
};

#endif // IOT_BTL_BLE_SCANNER_H
