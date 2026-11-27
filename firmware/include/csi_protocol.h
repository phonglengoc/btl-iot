#ifndef IOT_BTL_CSI_PROTOCOL_H
#define IOT_BTL_CSI_PROTOCOL_H

/**
 * @file csi_protocol.h
 * @brief 24-byte binary ESP-NOW packet protocol (T06) and 52-subcarrier CSI
 * amplitude helper.
 *
 * Simplified configuration:
 *   - Sampling rate: 20 Hz (50 ms/frame) -> 2.0-second window = 40 frames.
 *   - 52 active OFDM subcarrier amplitudes streamed over 115200 bps USB Serial
 * to PC Gateway.
 */

#include <math.h>
#include <stddef.h>
#include <stdint.h>


// ESP-NOW & CSI framing constants
#define CSI_PING_MAGIC_HEADER 0xC51Au
#define CSI_RAW_IQ_BYTES 128u // 64 OFDM subcarriers * 2 bytes (Imag/Real)
#define CSI_ACTIVE_SUBCARRIERS                                                 \
  52u // 52 active subcarriers (excluding guard bands & DC)
#define CSI_SAMPLE_RATE_HZ 20u // 20 frames/sec (50 ms period)
#define CSI_WINDOW_SAMPLES 40u // 2.0-second window @ 20 Hz = 40 samples

// Node 1 status flags
#define NODE1_STATUS_DHT20_OK (1u << 0)

/**
 * @brief Fixed 24-byte ESP-NOW packet sent at 20 Hz from Node 1 -> Node 2 (T01,
 * T06)
 */
#pragma pack(push, 1)
typedef struct {
  uint16_t magic_header; // Fixed 0xC51A identifier to filter our system packets
  uint32_t seq_id; // Incrementing sequence number (for packet-loss tracking)
  uint32_t tx_time_ms;     // Node 1 transmit timestamp (ms)
  float temp_c;            // Room temperature from I2C DHT20 sensor (T01)
  float humi_rh;           // Relative humidity from I2C DHT20 sensor (T01)
  uint8_t tx_status_flag;  // Node 1 DHT20 health status flag
  uint8_t reserved_pad[4]; // Reserved metadata (Wi-Fi channel, TX rate) ->
                           // exact 24 bytes
  uint8_t crc8_checksum; // CRC-8 checksum (polynomial 0x07) over first 23 bytes
} csi_ping_packet_t;
#pragma pack(pop)

#ifdef __cplusplus
static_assert(sizeof(csi_ping_packet_t) == 24,
              "csi_ping_packet_t MUST be exactly 24 bytes!");
#endif

/**
 * @brief Compute CRC-8 checksum for ESP-NOW packets (polynomial = 0x07, init =
 * 0x00)
 */
static inline uint8_t csi_crc8_compute(const uint8_t *data, size_t len) {
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
 * @brief Compute CRC-8 checksum for DHT20 sensor (polynomial = 0x31, init =
 * 0xFF per Aosong datasheet)
 */
static inline uint8_t dht20_crc8_compute(const uint8_t *data, size_t len) {
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
 * @brief Indices of the 52 active OFDM subcarriers out of 64 in IEEE 802.11n
 * HT20 (skipping guard bands [0..5], DC subcarrier [32], and [59..63])
 */
static const uint8_t CSI_ACTIVE_SC_INDICES[CSI_ACTIVE_SUBCARRIERS] = {
    6,  7,  8,  9,  10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
    24, 25, 26, 27, 28, 29, 30, 31, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42,
    43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58};

/**
 * @brief Convert 128 raw signed I/Q bytes from ESP32-S3 into 52 active
 * amplitudes: sqrt(I^2 + Q^2)
 */
static inline void csi_iq_to_amplitudes_52(const int8_t *iq_raw,
                                           float *out_amps_52) {
  for (size_t i = 0; i < CSI_ACTIVE_SUBCARRIERS; ++i) {
    const size_t sc = CSI_ACTIVE_SC_INDICES[i];
    const float im = (float)iq_raw[2u * sc];
    const float re = (float)iq_raw[2u * sc + 1u];
    out_amps_52[i] = sqrtf(im * im + re * re);
  }
}

#endif // IOT_BTL_CSI_PROTOCOL_H
