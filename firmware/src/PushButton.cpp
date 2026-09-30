#include "PushButton.h"

PushButton::PushButton(int pin, uint32_t debounceMs)
    : _pin(pin), _debounceMs(debounceMs) {}

void PushButton::begin() {
    pinMode(_pin, INPUT_PULLUP);
    // Niveau bas = appuyé, car l'autre borne du bouton est reliée à GND.
    _lastRawState = (digitalRead(_pin) == LOW);
    _stableState = _lastRawState;
    _lastChangeMs = millis();
}

void PushButton::update() {
    const bool raw = (digitalRead(_pin) == LOW);
    const uint32_t now = millis();

    if (raw != _lastRawState) {
        _lastRawState = raw;
        _lastChangeMs = now;
        return;
    }

    // L'état brut est resté identique assez longtemps : on le valide.
    if (raw != _stableState && (now - _lastChangeMs) >= _debounceMs) {
        _stableState = raw;
        if (raw) {
            _pressedEvent = true;
        } else {
            _releasedEvent = true;
        }
    }
}

bool PushButton::isDown() const {
    return _stableState;
}

bool PushButton::wasPressed() {
    const bool event = _pressedEvent;
    _pressedEvent = false;
    return event;
}

bool PushButton::wasReleased() {
    const bool event = _releasedEvent;
    _releasedEvent = false;
    return event;
}

void PushButton::clearEvents() {
    _pressedEvent = false;
    _releasedEvent = false;
}
