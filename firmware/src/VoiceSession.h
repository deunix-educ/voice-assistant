#pragma once

#include <Arduino.h>

#include "MqttLink.h"
#include "SerialFramer.h"

/**
 * @brief Une session de parole : START, chunks PCM, END.
 *
 * Par MQTT (étape 4) :
 *   voice/<id>/event     {"event":"start","session":"a1b2c3","rate":16000,...}
 *   voice/<id>/audio/in  1600 octets de PCM brut, 20 fois par seconde
 *   voice/<id>/event     {"event":"end","session":"a1b2c3","chunks":42,...}
 *
 * Le END porte le nombre de chunks envoyés : le serveur le compare au nombre
 * reçu et détecte ainsi les pertes, sans numéro de séquence (celui-ci viendra
 * à l'étape 16).
 *
 * Si MQTT n'est pas connecté à l'appui, la session part sur la liaison série,
 * au format de l'étape 1 : `make record` reste un outil de dépannage.
 */
class VoiceSession {
public:
    /// Transport de la session en cours.
    enum class Transport {
        Mqtt,
        Serial,
    };

    VoiceSession(MqttLink& mqtt, SerialFramer& framer);

    /**
     * @brief Ouvre une session et annonce son format.
     * @param transport  MQTT ou liaison série.
     * @param sampleRate Fréquence réellement en service.
     */
    void start(Transport transport, uint32_t sampleRate);

    /**
     * @brief Envoie un chunk de PCM. Peut bloquer (Wi-Fi) : appeler depuis loop().
     * @return true si le chunk a été confié au transport.
     */
    bool sendChunk(const uint8_t* pcm, size_t bytes);

    /**
     * @brief Ferme la session et publie son bilan.
     * @param captureOverruns Chunks perdus avant l'envoi, faute de place en FIFO.
     */
    void end(uint32_t captureOverruns);

    bool active() const;
    Transport transport() const;
    const char* id() const;
    uint32_t chunksSent() const;
    uint32_t sendFailures() const;
    uint32_t durationMs() const;

    /// Temps écoulé depuis start(), session en cours ou non.
    uint32_t elapsedMs() const;

private:
    MqttLink& _mqtt;
    SerialFramer& _framer;
    Transport _transport = Transport::Mqtt;
    bool _active = false;
    char _id[8] = {};          ///< 6 chiffres hexadécimaux + zéro final
    uint32_t _startMs = 0;
    uint32_t _durationMs = 0;
    uint32_t _chunks = 0;      ///< chunks confiés au transport
    uint32_t _failures = 0;    ///< chunks refusés par le transport
};
