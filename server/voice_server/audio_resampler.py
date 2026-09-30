"""Mise au format du système de tout audio destiné à un ESP32."""

from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np
import soxr

from voice_server.settings import AudioSettings


class AudioFormatError(ValueError):
    """Fichier audio que le module wave ne sait pas lire."""


class AudioResampler:
    """Convertit en PCM s16le mono au format du système, crête normalisée.

    L'ESP32 reste simple : il ne sait jouer que du 16 kHz mono. Tout le reste
    se fait ici — Piper, par exemple, produit du 22 050 Hz (étape 6).

    La normalisation de crête fixe le volume d'écoute : sans elle, une voix
    enregistrée à -30 dBFS sort presque inaudible du haut-parleur (étape 2).
    """

    def __init__(self, audio: AudioSettings, target_peak_dbfs: float | None = -3.0) -> None:
        """
        Args:
            audio: format de sortie (fréquence du système).
            target_peak_dbfs: crête visée, None pour ne pas toucher au niveau.
        """
        self._audio = audio
        self._target_peak = None if target_peak_dbfs is None else 10.0 ** (target_peak_dbfs / 20.0)

    def from_wav(self, path: Path) -> bytes:
        """Lit un WAV PCM (8, 16, 24 ou 32 bits, mono ou stéréo, toute fréquence)."""
        try:
            with wave.open(str(path), "rb") as wav:
                raw = wav.readframes(wav.getnframes())
                width = wav.getsampwidth()
                channels = wav.getnchannels()
                rate = wav.getframerate()
        except (wave.Error, EOFError) as error:  # EOFError : fichier tronqué
            raise AudioFormatError(
                f"{path} : {error}. Conversion : ffmpeg -i {path} -ar 16000 -ac 1 -sample_fmt s16 sortie.wav"
            ) from error
        samples = _decode_pcm(raw, width).reshape(-1, channels)
        return self.convert(samples, rate)

    def convert(self, samples: np.ndarray, rate: int) -> bytes:
        """Convertit des échantillons flottants dans [-1, 1], forme (n,) ou (n, canaux)."""
        signal = np.asarray(samples, dtype=np.float64)
        if signal.ndim == 2:
            signal = signal.mean(axis=1)  # mono : moyenne des canaux

        if rate != self._audio.sample_rate and signal.size > 0:
            # soxr : rééchantillonneur de référence, sans repliement audible.
            signal = soxr.resample(signal, rate, self._audio.sample_rate, quality="HQ")

        peak = float(np.max(np.abs(signal))) if signal.size else 0.0
        if self._target_peak is not None and peak > 0.0:
            signal = signal * (self._target_peak / peak)

        clipped = np.clip(np.round(signal * 32767.0), -32768, 32767)
        return clipped.astype("<i2").tobytes()


def _decode_pcm(raw: bytes, width: int) -> np.ndarray:
    """Décode du PCM entier little-endian en flottants dans [-1, 1]."""
    if width == 1:  # 8 bits : non signé, centré sur 128
        return (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    if width == 2:
        return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if width == 3:  # 24 bits : on complète chaque échantillon en 32 bits
        bytes3 = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        padded = np.zeros((bytes3.shape[0], 4), dtype=np.uint8)
        padded[:, 1:] = bytes3
        return padded.view("<i4").reshape(-1).astype(np.float64) / 2147483648.0
    if width == 4:
        return np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    raise AudioFormatError(f"largeur d'echantillon non geree : {width * 8} bits")


def peak_dbfs(pcm: bytes) -> float:
    """Crête d'un PCM s16le, en dBFS."""
    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")
    peak = int(np.max(np.abs(samples.astype(np.int32)))) if samples.size else 0
    return 20.0 * math.log10(peak / 32768.0) if peak > 0 else -math.inf
