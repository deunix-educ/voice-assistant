"""Tests de l'écoute mains libres, étape 13 : états, vie privée, fin de commande (sans modèle)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from voice_server.hands_free import HandsFreeListener, Mode
from voice_server.session_manager import SessionResult
from voice_server.settings import AudioSettings, WakeSettings
from voice_server.voice_activity import Segment, VadResult
from voice_server.wake_word import Detection

AUDIO = AudioSettings(sample_rate=16000, bits=16, channels=1, codec="pcm_s16le", chunk_ms=50)
WAKE = WakeSettings(enabled=True, words=("alexa",), threshold=0.5, thresholds={}, preroll_ms=500, end_silence_ms=800,
                    no_speech_timeout_s=4.0, max_command_s=10.0, cooldown_s=20.0, model_dir=Path("inutile"))
SILENCE = bytes(1600)
SPEECH = np.full(800, 3000, dtype="<i2").tobytes()
WAKE_WORD = np.full(800, 3001, dtype="<i2").tobytes()  # le faux détecteur réagit à cette valeur


class FakeDetector:
    def __init__(self) -> None:
        self.resets = 0

    def process(self, pcm: bytes) -> Detection | None:
        return Detection("alexa", 0.9) if pcm == WAKE_WORD else None

    def reset(self) -> None:
        self.resets += 1


class ChunkVad:
    """VAD de test : un chunk non nul est de la parole."""

    def detect(self, pcm: bytes) -> VadResult:
        samples = np.frombuffer(pcm, dtype="<i2")
        segments: list[Segment] = []
        for start in range(0, samples.size, 800):
            if np.any(samples[start:start + 800]):
                if segments and segments[-1].end == start:
                    segments[-1] = Segment(segments[-1].start, start + 800)
                else:
                    segments.append(Segment(start, start + 800))
        return VadResult(segments=tuple(segments), speech_pcm=b"", probabilities=np.zeros(0),
                         total_s=samples.size / 16000, speech_s=0.0, elapsed_s=0.0, sample_rate=16000)


class FakeLink:
    def __init__(self) -> None:
        self.controls: list[dict[str, object]] = []

    def publish(self, device: str, suffix: str, payload: bytes, qos: int) -> None:
        assert suffix == "control"
        self.controls.append(json.loads(payload))


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


class Harness:
    def __init__(self, tmp_path: Path, clock: Clock) -> None:
        self.link = FakeLink()
        self.commands: list[SessionResult] = []
        self.detectors: list[FakeDetector] = []
        self.clock = clock
        self.recordings = tmp_path / "recordings"

        def factory(device: str) -> FakeDetector:
            self.detectors.append(FakeDetector())
            return self.detectors[-1]

        self.listener = HandsFreeListener(WAKE, AUDIO, ChunkVad(), self.recordings, self.link,
                                          self.commands.append, factory, clock=clock)

    def feed(self, chunk: bytes, count: int = 1) -> None:
        for _ in range(count):
            self.listener.on_stream("esp32-01", chunk)
            self.clock.now += 0.05  # un chunk = 50 ms


@pytest.fixture
def h(tmp_path: Path, clock: Clock) -> Harness:
    return Harness(tmp_path, clock)


def test_standby_never_writes_anything(h: Harness) -> None:
    """Vie privée : sans mot de réveil, rien sur disque, rien transmis, mémoire bornée à 500 ms."""
    h.feed(SPEECH, 200)  # 10 s de conversation sans le mot

    assert h.commands == []
    assert not h.recordings.exists()
    assert h.listener.mode("esp32-01") is Mode.STANDBY


def test_wake_then_command_becomes_a_session(h: Harness) -> None:
    """Mot, commande, silence : une session, avec les 500 ms qui précèdent le mot."""
    h.feed(SILENCE, 20)
    h.feed(WAKE_WORD)
    h.feed(SPEECH, 30)   # 1,5 s de commande
    h.feed(SILENCE, 25)  # 1,25 s de silence : > 800 ms

    [result] = h.commands
    assert result.session_id.startswith("w")
    assert result.path.exists()
    assert 2.0 < result.duration_s < 3.5
    assert h.link.controls == [{"cmd": "capture", "active": True}, {"cmd": "capture", "active": False}]
    assert h.listener.mode("esp32-01") is Mode.ANSWER


def test_wake_without_command_goes_back_to_standby(h: Harness) -> None:
    """Mot seul : abandon après 4 s, rien écrit."""
    h.feed(WAKE_WORD)
    h.feed(SILENCE, 90)  # 4,5 s

    assert h.commands == [] and not h.recordings.exists()
    assert h.listener.mode("esp32-01") is Mode.STANDBY
    assert h.link.controls[-1] == {"cmd": "capture", "active": False}


def test_command_is_cut_at_max_duration(h: Harness) -> None:
    h.feed(WAKE_WORD)
    h.feed(SPEECH, 220)  # 11 s sans silence

    assert len(h.commands) == 1


def test_answer_is_not_heard_and_played_resumes(h: Harness) -> None:
    """Pendant la réponse, le micro est ignoré ; le bilan « played » relance la veille."""
    h.feed(WAKE_WORD)
    h.feed(SPEECH, 10)
    h.feed(SILENCE, 20)
    h.feed(WAKE_WORD, 5)  # la carte « s'entend » : ignoré
    assert len(h.commands) == 1

    h.listener.on_event("esp32-01", b'{"event":"played","session":"rep-w1"}')

    assert h.listener.mode("esp32-01") is Mode.STANDBY
    assert h.detectors[0].resets == 1  # historique du mot précédent effacé


def test_no_played_report_times_out(h: Harness) -> None:
    h.feed(WAKE_WORD)
    h.feed(SPEECH, 10)
    h.feed(SILENCE, 20)
    h.clock.now += 21

    h.listener.sweep()

    assert h.listener.mode("esp32-01") is Mode.STANDBY


def test_listen_is_sent_once_per_boot(h: Harness) -> None:
    """Écoute activée à la connexion et après un redémarrage, pas à chaque état périodique."""
    state = '{{"status":"online","uptime_s":{}}}'
    h.listener.on_state("esp32-01", state.format(5).encode())
    h.listener.on_state("esp32-01", state.format(35).encode())   # état périodique
    h.listener.on_state("esp32-01", state.format(3).encode())    # redémarrage

    assert h.link.controls == [{"cmd": "listen", "enabled": True}] * 2

    h.listener.stop()
    assert h.link.controls[-1] == {"cmd": "listen", "enabled": False}


def test_each_board_has_its_own_detector(h: Harness) -> None:
    """Deux cartes : deux historiques, jamais mélangés."""
    h.listener.on_stream("esp32-01", SILENCE)
    h.listener.on_stream("esp32-02", SILENCE)
    assert len(h.detectors) == 2


def test_burst_delivery_is_judged_on_audio_time(h: Harness) -> None:
    """Wi-Fi en rafale : 2,5 s de son arrivées en même temps donnent la même décision."""
    h.listener.on_stream("esp32-01", WAKE_WORD)
    for chunk in [SPEECH] * 30 + [SILENCE] * 25:   # aucun temps ne s'écoule à l'horloge
        h.listener.on_stream("esp32-01", chunk)

    assert len(h.commands) == 1


def test_stream_lost_during_command_is_closed(h: Harness) -> None:
    """La carte décroche en pleine commande : on traite ce qu'on a après 2 s sans chunk."""
    h.feed(WAKE_WORD)
    h.feed(SPEECH, 10)
    h.clock.now += 3

    h.listener.sweep()

    assert len(h.commands) == 1
