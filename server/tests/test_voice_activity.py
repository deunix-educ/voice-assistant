"""Tests de la VAD, étape 7 : segmentation (sans modèle) puis Silero sur des signaux connus."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

from voice_server.settings import VadSettings
from voice_server.voice_activity import (
    WINDOW_SAMPLES,
    Segment,
    VoiceActivityDetector,
    envelope,
    find_segments,
    pad_segments,
)

VAD = VadSettings(threshold=0.5, min_silence_ms=500, min_speech_ms=250, speech_pad_ms=150)


def probs(pattern: str) -> list[float]:
    """« # » = 0,9 (parole), « . » = 0,4 (entre les deux seuils), « _ » = 0,05 (silence)."""
    return [{"#": 0.9, ".": 0.4, "_": 0.05}[c] for c in pattern]


def test_single_phrase() -> None:
    """Silence, parole, silence : un segment aux bonnes bornes."""
    assert find_segments(probs("____######______"), 0.5, 3, 2) == [Segment(4, 10)]


def test_short_pause_does_not_split() -> None:
    """Une pause plus courte que min_silence ne coupe pas la phrase."""
    assert find_segments(probs("__###__###____"), 0.5, 3, 2) == [Segment(2, 10)]


def test_long_pause_splits() -> None:
    """Une pause plus longue sépare deux segments."""
    assert find_segments(probs("__###_____###____"), 0.5, 3, 2) == [Segment(2, 5), Segment(10, 13)]


def test_hysteresis_keeps_speech_between_thresholds() -> None:
    """Entre 0,35 et 0,5, on reste en parole : pas de battement autour du seuil."""
    assert find_segments(probs("_##......##____"), 0.5, 3, 2) == [Segment(1, 11)]


def test_short_noise_is_dropped() -> None:
    """Un clic d'une fenêtre n'est pas de la parole."""
    assert find_segments(probs("___#_______"), 0.5, 3, 2) == []


def test_speech_until_the_end() -> None:
    """Bouton relâché en pleine phrase : le segment va jusqu'au bout."""
    assert find_segments(probs("___#####"), 0.5, 3, 2) == [Segment(3, 8)]


def test_padding_merges_close_segments() -> None:
    """La marge élargit les segments, bornée par l'audio ; les chevauchements fusionnent."""
    segments = [Segment(100, 200), Segment(260, 300), Segment(900, 990)]
    assert pad_segments(segments, 50, 1000) == [Segment(50, 350), Segment(850, 1000)]


def test_envelope() -> None:
    assert envelope(np.array([0.9, 0.4, 0.05]), 0.5) == "#._"


def fake_model(pattern: str) -> Callable[[np.ndarray], np.ndarray]:
    """Modèle qui rend les probabilités d'un motif, quelle que soit l'entrée."""
    def model(audio: np.ndarray) -> np.ndarray:
        assert audio.size % WINDOW_SAMPLES == 0
        return np.array(probs(pattern), dtype=np.float32)
    return model


def test_detector_keeps_only_speech() -> None:
    """Le PCM rendu est la parole, marges comprises ; le reste est retiré."""
    pattern = "_" * 20 + "#" * 20 + "_" * 20  # 60 fenêtres = 1,92 s
    pcm = np.arange(60 * WINDOW_SAMPLES, dtype="<i2").tobytes()
    detector = VoiceActivityDetector(VAD, 16000, model=fake_model(pattern))

    result = detector.detect(pcm)

    pad = 150 * 16
    assert result.segments == (Segment(20 * WINDOW_SAMPLES - pad, 40 * WINDOW_SAMPLES + pad),)
    assert result.speech_s == pytest.approx((20 * WINDOW_SAMPLES + 2 * pad) / 16000)
    first = np.frombuffer(result.speech_pcm[:2], dtype="<i2")[0]
    assert first == 20 * WINDOW_SAMPLES - pad  # l'échantillon d'origine, pas un décalage


def test_detector_without_speech() -> None:
    detector = VoiceActivityDetector(VAD, 16000, model=fake_model("_" * 10))

    result = detector.detect(bytes(10 * WINDOW_SAMPLES * 2))

    assert not result.has_speech and result.speech_pcm == b""


def test_wrong_rate_is_refused() -> None:
    with pytest.raises(ValueError):
        VoiceActivityDetector(VAD, 22050, model=fake_model(""))


def test_silero_ignores_silence_and_noise() -> None:
    """Le vrai Silero : ni le silence ni un bruit blanc ne sont de la parole."""
    pytest.importorskip("faster_whisper")
    detector = VoiceActivityDetector(VAD, 16000)
    noise = np.random.default_rng(0).normal(0, 300, 32000).astype("<i2").tobytes()

    assert not detector.detect(bytes(32000)).has_speech
    assert not detector.detect(noise).has_speech
