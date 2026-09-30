#include "WifiLink.h"

#include "config.h"

WifiLink::WifiLink(const char* ssid, const char* password, const char* hostname)
    : _ssid(ssid), _password(password), _hostname(hostname) {}

void WifiLink::begin() {
    WiFi.mode(WIFI_STA);
    WiFi.setHostname(_hostname);
    // Économie d'énergie coupée : le modem endormi ajoute des retards de 100 ms
    // et plus sur la réception. Inacceptable pour un flux audio (étapes 4 et 5).
    WiFi.setSleep(false);
    WiFi.setAutoReconnect(true);
    WiFi.begin(_ssid, _password);
    _lostSinceMs = millis();
    _retrySinceMs = _lostSinceMs;
}

void WifiLink::update() {
    const bool now = connected();
    if (!now && (millis() - _retrySinceMs) >= WIFI_RESTART_MS) {
        // Filet de sécurité : la reconnexion automatique n'a rien donné, on repart de zéro.
        Serial.printf("# wifi : toujours absent apres %lu s, nouvelle connexion\n",
                      static_cast<unsigned long>((millis() - _lostSinceMs) / 1000));
        WiFi.disconnect();
        WiFi.begin(_ssid, _password);
        _retrySinceMs = millis();
    }
    if (now == _wasConnected) {
        return;
    }
    _wasConnected = now;

    if (now) {
        Serial.printf("# wifi : connecte en %lu ms, IP %s, signal %d dBm\n",
                      static_cast<unsigned long>(millis() - _lostSinceMs),
                      WiFi.localIP().toString().c_str(), WiFi.RSSI());
    } else {
        _lostSinceMs = millis();
        _retrySinceMs = _lostSinceMs;
        Serial.println("# wifi : connexion perdue, reconnexion automatique...");
    }
}

bool WifiLink::connected() const {
    return WiFi.status() == WL_CONNECTED;
}

int WifiLink::rssi() const {
    return connected() ? WiFi.RSSI() : 0;
}

IPAddress WifiLink::ip() const {
    return connected() ? WiFi.localIP() : IPAddress();
}
