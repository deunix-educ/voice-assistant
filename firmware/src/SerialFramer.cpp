#include "SerialFramer.h"

SerialFramer::SerialFramer(Stream& stream) : _stream(stream) {}

void SerialFramer::sendStart(uint32_t sampleRate, uint16_t bits, uint16_t channels) {
    // Descripteur de 8 octets : fréquence (4), bits (2), canaux (2), en little-endian.
    uint8_t payload[8];
    payload[0] = static_cast<uint8_t>(sampleRate & 0xFF);
    payload[1] = static_cast<uint8_t>((sampleRate >> 8) & 0xFF);
    payload[2] = static_cast<uint8_t>((sampleRate >> 16) & 0xFF);
    payload[3] = static_cast<uint8_t>((sampleRate >> 24) & 0xFF);
    payload[4] = static_cast<uint8_t>(bits & 0xFF);
    payload[5] = static_cast<uint8_t>((bits >> 8) & 0xFF);
    payload[6] = static_cast<uint8_t>(channels & 0xFF);
    payload[7] = static_cast<uint8_t>((channels >> 8) & 0xFF);

    sendFrame(FrameType::Start, payload, sizeof(payload));
}

void SerialFramer::sendAudio(const int16_t* pcm, size_t count) {
    // L'ESP32 est little-endian : les int16 sont déjà au bon format en mémoire.
    sendFrame(FrameType::Audio, reinterpret_cast<const uint8_t*>(pcm), count * sizeof(int16_t));
}

void SerialFramer::sendEnd() {
    sendFrame(FrameType::End, nullptr, 0);
}

void SerialFramer::sendFrame(FrameType type, const uint8_t* payload, size_t length) {
    uint8_t header[SerialProtocol::HEADER_SIZE];
    memcpy(header, SerialProtocol::MAGIC, sizeof(SerialProtocol::MAGIC));
    header[4] = static_cast<uint8_t>(type);
    header[5] = static_cast<uint8_t>(length & 0xFF);
    header[6] = static_cast<uint8_t>((length >> 8) & 0xFF);

    uint8_t checksum = 0;
    for (size_t i = 0; i < length; ++i) {
        checksum ^= payload[i];
    }

    _stream.write(header, sizeof(header));
    if (length > 0) {
        _stream.write(payload, length);
    }
    _stream.write(&checksum, 1);
}
