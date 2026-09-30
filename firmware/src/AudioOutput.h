#pragma once

#include <Arduino.h>
#include <driver/i2s_std.h>

/**
 * @brief Sortie audio vers l'ampli I2S MAX98357A, sur le périphérique I2S1.
 *
 * Reçoit du PCM 16 bits mono et l'émet en **stéréo, chaque échantillon copié
 * sur les deux voies**. Leçon de l'étape 1 : ne rien laisser au comportement
 * implicite d'un mode mono. Le MAX98357A joue par défaut (gauche + droite) / 2 :
 * avec deux voies identiques, le niveau est exact, et une éventuelle inversion
 * gauche/droite n'a aucune conséquence.
 *
 * Le canal tourne en permanence dès begin(). Quand rien n'est à jouer, le DMA
 * envoie des zéros : l'ampli reste cadencé et silencieux, ses broches ne
 * flottent jamais (c'était la cause du souffle au premier branchement).
 */
class AudioOutput {
public:
    /**
     * @param pinBclk Horloge bit vers l'ampli (BCLK).
     * @param pinLrc  Sélection de mot vers l'ampli (LRC).
     * @param pinDin  Données vers l'ampli (DIN).
     */
    AudioOutput(int pinBclk, int pinLrc, int pinDin);

    /**
     * @brief Démarre le canal I2S1 en émission ; l'ampli reçoit aussitôt du silence.
     * @param sampleRate Fréquence d'échantillonnage en Hz.
     * @param philips    true : cadrage Philips (MAX98357A) ; false : MSB (MAX98357B).
     * @return true si le démarrage a réussi.
     */
    bool begin(uint32_t sampleRate, bool philips);

    /**
     * @brief Émet des échantillons mono ; bloque jusqu'à ce que le DMA les accepte.
     *
     * Le blocage rythme naturellement l'appelant sur le débit du DAC. À n'appeler
     * que depuis un seul contexte (la tâche audio, ou setup() avant elle).
     *
     * @param samples Échantillons 16 bits signés.
     * @param count   Nombre d'échantillons.
     * @return Nombre d'échantillons émis.
     */
    size_t write(const int16_t* samples, size_t count);

    /**
     * @brief Joue un son pur, de façon bloquante, avec fondus de 10 ms.
     *
     * Sert de test de la sortie seule : aucun PC, aucune FIFO, aucune tâche.
     *
     * @param frequencyHz Fréquence en Hz.
     * @param durationMs  Durée en millisecondes.
     * @param amplitude   Amplitude crête, 0 à 32767.
     */
    void playTone(uint32_t frequencyHz, uint32_t durationMs, int16_t amplitude);

private:
    /// Trames traitées par passe : 256 = 16 ms à 16 kHz.
    static constexpr size_t BLOCK_FRAMES = 256;

    int _pinBclk;
    int _pinLrc;
    int _pinDin;
    i2s_chan_handle_t _txHandle = nullptr;
    uint32_t _sampleRate = 0;
    int16_t _stereo[BLOCK_FRAMES * 2] = {};  ///< tampon d'émission, voies entrelacées
    int16_t _tone[BLOCK_FRAMES] = {};        ///< tampon de génération du son pur
};
