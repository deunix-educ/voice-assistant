#pragma once

#include <stdint.h>

/**
 * @file pins_esp32_wroom.h
 * @brief Brochage de l'ESP32 WROOM (DevKit V1, 38 broches), environnement PlatformIO « esp32dev ».
 *
 * Les broches évitent les broches de « strapping » (0, 2, 5, 12, 15) pour les signaux audio, car
 * leur état au démarrage change le mode de boot. Détail et schéma : docs/BROCHAGE.md.
 */

/// Nom de la carte, affiché au démarrage : confirme que le bon environnement a été téléversé.
static constexpr const char* BOARD_NAME = "ESP32 WROOM (esp32dev)";

// --------------------------------------------------------- Broches micro INMP441

static constexpr int PIN_MIC_SCK = 25;  ///< Horloge bit (BCLK) du micro
static constexpr int PIN_MIC_WS  = 26;  ///< Sélection de mot (LRCL / WS)
static constexpr int PIN_MIC_SD  = 33;  ///< Donnée série sortante du micro (DOUT)

// ------------------------------------------------------ Broches ampli MAX98357A

static constexpr int PIN_AMP_BCLK = 27;  ///< Horloge bit
static constexpr int PIN_AMP_LRC  = 14;  ///< Sélection de mot
static constexpr int PIN_AMP_DIN  = 22;  ///< Donnée série entrante de l'ampli

// ------------------------------------------------------------ Interface utilisateur

static constexpr int PIN_BUTTON_PTT = 4;  ///< Bouton « Push-To-Talk », pull-up interne
static constexpr int PIN_STATUS_LED = 2;  ///< LED d'état (broche de strapping, sortie seule : acceptable)
/// LED de démonstration domotique (étape 11) : « la lumière » de la pièce de la carte.
/// GPIO 21 : libre, sans rôle au démarrage. LED + résistance 330 Ω vers GND.
static constexpr int PIN_DEMO_LIGHT = 21;

// ------------------------------------------------------------ Capteur d'empreinte R503 (branche fingerprint)

/// UART2 de l'ESP32 WROOM. RX2 reçoit le fil jaune (TX du capteur), TX2 envoie au fil de RX du capteur.
static constexpr int PIN_FP_RX = 16;
static constexpr int PIN_FP_TX = 17;

/// Signal de touche (fil bleu WAKEUP). GPIO 34 est une entrée seule, sans résistance
/// interne : le capteur pilote lui-même la ligne.
static constexpr int PIN_FP_TOUCH = 34;

// ------------------------------------------------------------ Moniteur série

/// Attente avant les premiers messages : le moniteur série se reconnecte après le reset de la carte
/// (pont USB-série CP210x/CH340). Sans cette attente, les premières lignes de l'auto-test sont perdues.
static constexpr uint32_t BOOT_SERIAL_DELAY_MS = 1500;
