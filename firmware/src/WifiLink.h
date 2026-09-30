#pragma once

#include <Arduino.h>
#include <WiFi.h>

/**
 * @brief Connexion Wi-Fi en mode station, non bloquante, reconnexion automatique.
 *
 * begin() lance la connexion et rend la main aussitôt : l'audio ne doit jamais
 * attendre le réseau. update() signale les changements d'état et, si le réseau
 * reste absent plus de WIFI_RESTART_MS, relance la connexion de zéro (étape 15).
 */
class WifiLink {
public:
    /**
     * @param ssid     Nom du réseau (2,4 GHz).
     * @param password Mot de passe.
     * @param hostname Nom de la carte sur le réseau local.
     */
    WifiLink(const char* ssid, const char* password, const char* hostname);

    /// Lance la connexion, sans attendre.
    void begin();

    /// À appeler à chaque tour de boucle : journalise connexion et perte, relance si besoin.
    void update();

    /// true si l'ESP32 a une adresse IP.
    bool connected() const;

    /// Puissance du signal reçu, en dBm (-50 excellent, -80 limite).
    int rssi() const;

    /// Adresse IP courante, ou 0.0.0.0.
    IPAddress ip() const;

private:
    const char* _ssid;
    const char* _password;
    const char* _hostname;
    bool _wasConnected = false;
    uint32_t _lostSinceMs = 0;  ///< début de la coupure en cours, pour la journaliser
    uint32_t _retrySinceMs = 0; ///< dernière relance de la connexion
};
