#pragma once

#include <Arduino.h>

/**
 * @brief Bouton poussoir anti-rebond, câblé entre la broche et GND.
 *
 * Le rebond (« bounce ») est l'oscillation mécanique du contact pendant quelques
 * millisecondes : sans filtrage, un seul appui produirait plusieurs fronts.
 * La broche utilise le pull-up interne : niveau haut au repos, bas quand on appuie.
 */
class PushButton {
public:
    /**
     * @param pin        Broche du bouton.
     * @param debounceMs Durée de stabilité exigée avant de valider un changement.
     */
    PushButton(int pin, uint32_t debounceMs);

    /// Configure la broche en entrée avec pull-up interne.
    void begin();

    /// À appeler à chaque tour de boucle : met à jour l'état filtré.
    void update();

    /// true tant que le bouton est maintenu enfoncé.
    bool isDown() const;

    /// true une seule fois, au front d'appui (consomme l'événement).
    bool wasPressed();

    /// true une seule fois, au front de relâchement (consomme l'événement).
    bool wasReleased();

    /// Oublie les événements en attente (un appui mémorisé pendant une session, par exemple).
    void clearEvents();

private:
    int _pin;
    uint32_t _debounceMs;
    bool _stableState = false;    ///< état filtré : true = enfoncé
    bool _lastRawState = false;   ///< dernière lecture brute
    uint32_t _lastChangeMs = 0;   ///< date du dernier changement brut
    bool _pressedEvent = false;
    bool _releasedEvent = false;
};
