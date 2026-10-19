"""Étape F6 : config.yaml peut inclure d'autres fichiers (clé « include », motifs admis)."""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

import pytest

from voice_server.__main__ import EXIT_SETUP, main
from voice_server.settings import read_config

SERVER_DIR = Path(__file__).resolve().parents[1]


def write(path: Path, text: str) -> Path:
    """Écrit un fichier YAML de test (dossiers créés au besoin)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- cas simples

def test_without_include_the_file_is_read_as_is(tmp_path: Path) -> None:
    config = write(tmp_path / "config.yaml", "audio:\n    sample_rate: 16000\n")
    assert read_config(config) == {"audio": {"sample_rate": 16000}}


def test_empty_file_gives_nothing(tmp_path: Path) -> None:
    assert read_config(write(tmp_path / "config.yaml", "")) == {}


def test_include_one_path_or_a_list(tmp_path: Path) -> None:
    write(tmp_path / "a.yaml", "home:\n    topic_prefix: home\n")
    write(tmp_path / "b.yaml", "machines:\n    topic_prefix: agent\n")
    one = write(tmp_path / "one.yaml", "include: a.yaml\naudio: {bits: 16}\n")
    many = write(tmp_path / "many.yaml", "include: [a.yaml, b.yaml]\n")
    assert read_config(one) == {"audio": {"bits": 16}, "home": {"topic_prefix": "home"}}
    assert read_config(many) == {"home": {"topic_prefix": "home"}, "machines": {"topic_prefix": "agent"}}


def test_paths_are_relative_to_the_including_file(tmp_path: Path) -> None:
    write(tmp_path / "conf" / "home.yaml", "include: pieces/salon.yaml\nhome: {topic_prefix: home}\n")
    write(tmp_path / "conf" / "pieces" / "salon.yaml", "home:\n    rooms: {salon: du salon}\n")
    config = write(tmp_path / "config.yaml", "include: conf/home.yaml\n")
    assert read_config(config) == {"home": {"topic_prefix": "home", "rooms": {"salon": "du salon"}}}


def test_absolute_path_is_accepted(tmp_path: Path) -> None:
    other = write(tmp_path / "ailleurs" / "x.yaml", "a: 1\n")
    config = write(tmp_path / "config.yaml", f"include: {other}\n")
    assert read_config(config) == {"a": 1}


# --------------------------------------------------------------------------- fusion

def test_mappings_add_up_across_files(tmp_path: Path) -> None:
    write(tmp_path / "a.yaml", "home:\n    rooms: {salon: du salon}\n")
    write(tmp_path / "b.yaml", "home:\n    rooms: {cuisine: de la cuisine}\n    boards: {esp32-01: salon}\n")
    config = write(tmp_path / "config.yaml", "include: [a.yaml, b.yaml]\nhome: {topic_prefix: home}\n")
    assert read_config(config) == {"home": {
        "topic_prefix": "home",
        "rooms": {"salon": "du salon", "cuisine": "de la cuisine"},
        "boards": {"esp32-01": "salon"},
    }}


def test_numeric_keys_add_up_too(tmp_path: Path) -> None:
    """fingerprint.users a des clés entières : deux fichiers peuvent nommer des doigts différents."""
    write(tmp_path / "a.yaml", "fingerprint:\n    users: {1: Denis}\n")
    write(tmp_path / "b.yaml", "fingerprint:\n    users: {2: Jessica}\n")
    config = write(tmp_path / "config.yaml", "include: [a.yaml, b.yaml]\n")
    assert read_config(config)["fingerprint"]["users"] == {1: "Denis", 2: "Jessica"}


def test_same_value_in_two_files_is_an_error_naming_both(tmp_path: Path) -> None:
    write(tmp_path / "conf" / "a.yaml", "home:\n    topic_prefix: home\n")
    config = write(tmp_path / "config.yaml", "include: conf/a.yaml\nhome:\n    topic_prefix: maison\n")
    with pytest.raises(ValueError, match=r"« home\.topic_prefix » est défini à la fois dans config\.yaml "
                                         r"et dans conf[/\\]a\.yaml"):
        read_config(config)


def test_same_finger_in_two_files_is_an_error(tmp_path: Path) -> None:
    write(tmp_path / "a.yaml", "fingerprint:\n    users: {1: Denis}\n")
    write(tmp_path / "b.yaml", "fingerprint:\n    users: {1: Jessica}\n")
    config = write(tmp_path / "config.yaml", "include: [a.yaml, b.yaml]\n")
    with pytest.raises(ValueError, match=r"fingerprint\.users\.1 »"):
        read_config(config)


def test_a_mapping_and_a_value_under_the_same_key_is_an_error(tmp_path: Path) -> None:
    write(tmp_path / "a.yaml", "home: {rooms: {salon: du salon}}\n")
    config = write(tmp_path / "config.yaml", "include: a.yaml\nhome:\n    rooms: tous\n")
    with pytest.raises(ValueError, match=r"« home\.rooms » est défini à la fois"):
        read_config(config)


def test_lists_are_never_concatenated(tmp_path: Path) -> None:
    write(tmp_path / "a.yaml", "access:\n    confirm: [door.open]\n")
    config = write(tmp_path / "config.yaml", "include: a.yaml\naccess:\n    confirm: [machine.reboot]\n")
    with pytest.raises(ValueError, match=r"« access\.confirm » est défini à la fois"):
        read_config(config)


# --------------------------------------------------------------------------- motifs

def test_pattern_loads_matching_files_sorted_by_name(tmp_path: Path) -> None:
    for name in ("cuisine", "salon", "bureau"):
        write(tmp_path / "pieces" / f"{name}.yaml", f"home:\n    rooms: {{{name}: de la {name}}}\n")
    write(tmp_path / "pieces" / "notes.txt", "pas du YAML : { ignoré\n")
    config = write(tmp_path / "config.yaml", "include: 'pieces/*.yaml'\n")
    rooms = read_config(config)["home"]["rooms"]
    assert list(rooms) == ["bureau", "cuisine", "salon"]


def test_recursive_pattern(tmp_path: Path) -> None:
    write(tmp_path / "machines" / "bureau" / "pc.yaml", "machines:\n    list: {pc-bureau: {name: le PC}}\n")
    write(tmp_path / "machines" / "nas.yaml", "machines:\n    list: {nas: {name: le NAS}}\n")
    config = write(tmp_path / "config.yaml", "include: 'machines/**/*.yaml'\n")
    assert sorted(read_config(config)["machines"]["list"]) == ["nas", "pc-bureau"]


def test_pattern_without_match_warns_but_is_not_an_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    config = write(tmp_path / "config.yaml", "include: 'pieces/*.yaml'\naudio: {bits: 16}\n")
    with caplog.at_level(logging.WARNING, logger="voice_server.settings"):
        assert read_config(config) == {"audio": {"bits": 16}}
    assert "pieces/*.yaml" in caplog.text


def test_overlapping_patterns_read_a_file_once(tmp_path: Path) -> None:
    write(tmp_path / "pieces" / "salon.yaml", "home:\n    rooms: {salon: du salon}\n")
    config = write(tmp_path / "config.yaml", "include: ['pieces/*.yaml', 'pieces/salon.yaml']\n")
    assert read_config(config) == {"home": {"rooms": {"salon": "du salon"}}}


# --------------------------------------------------------------------------- erreurs

def test_missing_file_is_an_error(tmp_path: Path) -> None:
    config = write(tmp_path / "config.yaml", "include: conf/absent.yaml\n")
    with pytest.raises(ValueError, match=r"fichier introuvable : conf[/\\]absent\.yaml"):
        read_config(config)


def test_include_loop_is_an_error(tmp_path: Path) -> None:
    write(tmp_path / "a.yaml", "include: b.yaml\n")
    write(tmp_path / "b.yaml", "include: a.yaml\n")
    config = write(tmp_path / "config.yaml", "include: a.yaml\n")
    with pytest.raises(ValueError, match=r"boucle d'inclusion \(config\.yaml → a\.yaml → b\.yaml → a\.yaml\)"):
        read_config(config)


def test_a_file_matching_its_own_pattern_is_a_loop(tmp_path: Path) -> None:
    write(tmp_path / "conf" / "all.yaml", "include: '*.yaml'\n")
    config = write(tmp_path / "config.yaml", "include: conf/all.yaml\n")
    with pytest.raises(ValueError, match="boucle d'inclusion"):
        read_config(config)


def test_too_many_levels_is_an_error(tmp_path: Path) -> None:
    for number in range(12):
        write(tmp_path / f"f{number}.yaml", f"include: f{number + 1}.yaml\n")
    write(tmp_path / "f12.yaml", "a: 1\n")
    with pytest.raises(ValueError, match="plus de 8 niveaux"):
        read_config(tmp_path / "f0.yaml")


@pytest.mark.parametrize("value", ["3", "{a: b}", "[1, 2]", "['']"])
def test_include_must_be_a_path_or_a_list_of_paths(tmp_path: Path, value: str) -> None:
    config = write(tmp_path / "config.yaml", f"include: {value}\n")
    with pytest.raises(ValueError, match="« include » doit être un chemin ou une liste de chemins"):
        read_config(config)


def test_included_file_must_be_a_mapping(tmp_path: Path) -> None:
    write(tmp_path / "liste.yaml", "- a\n- b\n")
    config = write(tmp_path / "config.yaml", "include: liste.yaml\n")
    with pytest.raises(ValueError, match=r"liste\.yaml : un mapping YAML est attendu"):
        read_config(config)


# --------------------------------------------------------------------------- le config.yaml du dépôt

def test_project_config_takes_its_sections_from_conf() -> None:
    """Les sections découpées arrivent bien de server/conf, et « include » ne reste pas dans le résultat."""
    config = read_config(SERVER_DIR / "config.yaml")
    assert "include" not in config
    for section in ("audio", "mqtt", "stt", "home", "machines", "access", "fingerprint"):
        assert section in config, section
    raw = (SERVER_DIR / "config.yaml").read_text(encoding="utf-8")
    for moved in ("home", "machines", "access", "fingerprint"):
        assert f"\n{moved}:" not in raw, f"{moved} devrait vivre dans conf/"


def test_mqtt_host_stays_readable_by_the_makefile() -> None:
    """Le Makefile lit mqtt.host dans config.yaml avec awk : la section mqtt doit y rester."""
    inside = False
    host = ""
    for line in (SERVER_DIR / "config.yaml").read_text(encoding="utf-8").splitlines():
        if line.startswith("mqtt:"):
            inside = True
            continue
        if inside and line.split()[:1] == ["host:"]:
            host = line.split()[1]
            break
    assert host


# --------------------------------------------------------------------------- démarrage du serveur

def test_server_refuses_to_start_on_a_conflict_and_says_why(config_copy: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Deux fichiers définissent access.default : pas de trace d'appels, le message nomme les deux fichiers."""
    config_copy.write_text(config_copy.read_text(encoding="utf-8") + "\naccess:\n    default: standard\n", encoding="utf-8")
    assert main(["--config", str(config_copy)]) == EXIT_SETUP
    error = capsys.readouterr().err
    assert "configuration invalide" in error
    assert "« access.default » est défini à la fois dans" in error
    assert "config.yaml" in error and "access.yaml" in error


def test_server_refuses_to_start_on_a_missing_included_file(config_copy: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (config_copy.parent / "conf" / "home.yaml").unlink()
    assert main(["--config", str(config_copy)]) == EXIT_SETUP
    assert "fichier introuvable" in capsys.readouterr().err
