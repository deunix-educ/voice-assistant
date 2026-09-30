"""Détection d'activité vocale (VAD) avec Silero (étape 7).

Silero donne, pour chaque fenêtre de 512 échantillons (32 ms à 16 kHz), la
probabilité qu'elle contienne de la parole. Ce module en déduit les segments
de parole, puis ne garde qu'eux : silences de début et de fin retirés, longs
silences intérieurs raccourcis.

Le modèle ONNX est celui que livre faster-whisper : pas de torch, rien à télécharger.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from voice_server.settings import VadSettings

logger = logging.getLogger(__name__)

WINDOW_SAMPLES = 512  # imposé par Silero à 16 kHz
HYSTERESIS = 0.15     # on sort de la parole sous threshold - 0,15 : pas de battement au seuil


@dataclass(frozen=True)
class Segment:
    """Segment de parole, en échantillons : [start, end[."""

    start: int
    end: int


@dataclass(frozen=True)
class VadResult:
    """Parole trouvée dans une session."""

    segments: tuple[Segment, ...]  # bornes après marge, dans l'audio d'origine
    speech_pcm: bytes              # la parole seule, segments mis bout à bout
    probabilities: np.ndarray      # une probabilité par fenêtre, pour le diagnostic
    total_s: float                 # durée de la session
    speech_s: float                # durée de parole conservée
    elapsed_s: float               # temps de calcul
    sample_rate: int

    @property
    def has_speech(self) -> bool:
        """Faux si la session ne contient aucune parole (appui muet, bruit)."""
        return bool(self.segments)

    def bounds(self) -> str:
        """Bornes des segments en secondes : « 0.45-1.80, 2.10-2.60 »."""
        return ", ".join(f"{s.start / self.sample_rate:.2f}-{s.end / self.sample_rate:.2f}"
                         for s in self.segments)


def find_segments(probabilities: Sequence[float], threshold: float,
                  min_silence_windows: int, min_speech_windows: int) -> list[Segment]:
    """Segments de parole, en fenêtres, à partir des probabilités de Silero.

    Entrée en parole dès threshold ; sortie après min_silence_windows fenêtres
    sous threshold - HYSTERESIS (une pause entre deux mots ne coupe pas la phrase).
    Les segments plus courts que min_speech_windows sont des bruits : clic, toux.
    """
    low = threshold - HYSTERESIS
    segments: list[Segment] = []
    start: int | None = None
    quiet = 0  # fenêtres calmes consécutives depuis la dernière parole
    for index, probability in enumerate(probabilities):
        if start is None:
            if probability >= threshold:
                start, quiet = index, 0
            continue
        quiet = quiet + 1 if probability < low else 0
        if quiet >= min_silence_windows:
            end = index - quiet + 1
            if end - start >= min_speech_windows:
                segments.append(Segment(start, end))
            start = None
    if start is not None and len(probabilities) - quiet - start >= min_speech_windows:
        segments.append(Segment(start, len(probabilities) - quiet))
    return segments


def pad_segments(segments: Sequence[Segment], pad: int, total: int) -> list[Segment]:
    """Élargit chaque segment de pad (début et fin de mot sont faibles) et fusionne les chevauchements."""
    padded: list[Segment] = []
    for segment in segments:
        start, end = max(0, segment.start - pad), min(total, segment.end + pad)
        if padded and start <= padded[-1].end:
            padded[-1] = Segment(padded[-1].start, end)
        else:
            padded.append(Segment(start, end))
    return padded


class VoiceActivityDetector:
    """Trouve la parole dans un PCM s16le 16 kHz mono et retire les silences."""

    def __init__(self, vad: VadSettings, sample_rate: int,
                 model: Callable[[np.ndarray], np.ndarray] | None = None) -> None:
        """
        Args:
            vad: seuil et durées minimales.
            sample_rate: fréquence du PCM ; Silero exige 16 kHz avec des fenêtres de 512.
            model: probabilités par fenêtre ; par défaut Silero, injectable pour les tests.

        Raises:
            ValueError: fréquence différente de 16 kHz.
        """
        if sample_rate != 16000:
            raise ValueError(f"Silero attend du 16 kHz avec des fenetres de 512, pas {sample_rate} Hz")
        if model is None:
            # Import tardif : le mode écho se passe de l'extra « speech ».
            from faster_whisper.vad import get_vad_model
            model = get_vad_model()
        self._model = model
        self._vad = vad
        self._sample_rate = sample_rate
        window_ms = WINDOW_SAMPLES * 1000 / sample_rate
        self._min_silence = max(1, round(vad.min_silence_ms / window_ms))
        self._min_speech = max(1, round(vad.min_speech_ms / window_ms))
        self._pad = vad.speech_pad_ms * sample_rate // 1000

    def detect(self, pcm: bytes) -> VadResult:
        """Analyse le PCM et rend la parole seule."""
        began = time.monotonic()
        samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")
        audio = samples.astype(np.float32) / 32768.0
        # Dernière fenêtre complétée par du silence : Silero n'accepte que des fenêtres entières.
        padded = np.pad(audio, (0, -audio.size % WINDOW_SAMPLES))
        probabilities = (np.asarray(self._model(padded), dtype=np.float32).reshape(-1)
                         if padded.size else np.zeros(0, dtype=np.float32))

        windows = find_segments(probabilities.tolist(), self._vad.threshold,
                                self._min_silence, self._min_speech)
        in_samples = [Segment(s.start * WINDOW_SAMPLES, min(samples.size, s.end * WINDOW_SAMPLES))
                      for s in windows]
        segments = pad_segments(in_samples, self._pad, samples.size)
        speech = b"".join(samples[s.start:s.end].tobytes() for s in segments)
        return VadResult(
            segments=tuple(segments),
            speech_pcm=speech,
            probabilities=probabilities,
            total_s=samples.size / self._sample_rate,
            speech_s=len(speech) / 2 / self._sample_rate,
            elapsed_s=time.monotonic() - began,
            sample_rate=self._sample_rate,
        )


def envelope(probabilities: np.ndarray, threshold: float) -> str:
    """Une lettre par fenêtre de 32 ms : « # » parole, « . » hésitation, « _ » silence."""
    low = threshold - HYSTERESIS
    return "".join("#" if p >= threshold else "." if p >= low else "_" for p in probabilities)
