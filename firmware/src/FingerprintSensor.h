#pragma once

#include <Arduino.h>

#include "FingerprintPacket.h"

/**
 * @brief Dialogue avec un capteur d'empreinte Grow R503, par liaison série.
 *
 * Étape F1 : poignée de main, paramètres, nombre d'empreintes, anneau lumineux et
 * signal de touche. L'enregistrement et la recherche viennent aux étapes suivantes.
 *
 * Les méthodes d'échange attendent la réponse (FP_REPLY_TIMEOUT_MS au plus) : à
 * n'appeler que depuis loop() ou setup(), jamais depuis une tâche audio.
 */
class FingerprintSensor {
public:
    /// Code de retour : ceux du capteur (0x00 = succès), plus trois erreurs locales.
    using Code = uint8_t;
    static constexpr Code OK = 0x00;
    static constexpr Code NO_FINGER = 0x02;
    static constexpr Code NO_MATCH_IN_LIBRARY = 0x09;
    static constexpr Code WRONG_PASSWORD = 0x13;
    static constexpr Code BAD_CHECKSUM = 0xE3;  ///< réponse altérée
    static constexpr Code BAD_REPLY = 0xE5;     ///< réponse inattendue ou d'une autre adresse
    static constexpr Code TIMEOUT = 0xE9;       ///< pas de réponse : câblage, débit ou alimentation

    /// Réglages lus dans le capteur.
    struct Parameters {
        uint16_t libraryCapacity = 0;  ///< empreintes que le capteur peut garder
        uint16_t securityLevel = 0;    ///< 1 (tolérant) à 5 (strict)
        uint32_t address = 0;
        uint16_t packetBytes = 0;
        uint32_t baud = 0;
    };

    /// Effet de l'anneau lumineux.
    enum class Aura : uint8_t { Breathing = 1, Flashing = 2, On = 3, Off = 4, FadeIn = 5, FadeOut = 6 };

    /// Couleur de l'anneau (le R503 simple n'a que ces trois-là).
    enum class Color : uint8_t { Red = 1, Blue = 2, Purple = 3 };

    FingerprintSensor(HardwareSerial& serial, int rxPin, int txPin, int touchPin);

    /**
     * @brief Ouvre la liaison et vérifie le mot de passe.
     * @return true si le capteur a répondu et accepté le mot de passe.
     */
    bool begin(uint32_t baud, uint32_t password);

    /// true si begin() a réussi : sinon, les autres méthodes ne tentent rien.
    bool present() const { return _present; }

    /// Poignée de main (0x40) : le capteur répond OK s'il est prêt.
    Code handShake();

    /// Lit les réglages du capteur.
    Code readParameters(Parameters& parameters);

    /// Nombre d'empreintes enregistrées.
    Code templateCount(uint16_t& count);

    /// Pilote l'anneau lumineux. @p speed : 0 à 255 ; @p times : 0 = sans fin.
    Code setAura(Aura effect, Color color, uint8_t speed, uint8_t times);

    /// true tant qu'un doigt couvre le capteur (signal de touche, sans dialogue série ;
    /// polarité : FP_TOUCH_ACTIVE_LOW dans config.h).
    bool touched() const;

    /// Texte français d'un code de retour, pour le journal.
    static const char* describe(Code code);

private:
    HardwareSerial& _serial;
    int _rxPin;
    int _txPin;
    int _touchPin;
    uint32_t _address = FingerprintPacket::DEFAULT_ADDRESS;
    bool _present = false;
    FingerprintPacket::Parser _parser;

    // Envoie une commande et rend le code de retour ; le contenu de la réponse reste dans _parser.
    Code exchange(const uint8_t* command, size_t length);
};
