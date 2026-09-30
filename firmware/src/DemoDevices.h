#pragma once

#include <Arduino.h>

/**
 * @brief Appareils de démonstration de la pièce où se trouve la carte (étape 11).
 *
 * La lumière est une vraie LED (PIN_DEMO_LIGHT, via une résistance de 330 Ω) ;
 * les volets et la porte sont simulés : leur état est gardé en mémoire et
 * affiché sur la liaison série. Un vrai relais se brancherait comme la LED.
 */
class DemoDevices {
public:
    /// @param lightPin Broche de la LED qui figure la lumière.
    explicit DemoDevices(int lightPin);

    /// Configure la broche de la LED, éteinte.
    void begin();

    /**
     * @brief Applique une commande.
     * @param device « light », « shutter » ou « door ».
     * @param action « on »/« off » pour la lumière, « open »/« close » pour volets et porte.
     * @return false si l'appareil ou l'action est inconnu (rien n'est modifié).
     */
    bool apply(const char* device, const char* action);

    /// État courant : « on », « off », « open », « closed », ou « ? » si l'appareil est inconnu.
    const char* state(const char* device) const;

private:
    int _lightPin;
    bool _lightOn = false;
    bool _shutterOpen = false;
    bool _doorOpen = false;
};
