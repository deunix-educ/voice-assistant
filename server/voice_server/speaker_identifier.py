"""Identification du locuteur : empreinte vocale comparée aux profils enrôlés (étape 9).

Une empreinte est un vecteur de 256 nombres qui résume le timbre d'une voix.
Deux empreintes de la même personne pointent à peu près dans la même direction :
on les compare par similarité cosinus (1 = même direction, 0 = sans rapport).
Au-dessus du seuil, on donne le nom du profil le plus proche ; sinon « Inconnu ».

C'est une estimation, jamais une preuve : une voix enregistrée, imitée ou
synthétisée peut tromper le système. Ne jamais s'en servir comme sécurité.
"""

from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from voice_server.settings import SpeakerSettings

logger = logging.getLogger(__name__)

UNKNOWN = "Inconnu"


@dataclass(frozen=True)
class VoiceProfile:
    """Profil vocal d'une personne : la moyenne de ses empreintes d'enrôlement."""

    name: str
    embedding: np.ndarray   # normalisé (norme 1)
    samples: int            # nombre de sessions moyennées
    model: str              # modèle d'empreintes : un profil ne se compare qu'au même modèle
    created: str


@dataclass(frozen=True)
class Identification:
    """Verdict pour une voix."""

    name: str | None        # None : personne n'atteint le seuil
    score: float            # similarité avec le profil le plus proche (NaN : pas d'empreinte)
    closest: str | None     # profil le plus proche, même sous le seuil (diagnostic)

    @property
    def label(self) -> str:
        """Le nom, ou « Inconnu »."""
        return self.name or UNKNOWN

    def describe(self) -> str:
        """« Denis (0.74) », « Inconnu (0.21, proche de Denis) », « Inconnu (trop peu de parole) »."""
        if math.isnan(self.score):
            return f"{UNKNOWN} (trop peu de parole)"
        if self.name is not None:
            return f"{self.name} ({self.score:.2f})"
        if self.closest is not None:
            return f"{UNKNOWN} ({self.score:.2f}, proche de {self.closest})"
        return f"{UNKNOWN} (aucun profil)"


def normalized(vector: np.ndarray) -> np.ndarray:
    """Vecteur ramené à la norme 1 : le cosinus devient un simple produit scalaire."""
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 and math.isfinite(norm) else np.full_like(vector, np.nan)


def profile_path(profiles_dir: Path, name: str) -> Path:
    """Fichier du profil : « Élodie Martin » → profiles/elodie-martin.json."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    if not slug:
        raise ValueError(f"nom de profil inutilisable : {name!r}")
    return profiles_dir / f"{slug}.json"


def save_profile(profiles_dir: Path, name: str, embeddings: list[np.ndarray], model: str) -> VoiceProfile:
    """Moyenne les empreintes d'enrôlement et écrit le profil (remplace l'ancien)."""
    mean = normalized(np.mean([normalized(e) for e in embeddings], axis=0))
    profile = VoiceProfile(name=name, embedding=mean, samples=len(embeddings), model=model,
                           created=datetime.now().isoformat(timespec="seconds"))
    path = profile_path(profiles_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "name": profile.name, "model": profile.model, "samples": profile.samples,
        "created": profile.created, "embedding": [round(float(v), 6) for v in profile.embedding],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(path)  # écriture atomique : le serveur ne lit jamais un fichier à moitié écrit
    return profile


def load_profiles(profiles_dir: Path, model: str) -> list[VoiceProfile]:
    """Lit les profils ; ceux d'un autre modèle d'empreintes sont ignorés (incomparables)."""
    profiles: list[VoiceProfile] = []
    for path in sorted(profiles_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            profile = VoiceProfile(name=str(data["name"]), embedding=normalized(np.asarray(data["embedding"], dtype=np.float32)),
                                   samples=int(data["samples"]), model=str(data["model"]), created=str(data["created"]))
        except (OSError, ValueError, KeyError) as error:
            logger.warning("profil illisible %s : %s", path.name, error)
            continue
        if profile.model != model:
            logger.warning("profil %s ignore : cree avec %s, pas %s (re-enrolez)", path.name, profile.model, model)
            continue
        profiles.append(profile)
    return profiles


class SpeakerIdentifier:
    """Donne un nom à une empreinte, d'après les profils du dossier.

    Le dossier est relu dès qu'il change : un enrôlement fait pendant que le
    serveur tourne est pris en compte sans le redémarrer.
    """

    def __init__(self, settings: SpeakerSettings, model: str) -> None:
        """
        Args:
            settings: dossier des profils et seuil.
            model: nom du modèle d'empreintes utilisé par la diarisation.
        """
        self._settings = settings
        self._model = model
        self._stamp: float | None = None
        self._profiles: list[VoiceProfile] = []
        self._reload_if_changed()

    @property
    def profiles(self) -> list[VoiceProfile]:
        """Profils chargés."""
        self._reload_if_changed()
        return self._profiles

    def identify(self, embedding: np.ndarray) -> Identification:
        """Profil le plus proche, s'il dépasse le seuil."""
        self._reload_if_changed()
        vector = normalized(np.asarray(embedding, dtype=np.float32))
        if not np.all(np.isfinite(vector)):
            return Identification(name=None, score=math.nan, closest=None)
        if not self._profiles:
            return Identification(name=None, score=0.0, closest=None)
        scores = [float(profile.embedding @ vector) for profile in self._profiles]
        best = int(np.argmax(scores))
        closest = self._profiles[best].name
        if scores[best] >= self._settings.similarity_threshold:
            return Identification(name=closest, score=scores[best], closest=closest)
        return Identification(name=None, score=scores[best], closest=closest)

    def _reload_if_changed(self) -> None:
        directory = self._settings.profiles_dir
        stamp = directory.stat().st_mtime if directory.is_dir() else None
        if stamp == self._stamp:
            return
        self._stamp = stamp
        self._profiles = load_profiles(directory, self._model) if stamp is not None else []
        names = ", ".join(f"{p.name} ({p.samples} sessions)" for p in self._profiles) or "aucun"
        logger.info("profils vocaux : %s", names)
