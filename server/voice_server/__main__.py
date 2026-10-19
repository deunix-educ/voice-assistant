"""Point d'entrée du serveur vocal : python -m voice_server --config config.yaml

Étape 4 : reçoit les sessions de parole des ESP32 par MQTT et les écrit en WAV.
Étape 5 : avec --echo, renvoie chaque session à la carte, qui la joue.
Étape 6 : par défaut, répond à chaque session : faster-whisper → Assistant → Piper.
Étape 7 : la VAD (Silero) retire les silences et écarte les sessions sans parole.
Étape 8 : la diarisation (pyannote) découpe la parole par locuteur.
Étape 9 : chaque locuteur est comparé aux profils vocaux : un nom, ou « Inconnu ».
Étape 10 : qui a dit quoi, publié sur voice/<carte>/transcript ; l'assistant appelle par son nom.
Étape 11 : domotique : « ferme les volets du salon » → home/salon/shutter/set (+ la carte si c'est sa pièce).
Étape 14 : droits par profil (complet / standard / limite), confirmation des actions sensibles.
Étape 15 : présence + testament (les cartes coupent leur micro si le serveur meurt), journal fichier.
Étape 16 : latence : empreinte directe pour une parole courte, cache de synthèse, envois sans attente.
Étape 15b : machines Linux : « éteins le PC du bureau » → agent/pc-bureau/command ; « allume » → Wake-on-LAN.
Branche fingerprint, F3 : un doigt reconnu par le capteur R503 ouvre la session (la carte l'annonce dans START).
Branche fingerprint, F4 : les droits suivent le doigt ; plus de VAD, de diarisation, de voix ni de mot de réveil.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import queue
import signal
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import yaml

from voice_server.audio_resampler import AudioResampler
from voice_server.audio_sender import AudioSender
from voice_server.fingerprint_book import FingerprintBook
from voice_server.mqtt_link import MqttLink
from voice_server.session_manager import Outcome, SessionManager, SessionResult
from voice_server.settings import Settings, load_settings

logger = logging.getLogger("voice_server")

# Code de sortie quand la configuration ou les modèles sont à corriger (EX_CONFIG de sysexits.h). Le service
# systemd (server/voice-server.service, RestartPreventExitStatus) ne relance pas le serveur dans ce cas.
EXIT_SETUP = 78

SWEEP_PERIOD_S = 0.2  # fréquence de vérification des sessions terminées ou muettes
LOG_FILE_BYTES = 1_000_000  # journal fichier : 1 Mo par fichier...
LOG_FILE_COUNT = 3          # ... et 3 anciens gardés : 4 Mo au plus


def setup_logging(settings: Settings) -> None:
    """Journal à l'écran, et copie dans un fichier tournant si config.yaml le demande."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.log_file is not None:
        settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        # Tournant : au-delà de 1 Mo, le fichier est renommé .1 et un nouveau commence ;
        # le disque (carte SD d'un Raspberry Pi) ne se remplit jamais.
        handlers.append(logging.handlers.RotatingFileHandler(
            settings.log_file, maxBytes=LOG_FILE_BYTES, backupCount=LOG_FILE_COUNT, encoding="utf-8"))
    logging.basicConfig(level=settings.log_level, format=settings.log_format, handlers=handlers, force=True)


def log_state(device: str, payload: bytes) -> None:
    """Journalise l'état publié par une carte (retenu : reçu dès l'abonnement)."""
    try:
        state = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if state.get("status") == "online":
        # La durée de fonctionnement distingue une republication périodique d'un redémarrage.
        logger.info("%s : en ligne depuis %s s, IP %s, signal %s dBm", device,
                    state.get("uptime_s"), state.get("ip"), state.get("rssi"))
    else:
        logger.warning("%s : %s", device, state.get("status"))


def make_echo(resampler: AudioResampler, sender: AudioSender) -> Callable[[SessionResult], object]:
    """Mode écho (étape 5) : la carte rejoue ce qu'elle vient d'enregistrer."""
    def echo(result: SessionResult) -> None:
        if result.duration_s < 0.2:
            logger.warning("%s : session %s trop courte pour l'echo (%.2f s) : bouton relache trop tot ?",
                           result.device, result.session_id, result.duration_s)
            return
        sender.send(result.device, resampler.from_wav(result.path), f"echo-{result.session_id}")
    return echo


def make_assistant(settings: Settings, resampler: AudioResampler, sender: AudioSender,
                   link: MqttLink | None = None) -> Callable[[SessionResult], object]:
    """Mode assistant (étapes 6-14, F4) : charge Whisper et Piper, puis répond à chaque session."""
    # Imports tardifs : le mode écho fonctionne sans l'extra « speech ».
    from voice_server.access_control import AccessPolicy
    from voice_server.assistant import Assistant
    from voice_server.command_router import CommandRouter
    from voice_server.home_control import HomeController
    from voice_server.machine_control import MachineController
    from voice_server.speech_to_text import SpeechToText
    from voice_server.text_to_speech import TextToSpeech
    from voice_server.voice_pipeline import VoicePipeline

    try:
        stt = SpeechToText(settings.stt, settings.audio.sample_rate)
        tts = TextToSpeech(settings.tts, resampler, settings.audio.sample_rate)
    except ImportError as error:
        raise ImportError(f"{error} : lancez 'make install-speech'") from error

    machines = None
    if link is not None:
        machines = MachineController(settings.machines, link)
        # États retenus des agents : reçus dès l'abonnement, même démarrés avant le serveur.
        link.on_topic(f"{settings.machines.topic_prefix}/+/state", machines.on_state)
        link.on_topic(f"{settings.machines.topic_prefix}/+/result", machines.on_result)
        logger.info("machines : %s", ", ".join(settings.machines.machines) or "aucune (section machines)")

    fingers = FingerprintBook(settings.fingerprint)
    policy = AccessPolicy(settings.access, settings.home, settings.machines)
    for warning in policy.audit(fingers.people()):
        logger.warning("droits : %s", warning)
    logger.info("doigts : %s", ", ".join(fingers.describe(finger_id) for finger_id in fingers.named_ids())
                or "aucun nomme (make finger-enroll NAME=...)")

    pipeline = VoicePipeline(
        stt=stt,
        assistant=Assistant(home=settings.home, machines=machines),
        tts=tts,
        player=sender,
        fingers=fingers,
        publisher=link,
        controller=CommandRouter(HomeController(settings.home, link), machines) if link is not None else None,
        policy=policy,
        silence_rms_dbfs=settings.stt.min_rms_dbfs,
    )
    return pipeline.handle


def worker(jobs: "queue.Queue[SessionResult | None]", handle: Callable[[SessionResult], object]) -> None:
    """Traite les sessions une à une, hors du fil réseau : chacune dure des secondes."""
    while True:
        result = jobs.get()
        if result is None:
            return
        try:
            handle(result)
        except Exception:  # une session ratée ne doit jamais arrêter le serveur
            logger.exception("%s : echec du traitement de %s", result.device, result.path.name)


def main(argv: list[str]) -> int:
    """Lance le serveur jusqu'à Ctrl-C ou SIGTERM. Rend EXIT_SETUP si la configuration ou les modèles sont à corriger."""
    parser = argparse.ArgumentParser(description="serveur de l'assistant vocal")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config.yaml")
    parser.add_argument("--echo", action="store_true",
                        help="renvoie chaque session a la carte, qui la joue (etape 5), sans modeles")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config)
    except (ValueError, OSError, yaml.YAMLError) as error:
        # Le journal n'est pas encore réglé (il dépend de la configuration) : message direct, sans trace d'appels.
        print(f"configuration invalide : {error}", file=sys.stderr)
        example = args.config.with_name(f"{args.config.stem}.example{args.config.suffix}")
        if isinstance(error, FileNotFoundError) and not args.config.exists() and example.exists():
            print(f"{args.config} n'existe pas : « make config » le crée depuis {example.name}", file=sys.stderr)
        return EXIT_SETUP
    setup_logging(settings)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)  # une ligne par transcription sinon
    logger.info("enregistrements dans %s", settings.recordings_dir)
    if settings.log_file is not None:
        logger.info("journal copie dans %s", settings.log_file)
    if settings.mqtt.username is None:
        logger.warning("pas d'identifiants MQTT (server/.env) : refuse par un broker securise (etape 15)")

    link = MqttLink(settings.mqtt, presence=True)
    resampler = AudioResampler(settings.audio)
    sender = AudioSender(link, settings.audio)
    if args.echo:
        logger.info("mode echo : chaque session sera rejouee par la carte")
        handle = make_echo(resampler, sender)
    else:
        try:
            handle = make_assistant(settings, resampler, sender, link)
        except ImportError as error:
            logger.error("%s", error)
            return EXIT_SETUP
        except FileNotFoundError as error:
            logger.error("%s : lancez 'make models'", error)
            return EXIT_SETUP
        logger.info("mode assistant : posez un doigt enregistre (ou appuyez sur le bouton) et parlez")

    jobs: "queue.Queue[SessionResult | None]" = queue.Queue()

    def on_finished(result: SessionResult) -> None:
        if result.outcome is not Outcome.INTERRUPTED:
            jobs.put(result)  # rend la main aussitôt : le traitement dure des secondes

    sessions = SessionManager(settings.audio, settings.sessions, settings.recordings_dir,
                              on_finished=on_finished,
                              describe_finger=FingerprintBook(settings.fingerprint).describe)
    threading.Thread(target=worker, args=(jobs, handle), daemon=True).start()

    link.on("state", log_state)
    link.on("event", sessions.on_event)
    link.on("audio/in", sessions.on_audio)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    link.start()
    try:
        while not stop.wait(SWEEP_PERIOD_S):
            sessions.sweep()
    except KeyboardInterrupt:
        pass
    finally:
        jobs.put(None)
        link.stop()
        logger.info("arret du serveur")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
