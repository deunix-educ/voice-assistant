"""Détection du mot de réveil avec openWakeWord (étape 13).

openWakeWord calcule un spectrogramme, puis une empreinte de chaque tranche de
80 ms, puis un petit modèle par mot donne un score entre 0 et 1. Les modèles
prêts à l'emploi sont anglais : « Alexa », « Hey Jarvis », « Hey Mycroft ».

Exécution en ONNX : la version TensorFlow Lite n'existe pas pour Python 3.13.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from voice_server.settings import WakeSettings

if TYPE_CHECKING:  # import réel dans __init__ : extra « wakeword » facultatif
    from openwakeword.model import Model

logger = logging.getLogger(__name__)

MODEL_VERSION = "v0.1"
FEATURE_MODELS = ("melspectrogram.onnx", "embedding_model.onnx")
NEAR_MISS_FLOOR = 0.15   # un pic de score au-dessus, sans atteindre le seuil : « presque reconnu »


@dataclass(frozen=True)
class Detection:
    """Un mot de réveil reconnu."""

    word: str       # « alexa », « hey_jarvis »…
    score: float


def model_files(settings: WakeSettings) -> list[str]:
    """Fichiers nécessaires : les deux modèles communs, puis un par mot."""
    return [*FEATURE_MODELS, *(f"{word}_{MODEL_VERSION}.onnx" for word in settings.words)]


class WakeWordDetector:
    """Donne au fil de l'eau les scores du mot de réveil, pour UNE carte.

    Le modèle garde un historique audio interne : un détecteur par carte, sinon
    les flux de deux cartes se mélangeraient.
    """

    def __init__(self, settings: WakeSettings, device: str = "") -> None:
        """
        Args:
            device: carte écoutée, pour le journal.

        Raises:
            FileNotFoundError: modèles absents (« make models »).
            ImportError: openwakeword non installé (« make install-wakeword »).
        """
        missing = [name for name in model_files(settings) if not (settings.model_dir / name).exists()]
        if missing:
            raise FileNotFoundError(f"modeles openWakeWord absents de {settings.model_dir} "
                                    f"({', '.join(missing)}) : lancez 'make models'")
        from openwakeword.model import Model

        directory = settings.model_dir
        self._model: Model = Model(
            wakeword_models=[str(directory / f"{word}_{MODEL_VERSION}.onnx") for word in settings.words],
            inference_framework="onnx",
            melspec_model_path=str(directory / FEATURE_MODELS[0]),
            embedding_model_path=str(directory / FEATURE_MODELS[1]),
        )
        self._thresholds = {word: settings.thresholds.get(word, settings.threshold) for word in settings.words}
        self._device = device
        self._peaks: dict[str, float] = {}  # pic de score en cours, par mot (diagnostic)

    def process(self, pcm: bytes) -> Detection | None:
        """Ajoute un chunk (s16le, 16 kHz) ; rend le mot s'il vient d'être reconnu."""
        samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")
        raw: Any = self._model.predict(samples)  # dict des scores (tuple seulement si timing=True)
        scores = {str(key).removesuffix(f"_{MODEL_VERSION}"): float(value) for key, value in raw.items()}
        detected: Detection | None = None
        for word, score in scores.items():
            threshold = self._thresholds.get(word, 1.0)
            if score >= threshold:
                if detected is None or score > detected.score:
                    detected = Detection(word=word, score=score)
            self._track_near_miss(word, score, threshold)
        if detected is not None:
            self._peaks.clear()
        return detected

    def _track_near_miss(self, word: str, score: float, threshold: float) -> None:
        """Journalise un mot presque reconnu : la mesure qui permet de régler son seuil."""
        if score >= NEAR_MISS_FLOOR:
            self._peaks[word] = max(self._peaks.get(word, 0.0), score)
            return
        peak = self._peaks.pop(word, 0.0)  # le pic vient de retomber
        if NEAR_MISS_FLOOR <= peak < threshold:
            logger.info("%s : mot presque reconnu « %s » : %.2f (seuil %.2f)", self._device, word, peak, threshold)

    def reset(self) -> None:
        """Oublie l'historique : à faire après une réponse, pour ne pas redétecter l'ancien mot."""
        self._model.reset()
        self._peaks.clear()
