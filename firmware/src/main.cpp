/**
 * @file main.cpp
 * @brief Étapes 1 à 5 — voix vers le serveur et réponses du serveur, par MQTT.
 *
 * Entrée (étape 4) : PTT -> micro I2S0 -> tâche de capture -> FIFO -> loop() -> MQTT audio/in
 *                    (ou liaison série si le broker est absent, format de l'étape 1).
 * Sortie (étapes 2 et 5) : MQTT audio/out ou trames série -> PlaybackStream -> FIFO
 *                          -> tâche audio -> I2S1 -> ampli.
 * Réseau (étape 3) : Wi-Fi + MQTT ; état retenu sur voice/<id>/state, testament « offline ».
 * Fiabilisation (étape 15) : compte MQTT, chien de garde des tâches, présence du serveur
 *                            (voice/server/status) signalée dans le journal.
 * Empreinte (branche fingerprint, F1 à F3) : capteur R503 ; un doigt reconnu ouvre la session
 *                            de parole comme le bouton, et la ferme quand il est levé.
 *
 * Les deux chemins sont indépendants : un micro en panne n'empêche pas la
 * lecture, et inversement. Aucune allocation dynamique dans les chemins audio.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <esp_system.h>
#include <esp_task_wdt.h>

#include <atomic>

#include "AudioFifo.h"
#include "AudioOutput.h"
#include "CommandHandler.h"
#include "DemoDevices.h"
#include "FingerprintEnrollment.h"
#include "FingerprintSensor.h"
#include "FingerprintTrigger.h"
#include "MicCapture.h"
#include "MicSelfTest.h"
#include "MqttLink.h"
#include "PlaybackStream.h"
#include "PushButton.h"
#include "SerialFrameReader.h"
#include "SerialFramer.h"
#include "StatusLed.h"
#include "VoiceSession.h"
#include "WifiConfig.h"
#include "WifiLink.h"
#include "config.h"

// secrets.h est optionnel : s'il est présent ses valeurs servent de repli si
// la NVS est vide. Sans lui, le portail AP/HTTP est lancé au premier démarrage.
#if __has_include("secrets.h")
#include "secrets.h"
#else
static constexpr const char* WIFI_SSID     = "";
static constexpr const char* WIFI_PASSWORD = "";
static constexpr const char* MQTT_HOST     = "";
static constexpr uint16_t    MQTT_PORT     = 1883;
static constexpr const char* MQTT_USER     = "";
static constexpr const char* MQTT_PASSWORD = "";
#endif

namespace {

using SerialProtocol::FrameType;

MicCapture mic(PIN_MIC_SCK, PIN_MIC_WS, PIN_MIC_SD);
MicSelfTest selfTest(mic);
AudioOutput speaker(PIN_AMP_BCLK, PIN_AMP_LRC, PIN_AMP_DIN);
// Deux FIFO, stockage statique fourni par nous (+1 octet exigé par FreeRTOS).
uint8_t playbackStorage[PLAYBACK_FIFO_BYTES + 1];
uint8_t captureStorage[CAPTURE_FIFO_BYTES + 1];
AudioFifo playbackFifo;  ///< PC -> tâche audio -> ampli
AudioFifo captureFifo;   ///< micro -> tâche de capture -> loop() -> réseau
PushButton button(PIN_BUTTON_PTT, BUTTON_DEBOUNCE_MS);
FingerprintSensor fingerprint(Serial2, PIN_FP_RX, PIN_FP_TX, PIN_FP_TOUCH);  ///< capteur R503 (branche fingerprint)
void onEnrollmentStep(FingerprintEnrollment::Step step, uint16_t id, const char* reason);
FingerprintEnrollment enrollment(fingerprint, onEnrollmentStep, FP_ENROLL_TIMEOUT_MS);
void onFingerEvent(FingerprintTrigger::Event event, uint16_t id, uint16_t score, const char* reason);

/// Durées du déclencheur « doigt » (config.h).
FingerprintTrigger::Timing fingerTiming() {
    FingerprintTrigger::Timing timing;
    timing.settleMs = FP_TOUCH_SETTLE_MS;
    timing.releaseMs = FP_RELEASE_MS;
    timing.identifyMs = FP_IDENTIFY_TIMEOUT_MS;
    timing.pollMs = FP_POLL_MS;
    timing.attempts = FP_IDENTIFY_ATTEMPTS;
    return timing;
}
FingerprintTrigger fingerTrigger(fingerprint, onFingerEvent, fingerTiming());
StatusLed led(PIN_STATUS_LED);
DemoDevices devices(PIN_DEMO_LIGHT);
SerialFramer framer(Serial);
SerialFrameReader reader(Serial);
WiFiClient network;
// cfg DOIT être déclaré avant wifi et mqtt : leurs constructeurs stockent des
// pointeurs vers ses tampons internes, remplis plus tard par cfg.begin().
WifiConfig cfg;
WifiLink wifi(cfg.ssid(), cfg.password(), DEVICE_ID);
MqttLink mqtt(network, cfg.mqttHost(), cfg.mqttPort(), DEVICE_ID, cfg.mqttUser(), cfg.mqttPassword());

/// Chunk en cours d'envoi réseau : 1600 octets, alloué une seule fois.
uint8_t outgoingChunk[AUDIO_CHUNK_BYTES];

/// Durée maximale d'une session d'enregistrement, garde-fou si le bouton reste bloqué (ms).
constexpr uint32_t SESSION_MAX_MS = 15000;

// --- Session de parole (étape 4), manipulée par loop() seulement.
VoiceSession session(mqtt, framer);
uint32_t captureOverrunsAtStart = 0;
bool micReady = false;
bool fingerSession = false;      ///< la session en cours a été ouverte par un doigt (F3) : il la ferme

bool serverOnline = false;       ///< présence annoncée par le serveur (étape 15), pour le journal

// Partagés entre loop() et la tâche de capture.
std::atomic<bool> captureWanted{false};   ///< loop() demande la capture
std::atomic<bool> captureRunning{false};  ///< la tâche capture effectivement
bool speakerReady = false;
uint32_t lastStateMs = 0;

// --- Lecture (étapes 2 et 5) et commandes (étape 5).
PlaybackStream playback(speaker, playbackFifo);
CommandHandler commands(playback, mqtt, devices);
void onFingerprintCommand(const char* command, int id, bool all);

// Pile et contrôle de la tâche de capture, alloués statiquement.
StaticTask_t captureTaskControl;
StackType_t captureTaskStack[CAPTURE_TASK_STACK_BYTES];

/**
 * @brief Tâche de capture : micro -> FIFO de capture, au rythme du micro.
 *
 * Elle ne touche jamais au réseau : un envoi Wi-Fi peut bloquer, et pendant ce
 * temps le DMA du micro (75 ms de réserve) déborderait. Ici, rien ne bloque
 * sauf la lecture du micro elle-même, qui rend la main toutes les 50 ms.
 */
void captureTask(void* /*unused*/) {
    static int16_t chunk[AUDIO_CHUNK_SAMPLES];
    esp_task_wdt_add(nullptr);  // surveillée par le chien de garde (étape 15)

    for (;;) {
        esp_task_wdt_reset();  // « je suis vivante » : au moins toutes les 50 ms
        if (!captureWanted.load()) {
            captureRunning.store(false);
            vTaskDelay(pdMS_TO_TICKS(5));
            continue;
        }

        if (!captureRunning.load()) {
            mic.flush();       // on jette l'audio capté avant l'appui
            mic.resetStats();  // les statistiques ne portent que sur cette session
            captureRunning.store(true);
        }

        const size_t samples = mic.read(chunk, AUDIO_CHUNK_SAMPLES);  // ~50 ms
        if (samples > 0) {
            // Tout ou rien : si la FIFO est pleine, le chunk est compté perdu.
            captureFifo.write(reinterpret_cast<const uint8_t*>(chunk), samples * sizeof(int16_t));
        }
    }
}

/// Lit un entier little-endian dans un payload.
uint32_t readLe(const uint8_t* data, size_t bytes) {
    uint32_t value = 0;
    for (size_t i = 0; i < bytes; ++i) {
        value |= static_cast<uint32_t>(data[i]) << (8 * i);
    }
    return value;
}

/// Traite une trame reçue du PC par la liaison série (étape 2).
void handleFrame(const SerialFrameReader::Frame& frame) {
    switch (frame.type) {
    case FrameType::Start:
        if (frame.length != SerialProtocol::START_PAYLOAD) {
            Serial.println("# lecture refusee : descripteur START invalide");
            return;
        }
        playback.open("serie", readLe(frame.payload, 4), readLe(frame.payload + 4, 2),
                      readLe(frame.payload + 6, 2), false);
        break;
    case FrameType::Audio:
        playback.push(frame.payload, frame.length);
        break;
    case FrameType::End:
        playback.close(-1);  // la liaison série n'annonce pas le nombre de chunks
        break;
    }
}

/// Affiche le bilan d'un flux joué, et le publie s'il venait du serveur.
void reportPlayback(const PlaybackStream::Report& report) {
    Serial.printf("# lecture %s : %s, %lu chunks recus", report.session,
                  report.endReceived ? "terminee" : "abandonnee (pas de END)",
                  static_cast<unsigned long>(report.chunks));
    if (report.announced >= 0) {
        Serial.printf(" sur %ld annonces", static_cast<long>(report.announced));
    }
    Serial.printf(", %lu refuses (FIFO pleine), %lu reamorcages, premier son a %lu ms, "
                  "marge mini %ld ms, %lu ms\n",
                  static_cast<unsigned long>(report.overruns),
                  static_cast<unsigned long>(report.underruns),
                  static_cast<unsigned long>(report.startMs),
                  static_cast<long>(report.minMarginMs),
                  static_cast<unsigned long>(report.durationMs));

    // Instants des réamorçages (depuis le premier son) : au début, au milieu, ou à la fin ?
    char gaps[48] = "";
    size_t used = 0;
    for (size_t i = 0; i < PlaybackStream::MAX_GAPS && i < report.underruns; ++i) {
        used += snprintf(gaps + used, sizeof(gaps) - used, "%s%lu", i == 0 ? "" : ",",
                         static_cast<unsigned long>(report.gapAtMs[i]));
    }
    if (report.underruns > 0) {
        Serial.printf("# lecture %s : reamorcages a [%s] ms du premier son\n", report.session, gaps);
    }

    if (!report.fromMqtt) {
        return;
    }
    char payload[304];
    snprintf(payload, sizeof(payload),
             "{\"event\":\"played\",\"session\":\"%s\",\"end\":%s,\"chunks\":%lu,"
             "\"announced\":%ld,\"overruns\":%lu,\"underruns\":%lu,\"duration_ms\":%lu,"
             "\"start_ms\":%lu,\"min_margin_ms\":%ld,\"gaps_ms\":[%s]}",
             report.session, report.endReceived ? "true" : "false",
             static_cast<unsigned long>(report.chunks), static_cast<long>(report.announced),
             static_cast<unsigned long>(report.overruns),
             static_cast<unsigned long>(report.underruns),
             static_cast<unsigned long>(report.durationMs),
             static_cast<unsigned long>(report.startMs),
             static_cast<long>(report.minMarginMs), gaps);
    mqtt.publish("event", payload, false);
}

/// Cause du dernier redémarrage, en mot court pour le JSON.
const char* resetReason() {
    switch (esp_reset_reason()) {
    case ESP_RST_POWERON:
        return "poweron";
    case ESP_RST_EXT:
    case ESP_RST_USB:
        return "external";  // broche EN, ouverture du port série, ou reset demandé par l'USB natif (ESP32-S3)
    case ESP_RST_SW:
        return "software";
    case ESP_RST_PANIC:
        return "panic";     // plantage du firmware
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT:
        return "watchdog";  // une tâche a monopolisé le processeur
    case ESP_RST_BROWNOUT:
        return "brownout";  // chute de tension : alimentation trop faible (ampli !)
    default:
        return "other";
    }
}

/**
 * @brief Publie l'état de la carte, en message retenu.
 *
 * Retenu : le broker garde le dernier état et le donne aussitôt à tout nouvel
 * abonné. Un serveur qui démarre après l'ESP32 sait donc immédiatement s'il est
 * en ligne, sans attendre la prochaine publication.
 */
void publishState() {
    char payload[192];
    snprintf(payload, sizeof(payload),
             "{\"status\":\"online\",\"ip\":\"%s\",\"rssi\":%d,\"uptime_s\":%lu,"
             "\"mic\":%s,\"speaker\":%s}",
             wifi.ip().toString().c_str(), wifi.rssi(),
             static_cast<unsigned long>(millis() / 1000), micReady ? "true" : "false",
             speakerReady ? "true" : "false");
    mqtt.publish("state", payload, true);
    lastStateMs = millis();
}

/// Présence du serveur : absent, plus rien ne sort du micro (étape 15).
void onServerStatus(const uint8_t* payload, size_t length) {
    char text[64];
    const size_t n = length < sizeof(text) - 1 ? length : sizeof(text) - 1;
    memcpy(text, payload, n);
    text[n] = '\0';
    const bool online = strstr(text, "\"online\"") != nullptr;
    if (online == serverOnline) {
        return;
    }
    serverOnline = online;
    if (online) {
        Serial.println("# serveur : en ligne");
        return;
    }
    // Testament du serveur (plantage) ou arrêt : une session ouverte maintenant ne recevrait pas de réponse.
    Serial.println("# serveur : ABSENT (une session de parole ne recevrait pas de reponse)");
}

/// Appelé à chaque connexion au broker.
void onMqttConnect(bool firstTime) {
    serverOnline = false;  // l'état retenu du serveur arrive avec l'abonnement
    mqtt.subscribe("control");
    mqtt.subscribe("audio/out");
    mqtt.subscribeTopic(MQTT_SERVER_STATUS_TOPIC);
    publishState();  // remplace aussitôt le « offline » retenu d'une absence précédente

    if (firstTime) {
        char payload[96];
        snprintf(payload, sizeof(payload), "{\"event\":\"boot\",\"reason\":\"%s\"}",
                 resetReason());
        mqtt.publish("event", payload, false);
    }
}

/// Appelé pour chaque message reçu. Court : pas d'attente, pas de traitement lourd.
void onMqttMessage(const char* suffix, const uint8_t* payload, size_t length) {
    if (strcmp(suffix, "audio/out") == 0) {
        playback.push(payload, length);  // ne fait que remplir la FIFO
    } else if (strcmp(suffix, "control") == 0) {
        commands.handle(payload, length);
    } else if (strcmp(suffix, MQTT_SERVER_STATUS_TOPIC) == 0) {
        onServerStatus(payload, length);
    }
}

/// Arrête la capture et jette ce qui restait : rien ne sort du micro sans session.
void discardCapture() {
    captureWanted.store(false);
    const uint32_t waitStart = millis();
    while (captureRunning.load() && (millis() - waitStart) < 200) {
        delay(1);
    }
    while (captureFifo.read(outgoingChunk, sizeof(outgoingChunk), 0) > 0) {
    }
}

/// Lance la capture du micro. Sans effet si elle tourne déjà (le doigt la lance dès la pose).
void beginCapture() {
    if (captureWanted.load()) {
        return;
    }
    captureOverrunsAtStart = captureFifo.overruns();
    captureWanted.store(true);
}

/**
 * Ouvre une session de parole : MQTT si possible, liaison série sinon.
 * @param fingerId Emplacement du doigt reconnu qui l'ouvre (F3) ; -1 pour le bouton.
 */
void startSession(int fingerId = -1, uint16_t fingerScore = 0) {
    const bool viaMqtt = mqtt.connected();
    if (!viaMqtt) {
        Serial.println("# session : MQTT non connecte, envoi par la liaison serie (make record)");
    }

    beginCapture();
    session.start(viaMqtt ? VoiceSession::Transport::Mqtt : VoiceSession::Transport::Serial,
                  mic.sampleRate(), fingerId, fingerScore);
    led.on();
}

/// Envoie au réseau les chunks complets en attente. Peut bloquer sur le Wi-Fi.
void drainCapture(bool includePartial) {
    for (;;) {
        const size_t available = captureFifo.available();
        if (available == 0 || (!includePartial && available < AUDIO_CHUNK_BYTES)) {
            return;
        }
        const size_t bytes = captureFifo.read(outgoingChunk, sizeof(outgoingChunk), 0);
        if (bytes == 0) {
            return;
        }
        session.sendChunk(outgoingChunk, bytes);
        esp_task_wdt_reset();  // chaque envoi peut attendre le Wi-Fi jusqu'à 10 s : on avance
    }
}

/// Ferme la session : arrête la capture, envoie le reste, publie le bilan.
void stopSession(const char* reason) {
    captureWanted.store(false);
    // La tâche finit le chunk en cours (50 ms au plus) avant de s'arrêter.
    const uint32_t waitStart = millis();
    while (captureRunning.load() && (millis() - waitStart) < 200) {
        delay(1);
    }

    drainCapture(true);
    const uint32_t overruns = captureFifo.overruns() - captureOverrunsAtStart;
    session.end(overruns);
    led.off();

    Serial.printf("\n# session %s terminee (%s, %s) : %lu chunks envoyes, %lu echecs d'envoi, "
                  "%lu perdus faute de place, %lu ms\n",
                  session.id(), reason,
                  session.transport() == VoiceSession::Transport::Mqtt ? "MQTT" : "serie",
                  static_cast<unsigned long>(session.chunksSent()),
                  static_cast<unsigned long>(session.sendFailures()),
                  static_cast<unsigned long>(overruns),
                  static_cast<unsigned long>(session.durationMs()));
    if (session.durationMs() < 300) {
        // Trop court pour contenir une parole : appui trop bref, ou bouton qui
        // « lâche » tout seul (contact intermittent, rebond). Le serveur l'ignore.
        Serial.printf("# ATTENTION : session de %lu ms seulement. Si vous teniez le bouton,\n"
                      "#   son contact est intermittent : verifiez ses fils sur la plaque.\n",
                      static_cast<unsigned long>(session.durationMs()));
    }
    selfTest.reportSession(mic.stats());
}

/**
 * @brief Chien de garde (étape 15) : délai porté à WATCHDOG_TIMEOUT_S, redémarrage si dépassé.
 *
 * loop() est surveillée à chaque tour ; les tâches capture et audio s'inscrivent elles-mêmes.
 * La tâche « idle » du cœur 0 reste surveillée : si le Wi-Fi (cœur 0) monopolise le processeur,
 * le chien de garde le voit aussi.
 */
void setupWatchdog() {
    const esp_task_wdt_config_t config = {
        .timeout_ms = WATCHDOG_TIMEOUT_S * 1000,
        .idle_core_mask = 1 << 0,
        .trigger_panic = true,  // redémarrage, et non simple message
    };
    if (esp_task_wdt_reconfigure(&config) != ESP_OK) {
        Serial.println("# ERREUR : chien de garde non configure");
        return;
    }
    enableLoopWDT();
    Serial.printf("# chien de garde : %lu s (loop, capture, audio)\n",
                  static_cast<unsigned long>(WATCHDOG_TIMEOUT_S));
}

/**
 * @brief Étape F1 : dialogue avec le capteur d'empreinte et essai de l'anneau lumineux.
 *
 * Affiche ce que le capteur répond, pour diagnostiquer le câblage sans autre outil.
 */
void probeFingerprint() {
    Serial.printf("# empreinte : R503 sur RX=GPIO %d, TX=GPIO %d, touche=GPIO %d, %lu bauds\n", PIN_FP_RX,
                  PIN_FP_TX, PIN_FP_TOUCH, static_cast<unsigned long>(FP_BAUD));
    if (!fingerprint.begin(FP_BAUD, FP_PASSWORD)) {
        // begin() ne dit pas pourquoi : on refait l'échange le plus simple pour afficher la cause.
        Serial.printf("# empreinte : ABSENT (%s). Verifier fils jaune/GPIO %d, marron-vert/GPIO %d, 3,3 V.\n",
                      FingerprintSensor::describe(fingerprint.handShake()), PIN_FP_RX, PIN_FP_TX);
        return;
    }
    FingerprintSensor::Parameters parameters;
    uint16_t count = 0;
    const auto shake = fingerprint.handShake();
    const auto read = fingerprint.readParameters(parameters);
    const auto stored = fingerprint.templateCount(count);
    Serial.printf("# empreinte : poignee de main : %s\n", FingerprintSensor::describe(shake));
    if (read == FingerprintSensor::OK) {
        Serial.printf("# empreinte : capacite %u empreintes, securite %u/5, paquets de %u octets, %lu bauds\n",
                      parameters.libraryCapacity, parameters.securityLevel, parameters.packetBytes,
                      static_cast<unsigned long>(parameters.baud));
    } else {
        Serial.printf("# empreinte : lecture des reglages : %s\n", FingerprintSensor::describe(read));
    }
    if (stored == FingerprintSensor::OK) {
        Serial.printf("# empreinte : %u empreinte(s) enregistree(s)\n", count);
    }
    // Au démarrage, retirez le doigt : un « doigt pose » ici veut dire polarité inversée
    // (FP_TOUCH_ACTIVE_LOW) ou fil bleu mal branché.
    Serial.printf("# empreinte : au repos, la ligne de touche est a %s -> %s\n",
                  digitalRead(PIN_FP_TOUCH) == HIGH ? "l'etat haut" : "l'etat bas",
                  fingerprint.touched() ? "lue comme doigt POSE (anormal : verifier FP_TOUCH_ACTIVE_LOW)"
                                        : "lue comme doigt retire (normal)");
    // Essai de l'anneau : trois couleurs d'affilée, pour voir que les commandes passent.
    for (const auto color : {FingerprintSensor::Color::Red, FingerprintSensor::Color::Blue,
                             FingerprintSensor::Color::Purple}) {
        fingerprint.setAura(FingerprintSensor::Aura::On, color, 0, 0);
        delay(350);
    }
    fingerprint.setAura(FingerprintSensor::Aura::Off, FingerprintSensor::Color::Blue, 0, 0);
    Serial.println("# empreinte : posez un doigt enregistre et parlez : anneau violet (identification) puis bleu "
                   "(j'ecoute) ; levez le doigt pour finir");
}

/**
 * @brief Étape F2 : une étape de l'enregistrement, journalisée et publiée sur voice/<id>/event.
 *
 * {"event":"fp_enroll","step":"start|retry|remove_finger|place_again|done|failed","id":3,"reason":"messy"}
 * Pour « failed/already_enrolled », « id » est l'emplacement du doigt déjà connu.
 */
void onEnrollmentStep(FingerprintEnrollment::Step step, uint16_t id, const char* reason) {
    using Step = FingerprintEnrollment::Step;
    const char* name = "start";
    switch (step) {
    case Step::Start: name = "start"; break;
    case Step::Retry: name = "retry"; break;
    case Step::RemoveFinger: name = "remove_finger"; break;
    case Step::PlaceAgain: name = "place_again"; break;
    case Step::Done: name = "done"; break;
    case Step::Failed: name = "failed"; break;
    }
    Serial.printf("# empreinte : enregistrement %s, emplacement %u%s%s\n", name, id, reason != nullptr ? ", " : "",
                  reason != nullptr ? reason : "");
    char payload[128];
    if (reason != nullptr) {
        snprintf(payload, sizeof(payload), "{\"event\":\"fp_enroll\",\"step\":\"%s\",\"id\":%u,\"reason\":\"%s\"}",
                 name, id, reason);
    } else {
        snprintf(payload, sizeof(payload), "{\"event\":\"fp_enroll\",\"step\":\"%s\",\"id\":%u}", name, id);
    }
    mqtt.publish("event", payload, false);
}

/// Publie l'échec d'une commande d'empreinte refusée avant même de toucher au capteur.
void publishEnrollmentRefused(int id, const char* reason) {
    char payload[128];
    snprintf(payload, sizeof(payload), "{\"event\":\"fp_enroll\",\"step\":\"failed\",\"id\":%d,\"reason\":\"%s\"}", id,
             reason);
    mqtt.publish("event", payload, false);
}

/// Étape F2 : liste des emplacements occupés, publiée sur voice/<id>/event.
void publishFingerprintList() {
    uint16_t ids[FingerprintSensor::DEFAULT_CAPACITY];
    size_t found = 0;
    uint16_t count = 0;
    const auto listed = fingerprint.listTemplates(ids, sizeof(ids) / sizeof(ids[0]), found);
    const auto counted = fingerprint.templateCount(count);
    char payload[1280];  // 200 emplacements de 3 chiffres + virgule au pire : ~800 octets
    int used = snprintf(payload, sizeof(payload), "{\"event\":\"fp_list\",\"ok\":%s,\"capacity\":%u,\"count\":%u,\"ids\":[",
                        listed == FingerprintSensor::OK ? "true" : "false", fingerprint.capacity(),
                        counted == FingerprintSensor::OK ? count : 0);
    for (size_t i = 0; listed == FingerprintSensor::OK && i < found && i < sizeof(ids) / sizeof(ids[0]) &&
                       used < static_cast<int>(sizeof(payload)) - 8;
         ++i) {
        used += snprintf(payload + used, sizeof(payload) - used, "%s%u", i == 0 ? "" : ",", ids[i]);
    }
    snprintf(payload + used, sizeof(payload) - used, "]}");
    Serial.printf("# empreinte : liste : %u emplacement(s) occupe(s), le capteur en compte %u (%s)\n",
                  static_cast<unsigned>(found), count, FingerprintSensor::describe(listed));
    mqtt.publish("event", payload, false);
}

/// Étape F2 : suppression d'une empreinte (ou de toutes), confirmée sur voice/<id>/event.
void deleteFingerprint(int id, bool all) {
    const auto code = all ? fingerprint.removeAll() : fingerprint.remove(static_cast<uint16_t>(id));
    char payload[128];
    if (all) {
        snprintf(payload, sizeof(payload), "{\"event\":\"fp_delete\",\"all\":true,\"ok\":%s}",
                 code == FingerprintSensor::OK ? "true" : "false");
    } else {
        snprintf(payload, sizeof(payload), "{\"event\":\"fp_delete\",\"id\":%d,\"ok\":%s}", id,
                 code == FingerprintSensor::OK ? "true" : "false");
    }
    Serial.printf("# empreinte : suppression %s : %s\n", all ? "de toutes" : "d'une", FingerprintSensor::describe(code));
    mqtt.publish("event", payload, false);
}

/// Commandes fp_* du serveur (CommandHandler) : enregistrer, supprimer, lister, annuler.
void onFingerprintCommand(const char* command, int id, bool all) {
    if (!fingerprint.present()) {
        Serial.println("# empreinte : commande ignoree, capteur absent");
        if (strcmp(command, "fp_enroll") == 0) {
            publishEnrollmentRefused(id, "sensor");
        }
        return;
    }
    if (strcmp(command, "fp_list") == 0) {
        publishFingerprintList();
    } else if (strcmp(command, "fp_cancel") == 0) {
        enrollment.cancel();
    } else if (strcmp(command, "fp_delete") == 0) {
        if (enrollment.busy()) {
            Serial.println("# empreinte : suppression refusee pendant un enregistrement");
        } else if (all || (id >= 0 && id < fingerprint.capacity())) {
            deleteFingerprint(id, all);
        }
    } else if (strcmp(command, "fp_enroll") == 0) {
        if (id < 0 || id >= fingerprint.capacity()) {
            publishEnrollmentRefused(id, "bad_id");
        } else if (fingerTrigger.busy() || !enrollment.begin(static_cast<uint16_t>(id), millis())) {
            publishEnrollmentRefused(id, "busy");
        }
    }
}

/**
 * @brief Étape F3 : ce que fait la carte à chaque étape du déclencheur « doigt ».
 *
 * Posé : le micro démarre aussitôt (le début de la phrase n'attend pas l'identification).
 * Reconnu : la session s'ouvre, avec l'emplacement du doigt dans son START.
 * Refusé : le son est jeté, voice/<id>/event reçoit {"event":"fp_denied","reason":"unknown"}.
 * Levé : la session se ferme.
 */
void onFingerEvent(FingerprintTrigger::Event event, uint16_t id, uint16_t score, const char* reason) {
    using Event = FingerprintTrigger::Event;
    switch (event) {
    case Event::Touched:
        Serial.println("# empreinte : doigt pose, identification...");
        if (session.active()) {
            break;  // le bouton a ouvert une session entre-temps : il garde le micro
        }
        beginCapture();
        led.on();
        break;
    case Event::Recognized:
        Serial.printf("# empreinte : doigt reconnu, emplacement %u (score %u)\n", id, score);
        if (session.active()) {
            break;
        }
        if (!mqtt.connected()) {
            // Sans broker, ni le serveur ni l'identité ne peuvent suivre : la liaison série ne porte pas le doigt.
            Serial.println("# empreinte : MQTT non connecte, session refusee");
            discardCapture();
            led.blink(1000);
            break;
        }
        startSession(id, score);
        fingerSession = true;
        break;
    case Event::Rejected: {
        Serial.printf("# empreinte : doigt refuse (%s)\n", reason);
        if (!session.active()) {
            discardCapture();
            led.blink(1000);
        }
        char payload[64];
        snprintf(payload, sizeof(payload), "{\"event\":\"fp_denied\",\"reason\":\"%s\"}", reason);
        mqtt.publish("event", payload, false);
        break;
    }
    case Event::Released:
        Serial.println("# empreinte : doigt retire");
        if (fingerSession && session.active()) {
            stopSession("doigt retire");
            led.blink(1000);
        }
        fingerSession = false;
        break;
    }
}

/**
 * @brief Étapes F1 à F3 : l'enregistrement d'un doigt, sinon le déclencheur de session.
 *
 * Un doigt ne peut ouvrir une session que si le micro est libre (ni bouton, ni session) et le
 * broker joignable. Le doigt qui a servi à l'enregistrement ne doit pas en ouvrir une en partant.
 */
void updateFingerprint() {
    if (!fingerprint.present()) {
        return;
    }
    if (enrollment.busy()) {
        enrollment.update(millis());
        fingerTrigger.requireLift();
        return;
    }
    const bool canStart = micReady && mqtt.connected() && !session.active();
    fingerTrigger.update(millis(), canStart);
}

}  // namespace

void setup() {
    // En tout premier : la sortie audio prend les broches de l'ampli et lui envoie
    // du silence. Laissées flottantes, elles le faisaient souffler à plein volume.
    speakerReady = speaker.begin(AUDIO_SAMPLE_RATE, AMP_FORMAT_PHILIPS);

    Serial.setRxBufferSize(SERIAL_RX_BUFFER_BYTES);  // avant begin(), sinon ignoré
    Serial.begin(SERIAL_BAUD);
#if ARDUINO_USB_CDC_ON_BOOT
    // USB natif (ESP32-S3) : sans terminal ouvert, la file d'émission se remplit ; chaque message attendrait
    // alors 100 ms qu'un lecteur arrive. On jette plutôt les messages : l'audio ne doit jamais attendre.
    Serial.setTxTimeoutMs(0);
#endif
    // Hors chemin audio : le moniteur série se reconnecte après le reset de la
    // carte. Sans cette attente, les premières lignes de l'auto-test sont perdues.
    delay(BOOT_SERIAL_DELAY_MS);

    led.begin();
    devices.begin();
    button.begin();

    // Détection du mode configuration : bouton PTT tenu dès le démarrage.
    // On attend WIFI_CONFIG_HOLD_MS ms ; si relâché avant, démarrage normal.
    bool forceConfig = false;
    if (digitalRead(PIN_BUTTON_PTT) == LOW) {
        Serial.println("# bouton PTT detecte au demarrage, attente pour confirmer...");
        const uint32_t holdStart = millis();
        while (digitalRead(PIN_BUTTON_PTT) == LOW) {
            if (millis() - holdStart >= WIFI_CONFIG_HOLD_MS) {
                forceConfig = true;
                break;
            }
            delay(10);
        }
    }

    // Charge les identifiants Wi-Fi + MQTT depuis la NVS ou secrets.h ;
    // lance le portail AP/HTTP si aucun n'est disponible ou si forcé.
    // Ne retourne pas en cas de portail (reboot après sauvegarde).
    cfg.begin(forceConfig,
              WIFI_SSID, WIFI_PASSWORD,
              MQTT_HOST, MQTT_PORT,
              MQTT_USER, MQTT_PASSWORD);

    Serial.println("\n# --- assistant vocal, etapes 1 a 3 : audio + Wi-Fi + MQTT ---");
    Serial.printf("# carte : %s\n", BOARD_NAME);
    Serial.printf("# format : %lu Hz, 16 bits, mono, chunk %lu ms = %u octets\n",
                  static_cast<unsigned long>(AUDIO_SAMPLE_RATE),
                  static_cast<unsigned long>(AUDIO_CHUNK_MS),
                  static_cast<unsigned>(AUDIO_CHUNK_BYTES));
    Serial.printf("# micro : SCK=GPIO %d, WS=GPIO %d, SD=GPIO %d\n", PIN_MIC_SCK, PIN_MIC_WS,
                  PIN_MIC_SD);
    Serial.printf("# ampli : BCLK=GPIO %d, LRC=GPIO %d, DIN=GPIO %d, cadrage %s\n", PIN_AMP_BCLK,
                  PIN_AMP_LRC, PIN_AMP_DIN, AMP_FORMAT_PHILIPS ? "Philips" : "MSB");

    // --- Sortie : test isolé par un bip, puis mise en place de la chaîne de lecture.
    if (!speakerReady) {
        Serial.println("# ERREUR : sortie audio I2S1 impossible a demarrer");
    } else {
        Serial.printf("# bip de demarrage : %lu Hz, %lu ms, -12 dBFS\n",
                      static_cast<unsigned long>(BOOT_TONE_HZ),
                      static_cast<unsigned long>(BOOT_TONE_MS));
        speaker.playTone(BOOT_TONE_HZ, BOOT_TONE_MS, BOOT_TONE_AMPLITUDE);

        if (!playbackFifo.begin(playbackStorage, PLAYBACK_FIFO_BYTES,
                                PLAYBACK_BLOCK_SAMPLES * sizeof(int16_t))) {
            Serial.println("# ERREUR : FIFO de lecture impossible a creer");
        } else {
            playback.begin();
            Serial.printf("# lecture prete : FIFO %u ms, tache audio sur le coeur %d\n",
                          static_cast<unsigned>(PLAYBACK_FIFO_BYTES / (AUDIO_SAMPLE_RATE / 1000 * 2)),
                          AUDIO_TASK_CORE);
        }
    }

    // --- Capteur d'empreinte (branche fingerprint, étapes F1 et F2).
    probeFingerprint();
    commands.setFingerprintCallback(onFingerprintCommand);

    // --- Entrée : auto-test du micro.
    micReady = selfTest.run();
    if (micReady) {
        // La tâche de capture ne démarre qu'après l'auto-test : ils partagent le micro.
        if (!captureFifo.begin(captureStorage, CAPTURE_FIFO_BYTES, AUDIO_CHUNK_BYTES)) {
            Serial.println("# ERREUR : FIFO de capture impossible a creer");
            micReady = false;
        } else {
            xTaskCreateStaticPinnedToCore(captureTask, "capture", CAPTURE_TASK_STACK_BYTES,
                                          nullptr, CAPTURE_TASK_PRIORITY, captureTaskStack,
                                          &captureTaskControl, CAPTURE_TASK_CORE);
            Serial.println("# micro pret. Appuyez sur le bouton PTT et parlez.");
            led.blink(1000);  // clignotement lent = prêt, au repos
        }
    }

    // --- Réseau, en dernier : l'auto-test du micro se fait sans les émissions
    // radio du Wi-Fi (pointes de 300 mA), qui pourraient fausser la mesure.
    Serial.printf("# reseau : Wi-Fi \"%s\", broker %s:%u, topics %s/%s/..., compte MQTT %s\n",
                  cfg.ssid(), cfg.mqttHost(), cfg.mqttPort(), MQTT_TOPIC_PREFIX, DEVICE_ID,
                  cfg.mqttPassword()[0] != '\0' ? "oui" : "NON (anonyme)");
    network.setConnectionTimeout(MQTT_CONNECT_TIMEOUT_MS);
    mqtt.setPort(cfg.mqttPort());  // synchronise _port avec la valeur chargée par cfg
    if (!mqtt.begin(onMqttMessage, onMqttConnect)) {
        Serial.println("# ERREUR : tampon MQTT impossible a allouer");
    }
    wifi.begin();

    // En dernier : l'auto-test du micro et le bip ont leurs propres durées, hors surveillance.
    setupWatchdog();
}

void loop() {
    // La réception série passe toujours en premier : elle ne doit jamais attendre.
    SerialFrameReader::Frame frame;
    while (reader.poll(frame)) {
        handleFrame(frame);
    }

    updateFingerprint();

    // Réseau : non bloquant, sauf pendant une tentative de connexion au broker.
    wifi.update();
    mqtt.update(wifi.connected());

    PlaybackStream::Report played;
    if (playback.takeReport(played)) {
        reportPlayback(played);
    }
    if (mqtt.connected() && (millis() - lastStateMs) >= STATE_PERIOD_MS) {
        publishState();
    }

    if (!micReady) {
        led.blink(150);  // clignotement rapide : micro en défaut, lecture toujours possible
        led.update();
        return;
    }

    button.update();
    if (fingerTrigger.busy()) {
        button.clearEvents();  // un doigt tient le micro (F3) : un appui à ce moment-là est perdu
    }

    if (!session.active()) {
        if (button.wasPressed()) {
            button.clearEvents();  // un relâchement périmé ne doit pas clore la session
            startSession();
        } else if (!fingerTrigger.busy()) {
            led.blink(1000);  // clignotement lent = prêt, au repos
        }
        led.update();
        return;
    }

    // --- Session en cours : la capture tourne dans sa tâche ; ici on envoie.
    drainCapture(false);

    if (fingerSession) {
        // La fin normale vient du doigt (onFingerEvent) ; ici seulement le garde-fou.
        if (session.elapsedMs() > SESSION_MAX_MS) {
            stopSession("duree maximale");
            fingerSession = false;
            led.blink(1000);
        }
    } else if (button.wasReleased() || !button.isDown()) {
        stopSession("bouton relache");
        button.clearEvents();  // un appui mémorisé pendant la session ne doit pas en relancer une
        led.blink(1000);
    } else if (session.elapsedMs() > SESSION_MAX_MS) {
        stopSession("duree maximale");
        led.blink(1000);
    }
}
