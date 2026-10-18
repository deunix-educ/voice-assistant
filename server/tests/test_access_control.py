"""Tests des droits par personne (étape 14, par doigt depuis F4) : niveaux, refus, confirmation."""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_server.access_control import AccessPolicy, Level, parse_level
from voice_server.home_control import DeviceCommand
from voice_server.settings import AccessSettings, HomeSettings, load_settings

HOME = HomeSettings(topic_prefix="home", rooms={"salon": "du salon"}, boards={"esp32-01": "salon"})
ACCESS = AccessSettings(
    unknown_level="limite", default_level="standard", users={"Denis": "complet", "Tom": "limite"},
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
    ("person", "expected"),
    [
        (None, Level.LIMITED),        # bouton PTT, ou doigt sans nom
        ("Denis", Level.FULL),
        ("Jessica", Level.STANDARD),  # nommée par un doigt, sans niveau : défaut
        ("Tom", Level.LIMITED),
        ("denis", Level.FULL),        # la casse ne compte pas
        ("DENIS", Level.FULL),
    ],
)
def test_levels(policy: AccessPolicy, person: str | None, expected: Level) -> None:
    assert policy.level(person) == expected


def test_names_are_compared_without_accents_either() -> None:
    access = AccessSettings(**{**ACCESS.__dict__, "users": {"Hélène": "complet"}})
    assert AccessPolicy(access, HOME).level("helene") == Level.FULL


def test_unknown_level_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="complet, standard ou limite"):
        parse_level("admin")


def test_unknown_person_cannot_command(policy: AccessPolicy) -> None:
    verdict = policy.check(LIGHT_ON, None)
    assert not verdict.allowed
    assert verdict.answer == "Je ne sais pas qui tient la carte : je ne peux pas allumer la lumière du salon."


def test_standard_profile_controls_lights_not_the_door(policy: AccessPolicy) -> None:
    assert policy.check(LIGHT_ON, "Jessica").allowed
    refused = policy.check(DOOR_OPEN, "Jessica")
    assert not refused.allowed and refused.answer is not None and refused.answer.startswith("Désolé Jessica")


def test_action_missing_from_rules_needs_full_level(policy: AccessPolicy) -> None:
    """On ne permet jamais par oubli : sans règle, seul le niveau complet passe."""
    shutter = DeviceCommand("open", "shutter", "salon")
    assert not policy.check(shutter, "Jessica").allowed
    assert policy.check(shutter, "Denis").allowed


def test_sensitive_action_asks_for_confirmation(policy: AccessPolicy) -> None:
    verdict = policy.check(DOOR_OPEN, "Denis")
    assert verdict.allowed and verdict.confirm
    assert verdict.answer == "Confirmez-vous : ouvrir la porte du salon ? Dites oui ou non."


def test_yes_from_the_same_person_executes(policy: AccessPolicy) -> None:
    policy.ask("esp32-01", DOOR_OPEN, "Denis", 1)
    resolution = policy.resolve("esp32-01", "Oui, vas-y.", "Denis", 1)

    assert resolution is not None and resolution.command == DOOR_OPEN
    assert resolution.answer == "J'ouvre la porte du salon."
    assert policy.resolve("esp32-01", "oui", "Denis", 1) is None  # une confirmation ne sert qu'une fois


def test_no_cancels(policy: AccessPolicy) -> None:
    policy.ask("esp32-01", DOOR_OPEN, "Denis", 1)
    resolution = policy.resolve("esp32-01", "Non, annule.", "Denis", 1)
    assert resolution is not None and resolution.command is None


def test_someone_else_cannot_confirm(policy: AccessPolicy) -> None:
    policy.ask("esp32-01", DOOR_OPEN, "Denis", 1)
    resolution = policy.resolve("esp32-01", "Oui", "Jessica", 2)
    assert resolution is not None and resolution.command is None
    assert resolution.answer.startswith("Seul Denis")


def test_another_finger_of_the_same_person_cannot_confirm(policy: AccessPolicy) -> None:
    """Le doigt exact de la demande : même un autre doigt de Denis (même un nom écrit autrement) est refusé."""
    policy.ask("esp32-01", DOOR_OPEN, "Denis", 1)
    resolution = policy.resolve("esp32-01", "oui", "denis", 3)
    assert resolution is not None and resolution.command is None
    assert resolution.answer == "Confirmez avec le même doigt que pour la demande. J'annule."


@pytest.mark.parametrize(("person", "finger"), [(None, None), (None, 4), ("Jessica", 2), ("Tom", 5)])
def test_yes_from_the_button_or_someone_else_is_refused(policy: AccessPolicy, person: str | None,
                                                        finger: int | None) -> None:
    """Bouton PTT, doigt sans nom ou doigt d'une autre personne : la demande est annulée."""
    policy.ask("esp32-01", DOOR_OPEN, "Denis", 1)
    resolution = policy.resolve("esp32-01", "oui", person, finger)
    assert resolution is not None and resolution.command is None
    assert resolution.answer == "Seul Denis peut confirmer. J'annule."


def test_a_refused_confirmation_is_forgotten(policy: AccessPolicy) -> None:
    """La demande annulée ne peut plus être confirmée, même par le bon doigt ensuite."""
    policy.ask("esp32-01", DOOR_OPEN, "Denis", 1)
    policy.resolve("esp32-01", "oui", "Jessica", 2)
    assert policy.resolve("esp32-01", "oui", "Denis", 1) is None


def test_unknown_person_cannot_start_a_confirmation(policy: AccessPolicy) -> None:
    policy.ask("esp32-01", DOOR_OPEN, None, None)
    assert policy.resolve("esp32-01", "oui", None, None) is None


def test_a_person_without_a_finger_cannot_start_a_confirmation(policy: AccessPolicy) -> None:
    policy.ask("esp32-01", DOOR_OPEN, "Denis", None)
    assert policy.resolve("esp32-01", "oui", "Denis", None) is None


def test_confirmation_expires(policy: AccessPolicy, clock: Clock) -> None:
    policy.ask("esp32-01", DOOR_OPEN, "Denis", 1)
    clock.now += 16
    assert policy.resolve("esp32-01", "oui", "Denis", 1) is None


def test_other_request_drops_the_pending_one(policy: AccessPolicy) -> None:
    """Autre chose que oui/non : la demande en attente est oubliée, la nouvelle est traitée."""
    policy.ask("esp32-01", DOOR_OPEN, "Denis", 1)
    assert policy.resolve("esp32-01", "Quelle heure est-il ?", "Denis", 1) is None
    assert policy.resolve("esp32-01", "oui", "Denis", 1) is None


def test_project_config_is_valid() -> None:
    """Le config.yaml du dépôt se charge, et ses règles s'appliquent vraiment (profil standard)."""
    settings = load_settings(Path(__file__).resolve().parents[1] / "config.yaml")
    policy = AccessPolicy(settings.access, settings.home, settings.machines)
    for action in ("on", "off"):  # on/off nus en YAML = booléens : Pierre refusé à l'essai réel
        assert policy.check(DeviceCommand(action, "light", "salon"), "Pierre").allowed
    assert policy.check(DeviceCommand("close", "shutter", "salon"), "Pierre").allowed
    assert not policy.check(DOOR_OPEN, "Pierre").allowed


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


def test_audit_reports_names_that_do_not_match(policy: AccessPolicy) -> None:
    """Un nom mal écrit ne plante rien : la personne reçoit le niveau par défaut. Le démarrage le dit."""
    warnings = policy.audit(["denis", "Jessica"])
    assert warnings == ["Jessica a un doigt mais pas de niveau dans access.users : niveau par défaut (standard)",
                        "access.users nomme Tom, qui n'a aucun doigt dans fingerprint.users : "
                        "elle ne pourra jamais être reconnue"]
    assert policy.audit(["Denis", "Tom"]) == []


def test_elision_in_refusal(policy: AccessPolicy) -> None:
    """« ne permet pas d'ouvrir », pas « de ouvrir »."""
    answer = policy.check(DOOR_OPEN, "Jessica").answer
    assert answer == "Désolé Jessica, votre profil ne permet pas d'ouvrir la porte du salon."
    close = policy.check(DeviceCommand("close", "door", "salon"), "Tom").answer
    assert close == "Désolé Tom, votre profil ne permet pas de fermer la porte du salon."
