#!/usr/bin/env python3
"""Agent de l'assistant vocal pour une machine Linux du réseau (étape 15b).

L'agent se connecte au broker MQTT et attend des NOMS d'action sur
agent/<id>/command. Il n'exécute que les actions de SON fichier de configuration
(/etc/voice-agent/agent.yaml), chacune étant une commande fixe, sans shell et
sans argument venu du réseau : un message forgé ne peut rien lancer d'autre.

    agent/<id>/state    {"status":"online","actions":[...]}  retenu ; testament « offline »
    agent/<id>/command  {"action":"shutdown","id":"...","by":"Denis"}  (QoS 1)
    agent/<id>/result   {"action":"shutdown","id":"...","ok":true,"code":0,"output":"..."}

Session propre : une commande envoyée pendant que la machine était éteinte n'est
jamais exécutée à son retour.

Autonome : il ne dépend que de paho-mqtt et PyYAML, pas du serveur.

Exemples :
    python3 voice_agent.py --config /etc/voice-agent/agent.yaml
    make agent-run          # sur le PC de développement, en mode essai (dry_run)
"""

from __future__ import annotations

import argparse
import json
import logging
import queue
import re
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import yaml
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

logger = logging.getLogger("voice_agent")

DEFAULT_TIMEOUT_S = 60.0
OUTPUT_MAX_CHARS = 300      # fin de la sortie de la commande, renvoyée au serveur
KEEPALIVE_S = 30            # hors ligne annoncé 45 s au plus après une coupure brutale
AGENT_ID = re.compile(r"[a-z0-9][a-z0-9-]*")

# Lance une commande : le même contrat que subprocess.run, remplaçable dans les tests.
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class ConfigError(ValueError):
    """Fichier de configuration de l'agent invalide."""


@dataclass(frozen=True)
class Action:
    """Une action permise : une commande fixe."""

    command: tuple[str, ...]   # programme et arguments, SANS shell : pas de ; | $() possibles
    timeout_s: float


@dataclass(frozen=True)
class AgentConfig:
    """Configuration de l'agent (agent.yaml)."""

    agent_id: str
    host: str
    port: int
    username: str | None
    password: str | None
    topic_prefix: str
    dry_run: bool              # essais : journalise la commande sans l'exécuter
    actions: dict[str, Action]

    def topic(self, suffix: str) -> str:
        """agent/<id>/<suffixe>."""
        return f"{self.topic_prefix}/{self.agent_id}/{suffix}"


@dataclass(frozen=True)
class Result:
    """Compte rendu d'une action, publié sur agent/<id>/result."""

    action: str
    request_id: str
    ok: bool
    code: int | None           # code de retour ; None si la commande n'a pas pu tourner
    output: str

    def payload(self) -> bytes:
        return json.dumps({"action": self.action, "id": self.request_id, "ok": self.ok,
                           "code": self.code, "output": self.output}, ensure_ascii=False).encode("utf-8")


def load_config(path: Path) -> AgentConfig:
    """Lit et vérifie agent.yaml ; une erreur dit précisément quoi corriger.

    Raises:
        ConfigError: fichier absent ou invalide.
    """
    try:
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"{path} : {error}") from error
    agent_id = str(data.get("id", ""))
    if not AGENT_ID.fullmatch(agent_id):
        raise ConfigError(f"{path} : id « {agent_id} » invalide (minuscules, chiffres, tirets : pc-bureau)")
    broker: dict[str, Any] = data.get("mqtt") or {}
    if not broker.get("host"):
        raise ConfigError(f"{path} : mqtt.host manquant (adresse IP du broker)")

    actions: dict[str, Action] = {}
    for name, spec in dict(data.get("actions") or {}).items():
        command = spec.get("command") if isinstance(spec, dict) else None
        if not isinstance(command, list) or not command or not all(isinstance(part, str) and part
                                                                  for part in command):
            raise ConfigError(f"{path} : action « {name} » : command doit être une liste, "
                              "ex. [systemctl, poweroff] (pas de chaîne shell)")
        timeout_s = float(spec.get("timeout_s", DEFAULT_TIMEOUT_S))
        if timeout_s <= 0:
            raise ConfigError(f"{path} : action « {name} » : timeout_s doit être positif")
        actions[str(name)] = Action(command=tuple(command), timeout_s=timeout_s)

    return AgentConfig(
        agent_id=agent_id,
        host=str(broker["host"]),
        port=int(broker.get("port", 1883)),
        username=str(broker.get("username") or "") or None,
        password=str(broker.get("password") or "") or None,
        topic_prefix=str(broker.get("topic_prefix", "agent")),
        dry_run=bool(data.get("dry_run", False)),
        actions=actions,
    )


def state_payload(config: AgentConfig, online: bool) -> bytes:
    """État retenu : en ligne avec la liste des actions, ou hors ligne."""
    if not online:
        return json.dumps({"status": "offline"}).encode()
    return json.dumps({"status": "online", "actions": sorted(config.actions),
                       "dry_run": config.dry_run}).encode()


def run_action(config: AgentConfig, name: str, request_id: str, runner: Runner = subprocess.run) -> Result:
    """Exécute une action de la liste ; tout autre nom est refusé."""
    action = config.actions.get(name)
    if action is None:
        return Result(name, request_id, False, None, f"action inconnue : {name}")
    shown = " ".join(action.command)
    if config.dry_run:
        logger.info("essai : %s (non execute, dry_run)", shown)
        return Result(name, request_id, True, 0, f"essai : {shown}")
    try:
        completed = runner(list(action.command), capture_output=True, text=True,
                           timeout=action.timeout_s, check=False)
    except FileNotFoundError:
        return Result(name, request_id, False, None, f"programme introuvable : {action.command[0]}")
    except subprocess.TimeoutExpired:
        return Result(name, request_id, False, None, f"arretee apres {action.timeout_s:g} s")
    output = f"{completed.stdout}{completed.stderr}".strip()[-OUTPUT_MAX_CHARS:]
    return Result(name, request_id, completed.returncode == 0, completed.returncode, output)


class VoiceAgent:
    """Client MQTT de l'agent : reçoit les demandes, les exécute une à une, rend compte.

    Les commandes tournent dans un fil de travail, jamais dans le fil réseau : une
    sauvegarde d'une heure ne doit pas couper la connexion au broker.
    """

    def __init__(self, config: AgentConfig, runner: Runner = subprocess.run) -> None:
        self._config = config
        self._runner = runner
        self._jobs: queue.Queue[tuple[str, str] | None] = queue.Queue()
        self._connected = threading.Event()
        # Session propre (clean_session, défaut MQTT 3.1.1) : pas de commande en attente au retour.
        self._client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2,
                                   client_id=f"voice-agent-{config.agent_id}")
        if config.username:
            self._client.username_pw_set(config.username, config.password)
        self._client.will_set(config.topic("state"), state_payload(config, False), qos=1, retain=True)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._worker = threading.Thread(target=self._work, name="actions", daemon=True)

    def start(self) -> None:
        """Connexion (asynchrone, avec reconnexion) et fil de travail."""
        config = self._config
        logger.info("agent %s : broker %s:%d, actions %s%s", config.agent_id, config.host, config.port,
                    ", ".join(sorted(config.actions)) or "aucune", " (ESSAI : rien n'est execute)"
                    if config.dry_run else "")
        if not config.username:
            logger.warning("pas de compte MQTT : tout appareil du reseau peut demander ces actions "
                           "(comptes et droits du broker, etape 15)")
        self._worker.start()
        self._client.connect_async(config.host, config.port, keepalive=KEEPALIVE_S)
        self._client.loop_start()

    def stop(self) -> None:
        """Annonce le départ (un arrêt propre ne déclenche pas le testament), puis se déconnecte."""
        if self._connected.is_set():
            info = self._client.publish(self._config.topic("state"), state_payload(self._config, False),
                                        qos=1, retain=True)
            try:
                info.wait_for_publish(2.0)
            except (RuntimeError, ValueError):
                pass
        self._client.disconnect()
        self._client.loop_stop()
        self._jobs.put(None)

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: mqtt.ConnectFlags,
                    reason_code: ReasonCode, properties: Properties | None) -> None:
        if reason_code.is_failure:
            logger.error("connexion refusee par le broker : %s (compte mqtt.username/password ?)", reason_code)
            return
        self._connected.set()
        client.subscribe(self._config.topic("command"), qos=1)
        client.publish(self._config.topic("state"), state_payload(self._config, True), qos=1, retain=True)
        logger.info("connecte : en attente sur %s", self._config.topic("command"))

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        try:
            request: dict[str, Any] = json.loads(message.payload)
            name = str(request["action"])
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
            logger.warning("demande illisible ignoree : %r", message.payload[:80])
            return
        logger.info("demande %s par %s (carte %s)", name, request.get("by") or "inconnu", request.get("board"))
        self._jobs.put((name, str(request.get("id", ""))))

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            name, request_id = job
            result = run_action(self._config, name, request_id, self._runner)
            log = logger.info if result.ok else logger.warning
            log("%s : %s (code %s)%s", name, "ok" if result.ok else "ECHEC", result.code,
                f" : {result.output}" if result.output else "")
            self._client.publish(self._config.topic("result"), result.payload(), qos=1)


def main(argv: list[str]) -> int:
    """Lance l'agent jusqu'à Ctrl-C ou SIGTERM (arrêt du service)."""
    parser = argparse.ArgumentParser(description="agent de l'assistant vocal pour une machine Linux")
    parser.add_argument("--config", type=Path, default=Path("/etc/voice-agent/agent.yaml"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    try:
        config = load_config(args.config)
    except ConfigError as error:
        logger.error("%s", error)
        return 1

    agent = VoiceAgent(config)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    agent.start()
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        agent.stop()
        logger.info("arret de l'agent")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
