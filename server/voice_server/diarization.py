"""Diarisation : qui parle quand, avec pyannote (étape 8).

La diarisation découpe l'audio en tours de parole étiquetés SPEAKER_00,
SPEAKER_01… Elle ne sait pas QUI parle : les étiquettes ne valent que pour
une session. Mettre un nom sur une voix, c'est l'identification (étape 9).

Elle ne reçoit que la parole trouvée par la VAD, jamais le flux continu du
micro : c'est le traitement le plus coûteux de la chaîne.

Étape 9 : une empreinte vocale par locuteur, calculée ici avec le modèle
d'empreintes du même pipeline (WeSpeaker ResNet34). Celles que rend pyannote ne
conviennent pas aux questions courtes : il ne garde que les fenêtres de 10 s où
le locuteur parle seul au moins 2 s ; en dessous, sa moyenne est NaN.
"""

from __future__ import annotations

import logging
import os
import time
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from voice_server.settings import DiarizationSettings

if TYPE_CHECKING:  # import réel dans __init__ : l'extra « diarization » est facultatif
    from pyannote.audio import Inference, Pipeline

logger = logging.getLogger(__name__)

MIN_EMBEDDING_S = 0.3  # en dessous, l'empreinte ne vaut rien : le locuteur reste « Inconnu »


@dataclass(frozen=True)
class SpeakerTurn:
    """Un tour de parole : un locuteur, de start_s à end_s."""

    speaker: str
    start_s: float
    end_s: float


@dataclass(frozen=True)
class Diarization:
    """Résultat de la diarisation d'une séquence parlée."""

    turns: tuple[SpeakerTurn, ...]   # dans l'ordre chronologique, sans chevauchement
    speakers: tuple[str, ...]        # étiquettes distinctes, ordre de embeddings
    embeddings: np.ndarray | None    # (locuteurs, dimension), ligne NaN si trop peu de parole
    audio_s: float
    elapsed_s: float

    def describe(self) -> str:
        """Tours de parole lisibles : « SPEAKER_00 0.00-1.20, SPEAKER_01 1.35-2.60 »."""
        return ", ".join(f"{t.speaker} {t.start_s:.2f}-{t.end_s:.2f}" for t in self.turns)


def disable_telemetry() -> None:
    """Coupe la télémétrie de pyannote.

    pyannote 4 envoie par défaut des statistiques d'usage (durée de l'audio,
    nombre de locuteurs) à otel.pyannote.ai : contraire à un assistant 100 % local.
    La variable est relue à chaque traitement : la forcer ici suffit.
    """
    os.environ["PYANNOTE_METRICS_ENABLED"] = "false"


class Diarizer:
    """Applique le pipeline pyannote à un PCM s16le mono, sur CPU."""

    def __init__(self, settings: DiarizationSettings, sample_rate: int) -> None:
        """
        Raises:
            FileNotFoundError: le modèle n'a pas été téléchargé.
            ImportError: l'extra « diarization » n'est pas installé.
        """
        if not (settings.model_dir / "config.yaml").exists():
            raise FileNotFoundError(f"modele de diarisation absent de {settings.model_dir} : "
                                    "HF_TOKEN dans server/.env, puis 'make models'")
        disable_telemetry()
        from pyannote.audio import Pipeline

        began = time.monotonic()
        # Dossier local : pyannote n'interroge pas le hub (ses sous-modèles sont des sous-dossiers).
        pipeline = Pipeline.from_pretrained(settings.model_dir)
        if pipeline is None:
            raise RuntimeError(f"pipeline pyannote illisible : {settings.model_dir}")
        self._pipeline: Pipeline = pipeline
        from pyannote.audio import Inference, Model

        # Empreintes : le sous-modèle du pipeline, appliqué à tout l'audio d'un locuteur.
        embedding_model = Model.from_pretrained(settings.model_dir / "embedding")
        if embedding_model is None:
            raise RuntimeError(f"modele d'empreintes illisible : {settings.model_dir / 'embedding'}")
        self._embedding: Inference = Inference(embedding_model, window="whole")
        dimension: Any = embedding_model.dimension  # propriété pyannote, mal typée par torch.nn.Module
        self._dimension = int(dimension)
        self._settings = settings
        self._sample_rate = sample_rate
        # Premier passage à vide : les calculs s'initialisent ici, pas à la première question.
        self.diarize(bytes(sample_rate * 2))
        logger.info("diarisation %s chargee en %.1f s", settings.model, time.monotonic() - began)

    def diarize(self, pcm: bytes) -> Diarization:
        """Rend les tours de parole et une empreinte par locuteur ; instants relatifs au début du PCM."""
        began = time.monotonic()
        audio = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
        waveform = self._waveform(audio)
        with warnings.catch_warnings():
            # torch prévient quand un morceau de parole est trop court pour un écart-type :
            # sans conséquence sur le résultat, mais une ligne de journal à chaque session.
            warnings.filterwarnings("ignore", message=r"std\(\): degrees of freedom")
            # Ses propres empreintes sont NaN sur une question courte (voir en tête du module) :
            # numpy le signale, mais on ne s'en sert pas.
            warnings.filterwarnings("ignore", message="Mean of empty slice", category=RuntimeWarning)
            warnings.filterwarnings("ignore", message="invalid value encountered in divide",
                                    category=RuntimeWarning)
            output: Any = self._pipeline({"waveform": waveform, "sample_rate": self._sample_rate},
                                         max_speakers=self._settings.max_speakers)

        # Version « exclusive » : un seul locuteur à la fois, pour découper le texte (étape 10)
        # et pour que chaque empreinte ne contienne qu'une voix.
        annotation = output.exclusive_speaker_diarization
        turns = tuple(
            SpeakerTurn(speaker=str(label), start_s=float(segment.start), end_s=float(segment.end))
            for segment, _, label in annotation.itertracks(yield_label=True)
        )
        speakers = tuple(str(label) for label in annotation.labels())
        embeddings = np.stack([self._speaker_embedding(audio, turns, speaker) for speaker in speakers]) \
            if speakers else None
        return Diarization(
            turns=turns,
            speakers=speakers,
            embeddings=embeddings,
            audio_s=audio.size / self._sample_rate,
            elapsed_s=time.monotonic() - began,
        )

    def embed(self, pcm: bytes) -> np.ndarray:
        """Empreinte vocale d'un PCM entier (enrôlement : une seule voix attendue)."""
        audio = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
        return self._embed(audio)

    def _speaker_embedding(self, audio: np.ndarray, turns: tuple[SpeakerTurn, ...], speaker: str) -> np.ndarray:
        """Empreinte de toute la parole d'un locuteur, ses tours mis bout à bout."""
        pieces = [audio[round(t.start_s * self._sample_rate):round(t.end_s * self._sample_rate)]
                  for t in turns if t.speaker == speaker]
        return self._embed(np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32))

    def _embed(self, audio: np.ndarray) -> np.ndarray:
        if audio.size < MIN_EMBEDDING_S * self._sample_rate:
            return np.full(self._dimension, np.nan, dtype=np.float32)
        embedding: Any = self._embedding({"waveform": self._waveform(audio), "sample_rate": self._sample_rate})
        return np.asarray(embedding, dtype=np.float32).reshape(-1)

    @staticmethod
    def _waveform(audio: np.ndarray) -> Any:
        """Audio en mémoire au format pyannote : tenseur (canaux, échantillons), sans fichier."""
        import torch

        return torch.from_numpy(audio.copy()).unsqueeze(0)


def timeline(diarization: Diarization, step_s: float = 0.1) -> list[str]:
    """Une ligne par locuteur, un caractère par step_s : « # » quand il parle."""
    width = max(1, round(diarization.audio_s / step_s))
    lines: list[str] = []
    for speaker in diarization.speakers:
        row = ["_"] * width
        for turn in diarization.turns:
            if turn.speaker == speaker:
                for index in range(round(turn.start_s / step_s), min(width, round(turn.end_s / step_s))):
                    row[index] = "#"
        lines.append(f"{speaker} {''.join(row)}")
    return lines
