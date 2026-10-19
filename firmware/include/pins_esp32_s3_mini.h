#pragma once

#include <stdint.h>

/**
 * @file pins_esp32_s3_mini.h
 * @brief Brochage de la carte « ESP32-S3 Mini » (18 broches, USB-C, flash 4 Mo), environnement
 *        PlatformIO « esp32s3mini » (branche fingerprint, étape F5).
 *
 * Broches de la carte, relevées sur sa sérigraphie, confirmées à l'essai (2026-10-19) :
 *   gauche, de l'USB vers le bas : TX  RX  1  2  3  4  5  6  7
 *   droite, de l'USB vers le bas : 5V GND 3V3 13 12 11 10  9  8
 * Les numéros sont ceux des GPIO. TX/RX (GPIO 43/44) restent libres, et GPIO 3 aussi : c'est une
 * broche de « strapping » (choix de la source du JTAG), à ne pas utiliser pour un signal.
 *
 * Évitées sur ce module : GPIO 0 (bouton BOOT), 45 et 46 (strapping), 19 et 20 (USB natif), 26 à 32
 * (flash et PSRAM en boîtier), 33 à 37 (PSRAM octale selon la variante). Détail : docs/BROCHAGE.md §1.5.
 */

/// Nom de la carte, affiché au démarrage : confirme que le bon environnement a été téléversé.
static constexpr const char* BOARD_NAME = "ESP32-S3 Mini (esp32s3mini)";

// --------------------------------------------------------- Broches micro INMP441 (côté gauche, 5-6-7)

static constexpr int PIN_MIC_SCK = 5;  ///< Horloge bit (BCLK) du micro
static constexpr int PIN_MIC_WS  = 6;  ///< Sélection de mot (LRCL / WS)
static constexpr int PIN_MIC_SD  = 7;  ///< Donnée série sortante du micro (DOUT)

// ------------------------------------------------------ Broches ampli MAX98357A (côté droit, 13-12-11)

static constexpr int PIN_AMP_BCLK = 13;  ///< Horloge bit
static constexpr int PIN_AMP_LRC  = 12;  ///< Sélection de mot
static constexpr int PIN_AMP_DIN  = 11;  ///< Donnée série entrante de l'ampli

// ------------------------------------------------------------ Interface utilisateur (côté droit, 10-9-8)

static constexpr int PIN_BUTTON_PTT = 10;  ///< Bouton « Push-To-Talk », pull-up interne, l'autre borne à GND
/// LED d'état externe : GPIO 9 -> résistance 330 Ω -> anode ; cathode -> GND. (La LED RVB de la carte,
/// étiquetée 48, est une LED adressable WS2812 : elle ne se pilote pas par un simple niveau.)
static constexpr int PIN_STATUS_LED = 9;
/// LED de démonstration domotique (étape 11) : « la lumière » de la pièce de la carte.
static constexpr int PIN_DEMO_LIGHT = 8;

// ------------------------------------------------------------ Capteur d'empreinte R503 (côté gauche, 1-2-4)

/// UART2. GPIO 1 reçoit le fil jaune (TX du capteur), GPIO 2 envoie au fil de RX du capteur.
static constexpr int PIN_FP_RX = 1;
static constexpr int PIN_FP_TX = 2;

/// Signal de touche (fil bleu WAKEUP), en entrée simple : le capteur pilote lui-même la ligne.
static constexpr int PIN_FP_TOUCH = 4;

// ------------------------------------------------------------ Moniteur série

/// Le moniteur série passe par l'USB natif de la puce (USB-Serial/JTAG), sans puce USB-série : le port
/// disparaît et revient à chaque reset, ce qui prend plus de temps que sur la WROOM.
static constexpr uint32_t BOOT_SERIAL_DELAY_MS = 3000;
