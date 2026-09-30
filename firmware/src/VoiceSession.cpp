#include "VoiceSession.h"

#include <esp_random.h>

#include "config.h"

VoiceSession::VoiceSession(MqttLink& mqtt, SerialFramer& framer) : _mqtt(mqtt), _framer(framer) {}

void VoiceSession::start(Transport transport, uint32_t sampleRate) {
    _transport = transport;
    _active = true;
    _chunks = 0;
    _failures = 0;
    _startMs = millis();
    _durationMs = 0;

    // Identifiant court, tiré du générateur matériel : distingue deux sessions
    // successives dans les journaux et les noms de fichiers du serveur.
    snprintf(_id, sizeof(_id), "%06lx", static_cast<unsigned long>(esp_random() & 0xFFFFFF));

    if (_transport == Transport::Serial) {
        _framer.sendStart(sampleRate, 16, 1);
        return;
    }

    char payload[192];
    snprintf(payload, sizeof(payload),
             "{\"event\":\"start\",\"session\":\"%s\",\"rate\":%lu,\"bits\":16,\"channels\":1,"
             "\"codec\":\"pcm_s16le\",\"chunk_ms\":%lu}",
             _id, static_cast<unsigned long>(sampleRate),
             static_cast<unsigned long>(AUDIO_CHUNK_MS));
    _mqtt.publish("event", payload, false);
}

bool VoiceSession::sendChunk(const uint8_t* pcm, size_t bytes) {
    if (!_active) {
        return false;
    }

    bool ok = true;
    if (_transport == Transport::Serial) {
        _framer.sendAudio(reinterpret_cast<const int16_t*>(pcm), bytes / sizeof(int16_t));
    } else {
        ok = _mqtt.publishBinary("audio/in", pcm, bytes);
    }

    if (ok) {
        ++_chunks;
    } else {
        ++_failures;
    }
    return ok;
}

void VoiceSession::end(uint32_t captureOverruns) {
    if (!_active) {
        return;
    }
    _active = false;
    _durationMs = millis() - _startMs;

    if (_transport == Transport::Serial) {
        _framer.sendEnd();
        return;
    }

    char payload[192];
    snprintf(payload, sizeof(payload),
             "{\"event\":\"end\",\"session\":\"%s\",\"chunks\":%lu,\"duration_ms\":%lu,"
             "\"send_failures\":%lu,\"capture_overruns\":%lu}",
             _id, static_cast<unsigned long>(_chunks), static_cast<unsigned long>(_durationMs),
             static_cast<unsigned long>(_failures), static_cast<unsigned long>(captureOverruns));
    _mqtt.publish("event", payload, false);
}

bool VoiceSession::active() const {
    return _active;
}

VoiceSession::Transport VoiceSession::transport() const {
    return _transport;
}

const char* VoiceSession::id() const {
    return _id;
}

uint32_t VoiceSession::chunksSent() const {
    return _chunks;
}

uint32_t VoiceSession::sendFailures() const {
    return _failures;
}

uint32_t VoiceSession::durationMs() const {
    return _durationMs;
}

uint32_t VoiceSession::elapsedMs() const {
    return millis() - _startMs;
}
