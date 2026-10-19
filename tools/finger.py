#!/usr/bin/env python3
"""Gère les empreintes du capteur R503 d'une carte, par MQTT (branche fingerprint, étape F2).

Le serveur vocal n'a pas besoin de tourner. La carte reçoit la commande sur
voice/<carte>/control, déroule l'enregistrement avec le capteur (l'anneau lumineux
guide le doigt) et répond par des événements sur voice/<carte>/event.

Un doigt est enregistré DEUX fois (même doigt, deux poses). Le capteur garde l'empreinte
sous un numéro ; la configuration (section fingerprint.users, dans server/conf/access.yaml) dit à qui il appartient.

Exemples :
    python3 tools/finger.py list
    python3 tools/finger.py enroll --name Denis          # premier emplacement libre
    python3 tools/finger.py enroll --id 3 --name Denis   # un deuxième doigt de Denis
    python3 tools/finger.py delete --id 3
    python3 tools/finger.py --host 192.168.1.4 list      # broker autre que celui de config.yaml
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import queue
import sys
from pathlib import Path
from typing import Any, Protocol

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from voice_server.fingerprint_book import FingerprintBook  # noqa: E402
from voice_server.mqtt_link import MqttLink  # noqa: E402
from voice_server.settings import load_settings  # noqa: E402

logger = logging.getLogger("finger")

LIST_TIMEOUT_S = 6.0       # le capteur répond en quelques ms ; le reste est le réseau
DELETE_TIMEOUT_S = 6.0
ENROLL_TIMEOUT_S = 120.0   # deux poses, plus les essais ratés

REASONS = {
    "timeout": "temps ecoule : aucun doigt pose a temps",
    "messy": "image trop brouillee : doigt propre et sec, pose franche, sans bouger",
    "few_features": "pas assez de details : posez le centre du doigt, bien a plat",
    "already_enrolled": "ce doigt est deja enregistre",
    "mismatch": "les deux poses ne sont pas le meme doigt : recommencez avec le meme doigt",
    "storage": "ecriture impossible dans la memoire du capteur",
    "bad_id": "numero d'emplacement hors du capteur",
    "busy": "un enregistrement est deja en cours sur la carte",
    "sensor": "le capteur ne repond pas correctement (cablage, alimentation 3,3 V)",
    "cancelled": "annule",
}


class Link(Protocol):
    """Ce que l'outil attend du lien MQTT : envoyer une commande, recevoir les événements de la carte."""

    def send(self, command: dict[str, Any]) -> None: ...

    def next_event(self, timeout_s: float) -> dict[str, Any] | None: ...


def enrollment_message(event: dict[str, Any], who: str) -> tuple[str, bool | None]:
    """Phrase à afficher pour un événement fp_enroll ; True/False quand l'enregistrement est fini."""
    step = event.get("step")
    reason = str(event.get("reason", ""))
    text = REASONS.get(reason, reason)
    if step == "start":
        return f"Posez le doigt de {who} sur le capteur (anneau bleu qui respire).", None
    if step == "retry":
        return f"Capture inutilisable : {text}. Retirez le doigt, puis reposez-le.", None
    if step == "remove_finger":
        return "Premiere pose reussie. Retirez le doigt.", None
    if step == "place_again":
        return "Reposez le MEME doigt.", None
    if step == "done":
        return f"Empreinte {event.get('id')} enregistree.", True
    if step == "failed":
        extra = f" (c'est l'empreinte {event.get('id')})" if reason == "already_enrolled" else ""
        return f"ECHEC : {text}{extra}.", False
    return f"evenement inattendu : {event}", None


class BoardLink:
    """Lien MQTT vers une carte : commandes sur control, événements filtrés sur sa carte."""

    def __init__(self, link: MqttLink, board: str) -> None:
        self._link = link
        self._board = board
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        link.on("event", self._on_event)

    def _on_event(self, device: str, payload: bytes) -> None:
        if device != self._board:
            return
        try:
            event = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if isinstance(event, dict) and str(event.get("event", "")).startswith("fp_"):
            self._events.put(event)  # type: ignore[arg-type]

    def send(self, command: dict[str, Any]) -> None:
        self._link.publish(self._board, "control", json.dumps(command).encode(), 1)

    def next_event(self, timeout_s: float) -> dict[str, Any] | None:
        try:
            return self._events.get(timeout=timeout_s)
        except queue.Empty:
            return None


def ask_list(link: Link) -> dict[str, Any] | None:
    """Demande la liste des empreintes ; None si la carte ne répond pas."""
    link.send({"cmd": "fp_list"})
    while True:
        event = link.next_event(LIST_TIMEOUT_S)
        if event is None or event.get("event") == "fp_list":
            return event


def command_list(link: Link, book: FingerprintBook) -> int:
    """Affiche les empreintes du capteur et leur propriétaire."""
    listing = ask_list(link)
    if listing is None:
        logger.error("la carte ne repond pas : en ligne sur ce broker ? firmware de la branche fingerprint ?")
        return 1
    if not listing.get("ok"):
        logger.error("le capteur n'a pas pu lister ses empreintes (cablage, alimentation)")
        return 1
    ids = [int(i) for i in listing.get("ids", [])]
    logger.info("%d empreinte(s) sur %s emplacements", len(ids), listing.get("capacity"))
    for finger_id in ids:
        logger.info("  %s", book.describe(finger_id))
    missing = [i for i in book.named_ids() if i not in ids]
    for finger_id in missing:
        logger.warning("  config.yaml nomme l'empreinte %d (%s), mais le capteur ne l'a pas", finger_id,
                       book.name_of(finger_id))
    if len(ids) != listing.get("count"):
        logger.warning("le capteur annonce %s empreintes mais en liste %d : lecture de la table d'index "
                       "a verifier", listing.get("count"), len(ids))
    return 0


def command_enroll(link: Link, book: FingerprintBook, name: str | None, finger_id: int | None) -> int:
    """Enregistre un doigt : choisit l'emplacement, guide les deux poses, dit quoi écrire dans config.yaml."""
    listing = ask_list(link)
    if listing is None or not listing.get("ok"):
        logger.error("la carte ou le capteur ne repond pas : voir 'finger.py list'")
        return 1
    used = [int(i) for i in listing.get("ids", [])]
    capacity = int(listing.get("capacity", 200))
    if finger_id is None and name:
        # Un emplacement déjà attribué à cette personne dans config.yaml, mais vide dans le capteur
        # (doigt supprimé), est réutilisé : la ligne de config.yaml reste juste.
        vacant = [candidate for candidate in book.ids_of(name) if candidate not in used]
        finger_id = vacant[0] if vacant else None
    if finger_id is None:
        try:
            finger_id = book.free_id(used, capacity)
        except ValueError as error:
            logger.error("%s", error)
            return 1
    elif finger_id in used:
        logger.error("l'emplacement %d est deja occupe par %s : 'finger.py delete --id %d' d'abord",
                     finger_id, book.describe(finger_id), finger_id)
        return 1
    who = name or book.name_of(finger_id) or f"l'empreinte {finger_id}"

    link.send({"cmd": "fp_enroll", "id": finger_id})
    try:
        while True:
            event = link.next_event(ENROLL_TIMEOUT_S)
            if event is None:
                logger.error("plus de nouvelles de la carte")
                link.send({"cmd": "fp_cancel"})
                return 1
            if event.get("event") != "fp_enroll":
                continue
            message, outcome = enrollment_message(event, who)
            (logger.info if outcome is not False else logger.error)("%s", message)
            if outcome is None:
                continue
            if outcome and book.name_of(finger_id) != name:
                logger.info("A ajouter dans server/conf/access.yaml :\n  fingerprint:\n    users:\n      %d: %s",
                            finger_id, name or "<nom>")
            return 0 if outcome else 1
    except KeyboardInterrupt:
        link.send({"cmd": "fp_cancel"})
        logger.info("annule")
        return 1


def command_delete(link: Link, book: FingerprintBook, finger_id: int | None, everything: bool) -> int:
    """Supprime une empreinte, ou toutes."""
    link.send({"cmd": "fp_delete", **({"all": True} if everything else {"id": finger_id})})
    while True:
        event = link.next_event(DELETE_TIMEOUT_S)
        if event is None:
            logger.error("la carte ne repond pas")
            return 1
        if event.get("event") == "fp_delete":
            break
    if not event.get("ok"):
        logger.error("le capteur a refuse la suppression")
        return 1
    if everything:
        logger.info("toutes les empreintes sont supprimees. Videz fingerprint.users dans server/conf/access.yaml.")
    else:
        number = int(finger_id or 0)
        logger.info("%s supprimee.%s", book.describe(number),
                    " Retirez sa ligne de fingerprint.users dans server/conf/access.yaml." if book.name_of(number) else "")
    return 0


def main(argv: list[str]) -> int:
    """Point d'entrée de la ligne de commande."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=SERVER_DIR / "config.yaml")
    parser.add_argument("--host", help="adresse du broker, si elle differe de mqtt.host de config.yaml")
    parser.add_argument("--board", default="esp32-01", help="carte a commander")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="liste les empreintes du capteur")
    enroll = commands.add_parser("enroll", help="enregistre un doigt")
    enroll.add_argument("--name", help="proprietaire du doigt")
    enroll.add_argument("--id", type=int, help="emplacement ; par defaut le premier libre")
    delete = commands.add_parser("delete", help="supprime une empreinte, ou toutes")
    group = delete.add_mutually_exclusive_group(required=True)
    group.add_argument("--id", type=int)
    group.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    settings = load_settings(args.config)
    book = FingerprintBook(settings.fingerprint)
    mqtt_settings = dataclasses.replace(settings.mqtt, client_id="voice-finger-tool",
                                        host=args.host or settings.mqtt.host)
    mqtt_link = MqttLink(mqtt_settings)
    board = BoardLink(mqtt_link, args.board)
    mqtt_link.start()
    try:
        if not mqtt_link.wait_connected(5.0):
            logger.error("broker %s:%d injoignable", mqtt_settings.host, mqtt_settings.port)
            return 1
        if args.command == "list":
            return command_list(board, book)
        if args.command == "enroll":
            return command_enroll(board, book, args.name, args.id)
        return command_delete(board, book, args.id, args.all)
    finally:
        mqtt_link.stop()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
