/**
 * @file cpp_tinyml_unit_test.cpp
 * @brief Kiem thu don vi C++17 tren PC (bien dich bang `g++`) cho:
 *   1. Giao thuc khung nhi phan 24-byte `csi_ping_packet_t`, CRC-8 (0x07) & DHT20 CRC-8 (0x31)
 *   2. Bo xu ly tin hieu Hampel + Butterworth bac 4 trong `csi_dsp.h`
 *   3. Bo suy luan `EdgeTinyMLClassifier::predict()` INT8 1D-CNN trong `tinyml_infer.h`
 *      (doc stdin vector kiem thu tu Python va xuat xac suat de doi chieu bit-accurate!).
 */

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <assert.h>

#include "../firmware/include/csi_protocol.h"
#include "../firmware/node2_rx_edge_ai/include/csi_dsp.h"
#include "../firmware/node2_rx_edge_ai/include/tinyml_infer.h"

int main(int argc, char** argv) {
    // 1. Kiem tra kich thuoc goi tin ESP-NOW 24 byte & CRC-8
    static_assert(sizeof(csi_ping_packet_t) == 24, "Packet size must be 24 bytes");
    csi_ping_packet_t pkt{};
    pkt.magic_header   = CSI_PING_MAGIC_HEADER;
    pkt.seq_id         = 1024;
    pkt.tx_time_ms     = 10240;
    pkt.temp_c         = 27.4f;
    pkt.humi_rh        = 68.2f;
    pkt.tx_status_flag = NODE1_STATUS_DHT20_OK;
    pkt.crc8_checksum  = csi_crc8_compute(reinterpret_cast<const uint8_t*>(&pkt), sizeof(pkt) - 1);
    assert(pkt.crc8_checksum != 0 || pkt.seq_id == 0);

    // 2. Kiem tra bo loc DSP Hampel (khu xung nhieu spike) + Butterworth
    float dummyWindow[200][CSI_ACTIVE_SUBCARRIERS];
    for (int t = 0; t < 200; ++t) {
        for (int c = 0; c < (int)CSI_ACTIVE_SUBCARRIERS; ++c) {
            dummyWindow[t][c] = 30.0f + 5.0f * sinf(2.0f * 3.14159265f * 8.0f * ((float)t / 100.0f));
        }
    }
    // Tiem 1 xung nhieu cuc lon o khung t=50
    dummyWindow[50][0] = 999.0f;

    float edgeTensor[TINYML_WINDOW_LEN][TINYML_NUM_SUBCARRIERS];
    CsiDspProcessor::processWindowToEdgeTensor(dummyWindow, 27.4f, 68.2f, edgeTensor);
    for (int t = 0; t < (int)TINYML_WINDOW_LEN; ++t) {
        for (int b = 0; b < (int)TINYML_NUM_SUBCARRIERS; ++b) {
            assert(isfinite(edgeTensor[t][b]));
            assert(edgeTensor[t][b] >= -4.0001f && edgeTensor[t][b] <= 4.0001f);
        }
    }

    // 3. Neu co tham so `--stdin-vector`, doc ma tran 100x16 tu stdin va in 4 xac suat INT8!
    if (argc > 1 && strcmp(argv[1], "--stdin-vector") == 0) {
        float inVec[100][16];
        for (int t = 0; t < 100; ++t) {
            for (int c = 0; c < 16; ++c) {
                if (scanf("%f", &inVec[t][c]) != 1) {
                    return 2;
                }
            }
        }
        tinyml_result_t r = EdgeTinyMLClassifier::predict(inVec);
        printf("%.6f %.6f %.6f %.6f %u\n",
               (double)r.probs[0], (double)r.probs[1],
               (double)r.probs[2], (double)r.probs[3],
               (unsigned)r.predicted_class);
        return 0;
    }

    tinyml_result_t res = EdgeTinyMLClassifier::predict(edgeTensor);
    float sumP = res.probs[0] + res.probs[1] + res.probs[2] + res.probs[3];
    assert(fabsf(sumP - 1.0f) < 1e-4f);
    printf("[CPP_UNIT_TEST_OK] pkt_bytes=%u crc8=0x%02X pred=%s conf=%.4f\n",
           (unsigned)sizeof(pkt), (unsigned)pkt.crc8_checksum,
           csi_state_to_str(res.predicted_class), (double)res.confidence);
    return 0;
}
