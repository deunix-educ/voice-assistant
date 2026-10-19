"""Étape F8 : config.example.yaml est le modèle versionné ; server/config.yaml est la copie locale, ignorée par git."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from voice_server.__main__ import EXIT_SETUP, main
from voice_server.settings import load_settings

SERVER_DIR = Path(__file__).resolve().parents[1]
REPO = SERVER_DIR.parent
EXAMPLE = SERVER_DIR / "config.example.yaml"


def make(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["make", "-s", *args], cwd=REPO, capture_output=True, text=True, check=False)


def test_example_loads_and_keeps_the_repository_broker() -> None:
    """Le modèle se charge seul (un clone neuf n'a pas de config.yaml) et vise le broker du dépôt."""
    settings = load_settings(EXAMPLE)

    assert settings.mqtt.host == "10.42.0.1"
    assert settings.home.rooms and settings.access.users


@pytest.mark.skipif(shutil.which("git") is None or not (REPO / ".git").exists(), reason="pas un dépôt git")
def test_local_config_is_ignored_by_git_and_the_example_is_not() -> None:
    def ignored(path: str) -> bool:
        return subprocess.run(["git", "check-ignore", "--no-index", "-q", path], cwd=REPO, check=False).returncode == 0

    assert ignored("server/config.yaml")
    assert not ignored("server/config.example.yaml")


@pytest.mark.skipif(shutil.which("make") is None, reason="make absent")
def test_make_config_creates_the_local_file_from_the_example(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"

    result = make("config", f"CONFIG_FILE={target}", f"CONFIG_EXAMPLE={EXAMPLE}")

    assert result.returncode == 0, result.stderr
    assert target.read_text(encoding="utf-8") == EXAMPLE.read_text(encoding="utf-8")
    assert "verifiez mqtt.host" in result.stdout


@pytest.mark.skipif(shutil.which("make") is None, reason="make absent")
def test_make_config_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("mqtt:\n    host: 192.168.1.4\n", encoding="utf-8")

    result = make("config", f"CONFIG_FILE={target}", f"CONFIG_EXAMPLE={EXAMPLE}")

    assert result.returncode == 0
    assert target.read_text(encoding="utf-8") == "mqtt:\n    host: 192.168.1.4\n"
    assert "existe deja" in result.stdout


@pytest.mark.skipif(shutil.which("make") is None, reason="make absent")
def test_make_install_creates_the_config() -> None:
    """« make install » passe par la cible config (make -n n'exécute rien)."""
    result = subprocess.run(["make", "-n", "install"], cwd=REPO, capture_output=True, text=True, check=False)

    assert "cp server/config.example.yaml server/config.yaml" in result.stdout


@pytest.mark.skipif(shutil.which("make") is None, reason="make absent")
def test_config_diff_shows_what_changed_from_the_example(tmp_path: Path) -> None:
    local = tmp_path / "config.yaml"
    local.write_text(EXAMPLE.read_text(encoding="utf-8").replace("10.42.0.1", "192.168.1.4"), encoding="utf-8")

    result = make("config-diff", f"CONFIG_FILE={local}", f"CONFIG_EXAMPLE={EXAMPLE}")

    assert "-    host: 10.42.0.1" in result.stdout and "+    host: 192.168.1.4" in result.stdout


def test_server_says_how_to_create_a_missing_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Un clone neuf lance le serveur sans config.yaml : le message dit quoi faire."""
    shutil.copy2(EXAMPLE, tmp_path / "config.example.yaml")
    shutil.copytree(SERVER_DIR / "conf", tmp_path / "conf")

    assert main(["--config", str(tmp_path / "config.yaml")]) == EXIT_SETUP

    assert "make config" in capsys.readouterr().err
