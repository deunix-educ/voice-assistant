#pragma once

#include <Arduino.h>

/**
 * @brief Configuration Wi-Fi et MQTT persistée en NVS (Preferences).
 *
 * Au premier démarrage (NVS vide, pas de secrets.h), ou si le bouton PTT est
 * tenu pendant WIFI_CONFIG_HOLD_MS au démarrage, la carte ouvre un point
 * d'accès Wi-Fi (SSID défini par WIFI_CONFIG_AP_SSID dans config.h) et sert
 * un formulaire de configuration sur http://192.168.4.1. Après validation,
 * les identifiants sont sauvegardés en NVS et la carte redémarre.
 *
 * En fonctionnement normal, les identifiants viennent de la NVS. Si la NVS
 * est vide et que secrets.h fournit des valeurs non vides, elles sont utilisées
 * comme repli sans être copiées en NVS.
 *
 * Les accesseurs retournent des pointeurs vers des tampons internes valides
 * pour toute la durée de vie de l'objet : ils peuvent être passés directement
 * aux constructeurs de WifiLink et MqttLink (trick pointeur — les tampons sont
 * remplis par begin() avant que begin() de ces classes ne soit appelé).
 */
class WifiConfig {
public:
    WifiConfig();
    ~WifiConfig();

    /**
     * @brief Charge les identifiants depuis la NVS ou le repli (secrets.h).
     *
     * Si forcePortal vaut true, ou si aucun identifiant valide n'est disponible,
     * lance le portail AP + HTTP et ne revient jamais (l'ESP32 redémarre après
     * sauvegarde).
     *
     * @param forcePortal  true pour forcer le portail (bouton tenu au démarrage).
     * @param fbSsid       Repli (secrets.h) : SSID — chaîne vide si absent.
     * @param fbPassword   Repli : mot de passe Wi-Fi.
     * @param fbMqttHost   Repli : adresse IP du broker.
     * @param fbMqttPort   Repli : port du broker.
     * @param fbMqttUser   Repli : utilisateur MQTT.
     * @param fbMqttPass   Repli : mot de passe MQTT.
     */
    void begin(bool forcePortal,
               const char* fbSsid,     const char* fbPassword,
               const char* fbMqttHost, uint16_t    fbMqttPort,
               const char* fbMqttUser, const char* fbMqttPass);

    const char* ssid()         const { return _ssid; }
    const char* password()     const { return _password; }
    const char* mqttHost()     const { return _mqttHost; }
    uint16_t    mqttPort()     const { return _mqttPort; }
    const char* mqttUser()     const { return _mqttUser; }
    const char* mqttPassword() const { return _mqttPassword; }

private:
    char     _ssid[64]         = {};
    char     _password[64]     = {};
    char     _mqttHost[64]     = {};
    uint16_t _mqttPort         = 1883;
    char     _mqttUser[32]     = {};
    char     _mqttPassword[64] = {};

    class WebServer* _server   = nullptr;

    bool loadFromNvs();
    void saveToNvs(const char* ssid, const char* password,
                   const char* mqttHost, uint16_t mqttPort,
                   const char* mqttUser, const char* mqttPassword);
    void runPortal();

    static WifiConfig* _instance;
    static void        onRoot();
    static void        onSave();
    static void        onNotFound();
};
