#pragma once

#include <Arduino.h>

#include "SerialProtocol.h"

/**
 * @brief Décode les trames reçues du PC sur l'UART, octet par octet, sans bloquer.
 *
 * Machine à états : on cherche le mot magique, puis on lit l'en-tête, le
 * payload et la somme de contrôle. Toute incohérence (type inconnu, longueur
 * excessive, somme fausse) fait repartir à la recherche du mot magique.
 *
 * Aucune allocation : le payload est copié dans un tampon interne de taille fixe.
 */
class SerialFrameReader {
public:
    /// Trame décodée. Le payload reste valide jusqu'au prochain appel de poll().
    struct Frame {
        SerialProtocol::FrameType type = SerialProtocol::FrameType::End;
        const uint8_t* payload = nullptr;
        size_t length = 0;
    };

    explicit SerialFrameReader(Stream& stream);

    /**
     * @brief Consomme les octets disponibles, sans jamais attendre.
     * @param frame Reçoit la trame dès qu'une trame complète et valide est lue.
     * @return true si @p frame vient d'être rempli ; rappeler poll() pour la suite.
     */
    bool poll(Frame& frame);

    /// Nombre de trames rejetées depuis le démarrage (somme ou en-tête incohérents).
    uint32_t rejectedFrames() const;

private:
    enum class State {
        Magic,     ///< recherche du mot magique
        Header,    ///< type + longueur
        Payload,   ///< données
        Checksum,  ///< somme de contrôle
    };

    Stream& _stream;
    State _state = State::Magic;
    size_t _magicMatched = 0;
    uint8_t _header[3] = {};  ///< type, longueur (poids faible), longueur (poids fort)
    size_t _headerRead = 0;
    uint8_t _payload[SerialProtocol::MAX_PAYLOAD] = {};
    size_t _length = 0;
    size_t _payloadRead = 0;
    uint8_t _checksum = 0;
    uint32_t _rejected = 0;

    // Repart à la recherche du mot magique
    void restart();
};
