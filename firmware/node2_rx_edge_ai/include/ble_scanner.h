#ifndef IOT_BTL_BLE_SCANNER_H
#define IOT_BTL_BLE_SCANNER_H

/**
 * @file ble_scanner.h
 * @brief Passive Bluetooth Low Energy (BLE 5.0) Resident Scanner (Technology Item T03, 1.5 points).
 *
 * Functionality (Sections 2.3.1 & 3.2.2 in `HK261_Group01_IoTReport.tex`):
 *   - Opens short passive scan windows (80 ms every 1000 ms on Core 0) to minimize contention
 *     with the Wi-Fi/BLE RF Coexistence Arbitrator while receiving 100 Hz CSI frames.
 *   - Matches advertised MAC addresses against the trusted `BLE_AUTHORIZED_MACS` whitelist.
 *   - If an authorized BLE tag is detected with `RSSI >= bleRssiMin` (default -75 dBm),
 *     confirms resident presence (`ble_authorized = true`).
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
            m_pScan->setActiveScan(false); // Passive scan reduces RF antenna coexistence overhead
            m_pScan->setInterval(160);     // 160 * 0.625 ms = 100 ms
            m_pScan->setWindow(80);        //  80 * 0.625 ms =  50 ms
        }
        memset(&m_state, 0, sizeof(m_state));
        strncpy(m_state.best_mac, "NONE", sizeof(m_state.best_mac) - 1);
        m_state.best_rssi = -100;
    }

    /**
     * @brief Perform a passive BLE advertising scan and update resident authorization state
     */
    void scanOnce(int rssiMinThresholdDbm) {
        if (!m_pScan) return;

        // 1-second scan (runs on dedicated Core 0 task without stalling Core 1 CSI DSP)
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
            // Maintain a 5-second grace period to prevent transient RSSI dropouts
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
