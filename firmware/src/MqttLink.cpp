#include "MqttLink.h"

#include "config.h"

namespace {
/// Message publié par le broker à notre place si l'on disparaît sans prévenir.
const char* const WILL_PAYLOAD = "{\"status\":\"offline\"}";
}  // namespace

MqttLink::MqttLink(Client& network, const char* host, uint16_t port, const char* deviceId,
                   const char* user, const char* password)
    : _client(network),
      _host(host),
      _port(port),
      _deviceId(deviceId),
      _user((user != nullptr && user[0] != '\0') ? user : deviceId),
      _password(password) {}

bool MqttLink::begin(MessageHandler onMessage, ConnectHandler onConnect) {
    _onMessage = onMessage;
    _onConnect = onConnect;

    _client.setServer(_host, _port);
    _client.setKeepAlive(MQTT_KEEPALIVE_S);
    _client.setSocketTimeout(MQTT_SOCKET_TIMEOUT_S);
    _client.setCallback([this](char* topic, uint8_t* payload, unsigned int length) {
        const char* suffix = suffixOf(topic);
        if (_onMessage) {
            _onMessage(suffix != nullptr ? suffix : topic, payload, length);
        }
    });

    buildTopic(_willTopic, sizeof(_willTopic), "state");

    if (strcmp(_user, _deviceId) != 0) {
        // Les droits du broker (pattern voice/%u/...) portent sur le nom d'utilisateur :
        // un autre nom que DEVICE_ID, et la carte n'a le droit de rien publier.
        Serial.printf("# mqtt : ATTENTION, utilisateur \"%s\" different de DEVICE_ID \"%s\" : "
                      "le broker refusera ses messages (mosquitto/acl)\n", _user, _deviceId);
    }

    // Le tampon par défaut (256 octets) ne contiendrait pas un chunk audio : un
    // message trop gros est rejeté sans aucun message d'erreur. Alloué une fois ici.
    return _client.setBufferSize(MQTT_BUFFER_BYTES);
}

void MqttLink::update(bool networkUp) {
    if (_client.connected()) {
        _client.loop();  // traite les messages reçus et entretient la connexion
        return;
    }
    if (!networkUp) {
        return;
    }

    // Tentatives espacées : chaque essai peut bloquer jusqu'à MQTT_SOCKET_TIMEOUT_S.
    const uint32_t now = millis();
    if (_attempted && (now - _lastAttemptMs) < MQTT_RETRY_MS) {
        return;
    }
    _attempted = true;
    _lastAttemptMs = now;
    connect();
}

bool MqttLink::connected() {
    return _client.connected();
}

bool MqttLink::publish(const char* suffix, const char* payload, bool retained) {
    if (!_client.connected()) {
        return false;
    }
    return _client.publish(buildTopic(_topic, sizeof(_topic), suffix), payload, retained);
}

bool MqttLink::publishBinary(const char* suffix, const uint8_t* data, size_t length) {
    if (!_client.connected()) {
        return false;
    }
    return _client.publish(buildTopic(_topic, sizeof(_topic), suffix), data,
                           static_cast<unsigned int>(length), false);
}

bool MqttLink::subscribe(const char* suffix) {
    if (!_client.connected()) {
        return false;
    }
    return _client.subscribe(buildTopic(_topic, sizeof(_topic), suffix), 1);
}

bool MqttLink::subscribeTopic(const char* topic) {
    if (!_client.connected()) {
        return false;
    }
    return _client.subscribe(topic, 1);
}

uint32_t MqttLink::connections() const {
    return _connections;
}

void MqttLink::connect() {
    Serial.printf("# mqtt : connexion a %s:%u...\n", _host, _port);

    // Mot de passe vide = connexion anonyme, refusée par un broker sécurisé (étape 15).
    const bool anonymous = (_password == nullptr || _password[0] == '\0');
    const char* user = anonymous ? nullptr : _user;
    const char* password = anonymous ? nullptr : _password;

    // Testament : QoS 1, retenu. Session propre : on ne veut pas recevoir, au
    // retour, des commandes périmées accumulées pendant l'absence.
    const bool ok = _client.connect(_deviceId, user, password, _willTopic, 1, true, WILL_PAYLOAD,
                                    true);
    if (!ok) {
        // Codes de PubSubClient : -4 délai dépassé, -2 connexion TCP refusée
        // (broker arrêté, mauvais port, pare-feu), 5 non autorisé...
        const int code = _client.state();
        Serial.printf("# mqtt : echec (code %d), nouvel essai dans %lu s\n", code,
                      static_cast<unsigned long>(MQTT_RETRY_MS / 1000));
        if (code == MQTT_CONNECT_BAD_CREDENTIALS || code == MQTT_CONNECT_UNAUTHORIZED) {
            Serial.printf("# mqtt : compte refuse. secrets.h : MQTT_PASSWORD %s ; le compte \"%s\" "
                          "existe-t-il (make mqtt-user NAME=%s) ?\n",
                          anonymous ? "VIDE" : "renseigne", _user, _deviceId);
        }
        return;
    }

    ++_connections;
    Serial.printf("# mqtt : connecte (connexion n°%lu)\n", static_cast<unsigned long>(_connections));
    if (_onConnect) {
        _onConnect(_connections == 1);
    }
}

const char* MqttLink::buildTopic(char* buffer, size_t size, const char* suffix) const {
    snprintf(buffer, size, "%s/%s/%s", MQTT_TOPIC_PREFIX, _deviceId, suffix);
    return buffer;
}

const char* MqttLink::suffixOf(const char* topic) const {
    // Attendu : <préfixe>/<id>/<suffixe>. On vérifie les deux premiers segments.
    const size_t prefixLength = strlen(MQTT_TOPIC_PREFIX);
    const size_t idLength = strlen(_deviceId);
    if (strncmp(topic, MQTT_TOPIC_PREFIX, prefixLength) != 0 || topic[prefixLength] != '/') {
        return nullptr;
    }
    const char* rest = topic + prefixLength + 1;
    if (strncmp(rest, _deviceId, idLength) != 0 || rest[idLength] != '/') {
        return nullptr;
    }
    return rest + idLength + 1;
}
