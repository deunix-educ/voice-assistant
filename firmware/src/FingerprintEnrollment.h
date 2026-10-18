#pragma once

#include <stddef.h>
#include <stdint.h>

#include <functional>

#include "FingerprintPort.h"

/**
 * @file FingerprintEnrollment.h
 * @brief Enregistrement d'une empreinte : deux poses du même doigt, sans attente bloquante.
 *
 * Déroulement (chaque attente est limitée par @p timeoutMs) :
 *
 *   attend un doigt → capture, extrait (tampon 1), vérifie qu'il n'est pas déjà connu
 *   → attend le retrait → attend un doigt → capture, extrait (tampon 2)
 *   → fusionne les deux (elles doivent être du même doigt) → écrit à l'emplacement demandé.
 *
 * update() est appelé à chaque tour de loop() : il ne dialogue avec le capteur que
 * lorsque le signal de touche annonce un doigt, au plus une fois toutes les pollMs.
 * Header seul, sans Arduino : testé sur le PC (server/tests/test_fingerprint_enrollment.py).
 */
class FingerprintEnrollment {
public:
    /// Étape annoncée à l'écouteur.
    enum class Step : uint8_t {
        Start,        ///< enregistrement lancé : poser le doigt
        Retry,        ///< capture inutilisable (reason) : retirer le doigt et recommencer
        RemoveFinger, ///< première pose réussie : retirer le doigt
        PlaceAgain,   ///< doigt retiré : reposer le MÊME doigt
        Done,         ///< empreinte enregistrée
        Failed,       ///< abandon (reason) ; pour already_enrolled, l'id est celui du doigt déjà connu
    };

    /// Reçoit chaque étape : (étape, id, motif ou nullptr).
    using Listener = std::function<void(Step, uint16_t, const char*)>;

    static constexpr uint8_t MAX_RETRIES = 5;  ///< captures inutilisables tolérées avant d'abandonner

    FingerprintEnrollment(FingerprintPort& port, Listener listener, uint32_t timeoutMs, uint32_t pollMs = 60,
                          uint32_t releaseMs = 300)
        : _port(port), _listener(std::move(listener)), _timeoutMs(timeoutMs), _pollMs(pollMs),
          _releaseMs(releaseMs) {}

    /// true pendant un enregistrement.
    bool busy() const { return _state != State::Idle; }

    /**
     * @brief Lance l'enregistrement à l'emplacement @p id.
     * @return false si un enregistrement est déjà en cours.
     */
    bool begin(uint16_t id, uint32_t now) {
        if (busy()) {
            return false;
        }
        _id = id;
        _retries = 0;
        enter(State::First, now);
        _listener(Step::Start, _id, nullptr);
        return true;
    }

    /// Abandonne l'enregistrement en cours (sans effet au repos).
    void cancel() {
        if (busy()) {
            fail("cancelled", _id, false);
        }
    }

    /// À appeler à chaque tour de loop() avec millis().
    void update(uint32_t now) {
        if (!busy()) {
            return;
        }
        if (now - _phaseStart > _timeoutMs) {
            fail("timeout", _id);
            return;
        }
        if (_state == State::Release) {
            watchRelease(now);
            return;
        }
        if (now - _lastPoll < _pollMs) {
            return;
        }
        _lastPoll = now;
        if (!_port.touched()) {
            return;
        }
        const auto image = _port.getImage();
        if (image == FingerprintPort::NO_FINGER) {
            return;  // le signal de touche précède la lecture : on réessaie au prochain tour
        }
        if (image != FingerprintPort::OK) {
            fail("sensor", _id);
            return;
        }
        if (_state == State::First) {
            firstPose(now);
        } else {
            secondPose(now);
        }
    }

private:
    enum class State : uint8_t { Idle, First, Release, Second };

    FingerprintPort& _port;
    Listener _listener;
    uint32_t _timeoutMs;
    uint32_t _pollMs;
    uint32_t _releaseMs;
    State _state = State::Idle;
    State _afterRelease = State::First;
    uint16_t _id = 0;
    uint8_t _retries = 0;
    uint32_t _phaseStart = 0;
    uint32_t _lastPoll = 0;
    uint32_t _releasedSince = 0;
    bool _releasing = false;

    // Entre dans une attente : l'anneau dit ce que le capteur attend.
    void enter(State state, uint32_t now) {
        _state = state;
        _phaseStart = now;
        _lastPoll = now;
        if (state == State::First || state == State::Second) {
            _port.setAura(FingerprintPort::Aura::Breathing, FingerprintPort::Color::Blue, 0x60, 0);
        } else if (state == State::Release) {
            _releasing = false;
            _port.setAura(FingerprintPort::Aura::Off, FingerprintPort::Color::Blue, 0, 0);
        }
    }

    void firstPose(uint32_t now) {
        const auto extracted = _port.extract(1);
        if (extracted != FingerprintPort::OK) {
            retry(extracted, now);
            return;
        }
        uint16_t found = 0;
        uint16_t score = 0;
        const auto searched = _port.search(1, found, score);
        if (searched == FingerprintPort::OK) {
            fail("already_enrolled", found);
            return;
        }
        if (searched != FingerprintPort::NO_MATCH_IN_LIBRARY) {
            fail("sensor", _id);
            return;
        }
        _port.setAura(FingerprintPort::Aura::On, FingerprintPort::Color::Purple, 0, 0);
        _listener(Step::RemoveFinger, _id, nullptr);
        _afterRelease = State::Second;
        enter(State::Release, now);
    }

    void secondPose(uint32_t now) {
        const auto extracted = _port.extract(2);
        if (extracted != FingerprintPort::OK) {
            retry(extracted, now);
            return;
        }
        const auto merged = _port.createModel();
        if (merged != FingerprintPort::OK) {
            fail(merged == FingerprintPort::MODEL_MISMATCH ? "mismatch" : "sensor", _id);
            return;
        }
        const auto stored = _port.store(1, _id);
        if (stored != FingerprintPort::OK) {
            fail(stored == FingerprintPort::BAD_LOCATION ? "bad_id"
                 : stored == FingerprintPort::FLASH_ERROR ? "storage" : "sensor", _id);
            return;
        }
        _state = State::Idle;
        _port.setAura(FingerprintPort::Aura::Flashing, FingerprintPort::Color::Blue, 0x20, 3);
        _listener(Step::Done, _id, nullptr);
    }

    // Capture inutilisable : on le dit, on attend le retrait du doigt, puis on recommence la même pose.
    void retry(FingerprintPort::Code code, uint32_t now) {
        if (code != FingerprintPort::IMAGE_MESSY && code != FingerprintPort::FEATURES_TOO_FEW) {
            fail("sensor", _id);
            return;
        }
        const char* reason = code == FingerprintPort::IMAGE_MESSY ? "messy" : "few_features";
        if (++_retries > MAX_RETRIES) {
            fail(reason, _id);
            return;
        }
        _listener(Step::Retry, _id, reason);
        _afterRelease = _state;
        enter(State::Release, now);
    }

    void watchRelease(uint32_t now) {
        if (_port.touched()) {
            _releasing = false;
            return;
        }
        if (!_releasing) {
            _releasing = true;
            _releasedSince = now;
            return;
        }
        if (now - _releasedSince < _releaseMs) {
            return;
        }
        const bool second = _afterRelease == State::Second;
        enter(_afterRelease, now);
        if (second) {
            _listener(Step::PlaceAgain, _id, nullptr);  // aussi après une capture ratée de la 2e pose
        }
    }

    void fail(const char* reason, uint16_t id, bool flash = true) {
        _state = State::Idle;
        _port.setAura(flash ? FingerprintPort::Aura::Flashing : FingerprintPort::Aura::Off,
                      FingerprintPort::Color::Red, 0x20, 3);
        _listener(Step::Failed, id, reason);
    }
};
