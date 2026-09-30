#include "DemoDevices.h"

#include <string.h>

DemoDevices::DemoDevices(int lightPin) : _lightPin(lightPin) {}

void DemoDevices::begin() {
    pinMode(_lightPin, OUTPUT);
    digitalWrite(_lightPin, LOW);
}

bool DemoDevices::apply(const char* device, const char* action) {
    const bool on = strcmp(action, "on") == 0;
    const bool off = strcmp(action, "off") == 0;
    const bool open = strcmp(action, "open") == 0;
    const bool close = strcmp(action, "close") == 0;

    if (strcmp(device, "light") == 0 && (on || off)) {
        _lightOn = on;
        digitalWrite(_lightPin, on ? HIGH : LOW);
        return true;
    }
    if (strcmp(device, "shutter") == 0 && (open || close)) {
        _shutterOpen = open;  // simulé : un moteur de volet se piloterait ici
        return true;
    }
    if (strcmp(device, "door") == 0 && (open || close)) {
        _doorOpen = open;     // simulé : jamais de gâche réelle commandée à la voix (voir README)
        return true;
    }
    return false;
}

const char* DemoDevices::state(const char* device) const {
    if (strcmp(device, "light") == 0) {
        return _lightOn ? "on" : "off";
    }
    if (strcmp(device, "shutter") == 0) {
        return _shutterOpen ? "open" : "closed";
    }
    if (strcmp(device, "door") == 0) {
        return _doorOpen ? "open" : "closed";
    }
    return "?";
}
