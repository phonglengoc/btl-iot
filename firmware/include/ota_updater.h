#ifndef IOT_BTL_OTA_UPDATER_H
#define IOT_BTL_OTA_UPDATER_H

/**
 * @file ota_updater.h
 * @brief Over-the-Air (OTA) Firmware Updater (Technology Item T14, 2.0 points)
 *        Integrated with CoreIOT OTA Repository (HTTP Chunk Streaming + SHA-256 Verification + Rollback).
 */

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Update.h>
#include "mbedtls/sha256.h"
#include "esp_ota_ops.h"

class CoreIOTOtaUpdater {
public:
    /**
     * @brief Mark the currently running OTA partition as valid after a clean boot.
     *        (If the new firmware crashes before this point, the bootloader automatically rolls back.)
     */
    static void confirmBootPartitionValid() {
        const esp_partition_t* running = esp_ota_get_running_partition();
        esp_ota_img_states_t ota_state;
        if (running && esp_ota_get_state_partition(running, &ota_state) == ESP_OK) {
            if (ota_state == ESP_OTA_IMG_PENDING_VERIFY) {
                esp_ota_mark_app_valid_cancel_rollback();
                Serial.println("[OTA-T14] Boot partition verified OK -> Rollback cancelled.");
            }
        }
    }

    /**
     * @brief Stream a firmware .bin image in 4096-byte chunks from the CoreIOT HTTP API,
     *        verify SHA-256 integrity, and flash to the inactive ota_0 / ota_1 slot.
     *
     * @param host           CoreIOT server hostname (e.g., "app.coreiot.io")
     * @param port           HTTP port (80 or 8080)
     * @param deviceToken    Device Access Token on CoreIOT
     * @param fwTitle        Firmware package title (`fw_title`)
     * @param targetVersion  Target firmware version (e.g., "1.1.0")
     * @param expectedSha256 64-character hex SHA-256 digest (empty to skip verification)
     * @return true on successful flash (device immediately reboots)
     */
    static bool performHttpOtaUpdate(
        const char* host,
        uint16_t port,
        const char* deviceToken,
        const char* fwTitle,
        const char* targetVersion,
        const char* expectedSha256
    ) {
        if (WiFi.status() != WL_CONNECTED) {
            Serial.println("[OTA-T14] ERROR: Wi-Fi not connected, cannot download OTA.");
            return false;
        }

        // Standard ThingsBoard / CoreIOT HTTP Firmware API URL:
        // GET http://<host>:<port>/api/v1/<ACCESS_TOKEN>/firmware?title=<fw_title>&version=<fw_version>
        String url = String("http://") + host + ":" + String(port) +
                     "/api/v1/" + String(deviceToken) +
                     "/firmware?title=" + String(fwTitle) +
                     "&version=" + String(targetVersion);

        Serial.printf("[OTA-T14] Starting OTA from CoreIOT: %s (target=%s)\n", fwTitle, targetVersion);

        HTTPClient http;
        http.begin(url);
        http.setTimeout(15000);
        int httpCode = http.GET();

        if (httpCode != HTTP_CODE_OK) {
            Serial.printf("[OTA-T14] HTTP GET failed, code=%d\n", httpCode);
            http.end();
            return false;
        }

        int totalLen = http.getSize();
        if (totalLen <= 0) {
            Serial.println("[OTA-T14] Invalid Content-Length from CoreIOT.");
            http.end();
            return false;
        }

        if (!Update.begin((size_t)totalLen, U_FLASH)) {
            Serial.printf("[OTA-T14] Not enough OTA space, err=%u\n", Update.getError());
            http.end();
            return false;
        }

        mbedtls_sha256_context shaCtx;
        mbedtls_sha256_init(&shaCtx);
        mbedtls_sha256_starts(&shaCtx, 0);

        WiFiClient* stream = http.getStreamPtr();
        uint8_t chunkBuf[4096];
        int remaining = totalLen;
        size_t writtenTotal = 0;

        while (http.connected() && remaining > 0) {
            size_t avail = stream->available();
            if (avail > 0) {
                size_t toRead = avail > sizeof(chunkBuf) ? sizeof(chunkBuf) : avail;
                int bytesRead = stream->readBytes(chunkBuf, toRead);
                if (bytesRead > 0) {
                    mbedtls_sha256_update(&shaCtx, chunkBuf, (size_t)bytesRead);
                    size_t written = Update.write(chunkBuf, (size_t)bytesRead);
                    if (written != (size_t)bytesRead) {
                        Serial.println("[OTA-T14] Flash write error during chunk streaming!");
                        mbedtls_sha256_free(&shaCtx);
                        Update.abort();
                        http.end();
                        return false;
                    }
                    writtenTotal += written;
                    remaining -= bytesRead;
                }
            } else {
                delay(2);
            }
        }

        uint8_t digest[32];
        mbedtls_sha256_finish(&shaCtx, digest);
        mbedtls_sha256_free(&shaCtx);
        http.end();

        char computedHex[65];
        for (int i = 0; i < 32; ++i) {
            snprintf(&computedHex[i * 2], 3, "%02x", digest[i]);
        }
        computedHex[64] = '\0';

        if (expectedSha256 && strlen(expectedSha256) == 64) {
            if (strcasecmp(computedHex, expectedSha256) != 0) {
                Serial.printf("[OTA-T14] SHA-256 MISMATCH! Expected=%s, Got=%s\n",
                              expectedSha256, computedHex);
                Update.abort();
                return false;
            }
            Serial.printf("[OTA-T14] SHA-256 verified OK: %s\n", computedHex);
        }

        if (!Update.end(true)) {
            Serial.printf("[OTA-T14] Update.end failed: %s\n", Update.errorString());
            return false;
        }

        Serial.printf("[OTA-T14] SUCCESS (%u bytes). Rebooting into new partition...\n",
                      (unsigned)writtenTotal);
        delay(500);
        ESP.restart();
        return true;
    }
};

#endif // IOT_BTL_OTA_UPDATER_H
