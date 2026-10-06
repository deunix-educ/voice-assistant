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
from voice_server.voice_activity import VoiceActivityDetector

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


def test_vad_finds_speech_between_silences(tts: TextToSpeech) -> None:
    """1 s de silence, une phrase, 1 s de silence : la VAD retrouve la phrase et retire le reste."""
    speech = tts.synthesize("Quelle heure est-il ?")
    silence = bytes(32000)
    detector = VoiceActivityDetector(SETTINGS.vad, SETTINGS.audio.sample_rate)

    result = detector.detect(silence + speech.pcm + silence)

    assert result.has_speech
    start = result.segments[0].start / 16000
    end = result.segments[-1].end / 16000
    assert 0.7 < start < 1.2                         # début de parole, marge comprise
    assert 1.0 + speech.audio_s - 0.4 < end < 1.0 + speech.audio_s + 0.3
    assert result.speech_s < speech.audio_s + 0.5    # les 2 s de silence sont parties


def test_words_have_increasing_times(tts: TextToSpeech, stt: SpeechToText) -> None:
    """Étape 10 : chaque mot a un instant, dans l'ordre, et les mots recomposent le texte."""
    transcript = stt.transcribe(tts.synthesize("Ferme les volets de la cuisine.").pcm)

    assert len(transcript.words) >= 5
    starts = [word.start_s for word in transcript.words]
    assert starts == sorted(starts)
    assert "".join(word.text for word in transcript.words).strip() == transcript.text


WAKE_READY = all((SETTINGS.wake.model_dir / name).exists()
                 for name in ("melspectrogram.onnx", "embedding_model.onnx", "alexa_v0.1.onnx"))


@pytest.mark.skipif(not WAKE_READY, reason="modeles openWakeWord absents : 'make models'")
def test_alexa_is_detected_and_silence_is_not(tts: TextToSpeech) -> None:
    """Étape 13 : « Alexa » dit par Piper déclenche ; le silence et une phrase ordinaire non."""
    pytest.importorskip("openwakeword")
    from voice_server.wake_word import WakeWordDetector

    def first_detection(pcm: bytes) -> str | None:
        detector = WakeWordDetector(SETTINGS.wake)
        padded = bytes(32000) + pcm + bytes(32000)
        for start in range(0, len(padded) - 1599, 1600):
            detection = detector.process(padded[start:start + 1600])
            if detection is not None:
                return detection.word
        return None

    loud = AudioResampler(SETTINGS.audio, -10.0)
    speaker = TextToSpeech(SETTINGS.tts, loud, SETTINGS.audio.sample_rate)
    assert first_detection(speaker.synthesize("Alexa, allume la lumière.").pcm) == "alexa"
    assert first_detection(speaker.synthesize("Bonjour, quelle heure est-il ?").pcm) is None
    assert first_detection(bytes(64000)) is None



@pytest.mark.skipif(not WAKE_READY, reason="modeles openWakeWord absents : 'make models'")
def test_per_word_threshold_and_near_miss(caplog: pytest.LogCaptureFixture, tts: TextToSpeech) -> None:
    """Un seuil propre au mot ; sous le seuil, le pic est journalisé comme « presque reconnu »."""
    pytest.importorskip("openwakeword")
    import dataclasses
    import logging

    from voice_server.wake_word import WakeWordDetector

    strict = dataclasses.replace(SETTINGS.wake, thresholds={"alexa": 1.01})  # inatteignable
    detector = WakeWordDetector(strict, "esp32-test")
    speaker = TextToSpeech(SETTINGS.tts, AudioResampler(SETTINGS.audio, -10.0), SETTINGS.audio.sample_rate)
    padded = bytes(32000) + speaker.synthesize("Alexa, allume la lumière.").pcm + bytes(64000)
    with caplog.at_level(logging.INFO, logger="voice_server.wake_word"):
        found = [detector.process(padded[i:i + 1600]) for i in range(0, len(padded) - 1599, 1600)]

    assert all(detection is None for detection in found)
    assert "presque reconnu « alexa »" in caplog.text


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


def test_transcription_without_word_times(tts: TextToSpeech, stt: SpeechToText) -> None:
    """Étape 16 : sans horodatage des mots, même texte, pas de mots."""
    pcm = tts.synthesize("Fermez la porte du garage.").pcm
    plain = stt.transcribe(pcm, words=False)
    assert plain.words == () and "garage" in plain.text.lower()
