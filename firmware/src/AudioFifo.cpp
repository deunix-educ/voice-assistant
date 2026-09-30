#include "AudioFifo.h"

bool AudioFifo::begin(uint8_t* storage, size_t capacity, size_t wakeBytes) {
    _handle = xStreamBufferCreateStatic(capacity, wakeBytes, storage, &_control);
    return _handle != nullptr;
}

bool AudioFifo::write(const uint8_t* data, size_t length) {
    if (_handle == nullptr) {
        return false;
    }

    // Tout ou rien : un seul écrivain, donc la place mesurée ne peut que grandir
    // entre ce test et l'écriture (le lecteur ne fait qu'en libérer).
    if (xStreamBufferSpacesAvailable(_handle) < length) {
        _overruns.fetch_add(1);
        return false;
    }

    xStreamBufferSend(_handle, data, length, 0);
    return true;
}

size_t AudioFifo::read(uint8_t* data, size_t length, uint32_t timeoutMs) {
    if (_handle == nullptr) {
        return 0;
    }
    return xStreamBufferReceive(_handle, data, length, pdMS_TO_TICKS(timeoutMs));
}

size_t AudioFifo::available() const {
    return (_handle == nullptr) ? 0 : xStreamBufferBytesAvailable(_handle);
}

uint32_t AudioFifo::overruns() const {
    return _overruns.load();
}
