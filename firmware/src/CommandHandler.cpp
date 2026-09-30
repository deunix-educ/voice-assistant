#include "CommandHandler.h"

#include <ArduinoJson.h>

CommandHandler::CommandHandler(PlaybackStream& playback, MqttLink& mqtt, DemoDevices& devices,
                               ModeCallback onListen, ModeCallback onCapture)
    : _playback(playback), _mqtt(mqtt), _devices(devices), _onListen(onListen), _onCapture(onCapture) {}

void CommandHandler::handle(const uint8_t* payload, size_t length) {
    JsonDocument doc;  // messages de contrôle : quelques dizaines d'octets
    const DeserializationError error = deserializeJson(doc, payload, length);
    if (error) {
        Serial.printf("# control : JSON illisible (%s)\n", error.c_str());
        return;
    }

    const char* event = doc["event"] | "";
    const char* command = doc["cmd"] | "";

    if (strcmp(event, "start") == 0) {
        const char* session = doc["session"] | "?";
        const bool accepted = _playback.open(session, doc["rate"] | 0UL, doc["bits"] | 0UL,
                                             doc["channels"] | 0UL, true);
        if (!accepted) {
            char reply[96];
            snprintf(reply, sizeof(reply), "{\"event\":\"rejected\",\"session\":\"%s\"}", session);
            _mqtt.publish("event", reply, false);
        }
        return;
    }

    if (strcmp(event, "end") == 0) {
        _playback.close(doc["chunks"] | -1L);
        return;
    }

    if (strcmp(command, "ping") == 0) {
        char reply[64];
        snprintf(reply, sizeof(reply), "{\"event\":\"pong\",\"uptime_ms\":%lu}",
                 static_cast<unsigned long>(millis()));
        _mqtt.publish("event", reply, false);
        Serial.println("# control : ping -> pong");
        return;
    }

    if (strcmp(command, "listen") == 0) {
        _onListen(doc["enabled"] | false);
        return;
    }

    if (strcmp(command, "capture") == 0) {
        _onCapture(doc["active"] | false);
        return;
    }

    if (strcmp(command, "device") == 0) {
        handleDevice(doc["device"] | "", doc["action"] | "");
        return;
    }

    const int shown = static_cast<int>(length > 120 ? 120 : length);
    Serial.printf("# control : message non reconnu : %.*s\n", shown,
                  reinterpret_cast<const char*>(payload));
}

void CommandHandler::handleDevice(const char* device, const char* action) {
    const bool ok = _devices.apply(device, action);
    const char* state = _devices.state(device);
    Serial.printf("# domotique : %s %s -> %s (%s)\n", device, action, ok ? "fait" : "REFUSE", state);

    // Confirmation : le serveur sait que la commande a été reçue ET exécutée.
    char reply[128];
    snprintf(reply, sizeof(reply),
             "{\"event\":\"device\",\"device\":\"%.16s\",\"action\":\"%.8s\",\"ok\":%s,\"state\":\"%s\"}",
             device, action, ok ? "true" : "false", state);
    _mqtt.publish("event", reply, false);
}
