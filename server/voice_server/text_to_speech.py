"""Synthèse vocale avec Piper (étape 6)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from voice_server.audio_resampler import AudioResampler
from voice_server.settings import TtsSettings

if TYPE_CHECKING:  # import réel dans __init__ : le mode écho se passe de l'extra « speech »
    from piper import PiperVoice, SynthesisConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Speech:
    """Parole synthétisée, déjà au format du système."""

    pcm: bytes          # s16le mono à la fréquence du système
    audio_s: float      # durée de la parole
    elapsed_s: float    # temps de calcul (synthèse et rééchantillonnage)


class TextToSpeech:
    """Transforme un texte en PCM prêt à envoyer à l'ESP32.

    Piper produit du 22 050 Hz en flottants ; AudioResampler le ramène au
    format du système (16 kHz, s16le) et fixe sa crête à -3 dBFS.
    """

    def __init__(self, tts: TtsSettings, resampler: AudioResampler, sample_rate: int) -> None:
        """
        Args:
            tts: réglages (voix, débit).
            resampler: mise au format du système.
            sample_rate: fréquence du système, pour calculer la durée produite.

        Raises:
            FileNotFoundError: la voix n'a pas été téléchargée.
        """
        if not tts.model_path.exists():
            raise FileNotFoundError(f"voix piper absente : {tts.model_path} : lancez 'make models'")
        from piper import PiperVoice, SynthesisConfig

        began = time.monotonic()
        self._voice: PiperVoice = PiperVoice.load(tts.model_path)
        self._config: SynthesisConfig = SynthesisConfig(length_scale=tts.length_scale)
        self._resampler = resampler
        self._sample_rate = sample_rate
        logger.info("voix piper %s chargee en %.1f s (%d Hz)", tts.voice,
                    time.monotonic() - began, self._voice.config.sample_rate)

    def synthesize(self, text: str) -> Speech:
        """Synthétise le texte ; Piper le découpe en phrases, qu'on remet bout à bout."""
        began = time.monotonic()
        parts: list[np.ndarray] = []
        rate = self._voice.config.sample_rate
        for chunk in self._voice.synthesize(text, syn_config=self._config):
            parts.append(chunk.audio_float_array)
            rate = chunk.sample_rate
        signal = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
        pcm = self._resampler.convert(signal, rate)
        return Speech(
            pcm=pcm,
            audio_s=len(pcm) / 2 / self._sample_rate,
            elapsed_s=time.monotonic() - began,
        )
