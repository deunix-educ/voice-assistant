"""Chargement de la configuration : config.yaml pour les réglages, .env pour les secrets."""

from __future__ import annotations

import glob
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from voice_server.text_normalize import normalize

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioSettings:
    """Format audio unique du système."""

    sample_rate: int
    bits: int
    channels: int
    codec: str
    chunk_ms: int

    @property
    def bytes_per_second(self) -> int:
        """Débit en octets par seconde (32 000 pour 16 kHz, 16 bits, mono)."""
        return self.sample_rate * self.channels * self.bits // 8


@dataclass(frozen=True)
class MqttSettings:
    """Connexion au broker."""

    host: str
    port: int
    keepalive: int
    client_id: str
    topic_prefix: str
    username: str | None
    password: str | None


@dataclass(frozen=True)
class SessionSettings:
    """Réception des sessions de parole."""

    max_seconds: float
    idle_timeout_s: float
    end_grace_s: float


@dataclass(frozen=True)
class SttSettings:
    """Transcription par faster-whisper (étape 6)."""

    model: str               # tiny, base, small, medium…
    language: str
    compute_type: str        # int8 : le plus rapide sur CPU
    beam_size: int           # 1 = décodage glouton, plus rapide ; 5 = plus précis
    no_speech_threshold: float
    model_dir: Path          # dossier du modèle téléchargé par « make models »
    cpu_threads: int = 0     # fils de calcul de Whisper ; 0 : défaut de la bibliothèque (4)
    min_rms_dbfs: float = -58.0  # session plus faible : silence, Whisper n'est pas appelé (F4, sans VAD)


@dataclass(frozen=True)
class TtsSettings:
    """Synthèse vocale par Piper (étape 6)."""

    voice: str
    length_scale: float      # > 1 : débit plus lent
    model_path: Path         # fichier .onnx, accompagné de son .onnx.json


@dataclass(frozen=True)
class HomeSettings:
    """Domotique (étape 11) : pièces connues et pièce de chaque carte."""

    topic_prefix: str            # commandes publiées sur <préfixe>/<pièce>/<appareil>/set
    rooms: dict[str, str]        # nom dit à voix haute → complément : « salon » → « du salon »
    boards: dict[str, str]       # carte → pièce où elle se trouve (« ici », pièce par défaut)


@dataclass(frozen=True)
class AccessSettings:
    """Droits par personne (étape 14). La personne est celle dont le doigt a ouvert la session (F4)."""

    unknown_level: str                   # bouton PTT, ou empreinte sans nom dans fingerprint.users
    default_level: str                   # personne nommée par un doigt, sans niveau dans users
    users: dict[str, str]                # nom de la personne → complet | standard | limite
    rules: dict[str, dict[str, str]]     # appareil → action → niveau minimal
    confirm: tuple[str, ...]             # « door.open » : demande « oui » avant d'exécuter
    confirm_timeout_s: float


@dataclass(frozen=True)
class MachineAction:
    """Une action d'agent Linux telle qu'on la dit (étape 15b)."""

    words: tuple[str, ...]       # mots normalisés qui la désignent : « eteins », « arrete »
    does: str                    # début de la réponse : « J'éteins »
    verb: str                    # infinitif, pour les questions et refus : « éteindre »


@dataclass(frozen=True)
class Machine:
    """Une machine du réseau, commandée par son agent (étape 15b)."""

    names: tuple[str, ...]       # comment on l'appelle : « pc du bureau », « ordinateur du bureau »
    name: str                    # comment l'assistant la nomme : « le PC du bureau »
    mac: str                     # Wake-on-LAN (« allume ») ; vide : pas de réveil à distance


@dataclass(frozen=True)
class MachinesSettings:
    """Machines Linux commandées par un agent MQTT (étape 15b)."""

    topic_prefix: str            # <préfixe>/<machine>/state, command, result
    broadcast: str               # adresse de diffusion du paquet Wake-on-LAN
    actions: dict[str, MachineAction]   # identifiant (shutdown...) → mots et phrases
    machines: dict[str, Machine]        # identifiant de l'agent (pc-bureau) → noms


@dataclass(frozen=True)
class FingerprintSettings:
    """Capteur d'empreinte (branche fingerprint, étape F2) : à qui appartient chaque empreinte."""

    users: dict[int, str]        # emplacement dans le capteur → nom (les niveaux de droits : access.users)


@dataclass(frozen=True)
class Settings:
    """Configuration complète du serveur."""

    audio: AudioSettings
    mqtt: MqttSettings
    sessions: SessionSettings
    stt: SttSettings
    tts: TtsSettings
    home: HomeSettings
    access: AccessSettings
    machines: MachinesSettings
    fingerprint: FingerprintSettings
    recordings_dir: Path
    models_dir: Path
    log_level: str
    log_format: str
    log_file: Path | None   # copie tournante du journal (étape 15), None : écran seul


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    """Rend une section du YAML, avec une erreur claire si elle manque."""
    value = data.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"configuration : section « {name} » absente ou invalide")
    return value  # type: ignore[return-value]


def _key(value: object) -> str:
    """Clé YAML en texte ; YAML 1.1 lit on/off/yes/no nus comme des booléens : on les rend tels quels."""
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def _machines(data: dict[str, Any]) -> MachinesSettings:
    """Section « machines » (étape 15b), facultative : sans elle, aucune machine."""
    section: dict[str, Any] = data.get("machines") or {}
    actions = {
        str(action): MachineAction(
            words=tuple(normalize(str(word)) for word in spec["words"]),
            does=str(spec["does"]),
            verb=str(spec["verb"]),
        )
        for action, spec in dict(section.get("actions") or {}).items()
    }
    machines = {
        str(machine): Machine(
            names=tuple(normalize(str(name)) for name in spec["names"]),
            name=str(spec["name"]),
            mac=str(spec.get("mac") or ""),
        )
        for machine, spec in dict(section.get("list") or {}).items()
    }
    return MachinesSettings(
        topic_prefix=str(section.get("topic_prefix", "agent")),
        broadcast=str(section.get("broadcast", "255.255.255.255")),
        actions=actions,
        machines=machines,
    )


def _fingerprint(data: dict[str, Any]) -> FingerprintSettings:
    """Section « fingerprint » (branche fingerprint), facultative : sans elle, aucune empreinte nommée."""
    section: dict[str, Any] = data.get("fingerprint") or {}
    users: dict[int, str] = {}
    for finger_id, name in dict(section.get("users") or {}).items():
        try:
            number = int(finger_id)
        except (TypeError, ValueError):
            raise ValueError(f"configuration, fingerprint.users : « {finger_id} » n'est pas un numero d'empreinte") from None
        if number < 0 or not str(name).strip():
            raise ValueError(f"configuration, fingerprint.users : empreinte {finger_id} : numero >= 0 et nom non vide requis")
        users[number] = str(name).strip()
    return FingerprintSettings(users=users)


class _ConfigReader:
    """Lit config.yaml et les fichiers de sa clé « include », et les réunit en un seul dictionnaire.

    Règles (volontairement simples, pour qu'un fichier de plus ne change jamais en silence une valeur) :
      - les mappings s'additionnent : « home.rooms » peut venir de plusieurs fichiers ;
      - toute autre valeur (texte, nombre, liste) définie dans deux fichiers est une erreur qui les nomme ;
      - « include » est un chemin ou une liste de chemins, relatifs au fichier qui les inclut ; les motifs
        (« conf/machines/*.yaml », « ** ») sont admis, triés par nom ; un fichier peut en inclure d'autres.
    """

    MAX_DEPTH = 8

    def __init__(self, root: Path) -> None:
        self._root = root
        self.merged: dict[str, Any] = {}
        self._origins: dict[tuple[str, ...], Path] = {}   # clé → fichier qui l'a définie
        self._loaded: set[Path] = set()

    def _name(self, path: Path) -> str:
        """Nom court d'un fichier : relatif au dossier de config.yaml quand c'est possible."""
        try:
            return str(path.relative_to(self._root))
        except ValueError:
            return str(path)

    def _expand(self, base: Path, pattern: str) -> list[Path]:
        """Fichiers désignés par un élément d'« include » ; un chemin sans motif doit exister."""
        candidate = Path(pattern)
        full = candidate if candidate.is_absolute() else base / candidate
        if not any(char in pattern for char in "*?["):
            if not full.is_file():
                raise ValueError(f"include : fichier introuvable : {self._name(full)}")
            return [full.resolve()]
        found = sorted(Path(name).resolve() for name in glob.glob(str(full), recursive=True) if Path(name).is_file())
        if not found:
            logger.warning("include : le motif %s ne désigne aucun fichier", pattern)
        return found

    def _merge(self, target: dict[Any, Any], source: dict[Any, Any], origin: Path, prefix: tuple[str, ...]) -> None:
        for key, value in source.items():
            where = (*prefix, _key(key))
            if key in target:
                first = self._origins[where]
                if not (isinstance(value, dict) and isinstance(target[key], dict)):
                    raise ValueError(f"« {'.'.join(where)} » est défini à la fois dans {self._name(first)} "
                                     f"et dans {self._name(origin)}")
            elif isinstance(value, dict):
                target[key] = {}
                self._origins[where] = origin
            else:
                target[key] = value
                self._origins[where] = origin
                continue
            self._merge(target[key], value, origin, where)

    def load(self, path: Path, chain: tuple[Path, ...] = ()) -> None:
        resolved = path.resolve()
        if resolved in chain:
            loop = " → ".join(self._name(item) for item in (*chain, resolved))
            raise ValueError(f"include : boucle d'inclusion ({loop})")
        if len(chain) >= self.MAX_DEPTH:
            raise ValueError(f"include : plus de {self.MAX_DEPTH} niveaux d'inclusion à partir de {self._name(resolved)}")
        if resolved in self._loaded:   # deux motifs qui se recouvrent : un fichier n'est lu qu'une fois
            return
        self._loaded.add(resolved)

        with resolved.open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError(f"{self._name(resolved)} : un mapping YAML est attendu à la racine du fichier")
        raw = data.pop("include", None)
        patterns = [raw] if isinstance(raw, str) else ([] if raw is None else raw)
        if not isinstance(patterns, list) or not all(isinstance(item, str) and item for item in patterns):
            raise ValueError(f"{self._name(resolved)} : « include » doit être un chemin ou une liste de chemins")

        self._merge(self.merged, data, resolved, ())
        for pattern in patterns:
            for included in self._expand(resolved.parent, pattern):
                self.load(included, (*chain, resolved))


def read_config(path: Path) -> dict[str, Any]:
    """Lit config.yaml et ses fichiers inclus (clé « include », motifs admis) : un seul dictionnaire."""
    reader = _ConfigReader(path.resolve().parent)
    reader.load(path)
    return reader.merged


def load_settings(path: Path) -> Settings:
    """Lit config.yaml (et les fichiers qu'il inclut), puis le .env voisin s'il existe (identifiants MQTT)."""
    data = read_config(path)

    # Les secrets ne sont jamais dans config.yaml : ils viennent de l'environnement.
    load_dotenv(path.parent / ".env")

    audio = _section(data, "audio")
    mqtt = _section(data, "mqtt")
    sessions = _section(data, "sessions")
    storage = _section(data, "storage")
    logging_cfg = _section(data, "logging")
    stt = _section(data, "stt")
    tts = _section(data, "tts")
    home = _section(data, "home")
    access = _section(data, "access")

    base = path.parent  # les chemins relatifs le sont par rapport à config.yaml
    models_dir = (base / str(storage["models_dir"])).resolve()
    return Settings(
        audio=AudioSettings(
            sample_rate=int(audio["sample_rate"]),
            bits=int(audio["bits"]),
            channels=int(audio["channels"]),
            codec=str(audio["codec"]),
            chunk_ms=int(audio["chunk_ms"]),
        ),
        mqtt=MqttSettings(
            host=str(mqtt["host"]),
            port=int(mqtt["port"]),
            keepalive=int(mqtt["keepalive"]),
            client_id=str(mqtt["client_id"]),
            topic_prefix=str(mqtt["topic_prefix"]),
            username=os.environ.get("MQTT_USERNAME") or None,
            password=os.environ.get("MQTT_PASSWORD") or None,
        ),
        sessions=SessionSettings(
            max_seconds=float(sessions["max_seconds"]),
            idle_timeout_s=float(sessions["idle_timeout_s"]),
            end_grace_s=float(sessions["end_grace_s"]),
        ),
        stt=SttSettings(
            model=str(stt["model"]),
            language=str(stt["language"]),
            compute_type=str(stt["compute_type"]),
            beam_size=int(stt["beam_size"]),
            no_speech_threshold=float(stt["no_speech_threshold"]),
            model_dir=models_dir / "whisper" / str(stt["model"]),
            cpu_threads=int(stt.get("cpu_threads", 0)),
            min_rms_dbfs=float(stt.get("min_rms_dbfs", -58.0)),
        ),
        tts=TtsSettings(
            voice=str(tts["voice"]),
            length_scale=float(tts["length_scale"]),
            model_path=models_dir / "piper" / f"{tts['voice']}.onnx",
        ),
        home=HomeSettings(
            topic_prefix=str(home["topic_prefix"]),
            rooms={str(name): str(complement) for name, complement in dict(home["rooms"]).items()},
            boards={str(board): str(room) for board, room in dict(home["boards"]).items()},
        ),
        access=AccessSettings(
            unknown_level=str(access["unknown"]),
            default_level=str(access["default"]),
            users={str(name): str(level) for name, level in dict(access.get("users") or {}).items()},
            rules={str(device): {_key(action): str(level) for action, level in dict(actions).items()}
                   for device, actions in dict(access["rules"]).items()},
            confirm=tuple(str(item) for item in access.get("confirm") or ()),
            confirm_timeout_s=float(access["confirm_timeout_s"]),
        ),
        machines=_machines(data),
        fingerprint=_fingerprint(data),
        recordings_dir=(base / str(storage["recordings_dir"])).resolve(),
        models_dir=models_dir,
        log_level=str(logging_cfg["level"]),
        log_format=str(logging_cfg["format"]),
        log_file=(base / str(logging_cfg["file"])).resolve() if logging_cfg.get("file") else None,
    )
