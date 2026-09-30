"""Aiguillage des commandes autorisées vers leur exécutant (étapes 11 et 15b)."""

from __future__ import annotations

import logging

from voice_server.home_control import HomeController
from voice_server.machine_control import Command, MachineCommand, MachineController

logger = logging.getLogger(__name__)


class CommandRouter:
    """Domotique vers HomeController, machines vers MachineController."""

    def __init__(self, home: HomeController | None, machines: MachineController | None) -> None:
        """
        Args:
            home: domotique ; None si le serveur n'a pas de lien MQTT.
            machines: agents Linux ; None si le serveur n'a pas de lien MQTT.
        """
        self._home = home
        self._machines = machines

    def execute(self, board: str, command: Command, speaker: str | None, session: str) -> list[str]:
        """Exécute la commande ; rend les destinations (journal, tests)."""
        if isinstance(command, MachineCommand):
            if self._machines is not None:
                return self._machines.execute(board, command, speaker, session)
        elif self._home is not None:
            return self._home.execute(board, command, speaker, session)
        logger.warning("%s : pas d'executant pour %s %s", board, command.action, command.device)
        return []
