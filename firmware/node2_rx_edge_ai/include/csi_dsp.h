#ifndef IOT_BTL_CSI_DSP_H
#define IOT_BTL_CSI_DSP_H

/**
 * @file csi_dsp.h
 * @brief Bo xu ly tin hieu so (DSP) thoi gian thuc tren ESP32-S3 cho Node 2 (T04, T06):
 *   1. Trich xuat bien do 52 song mang phu hoat dong tu 128 byte I/Q: |H_k| = sqrt(I^2 + Q^2)
 *   2. Bu tru trôi suy hao nhiet am tu cam bien I2C DHT20 cua Node 1 (T01)
 *   3. Bo loc trung vi truot nhan qua Hampel (cua so 2K+1 = 5, nguong 3 * 1.4826 * MAD)
 *   4. Gop 52 song mang thanh 16 nhom bang tan phu (Subcarrier Group Pooling)
 *   5. Bo loc thong dai IIR Butterworth bac 4 (0.2 - 15.0 Hz @ 100 Hz, 4 khoi Biquad SOS)
 *   6. Giam mau 2x (100 Hz -> 50 Hz) tao tensor X_edge kich thuoc [100][16] cho TinyML 1D-CNN.
 *
 * Thiet ke C++17 thuan khiet (khong phu thuoc Arduino) de bien dich duoc tren ca ESP32-S3
 * lan trinh bien dich `g++` tren PC phuc vu kiem thu don vi (Unit Test).
 */

#include <stdint.h>
#include <stddef.h>
#include <math.h>
#include <string.h>

#include "csi_protocol.h"
#include "model_data.h"

class CsiDspProcessor {
public:
    /**
     * @brief Chuyen mang 128 byte I/Q (int8_t) sang 52 gia tri bien do song mang hoat dong
     */
    static void iqToActiveAmplitudes(const int8_t iqRaw[128], float outAmps52[CSI_ACTIVE_SUBCARRIERS]) {
        for (size_t i = 0; i < CSI_ACTIVE_SUBCARRIERS; ++i) {
            uint8_t sc = CSI_ACTIVE_SC_INDICES[i];
            float re = (float)iqRaw[sc * 2u];
            float im = (float)iqRaw[sc * 2u + 1u];
            outAmps52[i] = sqrtf(re * re + im * im);
        }
    }

    /**
     * @brief Tinh nang luong chuyen dong tong the [0..100] tren cua so 200 khung x 52 song mang
     */
    static float computeMotionEnergy(const float windowAmps[200][CSI_ACTIVE_SUBCARRIERS]) {
        float sumSq = 0.0f;
        for (size_t ch = 0; ch < CSI_ACTIVE_SUBCARRIERS; ++ch) {
            float mean = 0.0f;
            for (size_t t = 0; t < 200; ++t) {
                mean += windowAmps[t][ch];
            }
            mean *= (1.0f / 200.0f);
            for (size_t t = 0; t < 200; ++t) {
                float d = windowAmps[t][ch] - mean;
                sumSq += d * d;
            }
        }
        float rms = sqrtf(sumSq / (200.0f * (float)CSI_ACTIVE_SUBCARRIERS));
        float idx = rms * 8.5f;
        if (idx < 0.0f) idx = 0.0f;
        if (idx > 100.0f) idx = 100.0f;
        return idx;
    }

    /**
     * @brief Bien doi cua so 2.0 giay (200 khung x 52 kenh @ 100 Hz) sang tensor [100][16]
     *        khop chinh xac voi `extract_edge_1dcnn_tensor()` trong Python.
     */
    static void processWindowToEdgeTensor(
        const float rawAmps[200][CSI_ACTIVE_SUBCARRIERS],
        float tempC,
        float humiRh,
        float outTensor[TINYML_WINDOW_LEN][TINYML_NUM_SUBCARRIERS]
    ) {
        // 1. He so hieu chuan nhiet am tu DHT20 (T01)
        float gainCorr = 1.0f + 0.0018f * (tempC - 27.0f) + 0.0012f * (humiRh - 65.0f);
        if (gainCorr < 0.85f) gainCorr = 0.85f;
        if (gainCorr > 1.20f) gainCorr = 1.20f;
        const float invGain = 1.0f / gainCorr;

        // 2. Loc Hampel nhan qua tren cua so 5 mau + Gop 52 -> 16 bang tan phu
        float pooled[200][TINYML_NUM_SUBCARRIERS];
        float ring5[5][CSI_ACTIVE_SUBCARRIERS];

        for (size_t t = 0; t < 200; ++t) {
            size_t slot = t % 5u;
            size_t count = (t < 5u) ? (t + 1u) : 5u;
            float clean52[CSI_ACTIVE_SUBCARRIERS];

            for (size_t ch = 0; ch < CSI_ACTIVE_SUBCARRIERS; ++ch) {
                float val = rawAmps[t][ch] * invGain;
                ring5[slot][ch] = val;

                float patch[5];
                for (size_t k = 0; k < count; ++k) {
                    patch[k] = ring5[k][ch];
                }
                float med = medianSmall(patch, count);

                float absDev[5];
                for (size_t k = 0; k < count; ++k) {
                    absDev[k] = fabsf(patch[k] - med);
                }
                float mad = 1.4826f * medianSmall(absDev, count);
                clean52[ch] = (fabsf(val - med) > 3.0f * (mad + 1e-5f)) ? med : val;
            }

            // Gop 52 song mang -> 16 bang tan (4 bang dau x 4 kenh + 12 bang sau x 3 kenh = 52)
            size_t scIdx = 0;
            for (size_t b = 0; b < TINYML_NUM_SUBCARRIERS; ++b) {
                size_t width = (b < 4u) ? 4u : 3u;
                float acc = 0.0f;
                for (size_t w = 0; w < width; ++w) {
                    acc += clean52[scIdx++];
                }
                pooled[t][b] = acc / (float)width;
            }
        }

        // 3. Khu DC offset (trung binh 16 mau dau) + Loc IIR Butterworth bac 4 (4 khoi Biquad Direct Form II Transposed)
        float bp[200][TINYML_NUM_SUBCARRIERS];
        for (size_t b = 0; b < TINYML_NUM_SUBCARRIERS; ++b) {
            float dc = 0.0f;
            for (size_t t = 0; t < 16u; ++t) {
                dc += pooled[t][b];
            }
            dc *= (1.0f / 16.0f);

            // Trang thai tre z1, z2 cho 4 tang SOS
            float z1[4] = {0.0f, 0.0f, 0.0f, 0.0f};
            float z2[4] = {0.0f, 0.0f, 0.0f, 0.0f};

            for (size_t t = 0; t < 200; ++t) {
                float x = pooled[t][b] - dc;
                for (size_t s = 0; s < 4u; ++s) {
                    const float b0 = CSI_BUTTER_SOS[s * 6u + 0u];
                    const float b1 = CSI_BUTTER_SOS[s * 6u + 1u];
                    const float b2 = CSI_BUTTER_SOS[s * 6u + 2u];
                    // a0 == 1.0f
                    const float a1 = CSI_BUTTER_SOS[s * 6u + 4u];
                    const float a2 = CSI_BUTTER_SOS[s * 6u + 5u];

                    float y = b0 * x + z1[s];
                    z1[s] = b1 * x - a1 * y + z2[s];
                    z2[s] = b2 * x - a2 * y;
                    x = y;
                }
                bp[t][b] = x;
            }
        }

        // 4. Decimation 2x (200 -> 100 mau @ 50 Hz) + Chuan hoa thang do vat ly [-4.0 .. +4.0]
        for (size_t t = 0; t < TINYML_WINDOW_LEN; ++t) {
            for (size_t b = 0; b < TINYML_NUM_SUBCARRIERS; ++b) {
                float dec = 0.5f * (bp[t * 2u][b] + bp[t * 2u + 1u][b]);
                float scaled = dec * (1.0f / 12.0f);
                if (scaled < -4.0f) scaled = -4.0f;
                if (scaled >  4.0f) scaled =  4.0f;
                outTensor[t][b] = scaled;
            }
        }
    }

private:
    static float medianSmall(float arr[5], size_t n) {
        // Insertion sort tren mang <= 5 phan tu (cuc nhanh, khong cap phat dong)
        for (size_t i = 1; i < n; ++i) {
            float key = arr[i];
            int j = (int)i - 1;
            while (j >= 0 && arr[j] > key) {
                arr[j + 1] = arr[j];
                --j;
            }
            arr[j + 1] = key;
        }
        if ((n & 1u) != 0u) {
            return arr[n / 2u];
        }
        return 0.5f * (arr[n / 2u - 1u] + arr[n / 2u]);
    }
};

#endif // IOT_BTL_CSI_DSP_H
