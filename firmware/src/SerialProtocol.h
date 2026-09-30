#pragma once

#include <stddef.h>
#include <stdint.h>

/**
 * @file SerialProtocol.h
 * @brief Format des trames binaires échangées avec le PC sur l'UART (étapes 1 et 2).
 *
 * Même format dans les deux sens, et identique à tools/serial_protocol.py :
 *
 *     octets 0..3 : "VSA1"        mot magique
 *     octet  4    : type          1 = START, 2 = AUDIO, 3 = END
 *     octets 5..6 : length        taille utile en octets (uint16 little-endian)
 *     octets 7..  : payload       length octets
 *     dernier     : checksum      OU exclusif de tous les octets du payload
 *
 * Le mot magique permet de se resynchroniser : du texte peut s'intercaler entre
 * les trames. À l'étape 4, MQTT remplace ce transport.
 */
namespace SerialProtocol {

/// Mot magique de début de trame (aucun caractère répété : resynchronisation simple).
constexpr uint8_t MAGIC[4] = {'V', 'S', 'A', '1'};

/// Mot magique + type + longueur.
constexpr size_t HEADER_SIZE = 7;

/// Taille maximale d'un payload ; un chunk légitime fait 1600 octets.
constexpr size_t MAX_PAYLOAD = 2048;

/// Taille du descripteur de format porté par une trame START.
constexpr size_t START_PAYLOAD = 8;

/// Types de trames.
enum class FrameType : uint8_t {
    Start = 1,  ///< début de flux, payload = fréquence (u32), bits (u16), canaux (u16)
    Audio = 2,  ///< chunk de PCM 16 bits signé little-endian
    End = 3,    ///< fin de flux, payload vide
};

}  // namespace SerialProtocol
