#ifndef IOT_BTL_CSI_DSP_H
#define IOT_BTL_CSI_DSP_H

/**
 * @file csi_dsp.h
 * @brief Real-time Digital Signal Processing (DSP) pipeline on ESP32-S3 for Node 2 (T04, T06):
 *   1. Extract 52 active OFDM subcarrier amplitudes from 128-byte I/Q: |H_k| = sqrt(I^2 + Q^2)
 *   2. Compensate temperature/humidity RF drift using Node 1's I2C DHT20 telemetry (T01)
 *   3. Causal Hampel sliding-median outlier filter (window 2K+1 = 5, threshold 3 * 1.4826 * MAD)
 *   4. Subcarrier group pooling from 52 active channels down to 16 sub-bands
 *   5. 4th-order Butterworth IIR bandpass filter (0.2 - 15.0 Hz @ 100 Hz, 4 Biquad SOS stages)
 *   6. 2x decimation (100 Hz -> 50 Hz) producing tensor X_edge [100][16] for TinyML 1D-CNN.
 *
 * Pure C++17 header (zero Arduino dependencies) so it compiles identically on ESP32-S3
 * and host `g++` for bit-exact unit testing.
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
     * @brief Convert raw 128-byte I/Q array (int8_t) into 52 active subcarrier amplitudes
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
     * @brief Compute aggregate motion energy index [0..100] over a 200-frame x 52-subcarrier window
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
     * @brief Transform a 2.0-second window (200 frames x 52 channels @ 100 Hz) into [100][16] tensor
     *        matching `extract_edge_1dcnn_tensor()` in Python.
     */
    static void processWindowToEdgeTensor(
        const float rawAmps[200][CSI_ACTIVE_SUBCARRIERS],
        float tempC,
        float humiRh,
        float outTensor[TINYML_WINDOW_LEN][TINYML_NUM_SUBCARRIERS]
    ) {
        // 1. Environmental drift compensation gain factor from DHT20 (T01)
        float gainCorr = 1.0f + 0.0018f * (tempC - 27.0f) + 0.0012f * (humiRh - 65.0f);
        if (gainCorr < 0.85f) gainCorr = 0.85f;
        if (gainCorr > 1.20f) gainCorr = 1.20f;
        const float invGain = 1.0f / gainCorr;

        // 2. Causal 5-sample Hampel outlier filter + 52 -> 16 subcarrier group pooling
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

            // Pool 52 subcarriers -> 16 sub-bands (first 4 bands x 4 ch + last 12 bands x 3 ch = 52)
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

        // 3. Remove DC offset (first 16-sample mean) + 4th-order Butterworth IIR (4 Biquad DF-II Transposed stages)
        float bp[200][TINYML_NUM_SUBCARRIERS];
        for (size_t b = 0; b < TINYML_NUM_SUBCARRIERS; ++b) {
            float dc = 0.0f;
            for (size_t t = 0; t < 16u; ++t) {
                dc += pooled[t][b];
            }
            dc *= (1.0f / 16.0f);

            // Delay states z1, z2 for 4 SOS sections
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

        // 4. 2x decimation (200 -> 100 samples @ 50 Hz) + Physical scale normalization [-4.0 .. +4.0]
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
        // Insertion sort for <= 5 elements (fast, zero dynamic allocation)
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
