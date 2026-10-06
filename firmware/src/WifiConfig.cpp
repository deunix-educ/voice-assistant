#include "WifiConfig.h"
#include "config.h"

#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>

// ------------------------------------------------------------------ NVS keys
namespace {

constexpr const char* NVS_NS   = "voice-cfg";
constexpr const char* KEY_SSID = "ssid";
constexpr const char* KEY_PASS = "pass";
constexpr const char* KEY_HOST = "mqttHost";
constexpr const char* KEY_PORT = "mqttPort";
constexpr const char* KEY_USER = "mqttUser";
constexpr const char* KEY_MPWD = "mqttPass";

// ------------------------------------------------------------------ HTML
// Encodage UTF-8 inline ; servi avec charset=utf-8.
const char HTML_FORM[] =
    "<!DOCTYPE html><html lang='fr'><head>"
    "<meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>Assistant vocal &#8212; configuration</title>"
    "<style>"
    "body{font-family:sans-serif;max-width:420px;margin:32px auto;padding:0 16px;color:#222}"
    "h2{color:#0078d4;margin-bottom:4px}"
    "p.s{color:#666;font-size:.9em;margin:0 0 20px}"
    "fieldset{border:1px solid #ccc;border-radius:6px;padding:12px;margin-bottom:16px}"
    "legend{font-weight:bold;padding:0 6px}"
    "label{display:block;margin-top:10px;font-size:.9em;color:#444}"
    "input{width:100%;padding:7px 8px;box-sizing:border-box;border:1px solid #bbb;"
           "border-radius:4px;margin-top:3px;font-size:1em}"
    "button{width:100%;padding:12px;background:#0078d4;color:#fff;border:none;"
            "border-radius:6px;font-size:1em;cursor:pointer}"
    "button:hover{background:#005fa3}"
    "</style></head><body>"
    "<h2>Assistant vocal</h2>"
    "<p class='s'>Renseignez le r&eacute;seau Wi-Fi et l&rsquo;adresse du broker MQTT,"
    " puis cliquez sur <em>Enregistrer</em>.</p>"
    "<form method='POST' action='/save'>"
    "<fieldset><legend>Wi-Fi</legend>"
    "<label>R&eacute;seau (SSID)</label>"
    "<input type='text' name='ssid' value='%SSID%' maxlength='63' required>"
    "<label>Mot de passe</label>"
    "<input type='password' name='pass' placeholder='(vide si r&eacute;seau ouvert)' maxlength='63'>"
    "</fieldset>"
    "<fieldset><legend>Broker MQTT</legend>"
    "<label>Adresse IP</label>"
    "<input type='text' name='mqttHost' value='%HOST%' placeholder='192.168.1.x' maxlength='63' required>"
    "<label>Port</label>"
    "<input type='number' name='mqttPort' value='%PORT%' min='1' max='65535' required>"
    "<label>Utilisateur (vide = connexion anonyme)</label>"
    "<input type='text' name='mqttUser' value='%USER%' maxlength='31'>"
    "<label>Mot de passe MQTT</label>"
    "<input type='password' name='mqttPass' placeholder='(vide si anonyme)' maxlength='63'>"
    "</fieldset>"
    "<button type='submit'>Enregistrer et red&eacute;marrer</button>"
    "</form></body></html>";

const char HTML_OK[] =
    "<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'>"
    "<title>Sauvegard&eacute;</title></head>"
    "<body style='font-family:sans-serif;text-align:center;padding:60px;color:#222'>"
    "<p style='font-size:3em'>&#10003;</p>"
    "<h2 style='color:#107c10'>Configuration sauvegard&eacute;e</h2>"
    "<p>L&rsquo;assistant red&eacute;marre dans quelques secondes&hellip;</p>"
    "</body></html>";

}  // namespace

WifiConfig* WifiConfig::_instance = nullptr;

WifiConfig::WifiConfig() {
    _instance = this;
    // Par défaut, l'utilisateur MQTT est DEVICE_ID : les règles ACL du broker
    // (pattern voice/%u/...) l'exigent. Ce repli garantit que MqttLink ne
    // stocke pas un pointeur vide avant que begin() soit appelé.
    strncpy(_mqttUser, DEVICE_ID, sizeof(_mqttUser) - 1);
}

WifiConfig::~WifiConfig() {
    delete _server;
}

// ------------------------------------------------------------------ NVS I/O

bool WifiConfig::loadFromNvs() {
    Preferences prefs;
    if (!prefs.begin(NVS_NS, true)) {
        return false;
    }
    if (!prefs.isKey(KEY_SSID)) {
        prefs.end();
        return false;
    }
    prefs.getString(KEY_SSID, _ssid,         sizeof(_ssid));
    prefs.getString(KEY_PASS, _password,     sizeof(_password));
    prefs.getString(KEY_HOST, _mqttHost,     sizeof(_mqttHost));
    _mqttPort = prefs.getUShort(KEY_PORT, 1883);
    prefs.getString(KEY_USER, _mqttUser,     sizeof(_mqttUser));
    prefs.getString(KEY_MPWD, _mqttPassword, sizeof(_mqttPassword));
    prefs.end();

    // Utilisateur vide → repli sur DEVICE_ID (requis par le broker ACL).
    if (_mqttUser[0] == '\0') {
        strncpy(_mqttUser, DEVICE_ID, sizeof(_mqttUser) - 1);
    }
    // SSID et hôte non vides : configuration valide.
    return _ssid[0] != '\0' && _mqttHost[0] != '\0';
}

void WifiConfig::saveToNvs(const char* ssid, const char* password,
                           const char* mqttHost, uint16_t mqttPort,
                           const char* mqttUser, const char* mqttPassword) {
    Preferences prefs;
    if (!prefs.begin(NVS_NS, false)) {
        Serial.println("# config : erreur d'ecriture NVS");
        return;
    }
    prefs.putString(KEY_SSID, ssid);
    prefs.putString(KEY_PASS, password);
    prefs.putString(KEY_HOST, mqttHost);
    prefs.putUShort(KEY_PORT, mqttPort);
    prefs.putString(KEY_USER, mqttUser);
    prefs.putString(KEY_MPWD, mqttPassword);
    prefs.end();
    Serial.println("# config : identifiants sauvegardes en NVS");
}

// ------------------------------------------------------------------ begin

void WifiConfig::begin(bool forcePortal,
                       const char* fbSsid,     const char* fbPassword,
                       const char* fbMqttHost, uint16_t    fbMqttPort,
                       const char* fbMqttUser, const char* fbMqttPass) {
    if (!forcePortal && loadFromNvs()) {
        Serial.printf("# config : Wi-Fi \"%s\", broker %s:%u (NVS)\n",
                      _ssid, _mqttHost, _mqttPort);
        return;
    }

    // Repli depuis secrets.h : utilisé si les deux champs essentiels sont renseignés,
    // sans être copié en NVS (l'utilisateur reste maître de la migration).
    if (!forcePortal && fbSsid[0] != '\0' && fbMqttHost[0] != '\0') {
        strncpy(_ssid,         fbSsid,     sizeof(_ssid) - 1);
        strncpy(_password,     fbPassword, sizeof(_password) - 1);
        strncpy(_mqttHost,     fbMqttHost, sizeof(_mqttHost) - 1);
        _mqttPort = fbMqttPort;
        const char* user = (fbMqttUser[0] != '\0') ? fbMqttUser : DEVICE_ID;
        strncpy(_mqttUser,     user,       sizeof(_mqttUser) - 1);
        strncpy(_mqttPassword, fbMqttPass, sizeof(_mqttPassword) - 1);
        Serial.printf("# config : Wi-Fi \"%s\", broker %s:%u (secrets.h)\n",
                      _ssid, _mqttHost, _mqttPort);
        return;
    }

    Serial.println(forcePortal
        ? "# config : portail force (bouton PTT tenu au demarrage)"
        : "# config : aucun identifiant disponible, lancement du portail");
    runPortal();  // ne retourne pas
}

// ------------------------------------------------------------------ Portail

void WifiConfig::onRoot() {
    String page = HTML_FORM;
    page.replace("%SSID%", _instance->_ssid);
    page.replace("%HOST%", _instance->_mqttHost);
    char portStr[8];
    snprintf(portStr, sizeof(portStr), "%u", _instance->_mqttPort);
    page.replace("%PORT%", portStr);
    page.replace("%USER%", _instance->_mqttUser);
    _instance->_server->send(200, "text/html; charset=utf-8", page);
}

void WifiConfig::onSave() {
    WebServer* srv = _instance->_server;

    const String ssid     = srv->arg("ssid");
    const String mqttHost = srv->arg("mqttHost");

    if (ssid.isEmpty()) {
        srv->send(400, "text/plain", "SSID manquant");
        return;
    }
    if (mqttHost.isEmpty()) {
        srv->send(400, "text/plain", "Adresse broker manquante");
        return;
    }

    const String   pass     = srv->arg("pass");
    const uint16_t port     = static_cast<uint16_t>(srv->arg("mqttPort").toInt());
    const String   mqttUser = srv->arg("mqttUser");
    const String   mqttPass = srv->arg("mqttPass");

    _instance->saveToNvs(
        ssid.c_str(), pass.c_str(),
        mqttHost.c_str(), port ? port : uint16_t(1883),
        mqttUser.c_str(), mqttPass.c_str());

    srv->send(200, "text/html; charset=utf-8", HTML_OK);
    delay(1500);
    ESP.restart();
}

void WifiConfig::onNotFound() {
    _instance->_server->sendHeader("Location", "/", true);
    _instance->_server->send(302, "text/plain", "");
}

void WifiConfig::runPortal() {
    WiFi.disconnect(true);
    WiFi.mode(WIFI_AP);
    if (!WiFi.softAP(WIFI_CONFIG_AP_SSID)) {
        Serial.println("# portail : echec softAP, redemarrage dans 5 s");
        delay(5000);
        ESP.restart();
    }

    Serial.printf(
        "# portail : AP \"%s\" actif "
        "-- connectez-vous au Wi-Fi puis allez sur http://192.168.4.1\n",
        WIFI_CONFIG_AP_SSID);

    // LED : clignotement rapide (100 ms) pendant toute la durée du portail.
    // La broche est déjà configurée en OUTPUT par led.begin() avant begin().
    pinMode(PIN_STATUS_LED, OUTPUT);

    _server = new WebServer(80);
    _server->on("/",     HTTP_GET,  onRoot);
    _server->on("/save", HTTP_POST, onSave);
    _server->onNotFound(onNotFound);
    _server->begin();

    Serial.println("# portail : en attente de configuration...");
    uint32_t lastToggle = 0;
    bool ledOn = false;
    for (;;) {
        _server->handleClient();
        if (millis() - lastToggle >= 100) {
            lastToggle = millis();
            ledOn = !ledOn;
            digitalWrite(PIN_STATUS_LED, ledOn ? HIGH : LOW);
        }
    }
}
