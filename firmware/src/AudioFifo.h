#pragma once

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/stream_buffer.h>

#include <atomic>

/**
 * @brief File d'attente d'octets PCM entre deux rythmes différents.
 *
 * Deux usages, même mécanisme :
 *   - lecture (étape 2) : la réception écrit par à-coups, la tâche audio lit au
 *     rythme régulier du DAC ;
 *   - capture (étape 4) : la tâche de capture écrit au rythme du micro, l'envoi
 *     réseau lit par à-coups, au gré du Wi-Fi.
 *
 * C'est un StreamBuffer FreeRTOS, conçu pour exactement un écrivain et un
 * lecteur. Son stockage est fourni par l'appelant (tableau statique) : aucune
 * allocation, et chaque FIFO a la taille qui convient à son usage.
 *
 * Invariant : on n'écrit que des chunks entiers, jamais un morceau. Un chunk
 * coupé laisserait un nombre impair d'octets, et tous les échantillons suivants
 * seraient lus à cheval sur deux valeurs — un bruit assourdissant.
 */
class AudioFifo {
public:
    AudioFifo() = default;

    /**
     * @brief Crée le StreamBuffer sur le stockage fourni.
     * @param storage   Tableau d'au moins @p capacity + 1 octets (exigence FreeRTOS).
     * @param capacity  Capacité utile en octets.
     * @param wakeBytes Nombre d'octets qui réveillent un lecteur en attente.
     * @return true si la création a réussi.
     */
    bool begin(uint8_t* storage, size_t capacity, size_t wakeBytes);

    /**
     * @brief Ajoute un chunk, sans jamais attendre.
     * @return true si le chunk entier a été accepté ; false s'il a été refusé
     *         faute de place (il est alors compté comme perdu, en entier).
     */
    bool write(const uint8_t* data, size_t length);

    /**
     * @brief Retire au plus @p length octets, en attendant au plus @p timeoutMs.
     * @return Nombre d'octets lus (pair, tant qu'on ne demande que des tailles paires).
     */
    size_t read(uint8_t* data, size_t length, uint32_t timeoutMs);

    /// Octets actuellement en attente.
    size_t available() const;

    /// Nombre de chunks refusés faute de place depuis le démarrage.
    uint32_t overruns() const;

private:
    StaticStreamBuffer_t _control = {};
    StreamBufferHandle_t _handle = nullptr;
    std::atomic<uint32_t> _overruns{0};  ///< lu par loop(), incrémenté par l'écrivain
};
