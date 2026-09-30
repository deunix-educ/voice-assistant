#include "MicCapture.h"

#include <driver/gpio.h>

#include "config.h"

MicCapture::MicCapture(int pinSck, int pinWs, int pinSd)
    : _pinSck(pinSck), _pinWs(pinWs), _pinSd(pinSd) {}

bool MicCapture::begin(uint32_t sampleRate, Format format) {
    // --- 1. Création du canal I2S0 en réception, l'ESP32 étant maître d'horloge.
    i2s_chan_config_t chanConfig = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    // 6 descripteurs de 200 trames stéréo de 8 octets = 9600 octets, soit 150 ms
    // de marge à 16 kHz : de quoi absorber les pauses de la boucle principale.
    chanConfig.dma_desc_num = 6;
    chanConfig.dma_frame_num = 200;
    chanConfig.auto_clear = false;

    if (i2s_new_channel(&chanConfig, nullptr, &_rxHandle) != ESP_OK) {
        _rxHandle = nullptr;
        return false;
    }

    // --- 2. Mots de 32 bits, les DEUX moitiés, cadrage choisi par l'appelant.
    const i2s_std_slot_config_t philips =
        I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO);
    const i2s_std_slot_config_t msb =
        I2S_STD_MSB_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO);

    i2s_std_config_t stdConfig = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(sampleRate),
        .slot_cfg = (format == Format::Philips) ? philips : msb,
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = static_cast<gpio_num_t>(_pinSck),
            .ws = static_cast<gpio_num_t>(_pinWs),
            .dout = I2S_GPIO_UNUSED,
            .din = static_cast<gpio_num_t>(_pinSd),
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };

    if (i2s_channel_init_std_mode(_rxHandle, &stdConfig) != ESP_OK) {
        end();
        return false;
    }

    // --- 3. Pull-down sur la ligne de données, exigé par le datasheet INMP441 :
    // le micro met sa sortie en haute impédance hors de sa moitié de trame et
    // après ses 24 bits utiles. Sans résistance, la ligne flotte et l'on lit
    // n'importe quoi pendant ces instants.
    gpio_set_pull_mode(static_cast<gpio_num_t>(_pinSd),
                       MIC_SD_PULLDOWN ? GPIO_PULLDOWN_ONLY : GPIO_FLOATING);

    if (i2s_channel_enable(_rxHandle) != ESP_OK) {
        end();
        return false;
    }

    _sampleRate = sampleRate;
    _format = format;
    _half = Half::First;
    _phase = 0;  // le canal démarre sur un début de trame : la phase est ancrée ici
    _lowBand = 0;
    resetStats();

    // Le micro vient d'être réveillé par ses horloges : on laisse passer son
    // transitoire. À partir d'ici, plus rien n'arrête les horloges.
    settle(MIC_SETTLE_MS);
    flush();
    return true;
}

void MicCapture::end() {
    if (_rxHandle == nullptr) {
        return;
    }

    i2s_channel_disable(_rxHandle);
    i2s_del_channel(_rxHandle);
    _rxHandle = nullptr;
    _sampleRate = 0;
}

void MicCapture::useHalf(Half half) {
    _half = half;
    flush();  // réamorce le filtre de continu sur la nouvelle moitié
}

MicCapture::Half MicCapture::half() const {
    return _half;
}

uint32_t MicCapture::sampleRate() const {
    return _sampleRate;
}

MicCapture::Format MicCapture::format() const {
    return _format;
}

size_t MicCapture::read(int16_t* buffer, size_t count) {
    if (_rxHandle == nullptr) {
        return 0;
    }

    // Une trame vaut deux mots. Le pilote rend ce qui est disponible, pas
    // forcément un nombre pair de mots : la phase est donc mémorisée entre les
    // appels, sinon les deux moitiés s'échangent et l'on enregistre la muette.
    const uint8_t wantedPhase = (_half == Half::First) ? 0 : 1;
    size_t produced = 0;

    while (produced < count) {
        size_t framesWanted = count - produced;
        if (framesWanted > SCRATCH_WORDS / 2) {
            framesWanted = SCRATCH_WORDS / 2;
        }

        size_t bytesRead = 0;
        const esp_err_t err = i2s_channel_read(_rxHandle, _scratch,
                                               framesWanted * 2 * sizeof(int32_t), &bytesRead,
                                               pdMS_TO_TICKS(200));

        // Les mots reçus sont traités même si la lecture a été interrompue :
        // les ignorer décalerait la phase pour tout le reste de la session.
        const size_t words = bytesRead / sizeof(int32_t);
        for (size_t i = 0; i < words; ++i) {
            const bool selected = (_phase == wantedPhase);
            _phase ^= 1;  // toujours avancer, même pour un mot ignoré

            if (!selected || produced >= count) {
                continue;
            }

            const int32_t word = _scratch[i];
            accumulate(_stats, word);

            // Les 24 bits utiles occupent les bits 31..8 : on travaille en 24 bits
            // jusqu'au bout, et l'on ne réduit à 16 bits qu'à la toute fin.
            const int32_t sample24 = word >> 8;

            // Passe-haut du premier ordre à 80 Hz : _lowBand suit tout ce qui est
            // plus lent (composante continue, grondement), on le retire.
            _lowBand += (sample24 - _lowBand) >> MIC_HPF_SHIFT;
            const int32_t filtered24 = sample24 - _lowBand;

            const int32_t magnitude = (filtered24 < 0) ? -filtered24 : filtered24;
            if (magnitude > _stats.filteredPeak) {
                _stats.filteredPeak = magnitude;
            }

            // Gain puis réduction 24 -> 16 bits, avec arrondi. |filtered24| < 2^25
            // et MIC_GAIN <= 64 : le produit tient dans un int32.
            int32_t out = (filtered24 * MIC_GAIN + 128) >> 8;

            // Écrêtage explicite : sans lui, un dépassement produirait un craquement.
            if (out > 32767) {
                out = 32767;
            } else if (out < -32768) {
                out = -32768;
            }

            buffer[produced] = static_cast<int16_t>(out);
            ++produced;
        }

        if (err != ESP_OK || bytesRead == 0) {
            break;
        }
    }

    return produced;
}

MicCapture::FrameStats MicCapture::probe(uint32_t durationMs) {
    FrameStats frames;
    if (_rxHandle == nullptr || _sampleRate == 0) {
        return frames;
    }

    const size_t target = (static_cast<size_t>(_sampleRate) * durationMs) / 1000;
    while (frames.first.wordCount < target) {
        size_t bytesRead = 0;
        const esp_err_t err = i2s_channel_read(_rxHandle, _scratch, sizeof(_scratch),
                                               &bytesRead, pdMS_TO_TICKS(200));

        const size_t words = bytesRead / sizeof(int32_t);
        for (size_t i = 0; i < words; ++i) {
            accumulate((_phase == 0) ? frames.first : frames.second, _scratch[i]);
            _phase ^= 1;
        }

        if (err != ESP_OK || bytesRead == 0) {
            break;
        }
    }

    return frames;
}

size_t MicCapture::readRaw(int32_t* words, size_t count) {
    if (_rxHandle == nullptr) {
        return 0;
    }

    // On ne rend que des trames entières : si le prochain mot est une 2e moitié,
    // on le jette pour que words[0] soit toujours une 1re moitié.
    if (_phase != 0) {
        size_t bytesRead = 0;
        i2s_channel_read(_rxHandle, _scratch, sizeof(int32_t), &bytesRead, pdMS_TO_TICKS(200));
        _phase ^= static_cast<uint8_t>((bytesRead / sizeof(int32_t)) & 1U);
        if (_phase != 0) {
            return 0;  // lecture impossible : on ne rend rien plutôt que du désaligné
        }
    }

    size_t copied = 0;
    while (copied < count) {
        size_t wanted = count - copied;
        if (wanted > SCRATCH_WORDS) {
            wanted = SCRATCH_WORDS;
        }

        size_t bytesRead = 0;
        const esp_err_t err = i2s_channel_read(_rxHandle, _scratch, wanted * sizeof(int32_t),
                                               &bytesRead, pdMS_TO_TICKS(200));

        const size_t got = bytesRead / sizeof(int32_t);
        for (size_t i = 0; i < got; ++i) {
            words[copied + i] = _scratch[i];
            _phase ^= 1;
        }
        copied += got;

        if (err != ESP_OK || bytesRead == 0) {
            break;
        }
    }

    return copied;
}

MicCapture::DataLineReport MicCapture::probeDataLine() {
    DataLineReport report;
    report.samples = 4000;  // environ 1 ms, soit une vingtaine de trames à 16 kHz

    const gpio_num_t pin = static_cast<gpio_num_t>(_pinSd);

    // gpio_get_level() lit l'état réel de la broche, même quand elle est routée
    // vers le périphérique I2S : les horloges peuvent donc continuer de tourner.
    gpio_set_pull_mode(pin, GPIO_PULLUP_ONLY);
    delayMicroseconds(200);  // laisse la résistance charger la capacité de la ligne
    for (uint32_t i = 0; i < report.samples; ++i) {
        report.onesWithPullUp += static_cast<uint32_t>(gpio_get_level(pin));
    }

    gpio_set_pull_mode(pin, GPIO_PULLDOWN_ONLY);
    delayMicroseconds(200);
    for (uint32_t i = 0; i < report.samples; ++i) {
        report.onesWithPullDown += static_cast<uint32_t>(gpio_get_level(pin));
    }

    // Retour à la configuration de service.
    gpio_set_pull_mode(pin, MIC_SD_PULLDOWN ? GPIO_PULLDOWN_ONLY : GPIO_FLOATING);
    return report;
}

void MicCapture::accumulate(RawStats& stats, int32_t word) {
    if (stats.wordCount == 0) {
        stats.minWord = word;
        stats.maxWord = word;
    } else if (word < stats.minWord) {
        stats.minWord = word;
    } else if (word > stats.maxWord) {
        stats.maxWord = word;
    }

    if (word == 0) {
        ++stats.zeroCount;
    }

    // Valeur efficace : (mot >> 12) tient sur 20 bits, son carré sur 40 : un
    // int64 ne déborde qu'après des millions de mots, bien au-delà d'une session.
    const int64_t reduced = static_cast<int64_t>(word >> 12);
    stats.sum += reduced;
    stats.sumSquares += reduced * reduced;

    // Le bit le plus bas jamais mis à 1 dit où s'arrêtent les données : 24 bits
    // bien cadrés s'arrêtent au bit 8. Ne dépend pas de l'amplitude du signal.
    stats.orBits |= static_cast<uint32_t>(word);

    ++stats.wordCount;
}

void MicCapture::resetStats() {
    _stats = RawStats();
}

MicCapture::RawStats MicCapture::stats() const {
    return _stats;
}

void MicCapture::flush() {
    if (_rxHandle == nullptr) {
        return;
    }

    // On vide le DMA par lecture, SANS arrêter le canal : arrêter les horloges
    // endormirait le micro, et son réveil produirait une dérive lente de la
    // composante continue, audible comme un « boum » en début de session.
    // La phase est suivie mot par mot, l'alignement est donc conservé.
    //
    // Au passage, on mesure la composante continue de la moitié retenue pour
    // amorcer le passe-haut : la session démarre sans transitoire.
    const uint8_t wantedPhase = (_half == Half::First) ? 0 : 1;
    int64_t sum = 0;
    size_t count = 0;
    size_t bytesRead = 0;

    do {
        bytesRead = 0;
        i2s_channel_read(_rxHandle, _scratch, sizeof(_scratch), &bytesRead, 0);

        const size_t words = bytesRead / sizeof(int32_t);
        for (size_t i = 0; i < words; ++i) {
            if (_phase == wantedPhase) {
                sum += _scratch[i] >> 8;  // en LSB 24 bits, comme le filtre
                ++count;
            }
            _phase ^= 1;
        }
    } while (bytesRead > 0);

    if (count > 0) {
        _lowBand = static_cast<int32_t>(sum / static_cast<int64_t>(count));
    }
}

void MicCapture::settle(uint32_t durationMs) {
    if (_rxHandle == nullptr) {
        return;
    }

    const uint32_t deadline = millis() + durationMs;
    while (static_cast<int32_t>(deadline - millis()) > 0) {
        size_t bytesRead = 0;
        const esp_err_t err = i2s_channel_read(_rxHandle, _scratch, sizeof(_scratch),
                                               &bytesRead, pdMS_TO_TICKS(50));

        // Mots jetés, mais comptés : un nombre impair inverse la phase.
        _phase ^= static_cast<uint8_t>((bytesRead / sizeof(int32_t)) & 1U);

        if (err != ESP_OK) {
            break;
        }
    }
}
