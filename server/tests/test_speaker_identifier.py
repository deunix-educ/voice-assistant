"""Tests de l'identification du locuteur, étape 9 : profils, cosinus, seuil (sans modèle)."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

from enroll import drop_outliers
from voice_server.settings import SpeakerSettings
from voice_server.speaker_identifier import (
    SpeakerIdentifier,
    load_profiles,
    normalized,
    profile_path,
    save_profile,
)

MODEL = "pyannote/speaker-diarization-community-1"


def voice(seed: int, noise: float = 0.0, base: int | None = None) -> np.ndarray:
    """Empreinte factice : une direction aléatoire, éventuellement bruitée autour d'une autre."""
    rng = np.random.default_rng(seed)
    center = np.random.default_rng(base).normal(size=256) if base is not None else rng.normal(size=256)
    return (center + noise * rng.normal(size=256)).astype(np.float32)


@pytest.fixture
def settings(tmp_path: Path) -> SpeakerSettings:
    return SpeakerSettings(profiles_dir=tmp_path / "profiles", similarity_threshold=0.45)


def test_profile_file_name(tmp_path: Path) -> None:
    """Nom lisible, sans accent ni espace ; un nom vide est refusé."""
    assert profile_path(tmp_path, "Élodie Martin").name == "elodie-martin.json"
    with pytest.raises(ValueError):
        profile_path(tmp_path, "  ")


def test_save_and_load(settings: SpeakerSettings) -> None:
    """Le profil est la moyenne normalisée ; il se relit tel quel."""
    saved = save_profile(settings.profiles_dir, "Denis", [voice(1, 0.3, base=0), voice(2, 0.3, base=0)], MODEL)

    [loaded] = load_profiles(settings.profiles_dir, MODEL)
    assert loaded.name == "Denis" and loaded.samples == 2
    assert float(np.linalg.norm(loaded.embedding)) == pytest.approx(1.0, abs=1e-4)
    assert float(loaded.embedding @ saved.embedding) == pytest.approx(1.0, abs=1e-4)
    assert not list(settings.profiles_dir.glob("*.tmp"))  # écriture atomique terminée


def test_profile_of_another_model_is_ignored(settings: SpeakerSettings) -> None:
    """Deux modèles d'empreintes ne sont pas comparables : le profil est écarté."""
    save_profile(settings.profiles_dir, "Denis", [voice(1)], "autre/modele")
    assert load_profiles(settings.profiles_dir, MODEL) == []


def test_unreadable_profile_is_skipped(settings: SpeakerSettings) -> None:
    settings.profiles_dir.mkdir(parents=True)
    (settings.profiles_dir / "casse.json").write_text("{pas du json", encoding="utf-8")
    assert load_profiles(settings.profiles_dir, MODEL) == []


def test_identify_known_and_unknown(settings: SpeakerSettings) -> None:
    """La même voix est reconnue ; une autre voix reste « Inconnu », avec le profil le plus proche."""
    save_profile(settings.profiles_dir, "Denis", [voice(i, 0.5, base=0) for i in range(1, 6)], MODEL)
    save_profile(settings.profiles_dir, "Alice", [voice(i, 0.5, base=100) for i in range(11, 16)], MODEL)
    identifier = SpeakerIdentifier(settings, MODEL)

    denis = identifier.identify(voice(42, 0.5, base=0))
    stranger = identifier.identify(voice(7))

    assert denis.name == "Denis" and denis.score > 0.45
    assert stranger.name is None and stranger.label == "Inconnu"
    assert stranger.closest in ("Denis", "Alice")
    assert "proche de" in stranger.describe()


def test_missing_embedding_is_unknown(settings: SpeakerSettings) -> None:
    """Trop peu de parole : empreinte NaN, jamais un nom."""
    save_profile(settings.profiles_dir, "Denis", [voice(1)], MODEL)
    verdict = SpeakerIdentifier(settings, MODEL).identify(np.full(256, np.nan, dtype=np.float32))

    assert verdict.name is None and math.isnan(verdict.score)
    assert verdict.describe() == "Inconnu (trop peu de parole)"


def test_no_profile(settings: SpeakerSettings) -> None:
    assert SpeakerIdentifier(settings, MODEL).identify(voice(1)).describe() == "Inconnu (aucun profil)"


def test_new_profile_is_seen_without_restart(settings: SpeakerSettings) -> None:
    """Un enrôlement fait pendant que le serveur tourne est pris en compte."""
    identifier = SpeakerIdentifier(settings, MODEL)
    assert identifier.identify(voice(1)).name is None

    save_profile(settings.profiles_dir, "Denis", [voice(1)], MODEL)
    stamp = settings.profiles_dir.stat().st_mtime + 1  # horloge des fichiers parfois grossière
    os.utime(settings.profiles_dir, (stamp, stamp))

    assert identifier.identify(voice(1)).name == "Denis"


def test_profile_file_is_readable_json(settings: SpeakerSettings) -> None:
    """Format ouvert : on sait ce qu'on stocke sur une personne."""
    save_profile(settings.profiles_dir, "Denis", [voice(1)], MODEL)
    data = json.loads(profile_path(settings.profiles_dir, "Denis").read_text(encoding="utf-8"))

    assert set(data) == {"name", "model", "samples", "created", "embedding"}
    assert len(data["embedding"]) == 256


def test_enrollment_drops_a_foreign_session() -> None:
    """Enrôlement : une session d'une autre voix parmi les vôtres est écartée."""
    samples = [(f"s{i}", normalized(voice(i, 0.5, base=0))) for i in range(1, 6)]
    samples.append(("intrus", normalized(voice(99))))

    kept = drop_outliers(samples, 0.45)

    assert [name for name, _ in kept] == ["s1", "s2", "s3", "s4", "s5"]
