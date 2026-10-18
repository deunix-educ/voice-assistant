"""Droits par personne : qui peut demander quoi (étape 14, par doigt depuis F4).

Trois niveaux, du plus au moins permissif :

    complet   tout, y compris les actions sensibles, qui demandent une confirmation ;
    standard  la domotique courante (lumières, volets, fermer la porte), réveiller
              ou verrouiller une machine (étape 15b) ;
    limite    la conversation seulement (heure, date…).

La personne est celle dont le doigt a ouvert la session (fingerprint.users de config.yaml) :
le capteur d'empreinte reconnaît le doigt, pas la voix, et la voix n'identifie plus personne.
Sans doigt reconnu et nommé (bouton PTT, empreinte sans nom), la personne est inconnue : niveau
access.unknown. Les actions sensibles demandent en plus une confirmation par le MÊME DOIGT que la
demande : un autre doigt de la même personne ne suffit pas. Les noms se comparent sans tenir compte
de la casse ni des accents.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
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


def same_person(first: str | None, second: str | None) -> bool:
    """« Denis » et « denis » sont la même personne ; None n'est personne."""
    return first is not None and second is not None and normalize(first) == normalize(second)


@dataclass(frozen=True)
class _Pending:
    command: Command
    person: str
    finger: int       # emplacement du doigt qui a fait la demande : lui seul peut confirmer
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
        self._users = {normalize(name): parse_level(level) for name, level in access.users.items()}
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

    def level(self, person: str | None) -> Level:
        """Niveau de la personne ; inconnue : access.unknown ; connue mais absente de users : access.default."""
        if person is None:
            return self._unknown
        return self._users.get(normalize(person), self._default)

    def audit(self, people: Iterable[str]) -> list[str]:
        """Incohérences entre les personnes aux doigts nommés et access.users, à journaliser au démarrage.

        Un nom mal écrit ne planterait rien : la personne recevrait en silence le niveau par défaut.
        """
        named = {normalize(name): name for name in people}
        warnings = [f"{name} a un doigt mais pas de niveau dans access.users : niveau par défaut "
                    f"({LEVEL_NAMES[self._default]})"
                    for key, name in sorted(named.items()) if key not in self._users]
        warnings += [f"access.users nomme {name}, qui n'a aucun doigt dans fingerprint.users : "
                     f"elle ne pourra jamais être reconnue"
                     for name in self._access.users if normalize(name) not in named]
        return warnings

    def check(self, command: Command, person: str | None) -> Verdict:
        """La personne peut-elle faire cette commande, et faut-il confirmer ?"""
        # Action absente des règles : réservée au niveau complet (on ne permet pas par oubli).
        required = self._rules.get(command.device, {}).get(command.action, Level.FULL)
        level = self._level_logged(person, command, required)
        what = self._request(command)
        if level < required:
            if person is None:
                return Verdict(False, False, f"Je ne sais pas qui tient la carte : je ne peux pas {what}.")
            return Verdict(False, False, f"Désolé {person}, votre profil ne permet pas {of(what)}.")
        if f"{command.device}.{command.action}" in self._access.confirm:
            return Verdict(True, True, f"Confirmez-vous : {what} ? Dites oui ou non.")
        return Verdict(True, False, None)

    def ask(self, board: str, command: Command, person: str | None, finger: int | None) -> None:
        """Retient une commande en attente du « oui » du même doigt."""
        if person is not None and finger is not None:
            self._pending[board] = _Pending(command, person, finger,
                                            self._clock() + self._access.confirm_timeout_s)

    def resolve(self, board: str, text: str, person: str | None, finger: int | None) -> Resolution | None:
        """Réponse à une confirmation en attente ; None : pas de confirmation, requête ordinaire.

        Args:
            person: personne dont le doigt a ouvert cette session, None si inconnue.
            finger: emplacement de ce doigt, None pour le bouton PTT.
        """
        pending = self._pending.pop(board, None)
        if pending is None or self._clock() > pending.expires:
            return None
        words = normalize(text).split()
        said_yes = any(word in YES for word in words) or "d accord" in " ".join(words)
        said_no = any(word in NO for word in words)
        if not (said_yes or said_no):
            return None  # autre chose : la demande en attente est abandonnée, on traite la nouvelle
        if finger != pending.finger:
            logger.warning("%s : confirmation de %s (empreinte %d) refusee, donnee par %s", board,
                           pending.person, pending.finger,
                           "le bouton" if finger is None else f"l'empreinte {finger} ({person or 'sans nom'})")
            if same_person(person, pending.person):
                return Resolution("Confirmez avec le même doigt que pour la demande. J'annule.", None)
            return Resolution(f"Seul {pending.person} peut confirmer. J'annule.", None)
        if said_no:
            return Resolution("D'accord, j'annule.", None)
        return Resolution(self._announce(pending.command), pending.command)

    def _request(self, command: Command) -> str:
        """« ouvrir la porte du salon », « éteindre le PC du bureau »."""
        return command.asked if isinstance(command, MachineCommand) else request(command, self._home)

    def _announce(self, command: Command) -> str:
        """« J'ouvre la porte du salon. », « J'éteins le PC du bureau. »"""
        return command.said if isinstance(command, MachineCommand) else announce(command, self._home)

    def _level_logged(self, person: str | None, command: Command, required: Level) -> Level:
        level = self.level(person)
        verdict = "autorise" if level >= required else "REFUSE"
        target = command.machine if isinstance(command, MachineCommand) else command.room
        logger.info("droits : %s (niveau %s) %s %s %s : %s (requis %s)",
                    person or "inconnu", LEVEL_NAMES[level], command.action, command.device, target,
                    verdict, LEVEL_NAMES[required])
        return level
