"""Tests des droits par profil, étape 14 : niveaux, refus, confirmation (sans modèle)."""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_server.access_control import AccessPolicy, Level, parse_level
from voice_server.home_control import DeviceCommand
from voice_server.settings import AccessSettings, HomeSettings, load_settings

HOME = HomeSettings(topic_prefix="home", rooms={"salon": "du salon"}, boards={"esp32-01": "salon"})
ACCESS = AccessSettings(
    unknown_level="limite", default_level="standard", users={"Denis": "complet", "Tom": "limite"},
    full_min_score=0.6,
    rules={"light": {"on": "standard", "off": "standard"}, "door": {"open": "complet", "close": "standard"}},
    confirm=("door.open",), confirm_timeout_s=15.0,
)
LIGHT_ON = DeviceCommand("on", "light", "salon")
DOOR_OPEN = DeviceCommand("open", "door", "salon")


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def policy(clock: Clock) -> AccessPolicy:
    return AccessPolicy(ACCESS, HOME, clock=clock)


@pytest.mark.parametrize(
    ("speaker", "score", "expected"),
    [
        (None, None, Level.LIMITED),          # voix inconnue
        ("Denis", 0.74, Level.FULL),
        ("Denis", 0.52, Level.STANDARD),      # reconnu de justesse : pas le niveau complet
        ("Jessica", 0.9, Level.STANDARD),     # enrôlée sans niveau : défaut
        ("Tom", 0.95, Level.LIMITED),
    ],
)
def test_levels(policy: AccessPolicy, speaker: str | None, score: float | None, expected: Level) -> None:
    assert policy.level(speaker, score) == expected


def test_unknown_level_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="complet, standard ou limite"):
        parse_level("admin")


def test_unknown_voice_cannot_command(policy: AccessPolicy) -> None:
    verdict = policy.check(LIGHT_ON, None, None)
    assert not verdict.allowed
    assert verdict.answer == "Je ne vous ai pas reconnu : je ne peux pas allumer la lumière du salon."


def test_standard_profile_controls_lights_not_the_door(policy: AccessPolicy) -> None:
    assert policy.check(LIGHT_ON, "Jessica", 0.9).allowed
    refused = policy.check(DOOR_OPEN, "Jessica", 0.9)
    assert not refused.allowed and refused.answer is not None and refused.answer.startswith("Désolé Jessica")


def test_action_missing_from_rules_needs_full_level(policy: AccessPolicy) -> None:
    """On ne permet jamais par oubli : sans règle, seul le niveau complet passe."""
    shutter = DeviceCommand("open", "shutter", "salon")
    assert not policy.check(shutter, "Jessica", 0.9).allowed
    assert policy.check(shutter, "Denis", 0.8).allowed


def test_sensitive_action_asks_for_confirmation(policy: AccessPolicy) -> None:
    verdict = policy.check(DOOR_OPEN, "Denis", 0.8)
    assert verdict.allowed and verdict.confirm
    assert verdict.answer == "Confirmez-vous : ouvrir la porte du salon ? Dites oui ou non."


def test_yes_from_the_same_person_executes(policy: AccessPolicy) -> None:
    policy.ask("esp32-01", DOOR_OPEN, "Denis")
    resolution = policy.resolve("esp32-01", "Oui, vas-y.", "Denis")

    assert resolution is not None and resolution.command == DOOR_OPEN
    assert resolution.answer == "J'ouvre la porte du salon."
    assert policy.resolve("esp32-01", "oui", "Denis") is None  # une confirmation ne sert qu'une fois


def test_no_cancels(policy: AccessPolicy) -> None:
    policy.ask("esp32-01", DOOR_OPEN, "Denis")
    resolution = policy.resolve("esp32-01", "Non, annule.", "Denis")
    assert resolution is not None and resolution.command is None


def test_someone_else_cannot_confirm(policy: AccessPolicy) -> None:
    policy.ask("esp32-01", DOOR_OPEN, "Denis")
    resolution = policy.resolve("esp32-01", "Oui", "Jessica")
    assert resolution is not None and resolution.command is None
    assert resolution.answer.startswith("Seul Denis")


def test_short_yes_is_accepted_when_the_requester_stays_the_closest_voice(policy: AccessPolicy) -> None:
    """Essai réel du 2026-10-06 : « OUI » de Denis à 0,35, sous le seuil d'identification."""
    policy.ask("esp32-01", DOOR_OPEN, "Denis")
    resolution = policy.resolve("esp32-01", "OUI", None, closest="Denis", score=0.35)
    assert resolution is not None and resolution.command == DOOR_OPEN


@pytest.mark.parametrize(
    ("speaker", "closest", "score"),
    [
        (None, "Denis", 0.12),      # trop loin de Denis : n'importe qui
        (None, "Jessica", 0.40),    # plus proche de quelqu'un d'autre
        (None, None, None),         # pas d'empreinte du tout
        ("Jessica", "Jessica", 0.8),  # reconnue comme quelqu'un d'autre
    ],
)
def test_short_yes_from_someone_else_is_refused(policy: AccessPolicy, speaker: str | None,
                                                closest: str | None, score: float | None) -> None:
    policy.ask("esp32-01", DOOR_OPEN, "Denis")
    resolution = policy.resolve("esp32-01", "oui", speaker, closest=closest, score=score)
    assert resolution is not None and resolution.command is None


def test_confirmation_expires(policy: AccessPolicy, clock: Clock) -> None:
    policy.ask("esp32-01", DOOR_OPEN, "Denis")
    clock.now += 16
    assert policy.resolve("esp32-01", "oui", "Denis") is None


def test_other_request_drops_the_pending_one(policy: AccessPolicy) -> None:
    """Autre chose que oui/non : la demande en attente est oubliée, la nouvelle est traitée."""
    policy.ask("esp32-01", DOOR_OPEN, "Denis")
    assert policy.resolve("esp32-01", "Quelle heure est-il ?", "Denis") is None
    assert policy.resolve("esp32-01", "oui", "Denis") is None


def test_project_config_is_valid() -> None:
    """Le config.yaml du dépôt se charge, et ses règles s'appliquent vraiment (profil standard)."""
    settings = load_settings(Path(__file__).resolve().parents[1] / "config.yaml")
    policy = AccessPolicy(settings.access, settings.home, settings.machines)
    for action in ("on", "off"):  # on/off nus en YAML = booléens : Pierre refusé à l'essai réel
        assert policy.check(DeviceCommand(action, "light", "salon"), "Pierre", 0.8).allowed
    assert policy.check(DeviceCommand("close", "shutter", "salon"), "Pierre", 0.8).allowed
    assert not policy.check(DOOR_OPEN, "Pierre", 0.8).allowed


def test_yaml_boolean_keys_are_read_as_on_off(tmp_path: Path) -> None:
    """« light: {on: standard} » sans guillemets : YAML lit True, le chargeur rend « on »."""
    text = (Path(__file__).resolve().parents[1] / "config.yaml").read_text(encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(text.replace('{"on": standard, "off": standard}', "{on: standard, off: standard}"),
                      encoding="utf-8")
    assert load_settings(config).access.rules["light"] == {"on": "standard", "off": "standard"}


def test_unknown_rule_action_is_rejected() -> None:
    """Une règle mal écrite est refusée au démarrage plutôt qu'ignorée en silence."""
    access = AccessSettings(**{**ACCESS.__dict__, "rules": {"light": {"True": "standard"}}})
    with pytest.raises(ValueError, match="action inconnue light.True"):
        AccessPolicy(access, HOME)


def test_barely_recognised_full_profile_is_asked_to_repeat(policy: AccessPolicy) -> None:
    """Denis reconnu à 0,59 : son profil permettrait, c'est la voix qui n'est pas assez sûre."""
    verdict = policy.check(DOOR_OPEN, "Denis", 0.59)
    assert not verdict.allowed
    assert verdict.answer == ("Denis, je ne reconnais pas assez nettement votre voix pour ouvrir "
                              "la porte du salon. Répétez, plus près du micro.")


def test_elision_in_refusal(policy: AccessPolicy) -> None:
    """« ne permet pas d'ouvrir », pas « de ouvrir »."""
    answer = policy.check(DOOR_OPEN, "Jessica", 0.9).answer
    assert answer == "Désolé Jessica, votre profil ne permet pas d'ouvrir la porte du salon."
    close = policy.check(DeviceCommand("close", "door", "salon"), "Tom", 0.9).answer
    assert close == "Désolé Tom, votre profil ne permet pas de fermer la porte du salon."
