#pragma once

#include <Arduino.h>

#include "SerialProtocol.h"

/**
 * @brief Encapsule des données binaires dans des trames envoyées sur l'UART.
 *
 * Le port série sert aussi aux messages de journalisation : il faut donc pouvoir
 * distinguer l'audio du texte. Format des trames : voir SerialProtocol.h.
 */
class SerialFramer {
public:
    using FrameType = SerialProtocol::FrameType;

    explicit SerialFramer(Stream& stream);

    /**
     * @brief Envoie une trame START décrivant le format audio.
     * @param sampleRate Fréquence d'échantillonnage en Hz.
     * @param bits       Nombre de bits par échantillon.
     * @param channels   Nombre de canaux.
     */
    void sendStart(uint32_t sampleRate, uint16_t bits, uint16_t channels);

    /**
     * @brief Envoie un chunk audio.
     * @param pcm   Échantillons 16 bits signés.
     * @param count Nombre d'échantillons.
     */
    void sendAudio(const int16_t* pcm, size_t count);

    /// Envoie la trame de fin de session.
    void sendEnd();

private:
    Stream& _stream;

    void sendFrame(FrameType type, const uint8_t* payload, size_t length);
};
