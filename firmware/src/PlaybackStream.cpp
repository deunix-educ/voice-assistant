#include "PlaybackStream.h"

#include <esp_task_wdt.h>

#include "config.h"

PlaybackStream::PlaybackStream(AudioOutput& speaker, AudioFifo& fifo)
    : _speaker(speaker), _fifo(fifo) {}

bool PlaybackStream::begin() {
    return xTaskCreateStaticPinnedToCore(taskEntry, "audio", sizeof(_taskStack), this,
                                         AUDIO_TASK_PRIORITY, _taskStack, &_taskControl,
                                         AUDIO_TASK_CORE) != nullptr;
}

bool PlaybackStream::open(const char* session, uint32_t rate, uint32_t bits, uint32_t channels,
                          bool fromMqtt) {
    if (_open) {
        // Le flux précédent n'est pas fini : le nouveau prend la place.
        Serial.printf("# lecture %s : interrompue par un nouveau flux\n", _report.session);
        finish(_closed);
    }

    if (rate != AUDIO_SAMPLE_RATE || bits != 16 || channels != 1) {
        Serial.printf("# lecture refusee : %lu Hz, %lu bits, %lu canaux ; attendu %lu Hz, 16 bits, mono\n",
                      static_cast<unsigned long>(rate), static_cast<unsigned long>(bits),
                      static_cast<unsigned long>(channels),
                      static_cast<unsigned long>(AUDIO_SAMPLE_RATE));
        return false;
    }

    // L'état d'abord : dès cet instant, la tâche audio ne peut plus compter de famine
    // pour ce flux (elle ne compte qu'en Playing), quoi qu'elle voie ensuite.
    _state.store(State::Priming);  // rien ne sera joué avant d'avoir la réserve

    _report = Report();
    strncpy(_report.session, session, sizeof(_report.session) - 1);
    _report.fromMqtt = fromMqtt;
    _open = true;
    _closed = false;
    _startMs = millis();
    _lastPushMs = _startMs;
    _overrunsAtStart = _fifo.overruns();
    _underruns.store(0);
    _firstSoundMs.store(0);
    _minMarginBytes.store(UINT32_MAX);
    for (auto& gap : _gapAtMs) {
        gap.store(0);
    }
    _endOfStream.store(false);
    Serial.printf("# lecture %s : debut du flux (%s)\n", _report.session, fromMqtt ? "MQTT" : "serie");
    return true;
}

void PlaybackStream::push(const uint8_t* data, size_t length) {
    // Longueur impaire : échantillon coupé en deux, jamais accepté.
    if (!_open || _closed || (length % 2) != 0) {
        return;
    }
    _fifo.write(data, length);
    ++_report.chunks;
    _lastPushMs = millis();
}

void PlaybackStream::close(int32_t announcedChunks) {
    if (!_open || _closed) {
        return;
    }
    _closed = true;
    _report.announced = announcedChunks;
    _report.endReceived = true;
    // Plus rien ne doit arriver : une FIFO qui se vide est désormais normale, et
    // un son plus court que la réserve doit être joué sans attendre davantage.
    _endOfStream.store(true);
}

bool PlaybackStream::takeReport(Report& report) {
    if (!_open) {
        return false;
    }

    const uint32_t silence = millis() - _lastPushMs;
    if (_closed) {
        // Le bilan attend que la FIFO soit vide : la lecture est alors terminée.
        if (_fifo.available() > 0 || silence < DRAIN_SILENCE_MS) {
            return false;
        }
        finish(true);
    } else if (silence >= ABANDON_MS) {
        _endOfStream.store(true);  // la source s'est tue : on joue ce qui reste
        finish(false);
    } else {
        return false;
    }

    report = _report;
    return true;
}

bool PlaybackStream::active() const {
    return _open;
}

void PlaybackStream::finish(bool endReceived) {
    _open = false;
    _report.endReceived = endReceived;
    _report.overruns = _fifo.overruns() - _overrunsAtStart;
    _report.underruns = _underruns.load();
    _report.durationMs = millis() - _startMs;
    const uint32_t firstSound = _firstSoundMs.load();
    _report.startMs = firstSound != 0 ? firstSound - _startMs : 0;
    const uint32_t margin = _minMarginBytes.load();
    _report.minMarginMs = margin == UINT32_MAX
        ? -1 : static_cast<int32_t>(margin / (AUDIO_SAMPLE_RATE / 1000 * sizeof(int16_t)));
    for (size_t i = 0; i < MAX_GAPS; ++i) {
        _report.gapAtMs[i] = _gapAtMs[i].load();
    }
}

void PlaybackStream::taskEntry(void* self) {
    static_cast<PlaybackStream*>(self)->taskLoop();
}

void PlaybackStream::taskLoop() {
    static int16_t block[PLAYBACK_BLOCK_SAMPLES];
    const size_t startBytes = PLAYBACK_START_MS * (AUDIO_SAMPLE_RATE / 1000) * sizeof(int16_t);
    esp_task_wdt_add(nullptr);  // surveillée par le chien de garde (étape 15)

    for (;;) {
        esp_task_wdt_reset();  // un tour dure au plus ~20 ms : attente FIFO ou bloc I2S
        if (_state.load() == State::Priming) {
            // Amorçage : rien n'est joué tant que la réserve n'est pas constituée,
            // sauf si la fin est annoncée (son plus court que la réserve).
            const size_t waiting = _fifo.available();
            if (waiting < startBytes && !(_endOfStream.load() && waiting > 0)) {
                vTaskDelay(pdMS_TO_TICKS(PRIME_POLL_MS));
                continue;
            }
            uint32_t notYet = 0;
            _firstSoundMs.compare_exchange_strong(notYet, millis());  // premier démarrage seulement
            _state.store(State::Playing);
        }
        if (_state.load() == State::Idle) {
            vTaskDelay(pdMS_TO_TICKS(PRIME_POLL_MS));
            continue;
        }

        const size_t bytes = _fifo.read(reinterpret_cast<uint8_t*>(block), sizeof(block), 20);
        const size_t samples = bytes / sizeof(int16_t);
        if (samples > 0) {
            _speaker.write(block, samples);  // bloque : c'est ce qui donne la cadence
        }
        if (_endOfStream.load()) {
            // Fin annoncée : la FIFO se vide normalement jusqu'au dernier échantillon,
            // puis la tâche se met au repos. Si open() a déjà lancé un nouveau flux,
            // l'état n'est plus Playing et l'échange échoue : on ne touche à rien.
            if (samples == 0 && _fifo.available() == 0) {
                State playing = State::Playing;
                _state.compare_exchange_strong(playing, State::Idle);
            }
            continue;
        }

        if (samples < PLAYBACK_BLOCK_SAMPLES) {
            // Réserve épuisée en plein flux : on s'arrête proprement et on réamorce,
            // plutôt que de jouer des bribes hachées au gré des arrivées.
            // Échange atomique : on ne compte que si l'on jouait encore CE flux. Un
            // START arrivé pendant le read() ci-dessus a déjà remis l'état à Priming,
            // et ce n'est alors pas une famine (faux compte trouvé à l'étape 12).
            State playing = State::Playing;
            if (_state.compare_exchange_strong(playing, State::Priming)) {
                const uint32_t index = _underruns.fetch_add(1);
                if (index < MAX_GAPS) {
                    _gapAtMs[index].store(millis() - _firstSoundMs.load());
                }
            }
            continue;
        }
        // Marge restante : c'est le retard réseau qu'on pourrait encore absorber.
        const uint32_t left = static_cast<uint32_t>(_fifo.available());
        if (left < _minMarginBytes.load()) {
            _minMarginBytes.store(left);
        }
    }
}
