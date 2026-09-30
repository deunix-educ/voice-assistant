"""Domotique : de la phrase à la commande MQTT (étape 11).

Une commande tient en trois mots : une ACTION (allumer, éteindre, ouvrir,
fermer), un APPAREIL (lumière, volets, porte) et une PIÈCE (salon, cuisine…).
L'analyse cherche ces mots dans la phrase transcrite, sans tenir compte des
accents ni de la conjugaison exacte : Whisper écrit « fermé » pour « fermez ».

Sans pièce, c'est celle de la carte qui a entendu la phrase (« ici »).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from voice_server.settings import HomeSettings
from voice_server.text_normalize import normalize

logger = logging.getLogger(__name__)

# Mots normalisés (minuscules, sans accents) → identifiant. Identifiants en anglais :
# ce sont eux qui voyagent dans les topics et le JSON, comme dans le firmware.
ACTION_WORDS = {
    "on": ("allume", "allumer", "allumez", "allumes", "rallume", "rallumer"),
    "off": ("eteins", "eteint", "eteindre", "eteignez", "coupe", "couper", "coupez"),
    "open": ("ouvre", "ouvrir", "ouvrez", "ouvres", "monte", "monter", "montez", "leve", "lever", "levez",
             "remonte", "remonter"),
    "close": ("ferme", "fermer", "fermez", "fermes", "baisse", "baisser", "baissez", "descends",
              "descendre", "descendez"),
}
DEVICE_WORDS = {
    "light": ("lumiere", "lumieres", "lampe", "lampes", "eclairage", "plafonnier"),
    "shutter": ("volet", "volets", "store", "stores"),
    "door": ("porte", "portes"),
}
ALLOWED = {"light": ("on", "off"), "shutter": ("open", "close"), "door": ("open", "close")}

DEVICE_PHRASE = {"light": "la lumière", "shutter": "les volets", "door": "la porte"}
FIRST_PERSON = {"on": "J'allume", "off": "J'éteins", "open": "J'ouvre", "close": "Je ferme"}
INFINITIVE = {"on": "allumer", "off": "éteindre", "open": "ouvrir", "close": "fermer"}


@dataclass(frozen=True)
class DeviceIntent:
    """Ce que la phrase demande ; un champ à None n'a pas été trouvé."""

    action: str | None     # on | off | open | close
    device: str | None     # light | shutter | door
    room: str | None       # pièce de config.yaml, telle qu'écrite (« salle de bain »)


@dataclass(frozen=True)
class DeviceCommand:
    """Commande complète et valide, prête à publier."""

    action: str
    device: str
    room: str


def slug(room: str) -> str:
    """Pièce utilisable dans un topic : « salle de bain » → « salle-de-bain », « entrée » → « entree »."""
    ascii_room = unicodedata.normalize("NFKD", room).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_room.lower()).strip("-")


def _first(words: list[str], vocabulary: dict[str, tuple[str, ...]]) -> str | None:
    """Premier identifiant dont un mot apparaît dans la phrase (dans l'ordre de la phrase)."""
    for word in words:
        for key, forms in vocabulary.items():
            if word in forms:
                return key
    return None


def parse_intent(text: str, rooms: list[str]) -> DeviceIntent | None:
    """Action, appareil et pièce de la phrase ; None si elle ne parle pas de domotique."""
    words = normalize(text).split()
    action = _first(words, ACTION_WORDS)
    device = _first(words, DEVICE_WORDS)
    if action is None and device is None:
        return None
    padded = f" {' '.join(words)} "
    # Pièces les plus longues d'abord : « salle de bain » avant un éventuel « bain ».
    room = next((name for name in sorted(rooms, key=len, reverse=True)
                 if f" {normalize(name)} " in padded), None)
    return DeviceIntent(action=action, device=device, room=room)


@dataclass(frozen=True)
class Decision:
    """Réponse à dire, et commande à exécuter si la demande est complète."""

    answer: str
    command: DeviceCommand | None


def announce(command: DeviceCommand, home: HomeSettings) -> str:
    """Ce que dit l'assistant en exécutant : « Je ferme les volets du salon. »"""
    return f"{FIRST_PERSON[command.action]} {DEVICE_PHRASE[command.device]} {home.rooms[command.room]}."


def request(command: DeviceCommand, home: HomeSettings) -> str:
    """La demande à l'infinitif : « ouvrir la porte du salon »."""
    return f"{INFINITIVE[command.action]} {DEVICE_PHRASE[command.device]} {home.rooms[command.room]}"


def decide(intent: DeviceIntent, home: HomeSettings, board: str) -> Decision:
    """Complète l'intention (appareil implicite, pièce de la carte) ou dit ce qui manque."""
    action, device = intent.action, intent.device
    if device is None and action in ("on", "off"):
        device = "light"  # « allume », « éteins » : c'est de la lumière qu'on parle
    if action is None:
        return Decision(f"Que dois-je faire avec {DEVICE_PHRASE[device or 'light']} ?", None)
    if device is None:
        return Decision(f"Que dois-je {INFINITIVE[action]} ?", None)
    if action not in ALLOWED[device]:
        return Decision(f"Je ne sais pas {INFINITIVE[action]} {DEVICE_PHRASE[device]}.", None)
    room = intent.room or home.boards.get(board)
    if room is None or room not in home.rooms:
        return Decision("Dans quelle pièce ?", None)
    command = DeviceCommand(action=action, device=device, room=room)
    return Decision(announce(command, home), command)


class TopicPublisher(Protocol):
    """Ce que la domotique attend du lien MQTT."""

    def publish(self, device: str, suffix: str, payload: bytes, qos: int) -> None: ...

    def publish_topic(self, topic: str, payload: bytes, qos: int) -> None: ...


class HomeController:
    """Publie les commandes : pour la domotique, et pour la carte si c'est sa pièce."""

    def __init__(self, home: HomeSettings, link: TopicPublisher) -> None:
        self._home = home
        self._link = link

    def execute(self, board: str, command: DeviceCommand, speaker: str | None, session: str) -> list[str]:
        """Publie la commande ; rend les topics utilisés (journal, tests).

        home/<pièce>/<appareil>/set, QoS 1, non retenu : une commande rejouée à un
        abonné qui arrive plus tard ferait bouger un volet sans que personne l'ait demandé.
        """
        topic = f"{self._home.topic_prefix}/{slug(command.room)}/{command.device}/set"
        payload = {"action": command.action, "by": speaker, "board": board, "session": session}
        self._link.publish_topic(topic, json.dumps(payload, ensure_ascii=False).encode("utf-8"), 1)
        topics = [topic]
        if self._home.boards.get(board) == command.room:
            local = {"cmd": "device", "device": command.device, "action": command.action}
            self._link.publish(board, "control", json.dumps(local).encode(), 1)
            topics.append(f"{board}/control")
        logger.info("%s : commande %s %s %s -> %s", board, command.action, command.device,
                    command.room, ", ".join(topics))
        return topics
