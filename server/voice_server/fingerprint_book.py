"""Carnet des empreintes : à qui appartient chaque emplacement du capteur (branche fingerprint, F2)."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from voice_server.settings import FingerprintSettings

logger = logging.getLogger(__name__)

UNKNOWN = "Inconnu"  # nom affiché quand la personne n'est pas connue (bouton PTT, doigt sans nom)
FIRST_ID = 1  # l'emplacement 0 reste libre : un numéro nul ne se confond jamais avec « personne »


class FingerprintBook:
    """Associe les numéros d'empreinte du capteur à des noms (config.yaml, section fingerprint)."""

    def __init__(self, settings: FingerprintSettings) -> None:
        self._users = dict(settings.users)

    def name_of(self, finger_id: int) -> str | None:
        """Nom du propriétaire, None si l'emplacement n'est pas nommé."""
        return self._users.get(finger_id)

    def person_of(self, finger_id: int | None) -> str | None:
        """La personne qui tient la carte : le nom du doigt reconnu, None si la session vient du bouton
        ou si l'emplacement n'a pas de nom dans config.yaml (journalisé : c'est un oubli probable)."""
        if finger_id is None:
            return None
        name = self._users.get(finger_id)
        if name is None:
            logger.warning("empreinte %d reconnue par le capteur mais sans nom dans fingerprint.users : "
                           "traitee comme une personne inconnue", finger_id)
        return name

    def people(self) -> list[str]:
        """Les personnes qui ont au moins un doigt, sans doublon, dans l'ordre des emplacements."""
        return list(dict.fromkeys(self._users[finger_id] for finger_id in sorted(self._users)))

    def ids_of(self, name: str) -> list[int]:
        """Emplacements d'une personne (elle peut avoir plusieurs doigts), dans l'ordre."""
        return sorted(finger_id for finger_id, owner in self._users.items() if owner == name)

    def named_ids(self) -> list[int]:
        """Tous les emplacements nommés dans config.yaml, dans l'ordre."""
        return sorted(self._users)

    def free_id(self, used: Iterable[int], capacity: int) -> int:
        """Premier emplacement libre : ni occupé dans le capteur, ni déjà nommé dans config.yaml.

        Raises:
            ValueError: le capteur est plein.
        """
        taken = set(used) | set(self._users)
        for candidate in range(FIRST_ID, capacity):
            if candidate not in taken:
                return candidate
        raise ValueError(f"capteur plein : les {capacity - FIRST_ID} emplacements sont occupes")

    def describe(self, finger_id: int) -> str:
        """« Denis (empreinte 1) » ou « empreinte 4 (sans nom dans config.yaml) »."""
        name = self.name_of(finger_id)
        if name is None:
            return f"empreinte {finger_id} (sans nom dans config.yaml)"
        return f"{name} (empreinte {finger_id})"
