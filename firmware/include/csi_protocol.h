#ifndef IOT_BTL_CSI_PROTOCOL_H
#define IOT_BTL_CSI_PROTOCOL_H

/**
 * @file csi_protocol.h
 * @brief 24-byte ESP-NOW binary frame protocol (T06) and USB-CDC telemetry definitions
 *        shared between Node 1 (Tx), Node 2 (Rx), and PC Local IoT Gateway (T08, T15).
 *
 * Matches Listing 3.1 in `HK261_Group01_IoTReport.tex`:
 *   sizeof(csi_ping_packet_t) == 24 bytes (packed)
 */

#include <stdint.h>
#include <stddef.h>

// ESP-NOW CSI ping packet identifier
#define CSI_PING_MAGIC_HEADER   0xC51Au
#define CSI_RAW_IQ_BYTES        128u    // 64 OFDM subcarriers * 2 bytes (Imag/Real or I/Q)
#define CSI_ACTIVE_SUBCARRIERS  52u     // 52 active OFDM subcarriers (excluding guard bands & DC)
#define TINYML_NUM_SUBCARRIERS  16u     // 16 pooled subcarrier groups fed into TinyML 1D-CNN
#define TINYML_WINDOW_LEN       100u    // 100 frames (2.0 s @ 50 Hz after 2x decimation from 100 Hz)
#define TINYML_NUM_CLASSES      4u      // 0: EMPTY, 1: STATIONARY, 2: WALKING, 3: FALL

// Node 1 status flags inside `tx_status_flag`
#define NODE1_STATUS_DHT20_OK   (1u << 0)
#define NODE1_STATUS_CALIB_REQ  (1u << 1)
#define NODE1_STATUS_OTA_READY  (1u << 2)

// Standardized 4-class activity labels across the entire system
typedef enum : uint8_t {
    CSI_STATE_EMPTY      = 0,
    CSI_STATE_STATIONARY = 1,
    CSI_STATE_WALKING    = 2,
    CSI_STATE_FALL       = 3
} csi_activity_state_t;

static inline const char* csi_state_to_str(uint8_t state) {
    switch (state) {
        case CSI_STATE_EMPTY:      return "EMPTY";
        case CSI_STATE_STATIONARY: return "STATIONARY";
        case CSI_STATE_WALKING:    return "WALKING";
        case CSI_STATE_FALL:       return "FALL";
        default:                   return "UNKNOWN";
    }
}

/**
 * @brief Fixed 24-byte ESP-NOW frame transmitted at 100 Hz from Node 1 -> Node 2 (T01, T06)
 *
 * Byte offsets:
 *   [0..1]   magic_header   (uint16_t) = 0xC51A
 *   [2..5]   seq_id         (uint32_t)
 *   [6..9]   tx_time_ms     (uint32_t)
 *   [10..13] temp_c         (float)    - Ambient temperature from I2C DHT20
 *   [14..17] humi_rh        (float)    - Relative humidity from I2C DHT20
 *   [18]     tx_status_flag (uint8_t)
 *   [19..22] reserved_pad   (uint8_t[4]) -> Reserved for Tx power / reference RSSI calibration
 *   [23]     crc8_checksum  (uint8_t)  - CRC-8 (poly 0x07) over the first 23 bytes
 */
#pragma pack(push, 1)
typedef struct {
    uint16_t magic_header;      // Fixed 0xC51A to filter system packets
    uint32_t seq_id;            // Monotonically increasing sequence ID (packet loss tracking)
    uint32_t tx_time_ms;        // Node 1 transmit timestamp (ms)
    float    temp_c;            // Temperature reading from I2C DHT20 (T01)
    float    humi_rh;           // Relative humidity reading from I2C DHT20 (T01)
    uint8_t  tx_status_flag;    // Node 1 operational status bitmask
    uint8_t  reserved_pad[4];   // Reserved (tx_power_dbm, fw_minor, etc.) -> Exact 24-byte frame
    uint8_t  crc8_checksum;     // CRC-8 checksum (polynomial 0x07)
} csi_ping_packet_t;
#pragma pack(pop)

#ifdef __cplusplus
static_assert(sizeof(csi_ping_packet_t) == 24, "csi_ping_packet_t MUST be exactly 24 bytes!");
#endif

/**
 * @brief Compute CRC-8 checksum (SMBus / ITU-T, polynomial = 0x07, init = 0x00)
 */
static inline uint8_t csi_crc8_compute(const uint8_t* data, size_t len) {
    uint8_t crc = 0x00u;
    for (size_t i = 0; i < len; ++i) {
        crc ^= data[i];
        for (uint8_t bit = 0; bit < 8; ++bit) {
            if (crc & 0x80u) {
                crc = (uint8_t)((crc << 1) ^ 0x07u);
            } else {
                crc = (uint8_t)(crc << 1);
            }
        }
    }
    return crc;
}

/**
 * @brief Compute CRC-8 for DHT20 sensor frames (polynomial = 0x31, init = 0xFF per Aosong datasheet)
 */
static inline uint8_t dht20_crc8_compute(const uint8_t* data, size_t len) {
    uint8_t crc = 0xFFu;
    for (size_t i = 0; i < len; ++i) {
        crc ^= data[i];
        for (uint8_t bit = 0; bit < 8; ++bit) {
            if (crc & 0x80u) {
                crc = (uint8_t)((crc << 1) ^ 0x31u);
            } else {
                crc = (uint8_t)(crc << 1);
            }
        }
    }
    return crc;
}

/**
 * @brief Mapping of 52 active LLTF subcarriers from the 64-subcarrier raw array (128 bytes I/Q).
 *        On ESP32-S3 802.11n HT20 LLTF:
 *        Indices k = 0..63 excluding lower guard [0..5], DC [32], and upper guard [59..63] -> 52 channels.
 */
static const uint8_t CSI_ACTIVE_SC_INDICES[CSI_ACTIVE_SUBCARRIERS] = {
     6,  7,  8,  9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
    19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31,
    33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45,
    46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58
};

#endif // IOT_BTL_CSI_PROTOCOL_H
