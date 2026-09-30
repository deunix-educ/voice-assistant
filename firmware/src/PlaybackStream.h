#pragma once

#include <Arduino.h>

#include <atomic>

#include "AudioFifo.h"
#include "AudioOutput.h"
#include "config.h"

/**
 * @brief Un flux de lecture vers le haut-parleur, quel que soit son transport.
 *
 * Même protocole pour la liaison série (étape 2) et pour MQTT (étape 5) :
 * open() à la trame START, push() pour chaque chunk, close() à la trame END.
 * push() est court et n'attend jamais : il peut être appelé depuis le rappel
 * MQTT. La tâche audio, créée par begin(), vide la FIFO au rythme du DAC.
 *
 * Tampon anti-gigue (étape 12) : rien n'est joué tant que la FIFO ne contient
 * pas PLAYBACK_START_MS de son (amorçage). Le Wi-Fi livre les chunks de façon
 * irrégulière ; la réserve absorbe ces écarts. Si elle s'épuise quand même, la
 * lecture s'arrête proprement et attend de l'avoir reconstituée (réamorçage),
 * au lieu de jouer des bribes hachées. Un son plus court que la réserve est joué
 * dès que la fin est annoncée.
 *
 * Un bilan est produit quand le dernier échantillon a été joué, ou quand la
 * source s'est tue sans envoyer de END.
 */
class PlaybackStream {
public:
    /// Bilan d'un flux, disponible via takeReport().
    struct Report {
        char session[16] = {};
        bool fromMqtt = false;      ///< transport du flux (pour savoir où publier le bilan)
        bool endReceived = false;   ///< false : la source s'est tue sans END
        uint32_t chunks = 0;        ///< chunks reçus
        int32_t announced = -1;     ///< chunks annoncés par le END, -1 si inconnu
        uint32_t overruns = 0;      ///< chunks refusés, FIFO pleine
        uint32_t underruns = 0;     ///< réserve épuisée en plein flux : une pause, puis réamorçage
        uint32_t durationMs = 0;    ///< du START au dernier échantillon joué
        uint32_t startMs = 0;       ///< du START au premier échantillon joué (amorçage)
        int32_t minMarginMs = -1;   ///< plus petite réserve pendant la lecture, -1 si non mesurée
        uint32_t gapAtMs[4] = {};   ///< instant des premiers réamorçages, depuis le premier son (diagnostic)
    };

    /// Nombre de réamorçages dont l'instant est gardé.
    static constexpr size_t MAX_GAPS = 4;

    PlaybackStream(AudioOutput& speaker, AudioFifo& fifo);

    /// Crée la tâche audio (la FIFO doit être prête).
    bool begin();

    /**
     * @brief Ouvre un flux. Un flux encore ouvert est clos d'office.
     * @return false si le format n'est pas celui du système : le flux est refusé.
     */
    bool open(const char* session, uint32_t rate, uint32_t bits, uint32_t channels,
              bool fromMqtt);

    /// Ajoute un chunk ; ignoré si aucun flux n'est ouvert ou si sa longueur est impaire.
    void push(const uint8_t* data, size_t length);

    /**
     * @brief Ferme le flux ; le bilan viendra quand tout aura été joué.
     * @param announcedChunks Chunks annoncés par la source, -1 si inconnu.
     */
    void close(int32_t announcedChunks);

    /**
     * @brief À appeler depuis loop() : true une seule fois, quand un bilan est prêt.
     * @param report Reçoit le bilan.
     */
    bool takeReport(Report& report);

    /// true entre open() et le bilan.
    bool active() const;

private:
    /// Silence après lequel on considère le flux fini (ms).
    static constexpr uint32_t DRAIN_SILENCE_MS = 300;
    /// Silence après lequel une source muette est abandonnée, sans END (ms).
    static constexpr uint32_t ABANDON_MS = 2000;
    /// Période de surveillance de la FIFO pendant l'amorçage (ms).
    static constexpr uint32_t PRIME_POLL_MS = 5;

    /// Ce que fait la tâche audio.
    enum class State : uint8_t {
        Idle,     ///< aucun flux
        Priming,  ///< réserve en cours de constitution : on ne joue rien
        Playing,  ///< lecture au rythme du DAC
    };

    AudioOutput& _speaker;
    AudioFifo& _fifo;
    bool _open = false;          ///< loop() seulement
    bool _closed = false;        ///< END reçu, on attend la fin de la lecture
    uint32_t _startMs = 0;
    uint32_t _lastPushMs = 0;
    uint32_t _overrunsAtStart = 0;
    Report _report;

    // Partagés avec la tâche audio.
    std::atomic<State> _state{State::Idle};
    std::atomic<bool> _endOfStream{false};     ///< END reçu (ou source abandonnée) : la FIFO peut se vider
    std::atomic<uint32_t> _underruns{0};
    std::atomic<uint32_t> _firstSoundMs{0};    ///< date du premier échantillon joué, 0 : pas encore
    std::atomic<uint32_t> _minMarginBytes{UINT32_MAX};
    std::atomic<uint32_t> _gapAtMs[MAX_GAPS] = {};

    StaticTask_t _taskControl;
    StackType_t _taskStack[AUDIO_TASK_STACK_BYTES];  // ESP-IDF : StackType_t = octet

    static void taskEntry(void* self);
    void taskLoop();
    void finish(bool endReceived);
};
