"""Tests de la domotique, étape 11 : phrase → intention → commande → topics (sans broker)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_server.assistant import Assistant
from voice_server.home_control import DeviceCommand, DeviceIntent, HomeController, decide, parse_intent, slug
from voice_server.settings import HomeSettings, load_settings

HOME = HomeSettings(topic_prefix="home",
                    rooms={"salon": "du salon", "cuisine": "de la cuisine", "salle de bain": "de la salle de bain"},
                    boards={"esp32-01": "salon"})
ROOMS = list(HOME.rooms)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Fermez les volets du salon.", DeviceIntent("close", "shutter", "salon")),
        ("fermé la porte", DeviceIntent("close", "door", None)),               # faute de Whisper
        ("Peux-tu allumer la lumière de la cuisine ?", DeviceIntent("on", "light", "cuisine")),
        ("Éteins les lampes", DeviceIntent("off", "light", None)),
        ("Baisse les stores de la salle de bain", DeviceIntent("close", "shutter", "salle de bain")),
        ("Quelle heure est-il ?", None),
    ],
)
def test_parse_intent(text: str, expected: DeviceIntent | None) -> None:
    assert parse_intent(text, ROOMS) == expected


@pytest.mark.parametrize(
    ("intent", "answer", "command"),
    [
        (DeviceIntent("close", "shutter", "cuisine"), "Je ferme les volets de la cuisine.",
         DeviceCommand("close", "shutter", "cuisine")),
        (DeviceIntent("on", None, None), "J'allume la lumière du salon.",       # « allume » : lumière, ici
         DeviceCommand("on", "light", "salon")),
        (DeviceIntent("open", None, None), "Que dois-je ouvrir ?", None),
        (DeviceIntent(None, "door", None), "Que dois-je faire avec la porte ?", None),
        (DeviceIntent("on", "shutter", None), "Je ne sais pas allumer les volets.", None),
    ],
)
def test_decide(intent: DeviceIntent, answer: str, command: DeviceCommand | None) -> None:
    decision = decide(intent, HOME, "esp32-01")
    assert (decision.answer, decision.command) == (answer, command)


def test_unknown_board_needs_a_room() -> None:
    """Une carte sans pièce déclarée ne devine pas : elle demande."""
    assert decide(DeviceIntent("on", "light", None), HOME, "esp32-99").answer == "Dans quelle pièce ?"


def test_slug() -> None:
    assert slug("salle de bain") == "salle-de-bain"
    assert slug("entrée") == "entree"


class FakeLink:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, int]] = []

    def publish(self, device: str, suffix: str, payload: bytes, qos: int) -> None:
        self.sent.append((f"voice/{device}/{suffix}", payload, qos))

    def publish_topic(self, topic: str, payload: bytes, qos: int) -> None:
        self.sent.append((topic, payload, qos))


def test_command_for_the_board_room_also_goes_to_the_board() -> None:
    """Pièce de la carte : domotique ET carte (LED de démonstration)."""
    link = FakeLink()
    HomeController(HOME, link).execute("esp32-01", DeviceCommand("on", "light", "salon"), "Denis", "a1b2c3")

    [(topic, payload, qos), (local_topic, local, local_qos)] = link.sent
    assert (topic, qos) == ("home/salon/light/set", 1)
    assert json.loads(payload) == {"action": "on", "by": "Denis", "board": "esp32-01", "session": "a1b2c3"}
    assert (local_topic, local_qos) == ("voice/esp32-01/control", 1)
    assert json.loads(local) == {"cmd": "device", "device": "light", "action": "on"}


def test_command_for_another_room_only_goes_to_home() -> None:
    link = FakeLink()
    HomeController(HOME, link).execute("esp32-01", DeviceCommand("close", "shutter", "salle de bain"), None, "s")

    assert [topic for topic, _, _ in link.sent] == ["home/salle-de-bain/shutter/set"]
    assert json.loads(link.sent[0][1])["by"] is None


def test_assistant_prefers_home_commands_then_chat() -> None:
    """Domotique d'abord ; le reste suit les règles de conversation."""
    assistant = Assistant(home=HOME)

    order = assistant.respond("Allume la lumière", "Denis", "esp32-01")
    chat = assistant.respond("Bonjour", "Denis", "esp32-01")

    assert order.command == DeviceCommand("on", "light", "salon") and order.text == "J'allume la lumière du salon."
    assert chat.command is None and chat.text.startswith("Bonjour Denis")
    assert Assistant().respond("Allume la lumière").command is None  # sans config : pas de domotique


def test_project_config_rooms_and_boards() -> None:
    """Le config.yaml du dépôt déclare la pièce de la carte parmi les pièces connues."""
    home = load_settings(Path(__file__).resolve().parents[1] / "config.yaml").home
    assert home.boards["esp32-01"] in home.rooms
