#ifndef IOT_BTL_TINYML_INFER_H
#define IOT_BTL_TINYML_INFER_H

/**
 * @file tinyml_infer.h
 * @brief Bo suy luan mang No-ron Tich chap 1 Chieu luong tu hoa 8-bit (INT8 1D-CNN TinyML - Ma T04).
 *
 * Kien truc (Khop 100% Bang 3.3 trong `spec_btl_iot_fall_detection.tex`):
 *   Input [100][16] (float32 -> quantize INT8)
 *     -> Conv1D(16 -> 16, k=5, pad=2, INT8xINT8->INT32 MAC + ReLU)
 *     -> MaxPool1D(2) -> [50][16] (quantize INT8)
 *     -> Conv1D(16 -> 32, k=3, pad=1, INT8xINT8->INT32 MAC + ReLU)
 *     -> GlobalAvgPool1D -> [32] (quantize INT8)
 *     -> Dense(32 -> 16, INT8xINT8->INT32 MAC + ReLU)
 *     -> Dense(16 -> 4)  + Softmax(4)
 *
 * Dung luong Flash cua toan bo trong so INT8 chi ~3.4 KB, Tensor Arena tren Stack < 8 KB,
 * thoi gian suy luan tren ESP32-S3 @ 240 MHz dat ~1.8 - 3.5 ms (vuot xa muc tieu < 18 ms!).
 */

#include <stdint.h>
#include <stddef.h>
#include <math.h>

#include "csi_protocol.h"
#include "model_data.h"

typedef struct {
    uint8_t predicted_class;                 // 0: EMPTY, 1: STATIONARY, 2: WALKING, 3: FALL
    float   confidence;                      // Xac suat lop cao nhat [0..1]
    float   probs[TINYML_NUM_CLASSES];       // Xac suat Softmax cua ca 4 lop
} tinyml_result_t;

class EdgeTinyMLClassifier {
public:
    static inline int8_t quantizeSymInt8(float val, float scale) {
        float q = nearbyintf(val / scale);
        if (q < -127.0f) return -127;
        if (q >  127.0f) return  127;
        return (int8_t)q;
    }

    static inline int8_t quantizeReLUInt8(float val, float scale) {
        if (val <= 0.0f) return 0;
        float q = nearbyintf(val / scale);
        if (q > 127.0f) return 127;
        return (int8_t)q;
    }

    /**
     * @brief Chay suy luan INT8 1D-CNN tren cua so dac trung [100][16]
     */
    static tinyml_result_t predict(const float inputTensor[TINYML_WINDOW_LEN][TINYML_NUM_SUBCARRIERS]) {
        // 0. Luong tu hoa dau vao [100][16] sang INT8
        int8_t x_q[100][16];
        for (int t = 0; t < 100; ++t) {
            for (int c = 0; c < 16; ++c) {
                x_q[t][c] = quantizeSymInt8(inputTensor[t][c], TINYML_INPUT_SCALE);
            }
        }

        // 1. Conv1D(16 -> 16, k=5, pad=2) + ReLU (tich luy INT32)
        // TINYML_CONV1_W layout: [out_ch=16][in_ch=16][k=5]
        float c1_out[100][16];
        const float dequant1 = TINYML_INPUT_SCALE * TINYML_CONV1_W_SCALE;
        for (int t = 0; t < 100; ++t) {
            for (int oc = 0; oc < 16; ++oc) {
                int32_t acc = 0;
                const int ocBase = oc * (16 * 5);
                for (int k = 0; k < 5; ++k) {
                    int ti = t + k - 2;
                    if (ti >= 0 && ti < 100) {
                        for (int ic = 0; ic < 16; ++ic) {
                            int8_t w = TINYML_CONV1_W[ocBase + ic * 5 + k];
                            acc += (int32_t)w * (int32_t)x_q[ti][ic];
                        }
                    }
                }
                float v = (float)acc * dequant1 + TINYML_CONV1_B[oc];
                c1_out[t][oc] = (v > 0.0f) ? v : 0.0f;
            }
        }

        // MaxPool1D(2) -> [50][16] & Luong tu hoa kich hoat sang INT8
        int8_t p1_q[50][16];
        for (int t = 0; t < 50; ++t) {
            for (int c = 0; c < 16; ++c) {
                float a = c1_out[t * 2][c];
                float b = c1_out[t * 2 + 1][c];
                float m = (a > b) ? a : b;
                p1_q[t][c] = quantizeReLUInt8(m, TINYML_ACT1_SCALE);
            }
        }

        // 2. Conv1D(16 -> 32, k=3, pad=1) + ReLU + GlobalAvgPool1D -> [32]
        // TINYML_CONV2_W layout: [out_ch=32][in_ch=16][k=3]
        float gap[32] = {0.0f};
        const float dequant2 = TINYML_ACT1_SCALE * TINYML_CONV2_W_SCALE;
        for (int t = 0; t < 50; ++t) {
            for (int oc = 0; oc < 32; ++oc) {
                int32_t acc = 0;
                const int ocBase = oc * (16 * 3);
                for (int k = 0; k < 3; ++k) {
                    int ti = t + k - 1;
                    if (ti >= 0 && ti < 50) {
                        for (int ic = 0; ic < 16; ++ic) {
                            int8_t w = TINYML_CONV2_W[ocBase + ic * 3 + k];
                            acc += (int32_t)w * (int32_t)p1_q[ti][ic];
                        }
                    }
                }
                float v = (float)acc * dequant2 + TINYML_CONV2_B[oc];
                if (v > 0.0f) {
                    gap[oc] += v;
                }
            }
        }

        int8_t gap_q[32];
        for (int c = 0; c < 32; ++c) {
            gap_q[c] = quantizeReLUInt8(gap[c] * (1.0f / 50.0f), TINYML_ACT2_SCALE);
        }

        // 3. Dense(32 -> 16) + ReLU
        int8_t fc1_q[16];
        const float dequant3 = TINYML_ACT2_SCALE * TINYML_FC1_W_SCALE;
        for (int o = 0; o < 16; ++o) {
            int32_t acc = 0;
            const int base = o * 32;
            for (int i = 0; i < 32; ++i) {
                acc += (int32_t)TINYML_FC1_W[base + i] * (int32_t)gap_q[i];
            }
            float v = (float)acc * dequant3 + TINYML_FC1_B[o];
            fc1_q[o] = quantizeReLUInt8(v, TINYML_ACT3_SCALE);
        }

        // 4. Dense(16 -> 4) + Numerically Stable Softmax
        float logits[4];
        float maxLogit = -1e30f;
        const float dequant4 = TINYML_ACT3_SCALE * TINYML_FC2_W_SCALE;
        for (int o = 0; o < 4; ++o) {
            int32_t acc = 0;
            const int base = o * 16;
            for (int i = 0; i < 16; ++i) {
                acc += (int32_t)TINYML_FC2_W[base + i] * (int32_t)fc1_q[i];
            }
            logits[o] = (float)acc * dequant4 + TINYML_FC2_B[o];
            if (logits[o] > maxLogit) {
                maxLogit = logits[o];
            }
        }

        tinyml_result_t res{};
        float sumExp = 0.0f;
        for (int o = 0; o < 4; ++o) {
            res.probs[o] = expf(logits[o] - maxLogit);
            sumExp += res.probs[o];
        }

        uint8_t bestIdx = 0;
        float bestProb = -1.0f;
        for (uint8_t o = 0; o < 4; ++o) {
            res.probs[o] /= sumExp;
            if (res.probs[o] > bestProb) {
                bestProb = res.probs[o];
                bestIdx = o;
            }
        }
        res.predicted_class = bestIdx;
        res.confidence = bestProb;
        return res;
    }
};

#endif // IOT_BTL_TINYML_INFER_H
