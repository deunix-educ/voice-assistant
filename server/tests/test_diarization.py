"""Tests de la diarisation, étape 8 : présentation, télémétrie, modèle réel s'il est présent."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from voice_server.diarization import Diarization, Diarizer, SpeakerTurn, disable_telemetry, timeline
from voice_server.settings import DiarizationSettings, load_settings

SETTINGS = load_settings(Path(__file__).resolve().parents[1] / "config.yaml")


def two_speakers() -> Diarization:
    return Diarization(
        turns=(SpeakerTurn("SPEAKER_00", 0.0, 0.5), SpeakerTurn("SPEAKER_01", 0.6, 1.0)),
        speakers=("SPEAKER_00", "SPEAKER_01"),
        embeddings=None, audio_s=1.0, elapsed_s=0.2,
    )


def test_describe() -> None:
    assert two_speakers().describe() == "SPEAKER_00 0.00-0.50, SPEAKER_01 0.60-1.00"


def test_timeline_one_row_per_speaker() -> None:
    assert timeline(two_speakers()) == ["SPEAKER_00 #####_____", "SPEAKER_01 ______####"]


def test_telemetry_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """pyannote 4 envoie des statistiques par défaut : jamais dans ce projet."""
    monkeypatch.setenv("PYANNOTE_METRICS_ENABLED", "true")
    disable_telemetry()
    assert os.environ["PYANNOTE_METRICS_ENABLED"] == "false"


def test_missing_model_says_what_to_do(tmp_path: Path) -> None:
    settings = DiarizationSettings(enabled=True, model="x/y", max_speakers=3, model_dir=tmp_path / "y")
    with pytest.raises(FileNotFoundError, match="make models"):
        Diarizer(settings, 16000)


@pytest.mark.skipif(not (SETTINGS.diarization.model_dir / "config.yaml").exists(),
                    reason="modele de diarisation absent : HF_TOKEN puis 'make models'")
def test_real_pipeline_runs_offline_without_telemetry() -> None:
    """Le vrai pipeline se charge du dossier local et ne trouve personne dans le silence."""
    pytest.importorskip("pyannote.audio")
    diarizer = Diarizer(SETTINGS.diarization, 16000)

    result = diarizer.diarize(np.zeros(32000, dtype="<i2").tobytes())

    assert result.turns == ()
    assert os.environ["PYANNOTE_METRICS_ENABLED"] == "false"


@pytest.mark.skipif(not (SETTINGS.diarization.model_dir / "config.yaml").exists(),
                    reason="modele de diarisation absent : HF_TOKEN puis 'make models'")
def test_embeddings_exist_for_a_short_question() -> None:
    """Une question de 2 s a une empreinte (celles de pyannote seraient NaN sous 2 s de parole)."""
    pytest.importorskip("pyannote.audio")
    from voice_server.audio_resampler import AudioResampler
    from voice_server.text_to_speech import TextToSpeech

    tts = TextToSpeech(SETTINGS.tts, AudioResampler(SETTINGS.audio, -10.0), 16000)
    diarizer = Diarizer(SETTINGS.diarization, 16000)
    first = diarizer.diarize(tts.synthesize("Quelle heure est-il ?").pcm)
    second = diarizer.embed(tts.synthesize("Fermez la porte du garage.").pcm)

    assert first.embeddings is not None and first.embeddings.shape == (1, 256)
    assert np.all(np.isfinite(first.embeddings))
    cosine = float(first.embeddings[0] @ second / np.linalg.norm(first.embeddings[0]) / np.linalg.norm(second))
    assert cosine > 0.45  # même voix Piper, deux phrases
