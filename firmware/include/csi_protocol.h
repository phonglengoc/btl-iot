#ifndef IOT_BTL_CSI_PROTOCOL_H
#define IOT_BTL_CSI_PROTOCOL_H

/**
 * @file csi_protocol.h
 * @brief Giao thuc goi tin nhi phan ESP-NOW 24-byte (T06) va ban tin USB-CDC JSON/Binary
 *        giua Node 1 (Tx), Node 2 (Rx) va PC Local IoT Gateway (T08, T15).
 *
 * Tuan thu 100% Doan ma 3.1 trong `spec_btl_iot_fall_detection.tex`:
 *   sizeof(csi_ping_packet_t) == 24 bytes (packed)
 */

#include <stdint.h>
#include <stddef.h>

// Ma nhan dien khung goi tin CSI ESP-NOW cua he thong
#define CSI_PING_MAGIC_HEADER   0xC51Au
#define CSI_RAW_IQ_BYTES        128u    // 64 OFDM subcarriers * 2 bytes (Imag/Real hoac I/Q)
#define CSI_ACTIVE_SUBCARRIERS  52u     // 52 song mang phu hoat dong (loai bo guard bands & DC)
#define TINYML_NUM_SUBCARRIERS  16u     // 16 nhom song mang dau vao cho TinyML 1D-CNN
#define TINYML_WINDOW_LEN       100u    // 100 khung (2.0 giay @ 50 Hz sau decimation 2x tu 100 Hz)
#define TINYML_NUM_CLASSES      4u      // 0: EMPTY, 1: STATIONARY, 2: WALKING, 3: FALL

// Co trang thai cua Node 1 trong `tx_status_flag`
#define NODE1_STATUS_DHT20_OK   (1u << 0)
#define NODE1_STATUS_CALIB_REQ  (1u << 1)
#define NODE1_STATUS_OTA_READY  (1u << 2)

// Nhan 4 trang thai hanh vi chuan hoa tren toan he thong
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
 * @brief Khung tin ESP-NOW kich thuoc co dinh 24 byte truyen 100 Hz tu Node 1 -> Node 2 (T01, T06)
 *
 * Byte offset:
 *   [0..1]   magic_header   (uint16_t) = 0xC51A
 *   [2..5]   seq_id         (uint32_t)
 *   [6..9]   tx_time_ms     (uint32_t)
 *   [10..13] temp_c         (float)    - Nhiet do tu I2C DHT20
 *   [14..17] humi_rh        (float)    - Do am tu I2C DHT20
 *   [18]     tx_status_flag (uint8_t)
 *   [19..22] reserved_pad   (uint8_t[4]) -> Du phong hieu chuan cong suat Tx / RSSI ref
 *   [23]     crc8_checksum  (uint8_t)  - CRC-8 da thuc 0x07 tinh tren 23 byte dau
 */
#pragma pack(push, 1)
typedef struct {
    uint16_t magic_header;      // Co dinh 0xC51A de loc dung goi cua he thong
    uint32_t seq_id;            // So thu tu goi tang dan (kiem tra ty le mat goi)
    uint32_t tx_time_ms;        // Nhan thoi gian phat tu Node 1 (ms)
    float    temp_c;            // Nhiet do doc tu cam bien I2C DHT20 (T01)
    float    humi_rh;           // Do am tuong doi tu cam bien I2C DHT20 (T01)
    uint8_t  tx_status_flag;    // Co trang thai cua Node 1
    uint8_t  reserved_pad[4];   // Du phong (tx_power_dbm, fw_minor, v.v.) -> Tong khung dung 24 byte
    uint8_t  crc8_checksum;     // Ma kiem tra loi CRC-8 da thuc 0x07
} csi_ping_packet_t;
#pragma pack(pop)

#ifdef __cplusplus
static_assert(sizeof(csi_ping_packet_t) == 24, "csi_ping_packet_t MUST be exactly 24 bytes!");
#endif

/**
 * @brief Tinh ma kiem tra loi vong CRC-8 (SMBus / ITU-T, polynomial = 0x07, init = 0x00)
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
 * @brief Tinh CRC-8 cho DHT20 (polynomial = 0x31, init = 0xFF theo datasheet Aosong DHT20)
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
 * @brief Anh xa 52 song mang phu hoat dong (active LLTF subcarriers) tu mang 64 song mang (128 byte I/Q)
 *        Tren ESP32-S3 802.11n HT20 LLTF:
 *        Chi so k = 0..63, bo cac guard band [0..5], DC [32], va guard band cuoi [59..63] -> con 52 kenh!
 */
static const uint8_t CSI_ACTIVE_SC_INDICES[CSI_ACTIVE_SUBCARRIERS] = {
     6,  7,  8,  9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
    19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31,
    33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45,
    46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58
};

#endif // IOT_BTL_CSI_PROTOCOL_H
