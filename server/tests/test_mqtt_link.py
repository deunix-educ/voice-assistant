"""Tests de la liaison MQTT du serveur, étape 15 : présence, refus du broker, journal fichier."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from voice_server.__main__ import setup_logging
from voice_server.mqtt_link import presence_topic, refusal_hint, split_topic
from voice_server.settings import load_settings

CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"


def test_presence_topic_is_not_taken_for_a_board() -> None:
    """Le serveur s'abonne à voice/+/state, event... : sa présence ne doit pas y tomber."""
    topic = presence_topic("voice")
    assert topic == "voice/server/status"
    parsed = split_topic(topic, "voice")
    assert parsed is not None and parsed[1] not in ("state", "event", "audio/in", "audio/stream")


def test_refused_credentials_point_to_the_fix() -> None:
    hint = refusal_hint(ReasonCode(PacketTypes.CONNACK, "Not authorized"), "voice-server")
    assert "server/.env" in hint and "make mqtt-user NAME=voice-server" in hint
    bad_password = refusal_hint(ReasonCode(PacketTypes.CONNACK, "Bad user name or password"), "voice-server")
    assert "server/.env" in bad_password


def test_other_refusal_names_the_client() -> None:
    hint = refusal_hint(ReasonCode(PacketTypes.CONNACK, "Server unavailable"), "voice-server")
    assert "voice-server" in hint and "server/.env" not in hint


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """setup_logging remplace la configuration globale : on remet celle de pytest après."""
    root = logging.getLogger()
    saved = (root.level, list(root.handlers))
    yield
    for handler in root.handlers:
        if handler not in saved[1]:
            handler.close()
    root.handlers[:] = saved[1]
    root.setLevel(saved[0])


def test_log_file_receives_the_journal(tmp_path: Path, restore_logging: None) -> None:
    settings = dataclasses.replace(load_settings(CONFIG), log_file=tmp_path / "logs" / "voice-server.log")
    setup_logging(settings)
    logging.getLogger("voice_server").info("essai du journal")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert settings.log_file is not None
    assert "essai du journal" in settings.log_file.read_text(encoding="utf-8")


def test_log_file_is_optional(tmp_path: Path) -> None:
    text = CONFIG.read_text(encoding="utf-8").replace("file: logs/voice-server.log", "file:")
    config = tmp_path / "config.yaml"
    config.write_text(text, encoding="utf-8")
    assert load_settings(config).log_file is None
    assert load_settings(CONFIG).log_file == (CONFIG.parent / "logs" / "voice-server.log").resolve()
