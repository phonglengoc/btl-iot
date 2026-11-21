#ifndef IOT_BTL_CSI_PROTOCOL_H
#define IOT_BTL_CSI_PROTOCOL_H

/**
 * @file csi_protocol.h
 * @brief Giao thuc goi tin nhi phan ESP-NOW 24-byte (T06) va tinh bien do 52 song mang CSI.
 *
 * Cau hinh rut gon:
 *   - Tan so lay mau: 20 Hz (50 ms/khung) -> Cua so 2.0 giay = 40 khung.
 *   - 52 song mang phu hoat dong (Active Subcarriers) duoc gui qua Serial 115200 bps ve PC Gateway.
 */

#include <stdint.h>
#include <stddef.h>
#include <math.h>

// Hang so khung tin ESP-NOW & CSI
#define CSI_PING_MAGIC_HEADER   0xC51Au
#define CSI_RAW_IQ_BYTES        128u    // 64 song mang OFDM * 2 byte (Imag/Real)
#define CSI_ACTIVE_SUBCARRIERS  52u     // 52 song mang phu hoat dong (bo guard band & DC)
#define CSI_SAMPLE_RATE_HZ      20u     // 20 khung/giay (chu ky 50 ms)
#define CSI_WINDOW_SAMPLES      40u     // Cua so 2.0 giay @ 20 Hz = 40 mau

// Co trang thai Node 1
#define NODE1_STATUS_DHT20_OK   (1u << 0)

/**
 * @brief Khung tin ESP-NOW co dinh 24 byte truyen 20 Hz tu Node 1 -> Node 2 (T01, T06)
 */
#pragma pack(push, 1)
typedef struct {
    uint16_t magic_header;      // Co dinh 0xC51A de loc dung goi cua he thong
    uint32_t seq_id;            // So thu tu goi tang dan (kiem tra mat goi)
    uint32_t tx_time_ms;        // Thoi gian phat tu Node 1 (ms)
    float    temp_c;            // Nhiet do doc tu cam bien I2C DHT20 (T01)
    float    humi_rh;           // Do am tuong doi tu cam bien I2C DHT20 (T01)
    uint8_t  tx_status_flag;    // Co trang thai DHT20 cua Node 1
    uint8_t  reserved_pad[4];   // Du phong (kenh Wi-Fi, cong suat Tx...) -> Tong dung 24 byte
    uint8_t  crc8_checksum;     // Ma kiem tra loi CRC-8 (da thuc 0x07) tren 23 byte dau
} csi_ping_packet_t;
#pragma pack(pop)

#ifdef __cplusplus
static_assert(sizeof(csi_ping_packet_t) == 24, "csi_ping_packet_t MUST be exactly 24 bytes!");
#endif

/**
 * @brief Tinh CRC-8 cho goi tin ESP-NOW (polynomial = 0x07, init = 0x00)
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
 * @brief Tinh CRC-8 cho cam bien DHT20 (polynomial = 0x31, init = 0xFF theo datasheet Aosong)
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
 * @brief Chi so 52 song mang phu hoat dong trong mang 64 song mang Wi-Fi 802.11n HT20
 *        (bo qua guard band [0..5], DC [32], va [59..63])
 */
static const uint8_t CSI_ACTIVE_SC_INDICES[CSI_ACTIVE_SUBCARRIERS] = {
     6,  7,  8,  9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
    19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31,
    33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45,
    46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58
};

/**
 * @brief Chuyen 128 byte tho I/Q tu ESP32-S3 thanh 52 gia tri bien do song: sqrt(I^2 + Q^2)
 */
static inline void csi_iq_to_amplitudes_52(const int8_t* iq_raw, float* out_amps_52) {
    for (size_t i = 0; i < CSI_ACTIVE_SUBCARRIERS; ++i) {
        const size_t sc = CSI_ACTIVE_SC_INDICES[i];
        const float im = (float)iq_raw[2u * sc];
        const float re = (float)iq_raw[2u * sc + 1u];
        out_amps_52[i] = sqrtf(im * im + re * re);
    }
}

#endif // IOT_BTL_CSI_PROTOCOL_H
