"""Droits par profil : qui peut demander quoi (étape 14).

Trois niveaux, du plus au moins permissif :

    complet   tout, y compris les actions sensibles, qui demandent une confirmation ;
    standard  la domotique courante (lumières, volets, fermer la porte), réveiller
              ou verrouiller une machine (étape 15b) ;
    limite    la conversation seulement (heure, date…).

L'identification vocale est une ESTIMATION : une imitation ou un enregistrement
peut tromper. Ces droits évitent les erreurs et les commandes d'un invité, pas
un attaquant déterminé. D'où deux garde-fous : le niveau complet exige une voix
reconnue nettement, et les actions sensibles une confirmation par la même personne.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

from voice_server.home_control import ALLOWED, announce, request
from voice_server.machine_control import MACHINE_DEVICE, Command, MachineCommand
from voice_server.settings import AccessSettings, HomeSettings, MachinesSettings
from voice_server.text_normalize import normalize

logger = logging.getLogger(__name__)

YES = ("oui", "confirme", "confirmer", "ok", "okay", "valide")
NO = ("non", "annule", "annuler", "stop", "laisse")


class Level(IntEnum):
    """Niveau de droits ; l'ordre permet de comparer (complet > standard > limite)."""

    LIMITED = 1
    STANDARD = 2
    FULL = 3


LEVELS = {"limite": Level.LIMITED, "standard": Level.STANDARD, "complet": Level.FULL}
LEVEL_NAMES = {level: name for name, level in LEVELS.items()}


def of(phrase: str) -> str:
    """« de » élidé devant une voyelle : « d'ouvrir », « d'éteindre », mais « de fermer »."""
    return f"d'{phrase}" if normalize(phrase)[:1] in "aeiouyh" else f"de {phrase}"


def parse_level(name: str) -> Level:
    """« complet » → Level.FULL ; erreur claire si le nom est inconnu."""
    level = LEVELS.get(normalize(name))
    if level is None:
        raise ValueError(f"niveau de droits inconnu : « {name} » (complet, standard ou limite)")
    return level


@dataclass(frozen=True)
class Verdict:
    """Décision pour une commande."""

    allowed: bool
    confirm: bool           # exécuter seulement après un « oui »
    answer: str | None      # refus ou question de confirmation ; None : on exécute


@dataclass(frozen=True)
class Resolution:
    """Réponse à une confirmation en attente."""

    answer: str
    command: Command | None   # à exécuter (oui), None (non, autre personne)


@dataclass(frozen=True)
class _Pending:
    command: Command
    speaker: str
    expires: float


class AccessPolicy:
    """Applique les droits de config.yaml et garde les confirmations en attente, par carte."""

    def __init__(self, access: AccessSettings, home: HomeSettings, machines: MachinesSettings | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        """
        Args:
            machines: actions des machines (étape 15b), pour valider access.rules.machine.

        Raises:
            ValueError: niveau ou action inconnus dans config.yaml.
        """
        self._access = access
        self._home = home
        self._clock = clock
        self._unknown = parse_level(access.unknown_level)
        self._default = parse_level(access.default_level)
        self._users = {name: parse_level(level) for name, level in access.users.items()}
        self._rules = {device: {action: parse_level(level) for action, level in actions.items()}
                       for device, actions in access.rules.items()}
        allowed = {**ALLOWED, MACHINE_DEVICE: tuple(machines.actions) if machines is not None else ()}
        for device, actions in self._rules.items():
            # Une règle mal écrite serait ignorée en silence (action exigeant alors « complet »).
            unknown = [f"{device}.{action}" for action in actions if action not in allowed.get(device, ())]
            if unknown:
                raise ValueError(f"config.yaml, access.rules : action inconnue {', '.join(unknown)} "
                                 f"(connues : {', '.join(f'{d}.{a}' for d, acts in allowed.items() for a in acts)})")
        self._pending: dict[str, _Pending] = {}

    def level(self, speaker: str | None, score: float | None) -> Level:
        """Niveau de la personne ; le niveau complet exige une identification nette."""
        if speaker is None:
            return self._unknown
        level = self._users.get(speaker, self._default)
        if level is Level.FULL and (score is None or score < self._access.full_min_score):
            return Level.STANDARD  # voix reconnue de justesse : pas assez sûr pour tout permettre
        return level

    def check(self, command: Command, speaker: str | None, score: float | None) -> Verdict:
        """La personne peut-elle faire cette commande, et faut-il confirmer ?"""
        # Action absente des règles : réservée au niveau complet (on ne permet pas par oubli).
        required = self._rules.get(command.device, {}).get(command.action, Level.FULL)
        level = self._level_logged(speaker, score, command, required)
        what = self._request(command)
        if level < required:
            if speaker is None:
                return Verdict(False, False, f"Je ne vous ai pas reconnu : je ne peux pas {what}.")
            if self._users.get(speaker, self._default) >= required:
                # Le profil le permet, mais la voix a été reconnue de justesse (full_min_score).
                return Verdict(False, False, f"{speaker}, je ne reconnais pas assez nettement votre voix "
                                             f"pour {what}. Répétez, plus près du micro.")
            return Verdict(False, False, f"Désolé {speaker}, votre profil ne permet pas {of(what)}.")
        if f"{command.device}.{command.action}" in self._access.confirm:
            return Verdict(True, True, f"Confirmez-vous : {what} ? Dites oui ou non.")
        return Verdict(True, False, None)

    def ask(self, board: str, command: Command, speaker: str | None) -> None:
        """Retient une commande en attente de « oui » de la même personne."""
        if speaker is not None:
            self._pending[board] = _Pending(command, speaker, self._clock() + self._access.confirm_timeout_s)

    def resolve(self, board: str, text: str, speaker: str | None) -> Resolution | None:
        """Réponse à une confirmation en attente ; None : pas de confirmation, requête ordinaire."""
        pending = self._pending.pop(board, None)
        if pending is None or self._clock() > pending.expires:
            return None
        words = normalize(text).split()
        said_yes = any(word in YES for word in words) or "d accord" in " ".join(words)
        said_no = any(word in NO for word in words)
        if not (said_yes or said_no):
            return None  # autre chose : la demande en attente est abandonnée, on traite la nouvelle
        if speaker != pending.speaker:
            logger.warning("%s : confirmation de %s refusee, donnee par %s", board, pending.speaker,
                           speaker or "une voix inconnue")
            return Resolution(f"Seul {pending.speaker} peut confirmer. J'annule.", None)
        if said_no:
            return Resolution("D'accord, j'annule.", None)
        return Resolution(self._announce(pending.command), pending.command)

    def _request(self, command: Command) -> str:
        """« ouvrir la porte du salon », « éteindre le PC du bureau »."""
        return command.asked if isinstance(command, MachineCommand) else request(command, self._home)

    def _announce(self, command: Command) -> str:
        """« J'ouvre la porte du salon. », « J'éteins le PC du bureau. »"""
        return command.said if isinstance(command, MachineCommand) else announce(command, self._home)

    def _level_logged(self, speaker: str | None, score: float | None, command: Command,
                      required: Level) -> Level:
        level = self.level(speaker, score)
        verdict = "autorise" if level >= required else "REFUSE"
        target = command.machine if isinstance(command, MachineCommand) else command.room
        logger.info("droits : %s (niveau %s, score %s) %s %s %s : %s (requis %s)",
                    speaker or "inconnu", LEVEL_NAMES[level], "?" if score is None else f"{score:.2f}",
                    command.action, command.device, target, verdict, LEVEL_NAMES[required])
        return level
