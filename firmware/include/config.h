#pragma once

#include <stddef.h>
#include <stdint.h>

/**
 * @file config.h
 * @brief Constantes matérielles et audio du firmware.
 *
 * Toutes les valeurs réglables du firmware sont ici : aucune constante magique
 * dans le code. Les broches évitent les broches de « strapping » (0, 2, 5, 12, 15)
 * pour les signaux audio, car leur état au démarrage change le mode de boot.
 */

// ---------------------------------------------------------------- Format audio

/// Fréquence d'échantillonnage unique de tout le système (Hz).
static constexpr uint32_t AUDIO_SAMPLE_RATE = 16000;

/// Durée d'un chunk audio (ms). 50 ms = compromis latence / surcoût protocole.
static constexpr uint32_t AUDIO_CHUNK_MS = 50;

/// Nombre d'échantillons 16 bits dans un chunk : 16000 x 0,050 = 800.
static constexpr size_t AUDIO_CHUNK_SAMPLES = (AUDIO_SAMPLE_RATE * AUDIO_CHUNK_MS) / 1000;

/// Taille d'un chunk en octets : 800 x 2 = 1600.
static constexpr size_t AUDIO_CHUNK_BYTES = AUDIO_CHUNK_SAMPLES * sizeof(int16_t);

// ------------------------------------------------------------------ Broches (par carte)

/**
 * Les broches dépendent de la carte : un en-tête par carte, choisi par le drapeau de compilation
 * de l'environnement PlatformIO (platformio.ini). Sans drapeau : l'ESP32 WROOM (DevKit V1).
 *
 *   BOARD_ESP32_S3_MINI  ->  pins_esp32_s3_mini.h   (environnement esp32s3mini)
 *   (aucun)              ->  pins_esp32_wroom.h     (environnement esp32dev)
 *
 * Chaque en-tête définit : PIN_MIC_SCK/WS/SD (micro INMP441), PIN_AMP_BCLK/LRC/DIN (ampli MAX98357A),
 * PIN_BUTTON_PTT, PIN_STATUS_LED, PIN_DEMO_LIGHT, PIN_FP_RX/TX/TOUCH (capteur R503),
 * BOARD_NAME et BOOT_SERIAL_DELAY_MS. Le brochage de chaque carte est détaillé dans docs/BROCHAGE.md.
 */
#if defined(BOARD_ESP32_S3_MINI)
#include "pins_esp32_s3_mini.h"
#else
#include "pins_esp32_wroom.h"
#endif

// ------------------------------------------------------------ Interface utilisateur

/// Durée d'anti-rebond du bouton (ms).
static constexpr uint32_t BUTTON_DEBOUNCE_MS = 30;

// ------------------------------------------------------------ Conversion micro

/**
 * Gain numérique appliqué après le passe-haut, avant la réduction à 16 bits.
 *
 * Valeur **mesurée**. Une phrase à 20-30 cm donne, une fois le grondement
 * infrasonore retiré par le passe-haut, une crête vers -25 dBFS : un gain de 4
 * la place vers -13 dBFS, soit ~22 % de la pleine échelle, avec 13 dB de marge.
 *
 * Les valeurs 16 puis 1 retenues plus tôt venaient de mesures faussées (cadrage
 * des bits erroné, puis infrasons dominants). L'auto-test annonce la valeur à
 * retenir à la fin de chaque session ; elle dépend du module et de la distance.
 *
 * Le gain est numérique : il ne dégrade pas le rapport signal/bruit, il replace
 * seulement le signal dans la plage utile.
 */
static constexpr int MIC_GAIN = 4;

/**
 * Durée de stabilisation du micro après activation du canal I2S (ms).
 *
 * L'INMP441 s'endort dès que ses horloges s'arrêtent, et chaque réveil produit
 * une composante continue qui dérive lentement. Mesuré sur le banc : encore
 * +23 % de pleine échelle et en mouvement 600 ms après le réveil. On jette donc
 * ce début, UNE seule fois au démarrage : ensuite, les horloges ne s'arrêtent
 * plus jamais (flush() vide le DMA par lecture).
 */
static constexpr uint32_t MIC_SETTLE_MS = 1500;

/**
 * Pull-down interne sur la ligne SD du micro.
 *
 * Le datasheet de l'INMP441 exige une résistance de 100 kOhms entre SD et la
 * masse : le micro met sa sortie en haute impédance hors de sa moitié de trame.
 * Beaucoup de modules bon marché ne la portent pas. Le pull-down interne de
 * l'ESP32 (environ 45 kOhms) remplit le même rôle sans composant à ajouter.
 */
static constexpr bool MIC_SD_PULLDOWN = true;

/// Affiche l'analyse détaillée des bits au démarrage (utile tant que l'étape 1 n'est pas validée).
static constexpr bool MIC_SELFTEST_VERBOSE = true;

/// Durée de l'auto-test du micro au démarrage (ms).
static constexpr uint32_t MIC_PROBE_MS = 300;

/**
 * Filtre passe-haut du micro, du premier ordre : bas += (x - bas) >> SHIFT ;
 * sortie = x - bas. Fréquence de coupure : fc = fs / (2 pi 2^SHIFT).
 *
 * 5 donne fc = 16000 / (2 pi 32) = 80 Hz. La voix n'a rien d'utile en dessous.
 * Mesuré sur le banc : avec l'ancien réglage (12, soit 0,6 Hz, qui ne retirait
 * que la composante continue), 87 % de l'énergie d'un enregistrement était
 * infrasonore — mouvements de la plaque d'essai quand on tient le bouton,
 * souffle de la parole. Avec 5, il en reste 0,6 %, et la voix 99 %.
 *
 * Calculé sur les 24 bits du micro : sur 16 bits, un pas de 1/32 laisserait une
 * zone morte de 31 LSB, source d'erreur continue et de distorsion à bas niveau.
 */
static constexpr int MIC_HPF_SHIFT = 5;

// ------------------------------------------------------------ Sortie audio (étape 2)

/**
 * Cadrage I2S attendu par l'ampli. Le MAX98357**A** suit la norme Philips, le
 * MAX98357**B** le cadrage MSB (c'est la lettre après le numéro, sur la puce).
 * Si le bip de démarrage sort râpeux ou soufflé au lieu d'être pur : basculer.
 */
static constexpr bool AMP_FORMAT_PHILIPS = true;

/// Bip de démarrage : teste la sortie SEULE, sans PC, sans FIFO, sans tâche.
static constexpr uint32_t BOOT_TONE_HZ = 440;
static constexpr uint32_t BOOT_TONE_MS = 600;

/**
 * Amplitude du bip : 8192 = 25 % de pleine échelle = -12 dBFS.
 * Avec le gain de 9 dB du MAX98357A sur 4 ohms, environ 0,15 W : net sans agresser.
 */
static constexpr int16_t BOOT_TONE_AMPLITUDE = 8192;

/**
 * FIFO de lecture : 24576 octets = 12288 échantillons = 768 ms à 16 kHz.
 * Elle contient la réserve anti-gigue (PLAYBACK_START_MS) et encore 318 ms pour
 * les rafales : après un retard, le réseau livre souvent plusieurs chunks d'un coup.
 * (16384 octets, 512 ms, jusqu'au 2026-10-23 ; agrandie avec la réserve, qui ne laissait
 * plus que 62 ms de rafale.)
 */
static constexpr size_t PLAYBACK_FIFO_BYTES = 24576;

/// Bloc lu par la tâche audio : 256 échantillons = 16 ms = 512 octets.
static constexpr size_t PLAYBACK_BLOCK_SAMPLES = 256;

/**
 * Tampon anti-gigue (étape 12) : réserve à constituer avant de jouer le premier
 * échantillon, et à reconstituer après une famine. 450 ms = 9 chunks = 14400 octets,
 * soit l'avance que le serveur envoie d'emblée (prefill_chunks de audio_sender.py, à
 * garder égal) : la lecture démarre dès qu'elle est arrivée. Un retard réseau plus court
 * que la réserve ne s'entend pas. Portée de 300 à 450 ms le 2026-10-23 : à -80 dBm, 5
 * lectures sur 7 s'interrompaient ; coût : environ 150 ms avant le premier son.
 */
static constexpr uint32_t PLAYBACK_START_MS = 450;

/// Tâche audio : au-dessus de loop() (priorité 1), sur le cœur 1 (le Wi-Fi prendra le 0).
static constexpr int AUDIO_TASK_PRIORITY = 5;
static constexpr int AUDIO_TASK_CORE = 1;
static constexpr uint32_t AUDIO_TASK_STACK_BYTES = 4096;

/**
 * Tampon de réception série : 8192 octets = 256 ms de PCM au débit de lecture.
 * Le tampon par défaut (256 octets) déborderait en 8 ms pendant qu'on enregistre.
 */
static constexpr size_t SERIAL_RX_BUFFER_BYTES = 8192;

// ------------------------------------------------------------ Capture vers le réseau (étape 4)

/**
 * FIFO de capture : 32768 octets = 1,02 s de PCM. L'envoi MQTT peut bloquer quand
 * le Wi-Fi peine ; la tâche de capture, elle, ne s'arrête jamais. Au-delà d'une
 * seconde de retard réseau, des chunks sont perdus — et comptés.
 */
static constexpr size_t CAPTURE_FIFO_BYTES = 32768;

/// Tâche de capture : même priorité et même cœur que la tâche de lecture.
static constexpr int CAPTURE_TASK_PRIORITY = 5;
static constexpr int CAPTURE_TASK_CORE = 1;
static constexpr uint32_t CAPTURE_TASK_STACK_BYTES = 4096;

// ------------------------------------------------------------ Liaison série (étape 1)

/// Débit série : 921 600 baud = ~92 kB/s, largement au-dessus des 32 kB/s d'audio.
static constexpr uint32_t SERIAL_BAUD = 921600;

/**
 * USB natif (ESP32-S3) : attente maximale d'un envoi quand le PC est branché mais qu'aucun programme n'a ouvert le
 * port. Jamais 0 : le cœur Arduino 3.2.1 (HWCDC::write) y décrémente un compteur déjà à 0, qui déborde, et l'envoi
 * ne rend plus la main avant l'arrivée d'un lecteur (démarrage figé, ni Wi-Fi ni MQTT). Passé ce délai, la sortie
 * est coupée et les messages sont jetés jusqu'au prochain lecteur.
 */
static constexpr uint32_t SERIAL_TX_TIMEOUT_MS = 10;

// -------------------------------------------------------- Configuration Wi-Fi (portail AP)

/**
 * SSID du point d'accès ouvert en mode configuration (premier démarrage ou
 * bouton PTT tenu pendant WIFI_CONFIG_HOLD_MS au démarrage). La carte sert
 * un formulaire HTTP sur http://192.168.4.1 tant que l'utilisateur n'a pas
 * validé les identifiants Wi-Fi + MQTT.
 */
static constexpr const char* WIFI_CONFIG_AP_SSID = "VoiceAssist-Config";

/// Durée de maintien du bouton PTT au démarrage pour forcer le portail (ms).
static constexpr uint32_t WIFI_CONFIG_HOLD_MS = 3000;

// ------------------------------------------------------------ Wi-Fi et MQTT (étape 3)

/// Identifiant du nœud, utilisé dans les topics MQTT : voice/<DEVICE_ID>/...
/// Sert aussi d'identifiant client MQTT : deux cartes ne doivent jamais le partager,
/// sinon le broker déconnecte l'une dès que l'autre se connecte, en boucle.
static constexpr const char* DEVICE_ID = "esp32-01";

/// Préfixe commun de tous les topics.
static constexpr const char* MQTT_TOPIC_PREFIX = "voice";

/**
 * Intervalle de maintien de connexion MQTT (s). Sans nouvelle de l'ESP32 pendant
 * 1,5 x cet intervalle, le broker publie le testament « offline ». 10 s : une
 * coupure de courant est signalée en 15 s au plus, pour 1 petit paquet toutes
 * les 10 s.
 */
static constexpr uint16_t MQTT_KEEPALIVE_S = 10;

/// Tampon de PubSubClient : un chunk de 1600 octets + topic + en-tête MQTT (étape 4).
static constexpr uint16_t MQTT_BUFFER_BYTES = 2048;

/// Délai entre deux tentatives de connexion au broker (ms).
static constexpr uint32_t MQTT_RETRY_MS = 5000;

/**
 * Attente maximale d'une réponse du broker (s). Une tentative de connexion bloque
 * loop() au plus MQTT_CONNECT_TIMEOUT_MS + ce temps, soit 3 s, et seulement quand le
 * broker est injoignable : l'audio, lui, tourne dans ses propres tâches (étape 15 :
 * borné et mesuré plutôt que déplacé dans une tâche, PubSubClient n'étant pas prévu
 * pour être partagé entre tâches).
 */
static constexpr uint16_t MQTT_SOCKET_TIMEOUT_S = 2;

/// Délai d'ouverture de la connexion TCP au broker (ms). 3000 par défaut dans le core ;
/// sur un réseau local, un broker présent répond en quelques millisecondes.
static constexpr uint32_t MQTT_CONNECT_TIMEOUT_MS = 1000;

/**
 * Présence du serveur (étape 15) : « online » retenu, remplacé par son testament
 * « offline » s'il disparaît. Suit MQTT_TOPIC_PREFIX. Serveur absent = micro coupé :
 * rien ne part plus sur le réseau sans personne pour l'écouter.
 */
static constexpr const char* MQTT_SERVER_STATUS_TOPIC = "voice/server/status";

/// Période de republication de l'état (ms) : IP, RSSI, durée de fonctionnement.
static constexpr uint32_t STATE_PERIOD_MS = 30000;

// ------------------------------------------------------------ Fiabilisation (étape 15)

/**
 * Chien de garde (watchdog) des tâches : une tâche surveillée (loop, capture, audio)
 * qui ne donne plus signe de vie pendant ce délai fait redémarrer la carte, avec la
 * cause « watchdog » dans l'événement boot. Mieux vaut 2 s de redémarrage qu'une
 * carte figée pour toujours.
 *
 * 15 s : au-dessus du pire blocage LÉGITIME trouvé dans le code réseau du core — un
 * envoi TCP attend jusqu'à 10 x 1 s que le Wi-Fi accepte des données
 * (NetworkClient::write). Le délai par défaut (5 s) redémarrerait la carte sur un
 * simple Wi-Fi faible.
 */
static constexpr uint32_t WATCHDOG_TIMEOUT_S = 15;

/// Wi-Fi absent depuis ce délai (ms) : on relance la connexion de zéro. Filet de
/// sécurité, si la reconnexion automatique n'aboutit pas (point d'accès redémarré...).
static constexpr uint32_t WIFI_RESTART_MS = 30000;

// ------------------------------------------------------------ Capteur d'empreinte R503 (branche fingerprint, F1)

/// Broches du capteur (PIN_FP_RX, PIN_FP_TX, PIN_FP_TOUCH) : voir l'en-tête de la carte. Le capteur parle par
/// l'UART2 (Serial2) à FP_BAUD ; PIN_FP_TOUCH est son fil bleu WAKEUP.

/// Mesuré sur le capteur de l'utilisateur (2026-10-18) : la ligne est HAUTE au repos et passe
/// à BAS quand un doigt est posé. true = actif à l'état bas ; à inverser si un autre modèle fait l'inverse.
static constexpr bool FP_TOUCH_ACTIVE_LOW = true;

/// Débit d'usine du capteur (57600 d'après la documentation ESPHome de ce composant).
static constexpr uint32_t FP_BAUD = 57600;

/// Enregistrement d'une empreinte (F2) : attente maximale à chaque pose du doigt, ou du retrait (ms).
static constexpr uint32_t FP_ENROLL_TIMEOUT_MS = 20000;

/// Session déclenchée par le doigt (F3) : posé = début, levé = fin.
/// Doigt posé depuis ce délai : sinon c'est un effleurement, ignoré.
static constexpr uint32_t FP_TOUCH_SETTLE_MS = 40;

/// Doigt levé depuis ce délai : la session se termine. Un décollement plus court (doigt qui glisse,
/// signal de touche qui saute) n'est pas un retrait. Ces 150 ms s'ajoutent à la fin de l'audio envoyé.
static constexpr uint32_t FP_RELEASE_MS = 150;

/// Délai total pour identifier le doigt, de la pose à la réponse du capteur. Le son est capté pendant ce
/// temps (CAPTURE_FIFO_BYTES = 1 s) ; au-delà d'une seconde, le début de la phrase serait perdu.
static constexpr uint32_t FP_IDENTIFY_TIMEOUT_MS = 3000;

/// Entre deux dialogues avec le capteur pendant l'identification (ms).
static constexpr uint32_t FP_POLL_MS = 40;

/// Captures inutilisables (image brouillée, trop peu de détails) tolérées avant de refuser le doigt.
static constexpr uint8_t FP_IDENTIFY_ATTEMPTS = 3;

/// Mot de passe du capteur : 0 à la sortie d'usine.
static constexpr uint32_t FP_PASSWORD = 0;

/// Attente de la réponse du capteur (ms). Une commande simple (mot de passe, paramètres, anneau) répond en quelques ms.
static constexpr uint32_t FP_REPLY_TIMEOUT_MS = 300;

/// Attente pour les commandes qui calculent (capture, extraction, recherche, fusion, écriture) : le capteur
/// peut y passer plusieurs centaines de ms. Un délai trop court faisait échouer l'enregistrement (F2).
static constexpr uint32_t FP_SLOW_REPLY_TIMEOUT_MS = 3000;
