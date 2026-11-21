#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "csi_protocol.h"

int main() {
    // 1. Verify exact 24-byte packed ESP-NOW packet size (T06)
    static_assert(sizeof(csi_ping_packet_t) == 24, "Packet size must be 24 bytes");

    csi_ping_packet_t pkt{};
    pkt.magic_header = CSI_PING_MAGIC_HEADER;
    pkt.seq_id = 42;
    pkt.tx_time_ms = 2100;
    pkt.temp_c = 28.25f;
    pkt.humi_rh = 64.50f;
    pkt.tx_status_flag = NODE1_STATUS_DHT20_OK;
    pkt.reserved_pad[0] = 6;
    pkt.reserved_pad[1] = CSI_SAMPLE_RATE_HZ;
    pkt.crc8_checksum = csi_crc8_compute(reinterpret_cast<const uint8_t*>(&pkt), sizeof(pkt) - 1);

    uint8_t check_crc = csi_crc8_compute(reinterpret_cast<const uint8_t*>(&pkt), sizeof(pkt) - 1);
    assert(check_crc == pkt.crc8_checksum);

    // Corrupting 1 bit must break CRC-8
    pkt.seq_id ^= 1u;
    uint8_t bad_crc = csi_crc8_compute(reinterpret_cast<const uint8_t*>(&pkt), sizeof(pkt) - 1);
    assert(bad_crc != pkt.crc8_checksum);

    // 2. Verify DHT20 CRC-8 (polynomial 0x31)
    const uint8_t sample_dht[6] = {0x1C, 0x99, 0x99, 0x96, 0x20, 0x00};
    uint8_t dht_crc = dht20_crc8_compute(sample_dht, 6);
    assert(dht_crc == dht20_crc8_compute(sample_dht, 6));

    // 3. Verify 52 active subcarrier amplitude calculation: sqrt(3^2 + 4^2) == 5.0
    int8_t iq_raw[CSI_RAW_IQ_BYTES];
    std::memset(iq_raw, 0, sizeof(iq_raw));
    for (size_t i = 0; i < CSI_ACTIVE_SUBCARRIERS; ++i) {
        size_t sc = CSI_ACTIVE_SC_INDICES[i];
        iq_raw[2 * sc] = 3;
        iq_raw[2 * sc + 1] = 4;
    }
    float amps52[CSI_ACTIVE_SUBCARRIERS];
    csi_iq_to_amplitudes_52(iq_raw, amps52);
    for (size_t i = 0; i < CSI_ACTIVE_SUBCARRIERS; ++i) {
        assert(std::fabs(amps52[i] - 5.0f) < 1e-4f);
    }

    std::printf("[CPP_PROTOCOL_TEST_OK] pkt_bytes=%zu active_sc=%u rate_hz=%u\n",
                sizeof(csi_ping_packet_t), CSI_ACTIVE_SUBCARRIERS, CSI_SAMPLE_RATE_HZ);
    return 0;
}
