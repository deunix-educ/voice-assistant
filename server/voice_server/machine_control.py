"""Machines Linux du réseau : de la phrase à l'agent de la machine (étape 15b).

Chaque machine fait tourner un AGENT (agent/voice_agent.py) : un petit client MQTT
qui n'exécute que les actions de SA liste, définie sur la machine elle-même. Le
serveur n'envoie qu'un NOM d'action (« shutdown »), jamais une commande shell :
même un message forgé ne peut rien faire d'autre que ce que la machine a permis.

    agent/<machine>/state    agent → tous    {"status":"online","actions":[...]}, retenu + testament
    agent/<machine>/command  serveur → agent {"action":"shutdown","id":...,"by":"Denis"}
    agent/<machine>/result   agent → serveur {"action":...,"ok":true,"code":0,"output":"..."}

« Allume le PC » est à part : éteinte, la machine n'a plus d'agent. Le serveur
envoie alors un paquet Wake-on-LAN (réveil par le réseau) à son adresse MAC.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from voice_server.home_control import DeviceCommand, TopicPublisher
from voice_server.settings import MachinesSettings
from voice_server.text_normalize import normalize

logger = logging.getLogger(__name__)

WAKE = "wake"          # action du serveur lui-même (Wake-on-LAN), pas de l'agent
WOL_PORT = 9           # port « discard » : l'usage pour le paquet magique
MACHINE_DEVICE = "machine"  # clé des droits : access.rules.machine.<action>


@dataclass(frozen=True)
class MachineIntent:
    """Ce que la phrase demande à une machine ; action None : pas trouvée."""

    machine: str           # identifiant de l'agent (pc-bureau)
    action: str | None     # shutdown, reboot, wake...


@dataclass(frozen=True)
class MachineCommand:
    """Commande complète pour une machine, avec ses phrases (fixées à la décision)."""

    action: str
    machine: str
    said: str                       # à l'exécution : « J'éteins le PC du bureau. »
    asked: str                      # refus, confirmation : « éteindre le PC du bureau »
    device: str = MACHINE_DEVICE    # pour les droits, comme DeviceCommand.device


# Toute commande que l'assistant peut produire : domotique (étape 11) ou machine (15b).
Command = DeviceCommand | MachineCommand


@dataclass(frozen=True)
class MachineDecision:
    """Réponse à dire, et commande à exécuter si la demande est possible."""

    answer: str
    command: MachineCommand | None


@dataclass(frozen=True)
class AgentStatus:
    """Dernier état publié par un agent (retenu par le broker)."""

    online: bool
    actions: tuple[str, ...]   # actions que la machine accepte


def agent_status(payload: bytes) -> AgentStatus | None:
    """{"status":"online","actions":[...]} → AgentStatus ; None si illisible."""
    try:
        state: dict[str, Any] = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(state, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        return None
    actions = state.get("actions") or []
    return AgentStatus(online=state.get("status") == "online",
                       actions=tuple(str(action) for action in actions))


def capitalized(phrase: str) -> str:
    """« le PC du bureau » → « Le PC du bureau » (en début de phrase)."""
    return phrase[:1].upper() + phrase[1:]


def parse_machine_intent(text: str, settings: MachinesSettings) -> MachineIntent | None:
    """Machine et action de la phrase ; None si elle ne nomme aucune machine."""
    words = normalize(text).split()
    padded = f" {' '.join(words)} "
    # Noms les plus longs d'abord : « ordinateur du bureau » avant un éventuel « ordinateur ».
    names = sorted(((name, machine) for machine, spec in settings.machines.items() for name in spec.names),
                   key=lambda item: len(item[0]), reverse=True)
    machine = next((machine for name, machine in names if f" {name} " in padded), None)
    if machine is None:
        return None
    action = next((action for word in words for action, spec in settings.actions.items()
                   if word in spec.words), None)
    return MachineIntent(machine=machine, action=action)


def decide_machine(intent: MachineIntent, settings: MachinesSettings,
                   status: AgentStatus | None) -> MachineDecision:
    """Vérifie que la demande est possible, d'après l'état annoncé par l'agent."""
    machine = settings.machines[intent.machine]
    name = machine.name
    if intent.action is None:
        return MachineDecision(f"Que dois-je faire avec {name} ?", None)
    spec = settings.actions[intent.action]
    online = status is not None and status.online
    if intent.action == WAKE:
        if online:
            return MachineDecision(f"{capitalized(name)} est déjà en marche.", None)
        if not machine.mac:
            return MachineDecision(f"Je ne sais pas {spec.verb} {name} à distance.", None)
    elif not online:
        # Tournures sans genre : le nom peut être « la tour du garage ».
        return MachineDecision(f"{capitalized(name)} ne répond pas : la machine est éteinte, "
                               "ou son agent est arrêté.", None)
    elif status is not None and intent.action not in status.actions:
        return MachineDecision(f"Je ne peux pas {spec.verb} {name} : son agent ne le permet pas.", None)
    command = MachineCommand(action=intent.action, machine=intent.machine,
                             said=f"{spec.does} {name}.", asked=f"{spec.verb} {name}")
    return MachineDecision(command.said, command)


def magic_packet(mac: str) -> bytes:
    """Paquet magique Wake-on-LAN : 6 octets 0xFF, puis 16 fois l'adresse MAC (102 octets).

    Raises:
        ValueError: adresse MAC mal écrite.
    """
    if not re.fullmatch(r"([0-9a-fA-F]{2}[:-]?){5}[0-9a-fA-F]{2}", mac.strip()):
        raise ValueError(f"adresse MAC invalide : « {mac} » (attendu : aa:bb:cc:dd:ee:ff)")
    return b"\xff" * 6 + bytes.fromhex(re.sub(r"[:-]", "", mac.strip())) * 16


def send_broadcast(packet: bytes, address: str) -> None:
    """Envoie un datagramme UDP en diffusion sur le réseau local."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (address, WOL_PORT))


class MachineController:
    """Suit l'état des agents, interprète les phrases qui nomment une machine, publie les commandes.

    on_state et on_result sont appelés depuis le fil réseau ; interpret et execute
    depuis le fil de travail : un verrou protège les états.
    """

    def __init__(self, settings: MachinesSettings, link: TopicPublisher,
                 send_udp: Callable[[bytes, str], None] = send_broadcast) -> None:
        """
        Args:
            send_udp: envoi du paquet Wake-on-LAN, remplaçable dans les tests.
        """
        self._settings = settings
        self._link = link
        self._send_udp = send_udp
        self._lock = threading.Lock()
        self._states: dict[str, AgentStatus] = {}

    # ------------------------------------------------------------ messages des agents

    def on_state(self, topic: str, payload: bytes) -> None:
        """État d'un agent (agent/<machine>/state), retenu : reçu dès l'abonnement."""
        machine = self._machine_of(topic)
        status = agent_status(payload)
        if machine is None or status is None:
            return
        with self._lock:
            previous = self._states.get(machine)
            self._states[machine] = status
        if previous == status:
            return
        if machine not in self._settings.machines:
            logger.warning("agent %s : absent de config.yaml (machines.list), on ne peut pas le nommer", machine)
        elif status.online:
            unknown = [action for action in status.actions if action not in self._settings.actions]
            logger.info("agent %s : en ligne, actions %s%s", machine, ", ".join(status.actions) or "aucune",
                        f" (sans mots dans config.yaml : {', '.join(unknown)})" if unknown else "")
        else:
            logger.warning("agent %s : hors ligne", machine)

    def on_result(self, topic: str, payload: bytes) -> None:
        """Compte rendu d'exécution (agent/<machine>/result) : pour le journal."""
        machine = self._machine_of(topic)
        try:
            result: dict[str, Any] = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        log = logger.info if result.get("ok") else logger.warning
        log("agent %s : %s %s (code %s)%s", machine, result.get("action"),
            "executee" if result.get("ok") else "ECHOUEE", result.get("code"),
            f" : {result['output']}" if result.get("output") else "")

    def status(self, machine: str) -> AgentStatus | None:
        """Dernier état connu de l'agent ; None s'il ne s'est jamais annoncé."""
        with self._lock:
            return self._states.get(machine)

    # ------------------------------------------------------------ phrases et commandes

    def interpret(self, text: str) -> MachineDecision | None:
        """Réponse à une phrase qui nomme une machine ; None si elle n'en nomme aucune."""
        intent = parse_machine_intent(text, self._settings)
        if intent is None:
            return None
        return decide_machine(intent, self._settings, self.status(intent.machine))

    def execute(self, board: str, command: MachineCommand, speaker: str | None, session: str) -> list[str]:
        """Réveil par le réseau, ou commande à l'agent ; rend les destinations (journal, tests)."""
        if command.action == WAKE:
            mac = self._settings.machines[command.machine].mac
            self._send_udp(magic_packet(mac), self._settings.broadcast)
            logger.info("%s : Wake-on-LAN %s (%s) -> %s:%d", board, command.machine, mac,
                        self._settings.broadcast, WOL_PORT)
            return [f"wol:{mac}"]
        # QoS 1, non retenu, et l'agent se connecte en session propre : une commande
        # n'est jamais rejouée plus tard (un « éteins » qui attendrait le prochain démarrage...).
        topic = f"{self._settings.topic_prefix}/{command.machine}/command"
        payload = {"action": command.action, "id": session, "by": speaker, "board": board}
        self._link.publish_topic(topic, json.dumps(payload, ensure_ascii=False).encode("utf-8"), 1)
        logger.info("%s : commande %s %s -> %s", board, command.action, command.machine, topic)
        return [topic]

    def _machine_of(self, topic: str) -> str | None:
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != self._settings.topic_prefix or not parts[1]:
            return None
        return parts[1]
