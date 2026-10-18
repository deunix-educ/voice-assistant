#pragma once

#include <Arduino.h>

#include "DemoDevices.h"
#include "MqttLink.h"
#include "PlaybackStream.h"

/**
 * @brief Interprète les messages JSON reçus sur voice/<id>/control.
 *
 * Deux familles de messages :
 *   - flux audio sortant (étape 5), clé « event » :
 *       {"event":"start","session":"s1","rate":16000,"bits":16,"channels":1,...}
 *       {"event":"end","session":"s1","chunks":42}
 *   - commandes, clé « cmd » :
 *       {"cmd":"ping"}  -> répond {"event":"pong","uptime_ms":...} sur voice/<id>/event
 *       {"cmd":"device","device":"light","action":"on"}  (étape 11)
 *           -> applique la commande aux appareils de la pièce, puis confirme :
 *              {"event":"device","device":"light","action":"on","ok":true,"state":"on"}
 *
 *       {"cmd":"fp_enroll","id":3}        (branche fingerprint, F2) enregistre un doigt
 *       {"cmd":"fp_delete","id":3} ou {"cmd":"fp_delete","all":true}   supprime une empreinte ou toutes
 *       {"cmd":"fp_list"} / {"cmd":"fp_cancel"}   liste les empreintes / abandonne l'enregistrement
 *           -> les réponses sont des événements « fp_… » (voir main.cpp)
 *
 * Le traitement reste court : il est appelé depuis le rappel MQTT, dans loop().
 */
class CommandHandler {
public:
    /// Rappel des commandes d'empreinte : nom de la commande, id (-1 si absent), « all » vrai ou faux.
    using FingerprintCallback = void (*)(const char* command, int id, bool all);

    CommandHandler(PlaybackStream& playback, MqttLink& mqtt, DemoDevices& devices);

    /// Branche les commandes d'empreinte (branche fingerprint) ; sans cela, elles sont ignorées.
    void setFingerprintCallback(FingerprintCallback callback) { _onFingerprint = callback; }

    /// Traite un message du topic control.
    void handle(const uint8_t* payload, size_t length);

private:
    PlaybackStream& _playback;
    MqttLink& _mqtt;
    DemoDevices& _devices;
    FingerprintCallback _onFingerprint = nullptr;

    /// Commande domotique : exécution puis confirmation sur voice/<id>/event.
    void handleDevice(const char* device, const char* action);
};
