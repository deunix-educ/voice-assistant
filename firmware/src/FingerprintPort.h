#pragma once

#include <stdint.h>

/**
 * @file FingerprintPort.h
 * @brief Ce que l'enregistrement d'une empreinte attend du capteur.
 *
 * Une interface plutôt que la classe du capteur : le déroulement de l'enregistrement
 * (FingerprintEnrollment.h) se teste alors sur le PC avec un faux capteur, sans carte.
 * Pas de dépendance à Arduino.
 */
class FingerprintPort {
public:
    /// Code de retour : ceux du capteur (0x00 = succès), plus trois erreurs locales.
    using Code = uint8_t;
    static constexpr Code OK = 0x00;
    static constexpr Code NO_FINGER = 0x02;
    static constexpr Code IMAGE_MESSY = 0x06;          ///< image trop brouillée
    static constexpr Code FEATURES_TOO_FEW = 0x07;     ///< pas assez de points caractéristiques
    static constexpr Code NO_MATCH = 0x08;
    static constexpr Code NO_MATCH_IN_LIBRARY = 0x09;  ///< empreinte inconnue du capteur
    static constexpr Code MODEL_MISMATCH = 0x0A;       ///< les deux poses ne sont pas le même doigt
    static constexpr Code BAD_LOCATION = 0x0B;         ///< emplacement hors de la bibliothèque
    static constexpr Code WRONG_PASSWORD = 0x13;
    static constexpr Code FLASH_ERROR = 0x18;          ///< écriture en mémoire du capteur impossible
    static constexpr Code BAD_CHECKSUM = 0xE3;         ///< réponse altérée
    static constexpr Code BAD_REPLY = 0xE5;            ///< réponse inattendue ou d'une autre adresse
    static constexpr Code TIMEOUT = 0xE9;              ///< pas de réponse : câblage, débit ou alimentation

    /// Effet de l'anneau lumineux.
    enum class Aura : uint8_t { Breathing = 1, Flashing = 2, On = 3, Off = 4, FadeIn = 5, FadeOut = 6 };

    /// Couleur de l'anneau (le R503 simple n'a que ces trois-là).
    enum class Color : uint8_t { Red = 1, Blue = 2, Purple = 3 };

    virtual ~FingerprintPort() = default;

    /// true tant qu'un doigt couvre le capteur (signal de touche, sans dialogue série).
    virtual bool touched() const = 0;

    /// Capture l'image du doigt posé dans le capteur (NO_FINGER si rien).
    virtual Code getImage() = 0;

    /// Extrait les caractéristiques de l'image dans le tampon 1 ou 2.
    virtual Code extract(uint8_t buffer) = 0;

    /// Cherche le contenu d'un tampon dans les empreintes enregistrées : emplacement et score.
    virtual Code search(uint8_t buffer, uint16_t& id, uint16_t& score) = 0;

    /// Fusionne les tampons 1 et 2 en un modèle.
    virtual Code createModel() = 0;

    /// Écrit le modèle du tampon à l'emplacement @p id.
    virtual Code store(uint8_t buffer, uint16_t id) = 0;

    /// Pilote l'anneau lumineux. @p speed : 0 à 255 ; @p times : 0 = sans fin.
    virtual Code setAura(Aura effect, Color color, uint8_t speed, uint8_t times) = 0;
};
