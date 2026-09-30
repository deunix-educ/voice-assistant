#pragma once

#include <Arduino.h>

/**
 * @brief LED d'état, non bloquante.
 *
 * Aucun delay() : la LED clignote grâce à une comparaison de dates dans update(),
 * ce qui laisse le chemin audio libre de toute attente.
 */
class StatusLed {
public:
    explicit StatusLed(int pin);

    /// Configure la broche en sortie, LED éteinte.
    void begin();

    /// Allume la LED en continu.
    void on();

    /// Éteint la LED.
    void off();

    /**
     * @brief Fait clignoter la LED.
     * @param periodMs Période complète du clignotement en millisecondes.
     * @param onMs     Durée allumée dans chaque période ; 0 = la moitié (clignotement régulier).
     *                 Un éclair bref (100 ms toutes les 2 s) signale l'écoute continue (étape 13).
     */
    void blink(uint32_t periodMs, uint32_t onMs = 0);

    /// À appeler à chaque tour de boucle : applique le clignotement.
    void update();

private:
    int _pin;
    bool _blinking = false;
    uint32_t _periodMs = 500;
    uint32_t _onMs = 250;
    uint32_t _lastToggleMs = 0;
    bool _state = false;

    void write(bool state);
};
