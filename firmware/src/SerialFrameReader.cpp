#include "SerialFrameReader.h"

using SerialProtocol::FrameType;

SerialFrameReader::SerialFrameReader(Stream& stream) : _stream(stream) {}

bool SerialFrameReader::poll(Frame& frame) {
    while (_stream.available() > 0) {
        const int value = _stream.read();
        if (value < 0) {
            break;
        }
        const uint8_t byte = static_cast<uint8_t>(value);

        switch (_state) {
        case State::Magic:
            if (byte == SerialProtocol::MAGIC[_magicMatched]) {
                ++_magicMatched;
                if (_magicMatched == sizeof(SerialProtocol::MAGIC)) {
                    _state = State::Header;
                    _headerRead = 0;
                }
            } else {
                // Aucun caractère ne se répète dans le mot magique : il suffit de
                // regarder si l'octet fautif peut commencer un nouveau mot magique.
                _magicMatched = (byte == SerialProtocol::MAGIC[0]) ? 1 : 0;
            }
            break;

        case State::Header: {
            _header[_headerRead++] = byte;
            if (_headerRead < sizeof(_header)) {
                break;
            }
            const uint8_t type = _header[0];
            _length = static_cast<size_t>(_header[1]) | (static_cast<size_t>(_header[2]) << 8);
            const bool knownType = type >= static_cast<uint8_t>(FrameType::Start) &&
                                   type <= static_cast<uint8_t>(FrameType::End);
            if (!knownType || _length > SerialProtocol::MAX_PAYLOAD) {
                ++_rejected;
                restart();
                break;
            }
            _payloadRead = 0;
            _checksum = 0;
            _state = (_length == 0) ? State::Checksum : State::Payload;
            break;
        }

        case State::Payload:
            _payload[_payloadRead++] = byte;
            _checksum ^= byte;
            if (_payloadRead == _length) {
                _state = State::Checksum;
            }
            break;

        case State::Checksum: {
            const bool valid = (byte == _checksum);
            const FrameType type = static_cast<FrameType>(_header[0]);
            restart();
            if (!valid) {
                ++_rejected;
                break;
            }
            frame.type = type;
            frame.payload = _payload;
            frame.length = _length;
            return true;  // on rend la main : le payload doit être consommé avant la suite
        }
        }
    }
    return false;
}

uint32_t SerialFrameReader::rejectedFrames() const {
    return _rejected;
}

void SerialFrameReader::restart() {
    _state = State::Magic;
    _magicMatched = 0;
    _headerRead = 0;
    // _payload et _length sont conservés : la trame qui vient d'être rendue les lit encore.
}
