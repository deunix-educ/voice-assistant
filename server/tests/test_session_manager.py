"""Tests du serveur, étape 4 : reconstitution des sessions (sans broker)."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from voice_server.mqtt_link import split_topic
from voice_server.session_manager import Outcome, SessionManager
from voice_server.settings import AudioSettings, SessionSettings, load_settings

AUDIO = AudioSettings(sample_rate=16000, bits=16, channels=1, codec="pcm_s16le", chunk_ms=50)
SESSIONS = SessionSettings(max_seconds=30.0, idle_timeout_s=5.0, end_grace_s=0.5)
CHUNK = bytes(1600)


class FakeClock:
    """Horloge pilotée par le test : on avance le temps sans attendre."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def start_event(session: str = "a1b2c3", rate: int = 16000) -> bytes:
    return json.dumps({"event": "start", "session": session, "rate": rate, "bits": 16,
                       "channels": 1, "codec": "pcm_s16le", "chunk_ms": 50}).encode()


def end_event(chunks: int, session: str = "a1b2c3") -> bytes:
    return json.dumps({"event": "end", "session": session, "chunks": chunks,
                       "duration_ms": chunks * 50, "send_failures": 0,
                       "capture_overruns": 0}).encode()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def manager(tmp_path: Path, clock: FakeClock) -> SessionManager:
    return SessionManager(AUDIO, SESSIONS, tmp_path, clock=clock)


def test_complete_session_is_written_immediately(manager: SessionManager) -> None:
    """START + 20 chunks + END : WAV d'une seconde, écrit dès le END."""
    manager.on_event("esp32-01", start_event())
    for _ in range(20):
        manager.on_audio("esp32-01", CHUNK)
    results = manager.on_event("esp32-01", end_event(20))

    assert len(results) == 1
    result = results[0]
    assert result.outcome is Outcome.COMPLETE
    assert result.chunks_received == 20
    with wave.open(str(result.path)) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        assert wav.getnframes() == 16000


def test_lost_chunks_are_reported_after_grace(manager: SessionManager, clock: FakeClock) -> None:
    """END annonce 20 chunks, 18 arrivent : bilan « lost_chunks » après le délai de grâce."""
    manager.on_event("esp32-01", start_event())
    for _ in range(18):
        manager.on_audio("esp32-01", CHUNK)
    assert manager.on_event("esp32-01", end_event(20)) == []

    clock.now += 0.2
    assert manager.sweep() == []  # on attend encore les chunks en vol
    clock.now += 0.4
    results = manager.sweep()
    assert [r.outcome for r in results] == [Outcome.LOST_CHUNKS]
    assert results[0].chunks_announced == 20


def test_late_chunk_within_grace_completes(manager: SessionManager, clock: FakeClock) -> None:
    """Le END arrive avant le dernier chunk (topics différents) : la session reste complète."""
    manager.on_event("esp32-01", start_event())
    for _ in range(19):
        manager.on_audio("esp32-01", CHUNK)
    manager.on_event("esp32-01", end_event(20))
    manager.on_audio("esp32-01", CHUNK)  # retardataire
    clock.now += 0.6

    assert [r.outcome for r in manager.sweep()] == [Outcome.COMPLETE]


def test_session_without_end_times_out(manager: SessionManager, clock: FakeClock) -> None:
    """L'ESP32 disparaît en pleine session : clôture après 5 s sans rien recevoir."""
    manager.on_event("esp32-01", start_event())
    manager.on_audio("esp32-01", CHUNK)
    clock.now += 4.9
    assert manager.sweep() == []
    clock.now += 0.2

    results = manager.sweep()
    assert [r.outcome for r in results] == [Outcome.TIMEOUT]
    assert results[0].chunks_announced is None


def test_new_start_interrupts_previous(manager: SessionManager) -> None:
    """Un START sans END préalable clôt la session précédente."""
    manager.on_event("esp32-01", start_event("aaaaaa"))
    manager.on_audio("esp32-01", CHUNK)
    results = manager.on_event("esp32-01", start_event("bbbbbb"))

    assert [(r.session_id, r.outcome) for r in results] == [("aaaaaa", Outcome.INTERRUPTED)]


def test_start_during_grace_keeps_end_verdict(manager: SessionManager) -> None:
    """Nouvel appui pendant le délai de grâce : la session précédente garde son vrai bilan.

    Cas trouvé par l'essai de bout en bout : elle était classée « interrompue ».
    """
    manager.on_event("esp32-01", start_event("aaaaaa"))
    for _ in range(18):
        manager.on_audio("esp32-01", CHUNK)
    manager.on_event("esp32-01", end_event(20, "aaaaaa"))  # 2 chunks manquent
    results = manager.on_event("esp32-01", start_event("bbbbbb"))

    assert [(r.session_id, r.outcome) for r in results] == [("aaaaaa", Outcome.LOST_CHUNKS)]


def test_wrong_format_is_refused(manager: SessionManager) -> None:
    """Une session annoncée à 48 kHz n'est pas ouverte : ses chunks sont orphelins."""
    manager.on_event("esp32-01", start_event(rate=48000))
    manager.on_audio("esp32-01", CHUNK)

    assert manager.orphan_chunks == 1


def test_devices_are_independent(manager: SessionManager) -> None:
    """Deux cartes parlent en même temps : deux fichiers distincts."""
    manager.on_event("esp32-01", start_event("111111"))
    manager.on_event("esp32-02", start_event("222222"))
    manager.on_audio("esp32-01", CHUNK)
    manager.on_audio("esp32-02", CHUNK)
    manager.on_audio("esp32-02", CHUNK)

    first = manager.on_event("esp32-01", end_event(1, "111111"))[0]
    second = manager.on_event("esp32-02", end_event(2, "222222"))[0]
    assert (first.chunks_received, second.chunks_received) == (1, 2)
    assert first.path != second.path


def test_garbage_event_is_ignored(manager: SessionManager) -> None:
    """Un événement illisible ne fait rien planter."""
    assert manager.on_event("esp32-01", b"\xff\x00pas du json") == []


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("voice/esp32-01/audio/in", ("esp32-01", "audio/in")),
        ("voice/esp32-01/event", ("esp32-01", "event")),
        ("other/esp32-01/event", None),
        ("voice/esp32-01", None),
        ("voice//event", None),
    ],
)
def test_split_topic(topic: str, expected: tuple[str, str] | None) -> None:
    """Découpage des topics reçus."""
    assert split_topic(topic, "voice") == expected


def test_project_config_loads() -> None:
    """Le config.yaml du dépôt se charge et reste cohérent avec le firmware."""
    settings = load_settings(Path(__file__).resolve().parents[1] / "config.yaml")

    assert settings.audio.bytes_per_second == 32000
    assert settings.mqtt.topic_prefix == "voice"
    assert settings.recordings_dir.is_absolute()


def test_finished_sessions_are_notified(tmp_path: Path, clock: FakeClock) -> None:
    """Le rappel on_finished reçoit chaque session terminée (utilisé par le mode écho)."""
    finished: list[str] = []
    manager = SessionManager(AUDIO, SESSIONS, tmp_path, clock=clock,
                             on_finished=lambda result: finished.append(result.session_id))
    manager.on_event("esp32-01", start_event())
    manager.on_audio("esp32-01", CHUNK)
    manager.on_event("esp32-01", end_event(1))

    assert finished == ["a1b2c3"]
