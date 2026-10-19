"""Fixtures communes aux tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def config_copy(tmp_path: Path) -> Path:
    """Copie de config.yaml et des fichiers qu'il inclut (server/conf) : modifiable sans toucher aux vrais.

    Rend le chemin du config.yaml copié ; les fichiers inclus sont dans le dossier conf voisin.
    """
    destination = tmp_path / "server"
    destination.mkdir()
    shutil.copy2(SERVER_DIR / "config.yaml", destination / "config.yaml")
    shutil.copytree(SERVER_DIR / "conf", destination / "conf")
    return destination / "config.yaml"
