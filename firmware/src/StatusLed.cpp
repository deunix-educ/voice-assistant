#include "StatusLed.h"

StatusLed::StatusLed(int pin) : _pin(pin) {}

void StatusLed::begin() {
    pinMode(_pin, OUTPUT);
    write(false);
}

void StatusLed::on() {
    _blinking = false;
    write(true);
}

void StatusLed::off() {
    _blinking = false;
    write(false);
}

void StatusLed::blink(uint32_t periodMs, uint32_t onMs) {
    const uint32_t lit = onMs == 0 ? periodMs / 2 : onMs;
    if (_blinking && _periodMs == periodMs && _onMs == lit) {
        return;  // déjà dans ce mode : ne pas réinitialiser la phase
    }
    _blinking = true;
    _periodMs = periodMs;
    _onMs = lit;
    _lastToggleMs = millis();
}

void StatusLed::update() {
    if (!_blinking) {
        return;
    }

    const uint32_t now = millis();
    const uint32_t phase = _state ? _onMs : (_periodMs - _onMs);  // durée de l'état courant
    if ((now - _lastToggleMs) >= phase) {
        _lastToggleMs = now;
        write(!_state);
    }
}

void StatusLed::write(bool state) {
    _state = state;
    digitalWrite(_pin, state ? HIGH : LOW);
}
