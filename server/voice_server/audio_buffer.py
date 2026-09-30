"""Accumulation du PCM d'une session et écriture en WAV."""

from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from voice_server.settings import AudioSettings


@dataclass(frozen=True)
class Levels:
    """Niveaux d'un signal, en dBFS (0 = pleine échelle)."""

    peak_dbfs: float
    rms_dbfs: float


class AudioBuffer:
    """Tampon PCM s16le borné en taille, pour une seule session."""

    def __init__(self, audio: AudioSettings, max_seconds: float) -> None:
        self._audio = audio
        self._max_bytes = int(audio.bytes_per_second * max_seconds)
        self._data = bytearray()
        self.truncated = False  # vrai si des données ont été refusées (trop long)

    def append(self, pcm: bytes) -> None:
        """Ajoute un chunk ; au-delà de la durée maximale, le surplus est ignoré."""
        room = self._max_bytes - len(self._data)
        if len(pcm) > room:
            self.truncated = True
            pcm = pcm[: max(0, room)]
        self._data.extend(pcm)

    @property
    def byte_count(self) -> int:
        """Nombre d'octets accumulés."""
        return len(self._data)

    @property
    def duration_s(self) -> float:
        """Durée accumulée, en secondes."""
        return len(self._data) / self._audio.bytes_per_second

    def levels(self) -> Levels:
        """Crête et valeur efficace du contenu."""
        samples = np.frombuffer(bytes(self._data[: len(self._data) // 2 * 2]), dtype="<i2")
        if samples.size == 0:
            return Levels(peak_dbfs=-math.inf, rms_dbfs=-math.inf)
        normalized = samples.astype(np.float64) / 32768.0
        peak = float(np.max(np.abs(normalized)))
        rms = float(np.sqrt(np.mean(normalized**2)))
        return Levels(
            peak_dbfs=20.0 * math.log10(peak) if peak > 0 else -math.inf,
            rms_dbfs=20.0 * math.log10(rms) if rms > 0 else -math.inf,
        )

    def write_wav(self, path: Path) -> None:
        """Écrit le contenu dans un fichier WAV (dossiers créés au besoin)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(self._audio.channels)
            wav.setsampwidth(self._audio.bits // 8)
            wav.setframerate(self._audio.sample_rate)
            wav.writeframes(bytes(self._data))


def read_pcm(path: Path) -> bytes:
    """Relit le PCM d'un WAV écrit par write_wav (format du système, sans conversion)."""
    with wave.open(str(path), "rb") as wav:
        return wav.readframes(wav.getnframes())
