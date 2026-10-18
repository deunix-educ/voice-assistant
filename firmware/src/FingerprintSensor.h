#pragma once

#include <Arduino.h>

#include "FingerprintPacket.h"
#include "FingerprintPort.h"
#include "config.h"

/**
 * @brief Dialogue avec un capteur d'empreinte Grow R503, par liaison série.
 *
 * Étape F1 : poignée de main, paramètres, nombre d'empreintes, anneau lumineux et
 * signal de touche. L'enregistrement et la recherche viennent aux étapes suivantes.
 *
 * Les méthodes d'échange attendent la réponse (FP_REPLY_TIMEOUT_MS au plus) : à
 * n'appeler que depuis loop() ou setup(), jamais depuis une tâche audio.
 */
class FingerprintSensor : public FingerprintPort {
public:
    /// Réglages lus dans le capteur.
    struct Parameters {
        uint16_t libraryCapacity = 0;  ///< empreintes que le capteur peut garder
        uint16_t securityLevel = 0;    ///< 1 (tolérant) à 5 (strict)
        uint32_t address = 0;
        uint16_t packetBytes = 0;
        uint32_t baud = 0;
    };

    /// Capacité des R503 ; remplacée par la valeur lue dans le capteur par readParameters().
    static constexpr uint16_t DEFAULT_CAPACITY = 200;

    FingerprintSensor(HardwareSerial& serial, int rxPin, int txPin, int touchPin);

    /**
     * @brief Ouvre la liaison et vérifie le mot de passe.
     * @return true si le capteur a répondu et accepté le mot de passe.
     */
    bool begin(uint32_t baud, uint32_t password);

    /// true si begin() a réussi : sinon, les autres méthodes ne tentent rien.
    bool present() const { return _present; }

    /// Empreintes que le capteur peut garder.
    uint16_t capacity() const { return _capacity; }

    /// Poignée de main (0x40) : le capteur répond OK s'il est prêt.
    Code handShake();

    /// Lit les réglages du capteur.
    Code readParameters(Parameters& parameters);

    /// Nombre d'empreintes enregistrées.
    Code templateCount(uint16_t& count);

    /**
     * @brief Emplacements occupés (table d'index du capteur).
     * @param ids      Reçoit les emplacements, dans l'ordre croissant.
     * @param maxIds   Taille de @p ids.
     * @param found    Nombre d'emplacements occupés.
     */
    Code listTemplates(uint16_t* ids, size_t maxIds, size_t& found);

    /// Efface l'empreinte de l'emplacement @p id.
    Code remove(uint16_t id);

    /// Efface toutes les empreintes.
    Code removeAll();

    // --- FingerprintPort
    bool touched() const override;
    Code getImage() override;
    Code extract(uint8_t buffer) override;
    Code search(uint8_t buffer, uint16_t& id, uint16_t& score) override;
    Code createModel() override;
    Code store(uint8_t buffer, uint16_t id) override;
    Code setAura(Aura effect, Color color, uint8_t speed, uint8_t times) override;

    /// Texte français d'un code de retour, pour le journal.
    static const char* describe(Code code);

private:
    HardwareSerial& _serial;
    int _rxPin;
    int _txPin;
    int _touchPin;
    uint32_t _address = FingerprintPacket::DEFAULT_ADDRESS;
    uint16_t _capacity = DEFAULT_CAPACITY;
    bool _present = false;
    FingerprintPacket::Parser _parser;

    // Envoie une commande et rend le code de retour ; le contenu de la réponse reste dans _parser.
    Code exchange(const uint8_t* command, size_t length, uint32_t timeoutMs = FP_REPLY_TIMEOUT_MS);
};
