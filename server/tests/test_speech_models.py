"""Tests des vrais modèles, étape 6 : ignorés tant que « make models » n'a pas tourné.

Piper dit une phrase, Whisper doit la relire : les deux modèles se vérifient
l'un l'autre, sans micro ni haut-parleur.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from voice_server.audio_resampler import AudioResampler, peak_dbfs
from voice_server.settings import Settings, load_settings
from voice_server.speech_to_text import SpeechToText
from voice_server.text_to_speech import TextToSpeech

SETTINGS: Settings = load_settings(Path(__file__).resolve().parents[1] / "config.yaml")

pytestmark = pytest.mark.skipif(
    not (SETTINGS.stt.model_dir / "model.bin").exists() or not SETTINGS.tts.model_path.exists(),
    reason="modeles absents : lancez 'make models'",
)


@pytest.fixture(scope="module")
def tts() -> TextToSpeech:
    return TextToSpeech(SETTINGS.tts, AudioResampler(SETTINGS.audio), SETTINGS.audio.sample_rate)


@pytest.fixture(scope="module")
def stt() -> SpeechToText:
    return SpeechToText(SETTINGS.stt, SETTINGS.audio.sample_rate)


def test_piper_output_is_system_format(tts: TextToSpeech) -> None:
    """Piper produit du 22 050 Hz : la sortie doit être en 16 kHz, crête -3 dBFS."""
    speech = tts.synthesize("Bonjour.")

    assert len(speech.pcm) % 2 == 0
    assert 0.3 < speech.audio_s < 3.0
    assert peak_dbfs(speech.pcm) == pytest.approx(-3.0, abs=0.1)


def test_round_trip(tts: TextToSpeech, stt: SpeechToText) -> None:
    """Ce que Piper dit, Whisper le relit."""
    transcript = stt.transcribe(tts.synthesize("Bonjour, comment tu t'appelles ?").pcm)

    assert "appelles" in transcript.text.lower()


def test_noise_hallucination_is_dropped(stt: SpeechToText) -> None:
    """Sur du bruit, Whisper invente parfois un sous-titre : il ne doit pas passer."""
    noise = np.random.default_rng(0).normal(0, 300, 32000).astype("<i2").tobytes()

    assert "amara" not in stt.transcribe(noise).text.lower()


def test_speech_between_silences_is_transcribed_without_vad(tts: TextToSpeech, stt: SpeechToText) -> None:
    """F4, sans VAD : 1 s de silence avant (l'identification du doigt), la phrase, 1 s après (le retrait)."""
    silence = bytes(32000)

    transcript = stt.transcribe(silence + tts.synthesize("Quelle heure est-il ?").pcm + silence)

    assert "heure" in transcript.text.lower()


def test_repeated_answer_comes_from_the_cache(tts: TextToSpeech) -> None:
    """Étape 16 : une réponse déjà dite n'est pas resynthétisée (Piper varie d'un essai à l'autre)."""
    first = tts.synthesize("J'allume la lumière du salon.")
    again = tts.synthesize("J'allume la lumière du salon.")
    other = tts.synthesize("J'éteins la lumière du salon.")

    assert again.pcm == first.pcm and again.audio_s == first.audio_s
    assert again.elapsed_s < 0.005 < first.elapsed_s
    assert other.pcm != first.pcm


def test_cache_keeps_a_bounded_number_of_answers(tts: TextToSpeech, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("voice_server.text_to_speech.CACHE_PHRASES", 2)
    fresh = TextToSpeech(SETTINGS.tts, AudioResampler(SETTINGS.audio), SETTINGS.audio.sample_rate)
    for text in ("Un.", "Deux.", "Trois."):
        fresh.synthesize(text)
    assert fresh.synthesize("Trois.").elapsed_s < 0.005   # encore en mémoire
    assert fresh.synthesize("Un.").elapsed_s > 0.005      # la plus ancienne est sortie
