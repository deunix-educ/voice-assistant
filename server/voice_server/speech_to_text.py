"""Transcription de la parole en texte avec faster-whisper (étape 6)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from voice_server.settings import SttSettings

if TYPE_CHECKING:  # import réel dans __init__ : le mode écho se passe de l'extra « speech »
    from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

# Phrases que Whisper « entend » dans le silence ou le bruit : elles viennent des
# sous-titres de vidéos qui ont servi à l'entraîner. Comparaison en minuscules.
KNOWN_HALLUCINATIONS = (
    "amara.org",
    "sous-titrage",
    "sous-titres",
    "merci d'avoir regardé",
)


@dataclass(frozen=True)
class Word:
    """Un mot et son instant dans l'audio transcrit (étape 10 : à qui l'attribuer)."""

    text: str               # tel que Whisper l'écrit, espace initiale comprise : « Ferme », « -moi »
    start_s: float
    end_s: float


@dataclass(frozen=True)
class Transcript:
    """Résultat d'une transcription."""

    text: str               # vide si rien d'exploitable n'a été reconnu
    audio_s: float          # durée de l'audio transcrit
    elapsed_s: float        # temps de calcul
    dropped: tuple[str, ...] = ()  # segments écartés comme hallucinations
    words: tuple[Word, ...] = ()   # mots des segments gardés, dans l'ordre


class SpeechToText:
    """Transcrit un PCM s16le 16 kHz mono en texte, avec un modèle local.

    Le modèle est lu dans le dossier rempli par « make models » : aucun accès
    réseau au démarrage, ni pendant la transcription.
    """

    def __init__(self, stt: SttSettings, sample_rate: int) -> None:
        """
        Args:
            stt: réglages (modèle, langue, précision de calcul).
            sample_rate: fréquence du PCM reçu ; Whisper exige 16 kHz.

        Raises:
            FileNotFoundError: le modèle n'a pas été téléchargé.
            ValueError: fréquence différente de 16 kHz.
        """
        if sample_rate != 16000:
            raise ValueError(f"Whisper attend du 16 kHz, pas {sample_rate} Hz")
        if not (stt.model_dir / "model.bin").exists():
            raise FileNotFoundError(f"modele whisper absent de {stt.model_dir} : lancez 'make models'")
        self._stt = stt
        self._sample_rate = sample_rate

        from faster_whisper import WhisperModel

        began = time.monotonic()
        # int8 : poids quantifiés sur 8 bits, 2 à 4 fois plus rapide sur CPU que float32.
        self._model: WhisperModel = WhisperModel(str(stt.model_dir), device="cpu", compute_type=stt.compute_type,
                                                 cpu_threads=stt.cpu_threads)
        # Le premier appel initialise les calculs : on le fait ici plutôt qu'à la première question.
        self.transcribe(bytes(sample_rate * 2))
        logger.info("whisper %s charge en %.1f s", stt.model, time.monotonic() - began)

    def transcribe(self, pcm: bytes, words: bool = True) -> Transcript:
        """Transcrit le PCM et écarte les segments suspects.

        Args:
            words: horodater chaque mot. Utile seulement pour répartir le texte entre
                plusieurs locuteurs (étape 10) ; sans cela, ~0,1 s de moins (mesuré, étape 16).
        """
        began = time.monotonic()
        audio = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(
            audio,
            language=self._stt.language,
            beam_size=self._stt.beam_size,
            # Chaque question est indépendante : sans cela, une erreur se propage d'un segment à l'autre.
            condition_on_previous_text=False,
            no_speech_threshold=self._stt.no_speech_threshold,
            vad_filter=False,  # la VAD (étape 7) a déjà retiré les silences
            word_timestamps=words,  # instant de chaque mot : +5 à 10 % de calcul (mesuré)
        )

        kept: list[str] = []
        dropped: list[str] = []
        found: list[Word] = []
        for segment in segments:  # générateur : le calcul se fait pendant ce parcours
            text = segment.text.strip()
            if not text:
                continue
            if any(marker in text.lower() for marker in KNOWN_HALLUCINATIONS):
                dropped.append(text)
            else:
                kept.append(text)
                found.extend(Word(text=w.word, start_s=float(w.start), end_s=float(w.end))
                             for w in segment.words or ())

        return Transcript(
            text=" ".join(kept),
            audio_s=audio.size / self._sample_rate,
            elapsed_s=time.monotonic() - began,
            dropped=tuple(dropped),
            words=tuple(found),
        )
