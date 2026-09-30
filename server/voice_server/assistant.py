"""Choix de la réponse à une phrase transcrite (étapes 6, 10, 11 et 15b).

D'abord les machines (étape 15b) : une phrase qui nomme une machine (« le PC du
bureau ») la concerne, même si elle contient « éteins ». Puis la domotique
(étape 11) : une action, un appareil, une pièce donnent une commande. Sinon,
quelques règles de conversation dont on connaît d'avance la réponse (heure,
date, salutations) ; à défaut, la phrase est répétée.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from voice_server.home_control import decide, parse_intent
from voice_server.machine_control import Command, MachineDecision
from voice_server.settings import HomeSettings
from voice_server.text_normalize import normalize

DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
          "août", "septembre", "octobre", "novembre", "décembre")

NOTHING_HEARD = "Je n'ai rien entendu."


@dataclass(frozen=True)
class Reply:
    """Ce que l'assistant dit, et la commande domotique à exécuter s'il y en a une."""

    text: str
    command: Command | None = None


class MachineInterpreter(Protocol):
    """Ce que l'assistant attend des machines (MachineController)."""

    def interpret(self, text: str) -> MachineDecision | None: ...


def say_time(now: datetime) -> str:
    """L'heure écrite comme on la dit : Piper lit mal « 14:05 »."""
    if now.hour == 0:
        hours = "minuit"
    elif now.hour == 12:
        hours = "midi"
    elif now.hour == 1:
        hours = "une heure"  # écrit « 1 heure », Piper dit « un heure »
    else:
        hours = f"{now.hour} heures"
    return f"Il est {hours} {now.minute}." if now.minute else f"Il est {hours}."


def say_date(now: datetime) -> str:
    """La date en toutes lettres, « premier » pour le 1er du mois."""
    day = "premier" if now.day == 1 else str(now.day)
    return f"Nous sommes le {DAYS[now.weekday()]} {day} {MONTHS[now.month - 1]} {now.year}."


class Assistant:
    """Associe une réponse à une phrase, par mots-clés.

    Les règles sont essayées dans l'ordre : la plus précise d'abord, pour que
    « bonjour, quelle heure est-il ? » donne l'heure et non une salutation.
    """

    def __init__(self, clock: Callable[[], datetime] = datetime.now, home: HomeSettings | None = None,
                 machines: MachineInterpreter | None = None) -> None:
        """
        Args:
            clock: source de l'heure, injectable pour les tests.
            home: pièces et cartes (étape 11) ; None : pas de domotique.
            machines: machines du réseau (étape 15b) ; None : pas de machines.
        """
        self._clock = clock
        self._home = home
        self._machines = machines
        # (mots-clés normalisés, réponse) : une seule occurrence suffit.
        self._rules: list[tuple[tuple[str, ...], Callable[[str | None], str]]] = [
            # Whisper écrit parfois « Quel heure » : on accepte les deux accords.
            (("quelle heure", "quel heure", "l heure", "heure est il"), lambda who: say_time(self._clock())),
            (("quel jour", "la date", "quelle date"), lambda who: say_date(self._clock())),
            (("comment tu t appelles", "qui es tu", "ton nom"),
             lambda who: "Je suis votre assistant vocal, et je fonctionne sans Internet."),
            # Étape 10 : l'assistant sait qui parle, il l'appelle par son nom.
            (("bonjour", "bonsoir", "salut"),
             lambda who: f"Bonjour {who} ! Que puis-je faire pour vous ?" if who
             else "Bonjour ! Que puis-je faire pour vous ?"),
            (("merci",), lambda who: f"Avec plaisir, {who}." if who else "Avec plaisir."),
            # Étape 11 : sans Internet, pas de météo ; mieux vaut le dire que répéter la question.
            (("meteo", "quel temps"), lambda who: "Je fonctionne sans Internet : je n'ai pas accès à la météo."),
        ]

    def respond(self, text: str, speaker: str | None = None, board: str = "") -> Reply:
        """Machines, puis domotique, puis conversation.

        Args:
            text: phrase transcrite.
            speaker: nom de la personne reconnue, None si inconnue.
            board: carte qui a entendu la phrase : sa pièce est la pièce par défaut.
        """
        if self._machines is not None:
            decision = self._machines.interpret(text)
            if decision is not None:
                return Reply(decision.answer, decision.command)
        if self._home is not None:
            intent = parse_intent(text, list(self._home.rooms))
            if intent is not None:
                decision = decide(intent, self._home, board)
                return Reply(decision.answer, decision.command)
        return Reply(self.reply(text, speaker))

    def reply(self, text: str, speaker: str | None = None) -> str:
        """Rend la réponse à dire ; à défaut de règle, répète ce qui a été compris.

        Args:
            text: phrase transcrite.
            speaker: nom de la personne reconnue, None si inconnue.
        """
        words = normalize(text)
        if not words:
            return NOTHING_HEARD
        padded = f" {words} "  # espaces : « salut » ne doit pas reconnaître « salutations »
        for keywords, answer in self._rules:
            if any(f" {keyword} " in padded for keyword in keywords):
                return answer(speaker)
        return f"Vous avez dit : {text.strip()}"
