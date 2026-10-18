#pragma once

#include <stddef.h>
#include <stdint.h>

#include <functional>

#include "FingerprintPort.h"

/**
 * @file FingerprintTrigger.h
 * @brief Le doigt déclenche la session de parole : posé = début, levé = fin (branche fingerprint, F3).
 *
 * Déroulement :
 *
 *   doigt posé depuis settleMs  → Touched   (la carte peut déjà capter le son)
 *   capture, extraction, recherche dans le capteur, au plus identifyMs
 *       → Recognized (emplacement, score)   le doigt est connu : la session s'ouvre
 *       → Rejected   (motif)                doigt inconnu, image inutilisable, capteur en défaut
 *   doigt levé depuis releaseMs → Released  (seulement après Recognized : la session se ferme)
 *
 * Après Rejected, rien ne se passe jusqu'à ce que le doigt ait quitté le capteur : un doigt inconnu
 * laissé posé ne relance pas l'identification en boucle.
 *
 * Le signal de touche (sans dialogue série) suffit à voir le doigt arriver et partir ; le capteur
 * n'est interrogé que pendant l'identification, au plus une fois toutes les pollMs.
 * Header seul, sans Arduino : testé sur le PC (server/tests/test_fingerprint_trigger.py).
 */
class FingerprintTrigger {
public:
    enum class Event : uint8_t {
        Touched,     ///< doigt posé : l'identification commence
        Recognized,  ///< doigt connu : id et score renseignés
        Rejected,    ///< doigt refusé : motif « unknown », « image », « lifted » ou « sensor »
        Released,    ///< doigt levé après Recognized : fin de session
    };

    /// Reçoit chaque événement : (événement, emplacement, score, motif ou nullptr).
    using Listener = std::function<void(Event, uint16_t, uint16_t, const char*)>;

    /// Durées en ms, et nombre de captures inutilisables tolérées.
    struct Timing {
        uint32_t settleMs = 40;     ///< doigt posé depuis ce délai : ce n'est pas un effleurement
        uint32_t releaseMs = 150;   ///< doigt levé depuis ce délai : micro-décollements ignorés
        uint32_t identifyMs = 3000; ///< délai total pour identifier, à partir de Touched
        uint32_t pollMs = 40;       ///< entre deux dialogues avec le capteur
        uint8_t attempts = 3;       ///< captures inutilisables tolérées avant d'abandonner
    };

    FingerprintTrigger(FingerprintPort& port, Listener listener, const Timing& timing)
        : _port(port), _listener(std::move(listener)), _timing(timing) {}

    /// true dès qu'un doigt a été vu et tant qu'il n'est pas reparti : le micro lui appartient.
    bool busy() const { return _state != State::Idle; }

    /**
     * @brief Un doigt déjà posé ne déclenche rien : il doit d'abord quitter le capteur.
     *
     * À appeler quand le capteur sert à autre chose (enregistrement d'un doigt) : le doigt qui s'y
     * trouve encore ne doit pas ouvrir une session. Vaut aussi au démarrage.
     */
    void requireLift() { _needLift = true; }

    /**
     * @brief À appeler à chaque tour de loop() avec millis().
     * @param canStart false tant qu'une autre session tient le micro (bouton) : un doigt posé alors
     *                 est ignoré, même si la session finit avant qu'il parte.
     */
    void update(uint32_t now, bool canStart) {
        switch (_state) {
        case State::Idle:
            idle(now, canStart);
            break;
        case State::Settling:
            settle(now);
            break;
        case State::Identifying:
            identify(now);
            break;
        case State::Held:
            if (fingerGone(now)) {
                _state = State::Idle;
                _needLift = false;
                _port.setAura(FingerprintPort::Aura::Off, FingerprintPort::Color::Blue, 0, 0);
                _listener(Event::Released, 0, 0, nullptr);
            }
            break;
        case State::Lifting:
            if (fingerGone(now)) {  // l'anneau clignote encore en rouge : on ne l'éteint pas
                _state = State::Idle;
                _needLift = false;
            }
            break;
        }
    }

private:
    enum class State : uint8_t { Idle, Settling, Identifying, Held, Lifting };

    FingerprintPort& _port;
    Listener _listener;
    Timing _timing;
    State _state = State::Idle;
    bool _needLift = true;  // un doigt déjà là au démarrage n'ouvre pas de session
    bool _absent = false;
    uint32_t _since = 0;
    uint32_t _absentSince = 0;
    uint32_t _lastPoll = 0;
    uint8_t _failures = 0;

    void idle(uint32_t now, bool canStart) {
        if (!_port.touched()) {
            _needLift = false;
            return;
        }
        if (!canStart || _needLift) {
            _needLift = true;
            return;
        }
        _state = State::Settling;
        _since = now;
    }

    void settle(uint32_t now) {
        if (!_port.touched()) {
            _state = State::Idle;  // effleurement
            return;
        }
        if (now - _since < _timing.settleMs) {
            return;
        }
        _state = State::Identifying;
        _since = now;
        _lastPoll = now - _timing.pollMs;  // premier dialogue au tour suivant
        _failures = 0;
        _absent = false;
        _port.setAura(FingerprintPort::Aura::On, FingerprintPort::Color::Purple, 0, 0);
        _listener(Event::Touched, 0, 0, nullptr);
    }

    void identify(uint32_t now) {
        if (fingerGone(now)) {
            reject("lifted");
            return;
        }
        if (now - _since > _timing.identifyMs) {
            reject("image");
            return;
        }
        if (now - _lastPoll < _timing.pollMs) {
            return;
        }
        _lastPoll = now;

        const auto image = _port.getImage();
        if (image == FingerprintPort::NO_FINGER || image == FingerprintPort::IMAGE_FAIL) {
            return;  // le signal de touche précède la lecture : on réessaie au prochain tour
        }
        if (image != FingerprintPort::OK) {
            reject("sensor");
            return;
        }
        const auto extracted = _port.extract(1);
        if (extracted == FingerprintPort::IMAGE_MESSY || extracted == FingerprintPort::FEATURES_TOO_FEW) {
            if (++_failures >= _timing.attempts) {
                reject("image");
            }
            return;  // le doigt est toujours là : nouvelle capture au prochain tour
        }
        if (extracted != FingerprintPort::OK) {
            reject("sensor");
            return;
        }
        uint16_t id = 0;
        uint16_t score = 0;
        const auto searched = _port.search(1, id, score);
        if (searched == FingerprintPort::OK) {
            _state = State::Held;
            _absent = false;
            _port.setAura(FingerprintPort::Aura::On, FingerprintPort::Color::Blue, 0, 0);
            _listener(Event::Recognized, id, score, nullptr);
        } else if (searched == FingerprintPort::NO_MATCH_IN_LIBRARY) {
            reject("unknown");
        } else {
            reject("sensor");
        }
    }

    // Le doigt est-il parti pour de bon ? Un décollement plus court que releaseMs n'est pas un retrait.
    bool fingerGone(uint32_t now) {
        if (_port.touched()) {
            _absent = false;
            return false;
        }
        if (!_absent) {
            _absent = true;
            _absentSince = now;
        }
        return now - _absentSince >= _timing.releaseMs;
    }

    void reject(const char* reason) {
        _state = State::Lifting;
        _absent = false;
        _port.setAura(FingerprintPort::Aura::Flashing, FingerprintPort::Color::Red, 0x20, 3);
        _listener(Event::Rejected, 0, 0, reason);
    }
};
