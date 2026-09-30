"""Envoi de PCM à un ESP32 par MQTT, au rythme réel (étape 5)."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Protocol

from voice_server.settings import AudioSettings

logger = logging.getLogger(__name__)


class Publisher(Protocol):
    """Ce dont l'envoi a besoin du lien MQTT : publier vers une carte."""

    def publish(self, device: str, suffix: str, payload: bytes, qos: int) -> None: ...


def send_times(count: int, chunk_s: float, prefill: int) -> list[float]:
    """Instants d'envoi de chaque chunk, en secondes depuis le début.

    Les `prefill` premiers partent aussitôt : c'est l'avance qui protège l'ESP32
    des irrégularités du Wi-Fi. Chaque suivant part quand l'ESP32 a joué
    l'équivalent d'un chunk, ce qui maintient l'avance constante.
    """
    return [max(0.0, (index - prefill + 1) * chunk_s) for index in range(count)]


class AudioSender:
    """Envoie un PCM déjà au format du système : START, chunks, END.

    START et END passent sur voice/<carte>/control en QoS 1 (garantis) ;
    les chunks sur voice/<carte>/audio/out en QoS 0 (rapides, perte possible,
    comptée par l'ESP32 grâce au nombre annoncé dans le END).

    L'appel est bloquant pendant toute la durée du son : à lancer dans un fil
    dédié, jamais dans le fil réseau de paho.
    """

    def __init__(
        self,
        link: Publisher,
        audio: AudioSettings,
        prefill_chunks: int = 6,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            link: lien MQTT.
            audio: format du système.
            prefill_chunks: avance envoyée d'un coup ; 6 chunks = 300 ms, la FIFO
                de l'ESP32 en contient 512.
            sleep, clock: injectables pour les tests.
        """
        self._link = link
        self._audio = audio
        self._prefill = prefill_chunks
        self._sleep = sleep
        self._clock = clock

    @property
    def chunk_bytes(self) -> int:
        """Taille d'un chunk : 1600 octets pour 50 ms à 16 kHz."""
        return self._audio.bytes_per_second * self._audio.chunk_ms // 1000

    def send(self, device: str, pcm: bytes, session: str | None = None) -> str:
        """Envoie le PCM et rend l'identifiant de la session de lecture."""
        session_id = session or f"srv-{uuid.uuid4().hex[:6]}"
        even = pcm[: len(pcm) // 2 * 2]  # jamais de demi-échantillon
        size = self.chunk_bytes
        chunks = [even[i : i + size] for i in range(0, len(even), size)]
        schedule = send_times(len(chunks), self._audio.chunk_ms / 1000.0, self._prefill)

        start = {"event": "start", "session": session_id, "rate": self._audio.sample_rate,
                 "bits": self._audio.bits, "channels": self._audio.channels,
                 "codec": self._audio.codec, "chunk_ms": self._audio.chunk_ms}
        self._link.publish(device, "control", json.dumps(start).encode(), 1)

        began = self._clock()
        worst_delay = 0.0
        for chunk, due in zip(chunks, schedule):
            wait = began + due - self._clock()
            if wait > 0:
                self._sleep(wait)
            else:
                worst_delay = max(worst_delay, -wait)
            self._link.publish(device, "audio/out", chunk, 0)

        end = {"event": "end", "session": session_id, "chunks": len(chunks)}
        self._link.publish(device, "control", json.dumps(end).encode(), 1)
        logger.info("%s : lecture %s envoyee, %d chunks (%.2f s), pire retard d'envoi %.0f ms",
                    device, session_id, len(chunks), len(even) / self._audio.bytes_per_second,
                    worst_delay * 1000)
        return session_id
