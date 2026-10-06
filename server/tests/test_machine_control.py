"""Tests des machines Linux, étape 15b : phrases, décisions, droits, Wake-on-LAN (sans réseau)."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest

from voice_server.access_control import AccessPolicy
from voice_server.assistant import Assistant
from voice_server.command_router import CommandRouter
from voice_server.home_control import DeviceCommand, HomeController
from voice_server.machine_control import (
    AgentStatus,
    MachineCommand,
    MachineController,
    MachineIntent,
    decide_machine,
    magic_packet,
    parse_machine_intent,
)
from voice_server.mqtt_link import MqttLink
from voice_server.settings import MachinesSettings, load_settings

SETTINGS = load_settings(Path(__file__).resolve().parents[1] / "config.yaml")
MACHINES = SETTINGS.machines
ONLINE = AgentStatus(online=True, actions=("lock", "reboot", "shutdown"))
ONLINE_PAYLOAD = json.dumps({"status": "online", "actions": ["lock", "reboot", "shutdown"]}).encode()
MAC = "aa:bb:cc:dd:ee:ff"


class FakeLink:
    def __init__(self) -> None:
        self.topics: list[tuple[str, dict[str, object]]] = []

    def publish(self, device: str, suffix: str, payload: bytes, qos: int) -> None:
        self.topics.append((f"voice/{device}/{suffix}", json.loads(payload)))

    def publish_topic(self, topic: str, payload: bytes, qos: int) -> None:
        self.topics.append((topic, json.loads(payload)))


def with_mac(mac: str = MAC) -> MachinesSettings:
    """Machines de config.yaml, avec une adresse MAC : le réveil devient possible."""
    machine = dataclasses.replace(MACHINES.machines["pc-bureau"], mac=mac)
    return dataclasses.replace(MACHINES, machines={"pc-bureau": machine})


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("Éteins le PC du bureau.", "shutdown"),
        ("Éteins le P.C. du bureau.", "shutdown"),
        ("Tu peux redémarrer l'ordinateur du bureau ?", "reboot"),
        ("Verrouille le PC du bureau", "lock"),
        # Mots anglais, tels que Whisper les a écrits à l'essai réel du 2026-10-06.
        ("Locke le PC du bureau", "lock"),
        ("Reboot le PC du bureau", "reboot"),
        ("Shut down le PC du bureau", "shutdown"),
        ("Allume le PC du bureau", "wake"),
        ("Le PC du bureau", None),
    ],
)
def test_phrases_naming_a_machine(text: str, action: str | None) -> None:
    assert parse_machine_intent(text, MACHINES) == MachineIntent("pc-bureau", action)


def test_room_alone_is_not_a_machine() -> None:
    """« bureau » est une pièce : seule « PC du bureau » désigne la machine."""
    assert parse_machine_intent("Éteins la lumière du bureau", MACHINES) is None


def test_command_carries_its_sentences() -> None:
    decision = decide_machine(MachineIntent("pc-bureau", "shutdown"), MACHINES, ONLINE)
    assert decision.command == MachineCommand("shutdown", "pc-bureau", said="J'éteins le PC du bureau.",
                                              asked="éteindre le PC du bureau")
    assert decision.answer == "J'éteins le PC du bureau."


@pytest.mark.parametrize(
    ("action", "status", "answer"),
    [
        (None, ONLINE, "Que dois-je faire avec le PC du bureau ?"),
        ("shutdown", None, "Le PC du bureau ne répond pas : la machine est éteinte, ou son agent est arrêté."),
        ("shutdown", AgentStatus(False, ()), "Le PC du bureau ne répond pas : la machine est éteinte, "
                                             "ou son agent est arrêté."),
        ("lock", AgentStatus(True, ("shutdown",)), "Je ne peux pas verrouiller le PC du bureau : "
                                                   "son agent ne le permet pas."),
        ("wake", ONLINE, "Le PC du bureau est déjà en marche."),
        ("wake", None, "Je ne sais pas allumer le PC du bureau à distance."),  # pas d'adresse MAC
    ],
)
def test_impossible_requests_are_explained(action: str | None, status: AgentStatus | None, answer: str) -> None:
    decision = decide_machine(MachineIntent("pc-bureau", action), MACHINES, status)
    assert decision.command is None and decision.answer == answer


def test_wake_needs_the_machine_off_and_a_mac() -> None:
    decision = decide_machine(MachineIntent("pc-bureau", "wake"), with_mac(), None)
    assert decision.command is not None and decision.answer == "J'allume le PC du bureau."


def test_magic_packet() -> None:
    packet = magic_packet("AA-BB-CC-DD-EE-FF")
    assert len(packet) == 102 and packet[:6] == b"\xff" * 6
    assert packet[6:12] == bytes.fromhex("aabbccddeeff") and packet[6:] == packet[6:12] * 16
    with pytest.raises(ValueError, match="adresse MAC invalide"):
        magic_packet("aa:bb:cc")


def test_controller_follows_agent_states_and_publishes_commands() -> None:
    link = FakeLink()
    controller = MachineController(MACHINES, link)
    unknown = controller.interpret("Éteins le PC du bureau")
    assert unknown is not None and unknown.command is None  # agent jamais annoncé

    controller.on_state("agent/pc-bureau/state", ONLINE_PAYLOAD)
    decision = controller.interpret("Éteins le PC du bureau")
    assert decision is not None and decision.command is not None
    assert controller.execute("esp32-01", decision.command, "Denis", "w123") == ["agent/pc-bureau/command"]
    assert link.topics == [("agent/pc-bureau/command",
                            {"action": "shutdown", "id": "w123", "by": "Denis", "board": "esp32-01"})]

    controller.on_state("agent/pc-bureau/state", b'{"status": "offline"}')
    offline = controller.interpret("Éteins le PC du bureau")
    assert offline is not None and offline.command is None


def test_wake_sends_the_magic_packet_not_a_message() -> None:
    sent: list[tuple[bytes, str]] = []
    link = FakeLink()
    controller = MachineController(with_mac(), link, send_udp=lambda packet, address: sent.append((packet, address)))
    command = MachineCommand("wake", "pc-bureau", said="J'allume le PC du bureau.", asked="allumer le PC du bureau")
    controller.execute("esp32-01", command, "Denis", "w1")
    assert sent == [(magic_packet(MAC), "255.255.255.255")] and link.topics == []


def test_machine_is_matched_before_home() -> None:
    """« Éteins le PC du bureau » ne doit pas éteindre la lumière du bureau."""
    controller = MachineController(MACHINES, FakeLink())
    controller.on_state("agent/pc-bureau/state", ONLINE_PAYLOAD)
    assistant = Assistant(clock=lambda: datetime(2026, 9, 30, 12), home=SETTINGS.home, machines=controller)
    assert isinstance(assistant.respond("Éteins le PC du bureau", "Denis", "esp32-01").command, MachineCommand)
    home = assistant.respond("Éteins la lumière du bureau", "Denis", "esp32-01").command
    assert home == DeviceCommand("off", "light", "bureau")


def test_rights_and_confirmation_for_machines() -> None:
    """config.yaml : verrouiller = standard ; éteindre = complet + confirmation."""
    policy = AccessPolicy(SETTINGS.access, SETTINGS.home, MACHINES)
    lock = decide_machine(MachineIntent("pc-bureau", "lock"), MACHINES, ONLINE).command
    shutdown = decide_machine(MachineIntent("pc-bureau", "shutdown"), MACHINES, ONLINE).command
    assert lock is not None and shutdown is not None

    assert policy.check(lock, "Pierre", 0.8).allowed
    refused = policy.check(shutdown, "Pierre", 0.8)
    assert not refused.allowed and refused.answer == ("Désolé Pierre, votre profil ne permet pas "
                                                      "d'éteindre le PC du bureau.")
    verdict = policy.check(shutdown, "Denis", 0.8)
    assert verdict.confirm and verdict.answer == "Confirmez-vous : éteindre le PC du bureau ? Dites oui ou non."
    policy.ask("esp32-01", shutdown, "Denis")
    resolution = policy.resolve("esp32-01", "oui", "Denis")
    assert resolution is not None and resolution.command == shutdown
    assert resolution.answer == "J'éteins le PC du bureau."


def test_router_sends_each_command_to_its_controller() -> None:
    link = FakeLink()
    router = CommandRouter(HomeController(SETTINGS.home, link), MachineController(MACHINES, link))
    machine = MachineCommand("lock", "pc-bureau", said="", asked="")
    assert router.execute("esp32-01", machine, None, "s") == ["agent/pc-bureau/command"]
    assert router.execute("esp32-01", DeviceCommand("on", "light", "cuisine"), None, "s") == \
        ["home/cuisine/light/set"]


def test_mqtt_link_routes_agent_topics() -> None:
    """Les états des agents (agent/+/state) arrivent au bon rappel, pas à ceux des cartes."""
    link = MqttLink(SETTINGS.mqtt)
    received: list[str] = []
    link.on("state", lambda device, payload: received.append(f"carte {device}"))
    link.on_topic("agent/+/state", lambda topic, payload: received.append(topic))
    for topic in ("agent/pc-bureau/state", "voice/esp32-01/state", "agent/pc-bureau/result"):
        message = mqtt.MQTTMessage(topic=topic.encode())
        message.payload = b"{}"
        link._on_message(link._client, None, message)  # pyright: ignore[reportPrivateUsage]
    assert received == ["agent/pc-bureau/state", "carte esp32-01"]
