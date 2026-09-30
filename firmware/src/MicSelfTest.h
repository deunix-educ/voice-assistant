#pragma once

#include <Arduino.h>

#include "MicCapture.h"

/**
 * @brief Auto-test du micro au démarrage : détermine le cadrage et la moitié de trame.
 *
 * Le micro n'est réveillé qu'UNE fois : l'INMP441 s'endort dès que ses horloges
 * s'arrêtent, et chaque réveil produit une dérive lente de sa composante
 * continue. Multiplier les essais, c'était mesurer des réveils, pas la pièce.
 *
 * Tout se décide sur une capture lue en cadrage MSB, par la **structure** des
 * mots reçus, jamais par leur niveau :
 *
 *   - la moitié de trame utile est celle qui n'est ni nulle ni constante ;
 *   - 24 bits utiles : le micro est cadré MSB, c'est gagné ;
 *   - 25 bits utiles : le micro suit le cadrage Philips (un bit de retard),
 *     on bascule et l'on vérifie ;
 *   - toute autre structure est un défaut, et l'analyse bit à bit s'affiche.
 *
 * Le niveau au repos est affiché pour information : il dépend du bruit de la
 * pièce et de la stabilisation du micro, pas de la justesse du réglage.
 */
class MicSelfTest {
public:
    explicit MicSelfTest(MicCapture& mic);

    /**
     * @brief Détermine le cadrage et la moitié de trame, puis configure le micro.
     * @return true si le micro est lu correctement ; il reste alors prêt à capturer.
     */
    bool run();

    /**
     * @brief Imprime le niveau d'une session de parole et le gain conseillé.
     * @param stats Statistiques de la moitié retenue pendant la session.
     */
    void reportSession(const MicCapture::RawStats& stats) const;

private:
    MicCapture& _mic;

    // Capture, affiche le tableau et renvoie la moitié qui porte le micro (ou nullptr)
    const MicCapture::RawStats* measure(MicCapture::FrameStats& frames,
                                        MicCapture::Half& activeHalf);

    // Diagnostic complet en cas d'échec
    void reportFailure(const char* reason);

    // Analyse bit à bit et vidage hexadécimal de la configuration courante
    void dumpBits(const char* label);

    // Test électrique de la ligne SD, horloges correctes
    void reportDataLine();

    // Verdict structurel pour une moitié de trame
    static const char* verdictFor(const MicCapture::RawStats& stats);

    // true si la moitié porte des données (ni nulle, ni constante)
    static bool carriesData(const MicCapture::RawStats& stats);

    // Composante continue (moyenne) en LSB 24 bits
    static float dcLsb24(const MicCapture::RawStats& stats);

    // Valeur efficace des variations, composante continue retirée, en LSB 24 bits
    static float acRmsLsb24(const MicCapture::RawStats& stats);

    // Conversion en dBFS (pleine échelle 24 bits)
    static int dbfs(float lsb24);

    // Nombre de bits utiles : du bit 31 au plus bas bit jamais mis à 1
    static int usedBits(const MicCapture::RawStats& stats);

    static const char* formatName(MicCapture::Format format);
    static const char* halfName(MicCapture::Half half);
};
