"""Étape F7 : l'unité systemd du serveur (server/voice-server.service) et les cibles du Makefile qui l'installent."""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from voice_server.__main__ import EXIT_SETUP

REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make absent")


def make(*args: str, path: str | None = None) -> subprocess.CompletedProcess[str]:
    """Lance make à la racine du dépôt, avec un PATH choisi au besoin."""
    env = {**os.environ, "PATH": path} if path else None
    return subprocess.run(["make", "-s", *args], cwd=REPO, capture_output=True, text=True, env=env, check=False)


def render(folder: Path, user: str = "alice") -> str:
    result = make("server-unit", f"SERVICE_USER={user}", f"SERVER_DIR={folder}")
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_unit_is_rendered_for_the_given_user_and_folder(tmp_path: Path) -> None:
    text = render(tmp_path)

    assert "@USER@" not in text and "@SERVER_DIR@" not in text
    assert "User=alice" in text
    assert f"WorkingDirectory={tmp_path}" in text
    assert f"ExecStart={tmp_path}/.venv/bin/python -m voice_server --config config.yaml" in text


def test_unit_does_not_restart_when_the_setup_is_wrong(tmp_path: Path) -> None:
    """Configuration ou modèles à corriger : le code de sortie du serveur est celui que systemd ne relance pas."""
    lines = render(tmp_path).splitlines()

    assert f"RestartPreventExitStatus={EXIT_SETUP}" in lines
    assert "Restart=on-failure" in lines


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze absent")
def test_unit_passes_systemd_analyze(tmp_path: Path) -> None:
    """systemd lit l'unité sans erreur ni avertissement (programme, dossier et compte existent)."""
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(Path(sys.executable).resolve())
    unit = tmp_path / "voice-server.service"
    unit.write_text(render(tmp_path, user=getpass.getuser()), encoding="utf-8")

    result = subprocess.run(["systemd-analyze", "verify", str(unit)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == ""


def fake_systemctl(folder: Path, exit_code: int) -> str:
    """PATH où « systemctl » répond toujours exit_code (0 = le service est actif, 3 = inactif)."""
    script = folder / "systemctl"
    script.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    script.chmod(0o755)
    return f"{folder}{os.pathsep}{os.environ['PATH']}"


def test_manual_run_is_refused_while_the_service_runs(tmp_path: Path) -> None:
    """Deux serveurs de même identifiant MQTT se chassent du broker : la garde de « make run » refuse."""
    result = make("server-guard", path=fake_systemctl(tmp_path, 0))

    assert result.returncode != 0
    assert "tourne deja" in result.stdout + result.stderr


def test_manual_run_is_allowed_when_the_service_is_stopped(tmp_path: Path) -> None:
    assert make("server-guard", path=fake_systemctl(tmp_path, 3)).returncode == 0


def test_run_depends_on_the_guard() -> None:
    """« make run » et « make run-echo » passent par la garde (make -n n'exécute rien)."""
    for target in ("run", "run-echo"):
        result = subprocess.run(["make", "-n", target], cwd=REPO, capture_output=True, text=True, check=False)
        assert "systemctl is-active --quiet voice-server" in result.stdout, target
