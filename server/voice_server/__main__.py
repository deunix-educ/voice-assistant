"""Point d'entrée du serveur vocal : python -m voice_server --config config.yaml

Étape 4 : reçoit les sessions de parole des ESP32 par MQTT et les écrit en WAV.
Étape 5 : avec --echo, renvoie chaque session à la carte, qui la joue.
Étape 6 : par défaut, répond à chaque session : faster-whisper → Assistant → Piper.
Étape 7 : la VAD (Silero) retire les silences et écarte les sessions sans parole.
Étape 8 : la diarisation (pyannote) découpe la parole par locuteur.
Étape 9 : chaque locuteur est comparé aux profils vocaux : un nom, ou « Inconnu ».
Étape 10 : qui a dit quoi, publié sur voice/<carte>/transcript ; l'assistant appelle par son nom.
Étape 11 : domotique : « ferme les volets du salon » → home/salon/shutter/set (+ la carte si c'est sa pièce).
Étape 13 : mains libres : « Alexa, allume la lumière », sans le bouton.
Étape 14 : droits par profil (complet / standard / limite), confirmation des actions sensibles.
Étape 15 : présence + testament (les cartes coupent leur micro si le serveur meurt), journal fichier.
Étape 15b : machines Linux : « éteins le PC du bureau » → agent/pc-bureau/command ; « allume » → Wake-on-LAN.
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
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from voice_server.audio_resampler import AudioResampler
from voice_server.audio_sender import AudioSender
from voice_server.mqtt_link import MqttLink
from voice_server.session_manager import Outcome, SessionManager, SessionResult
from voice_server.settings import Settings, load_settings

if TYPE_CHECKING:
    from voice_server.hands_free import HandsFreeListener

logger = logging.getLogger("voice_server")

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
    """Mode assistant (étapes 6-14) : charge les modèles, puis répond à chaque session."""
    # Imports tardifs : le mode écho fonctionne sans l'extra « speech ».
    from voice_server.access_control import AccessPolicy
    from voice_server.assistant import Assistant
    from voice_server.command_router import CommandRouter
    from voice_server.diarization import Diarizer
    from voice_server.home_control import HomeController
    from voice_server.machine_control import MachineController
    from voice_server.speaker_identifier import SpeakerIdentifier
    from voice_server.speech_to_text import SpeechToText
    from voice_server.text_to_speech import TextToSpeech
    from voice_server.voice_activity import VoiceActivityDetector
    from voice_server.voice_pipeline import VoicePipeline

    try:
        stt = SpeechToText(settings.stt, settings.audio.sample_rate)
        tts = TextToSpeech(settings.tts, resampler, settings.audio.sample_rate)
        vad = VoiceActivityDetector(settings.vad, settings.audio.sample_rate)
    except ImportError as error:
        raise ImportError(f"{error} : lancez 'make install-speech'") from error

    diarizer = None
    identifier = None
    if settings.diarization.enabled:
        try:
            diarizer = Diarizer(settings.diarization, settings.audio.sample_rate)
        except ImportError as error:
            raise ImportError(f"{error} : lancez 'make install-diarization', ou "
                              "diarization.enabled: false dans config.yaml") from error
        identifier = SpeakerIdentifier(settings.speaker, settings.diarization.model)
    else:
        logger.info("diarisation desactivee (config.yaml) : pas d'identification du locuteur")

    machines = None
    if link is not None:
        machines = MachineController(settings.machines, link)
        # États retenus des agents : reçus dès l'abonnement, même démarrés avant le serveur.
        link.on_topic(f"{settings.machines.topic_prefix}/+/state", machines.on_state)
        link.on_topic(f"{settings.machines.topic_prefix}/+/result", machines.on_result)
        logger.info("machines : %s", ", ".join(settings.machines.machines) or "aucune (config.yaml)")

    pipeline = VoicePipeline(
        vad=vad,
        stt=stt,
        assistant=Assistant(home=settings.home, machines=machines),
        tts=tts,
        player=sender,
        diarizer=diarizer,
        identifier=identifier,
        publisher=link,
        controller=CommandRouter(HomeController(settings.home, link), machines) if link is not None else None,
        policy=AccessPolicy(settings.access, settings.home, settings.machines),
    )
    return pipeline.handle


def make_listener(settings: Settings, link: MqttLink,
                  on_command: Callable[[SessionResult], None]) -> "HandsFreeListener":
    """Écoute mains libres (étape 13) : un détecteur de mot de réveil par carte."""
    from voice_server.hands_free import HandsFreeListener
    from voice_server.voice_activity import VoiceActivityDetector
    from voice_server.wake_word import WakeWordDetector

    WakeWordDetector(settings.wake)  # vérifie modèles et installation dès le démarrage
    return HandsFreeListener(
        settings.wake, settings.audio, VoiceActivityDetector(settings.vad, settings.audio.sample_rate),
        settings.recordings_dir, link, on_command,
        detector_factory=lambda device: WakeWordDetector(settings.wake, device),
    )


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
    """Lance le serveur jusqu'à Ctrl-C."""
    parser = argparse.ArgumentParser(description="serveur de l'assistant vocal")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config.yaml")
    parser.add_argument("--echo", action="store_true",
                        help="renvoie chaque session a la carte, qui la joue (etape 5), sans modeles")
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
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
            return 1
        except FileNotFoundError as error:
            logger.error("%s (ou diarization.enabled: false dans config.yaml)", error)
            return 1
        logger.info("mode assistant : appuyez sur le bouton et posez une question")

    jobs: "queue.Queue[SessionResult | None]" = queue.Queue()

    def on_finished(result: SessionResult) -> None:
        if result.outcome is not Outcome.INTERRUPTED:
            jobs.put(result)  # rend la main aussitôt : le traitement dure des secondes

    sessions = SessionManager(settings.audio, settings.sessions, settings.recordings_dir,
                              on_finished=on_finished)
    listener = None
    if settings.wake.enabled and not args.echo:
        try:
            listener = make_listener(settings, link, on_finished)
        except (ImportError, FileNotFoundError) as error:
            logger.error("%s : lancez 'make install-wakeword' et 'make models', "
                         "ou wake.enabled: false dans config.yaml", error)
            return 1
        logger.info("mains libres : dites %s", ", ".join(f"« {w} »" for w in settings.wake.words))
    threading.Thread(target=worker, args=(jobs, handle), daemon=True).start()

    def on_state(device: str, payload: bytes) -> None:
        log_state(device, payload)
        if listener is not None:
            listener.on_state(device, payload)

    def on_event(device: str, payload: bytes) -> None:
        sessions.on_event(device, payload)
        if listener is not None:
            listener.on_event(device, payload)

    link.on("state", on_state)
    link.on("event", on_event)
    link.on("audio/in", sessions.on_audio)
    if listener is not None:
        link.on("audio/stream", listener.on_stream)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    link.start()
    try:
        while not stop.wait(SWEEP_PERIOD_S):
            sessions.sweep()
            if listener is not None:
                listener.sweep()
    except KeyboardInterrupt:
        pass
    finally:
        jobs.put(None)
        if listener is not None:
            listener.stop()  # les cartes cessent d'envoyer leur micro
            time.sleep(0.5)  # laisse partir ces messages avant la déconnexion
        link.stop()
        logger.info("arret du serveur")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
