#include "AudioOutput.h"

#include <math.h>

AudioOutput::AudioOutput(int pinBclk, int pinLrc, int pinDin)
    : _pinBclk(pinBclk), _pinLrc(pinLrc), _pinDin(pinDin) {}

bool AudioOutput::begin(uint32_t sampleRate, bool philips) {
    // --- 1. Canal I2S1 en émission, l'ESP32 maître d'horloge.
    i2s_chan_config_t chanConfig = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    // 6 descripteurs de 240 trames = 1440 trames = 90 ms de marge : c'est aussi la
    // latence ajoutée par le DMA entre write() et le haut-parleur.
    chanConfig.dma_desc_num = 6;
    chanConfig.dma_frame_num = 240;
    // DMA à court de données : il envoie des zéros (silence) au lieu de répéter
    // en boucle le dernier tampon, ce qui produirait un bourdonnement.
    chanConfig.auto_clear = true;

    if (i2s_new_channel(&chanConfig, &_txHandle, nullptr) != ESP_OK) {
        _txHandle = nullptr;
        return false;
    }

    // --- 2. 16 bits, deux voies, cadrage choisi. Horloge bit : 16000 x 32 = 512 kHz.
    const i2s_std_slot_config_t philipsSlots =
        I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO);
    const i2s_std_slot_config_t msbSlots =
        I2S_STD_MSB_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO);

    i2s_std_config_t stdConfig = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(sampleRate),
        .slot_cfg = philips ? philipsSlots : msbSlots,
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,  // le MAX98357A n'a pas besoin d'horloge maître
            .bclk = static_cast<gpio_num_t>(_pinBclk),
            .ws = static_cast<gpio_num_t>(_pinLrc),
            .dout = static_cast<gpio_num_t>(_pinDin),
            .din = I2S_GPIO_UNUSED,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };

    if (i2s_channel_init_std_mode(_txHandle, &stdConfig) != ESP_OK ||
        i2s_channel_enable(_txHandle) != ESP_OK) {
        i2s_del_channel(_txHandle);
        _txHandle = nullptr;
        return false;
    }

    _sampleRate = sampleRate;
    return true;
}

size_t AudioOutput::write(const int16_t* samples, size_t count) {
    if (_txHandle == nullptr) {
        return 0;
    }

    size_t done = 0;
    while (done < count) {
        size_t frames = count - done;
        if (frames > BLOCK_FRAMES) {
            frames = BLOCK_FRAMES;
        }

        // Mono -> stéréo : le même échantillon sur la voie gauche et la droite.
        for (size_t i = 0; i < frames; ++i) {
            _stereo[i * 2] = samples[done + i];
            _stereo[(i * 2) + 1] = samples[done + i];
        }

        size_t written = 0;
        if (i2s_channel_write(_txHandle, _stereo, frames * 2 * sizeof(int16_t), &written,
                              portMAX_DELAY) != ESP_OK) {
            break;
        }
        done += written / (2 * sizeof(int16_t));
    }
    return done;
}

void AudioOutput::playTone(uint32_t frequencyHz, uint32_t durationMs, int16_t amplitude) {
    if (_txHandle == nullptr || _sampleRate == 0) {
        return;
    }

    const size_t total = (static_cast<size_t>(_sampleRate) * durationMs) / 1000;
    const size_t fade = _sampleRate / 100;  // 10 ms : un démarrage brutal claquerait
    const float step = 2.0F * static_cast<float>(M_PI) * static_cast<float>(frequencyHz) /
                       static_cast<float>(_sampleRate);
    float phase = 0.0F;

    size_t done = 0;
    while (done < total) {
        size_t count = total - done;
        if (count > BLOCK_FRAMES) {
            count = BLOCK_FRAMES;
        }

        for (size_t i = 0; i < count; ++i) {
            const size_t n = done + i;
            float gain = 1.0F;
            if (n < fade) {
                gain = static_cast<float>(n) / static_cast<float>(fade);
            } else if (total - n < fade) {
                gain = static_cast<float>(total - n) / static_cast<float>(fade);
            }
            _tone[i] = static_cast<int16_t>(lroundf(static_cast<float>(amplitude) * gain *
                                                    sinf(phase)));
            phase += step;
            if (phase > 2.0F * static_cast<float>(M_PI)) {
                phase -= 2.0F * static_cast<float>(M_PI);  // évite la perte de précision
            }
        }

        write(_tone, count);
        done += count;
    }
}
