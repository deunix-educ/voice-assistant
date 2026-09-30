#pragma once

#include <Arduino.h>
#include <PubSubClient.h>

#include <functional>

/**
 * @brief Connexion au broker MQTT, avec testament et reconnexion espacée.
 *
 * Tous les topics ont la forme  <préfixe>/<identifiant>/<suffixe>, par exemple
 * voice/esp32-01/state. On ne manipule ici que le suffixe.
 *
 * Le **testament** (Last Will) est confié au broker à la connexion : si l'ESP32
 * disparaît sans prévenir (coupure de courant, plantage), c'est le broker qui
 * publie « offline » à sa place, en message retenu.
 *
 * Le rappel des messages reçus doit rester court : il sera appelé depuis
 * PubSubClient, dans loop(). À l'étape 5, il ne fera que remplir la FIFO audio.
 *
 * Étape 15 : le broker exige un compte. Le nom d'utilisateur de la carte est son
 * identifiant (DEVICE_ID) : les droits du broker (mosquitto/acl) en dépendent.
 */
class MqttLink {
public:
    /// Rappel des messages reçus : suffixe pour nos topics (« control »), topic complet
    /// pour ceux de subscribeTopic() (« voice/server/status »), payload, longueur.
    using MessageHandler = std::function<void(const char*, const uint8_t*, size_t)>;

    /// Rappel de connexion : appelé à chaque (re)connexion au broker.
    using ConnectHandler = std::function<void(bool firstTime)>;

    /**
     * @param network  Transport TCP (un WiFiClient).
     * @param host     Adresse IP du broker.
     * @param port     Port du broker.
     * @param deviceId Identifiant de la carte : client MQTT et segment des topics.
     * @param user     Utilisateur MQTT ; vide : deviceId (les droits du broker l'exigent).
     * @param password Mot de passe MQTT ; vide : connexion anonyme (broker non sécurisé).
     */
    MqttLink(Client& network, const char* host, uint16_t port, const char* deviceId,
             const char* user, const char* password);

    /**
     * @brief Prépare le client ; aucune connexion n'est tentée ici.
     * @param onMessage Appelé pour chaque message reçu sur un topic abonné.
     * @param onConnect Appelé après chaque connexion réussie (abonnements, état).
     * @return true si le tampon de @ref MQTT_BUFFER_BYTES a pu être alloué.
     */
    bool begin(MessageHandler onMessage, ConnectHandler onConnect);

    /**
     * @brief À appeler à chaque tour de boucle.
     * @param networkUp true si le Wi-Fi est connecté : sinon, inutile d'essayer.
     */
    void update(bool networkUp);

    /// true si la session MQTT est ouverte.
    bool connected();

    /**
     * @brief Publie un texte (JSON) sur <préfixe>/<id>/<suffixe>.
     * @return true si le message a été confié à la pile TCP.
     */
    bool publish(const char* suffix, const char* payload, bool retained);

    /**
     * @brief Publie des octets bruts (PCM) sur <préfixe>/<id>/<suffixe>, non retenu.
     *
     * Peut bloquer tant que la pile TCP n'a pas de place : ne jamais l'appeler
     * depuis une tâche audio, seulement depuis loop().
     *
     * @return true si le message a été confié à la pile TCP.
     */
    bool publishBinary(const char* suffix, const uint8_t* data, size_t length);

    /// S'abonne à <préfixe>/<id>/<suffixe>, en QoS 1.
    bool subscribe(const char* suffix);

    /// S'abonne à un topic complet, hors de nos topics (présence du serveur), en QoS 1.
    bool subscribeTopic(const char* topic);

    /// Nombre de connexions réussies depuis le démarrage.
    uint32_t connections() const;

private:
    PubSubClient _client;
    const char* _host;
    uint16_t _port;
    const char* _deviceId;
    const char* _user;
    const char* _password;
    MessageHandler _onMessage;
    ConnectHandler _onConnect;
    uint32_t _lastAttemptMs = 0;
    bool _attempted = false;
    uint32_t _connections = 0;
    char _topic[96] = {};       ///< topic en cours de construction
    char _willTopic[96] = {};   ///< topic du testament, conservé pour chaque connexion

    // Tente une connexion, testament compris
    void connect();

    // Construit <préfixe>/<id>/<suffixe> dans le tampon donné
    const char* buildTopic(char* buffer, size_t size, const char* suffix) const;

    // Rend le suffixe d'un topic reçu, ou nullptr s'il n'est pas de la forme attendue
    const char* suffixOf(const char* topic) const;
};
