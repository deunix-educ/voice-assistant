#pragma once

#include <Arduino.h>
#include <driver/i2s_std.h>

/**
 * @brief Capture audio depuis le micro I2S INMP441.
 *
 * Le micro fournit des échantillons 24 bits alignés à gauche dans des mots de
 * 32 bits. Cette classe lit ces mots via le DMA du périphérique I2S0, les
 * convertit en PCM 16 bits signé, supprime l'offset continu et applique un gain.
 *
 * Lecture en **stéréo** : une trame I2S contient deux moitiés, et l'INMP441
 * n'émet que dans l'une des deux, choisie par sa broche L/R. On lit les deux et
 * on choisit en logiciel, en suivant la phase mot par mot.
 */
class MicCapture {
public:
    /// Moitié de trame I2S retenue.
    enum class Half {
        First,   ///< premier mot de chaque trame
        Second,  ///< second mot de chaque trame
    };

    /**
     * @brief Cadrage des bits dans la trame.
     *
     * Philips (I2S standard) : le premier bit de donnée arrive un coup d'horloge
     * APRÈS le changement de WS. MSB (justifié à gauche) : il arrive EN MÊME
     * TEMPS. Un cadrage faux décale tout le mot d'un bit : le bit de signe tombe
     * hors de sa place, et un signal faible prend l'allure d'un bruit pleine
     * échelle.
     */
    enum class Format {
        Philips,
        Msb,
    };

    /// Statistiques sur des mots I2S bruts, avant toute conversion.
    struct RawStats {
        int32_t minWord = 0;        ///< mot le plus négatif observé
        int32_t maxWord = 0;        ///< mot le plus positif observé
        size_t wordCount = 0;       ///< nombre de mots examinés
        size_t zeroCount = 0;       ///< nombre de mots exactement nuls
        int64_t sum = 0;            ///< somme de (mot >> 12), pour la composante continue
        int64_t sumSquares = 0;     ///< somme des carrés de (mot >> 12), pour la valeur efficace
        uint32_t orBits = 0;        ///< OU de tous les mots : quels bits ont servi au moins une fois
        int32_t filteredPeak = 0;   ///< crête après passe-haut, avant gain, en LSB 24 bits (read() seul)
    };

    /// Relevé simultané des deux moitiés d'une trame.
    struct FrameStats {
        RawStats first;
        RawStats second;
    };

    /// Résultat du test électrique de la ligne de données.
    struct DataLineReport {
        uint32_t samples = 0;           ///< nombre de lectures par essai
        uint32_t onesWithPullUp = 0;    ///< niveaux hauts lus avec pull-up interne
        uint32_t onesWithPullDown = 0;  ///< niveaux hauts lus avec pull-down interne
    };

    /**
     * @param pinSck Broche d'horloge bit (SCK / BCLK du micro).
     * @param pinWs  Broche de sélection de mot (WS / LRCL).
     * @param pinSd  Broche de donnée série venant du micro (SD / DOUT).
     */
    MicCapture(int pinSck, int pinWs, int pinSd);

    /**
     * @brief Initialise le périphérique I2S0 en réception stéréo.
     * @param sampleRate Fréquence d'échantillonnage en Hz.
     * @param format     Cadrage des bits dans la trame.
     * @return true si l'initialisation a réussi.
     */
    bool begin(uint32_t sampleRate, Format format = Format::Philips);

    /// Arrête et libère le canal I2S (obligatoire avant un nouveau begin()).
    void end();

    /// Choisit la moitié de trame conservée par read().
    void useHalf(Half half);

    /// Moitié de trame actuellement conservée.
    Half half() const;

    /// Fréquence d'échantillonnage réellement en service.
    uint32_t sampleRate() const;

    /// Cadrage actuellement en service.
    Format format() const;

    /**
     * @brief Lit et convertit des échantillons audio.
     * @param buffer Destination, au moins @p count échantillons.
     * @param count  Nombre d'échantillons 16 bits demandés.
     * @return Nombre d'échantillons réellement écrits (0 en cas d'erreur).
     */
    size_t read(int16_t* buffer, size_t count);

    /**
     * @brief Écoute pendant une durée donnée et analyse les deux moitiés.
     *
     * Les échantillons lus sont consommés : ne pas appeler pendant une session.
     *
     * @param durationMs Durée d'écoute en millisecondes.
     * @return Statistiques séparées des deux moitiés de trame.
     */
    FrameStats probe(uint32_t durationMs);

    /**
     * @brief Copie des mots I2S bruts, sans aucune conversion.
     *
     * Le premier mot copié est toujours une 1re moitié de trame.
     *
     * @param words Destination.
     * @param count Nombre de mots souhaités.
     * @return Nombre de mots réellement copiés.
     */
    size_t readRaw(int32_t* words, size_t count);

    /**
     * @brief Teste si la ligne de données est réellement pilotée par le micro.
     *
     * On applique le pull-up puis le pull-down interne (~45 kOhms) et on
     * échantillonne la broche : une sortie logique écrase ces résistances, une
     * ligne en l'air les suit. Les horloges doivent être CORRECTES pendant le
     * test, sinon le micro ne pilote rien et le verdict ne veut rien dire.
     */
    DataLineReport probeDataLine();

    /// Remet à zéro les statistiques accumulées par read().
    void resetStats();

    /// Statistiques de la moitié retenue, depuis le dernier resetStats().
    RawStats stats() const;

    /**
     * @brief Vide les tampons DMA sans arrêter les horloges du micro.
     *
     * À appeler au début d'une session : sans cela, les premières millisecondes
     * enregistrées seraient de l'audio ancien, capté avant l'appui sur le bouton.
     * Amorce aussi le filtre de composante continue avec la moyenne mesurée.
     */
    void flush();

    /// Met à jour un relevé avec un mot brut (public pour les outils de diagnostic).
    static void accumulate(RawStats& stats, int32_t word);

private:
    /// Taille du tampon de travail 32 bits (1 Kio de RAM, alloué une seule fois).
    static constexpr size_t SCRATCH_WORDS = 256;

    int _pinSck;
    int _pinWs;
    int _pinSd;
    i2s_chan_handle_t _rxHandle = nullptr;
    uint32_t _sampleRate = 0;                ///< fréquence retenue par begin()
    Format _format = Format::Philips;        ///< cadrage retenu par begin()
    Half _half = Half::First;                ///< moitié de trame conservée
    uint8_t _phase = 0;                      ///< 0 si le prochain mot lu est une 1re moitié
    int32_t _lowBand = 0;                    ///< basses fréquences suivies par le passe-haut, en LSB 24 bits
    int32_t _scratch[SCRATCH_WORDS] = {};    ///< tampon de lecture brute
    RawStats _stats;                         ///< statistiques, alimentées par read()

    // Jette les échantillons pendant le transitoire de mise en route du micro
    void settle(uint32_t durationMs);
};
