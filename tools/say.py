#!/usr/bin/env python3
"""Fait dire une phrase par une voix Piper, en WAV et MP3 : pour la jouer au micro depuis un téléphone.

Sert à tester l'identification (étape 9) et les droits (étape 14) sans seconde
personne. Les voix sont téléchargées au premier usage dans server/models/piper/.
Voix françaises : fr_FR-siwis-medium, fr_FR-tom-medium, fr_FR-upmc-medium
(SPEAKER 0 = Jessica, 1 = Pierre), fr_FR-gilles-low.

Mot de réveil (--wake) : une voix française le dit « à la française » et le
modèle ne le reconnaît pas (score 0,00 mesuré, même en graphie phonétique ;
les rares réussites retombent à 0 au moindre bruit). Il est donc dit par une
voix ANGLAISE (--wake-voice), puis la voix choisie dit la commande. Le serveur
identifie la personne sur la dernière intervention : c'est bien la voix de la
commande qui compte. Mais les 0,5 s d'audio gardées avant la détection
contiennent la fin du mot : une commande d'au moins 2 s garde l'identification
nette (« Ouvre la porte du salon, s'il te plaît. »). Le fichier est vérifié par
le vrai détecteur de mot de réveil.

0,5 s de silence avant et après : le temps d'appuyer sur le bouton, ou de laisser
le mot de réveil être reconnu. Sortie en 16 kHz, comme le micro.

Exemples :
    python3 tools/say.py "Oui, je confirme." --voice fr_FR-upmc-medium --speaker 1
    python3 tools/say.py "Ouvre la porte du salon, s'il te plaît." --wake "Hey Mycroft"
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import wave
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from voice_server.audio_resampler import AudioResampler  # noqa: E402
from voice_server.settings import Settings, load_settings  # noqa: E402

logger = logging.getLogger("say")

PAUSE_AFTER_WAKE_S = 0.6  # pause naturelle entre « Hey Mycroft » et la commande
EDGE_SILENCE_S = 0.5


def synthesize(voices_dir: Path, name: str, text: str, speaker: int | None, resampler: AudioResampler) -> bytes:
    """Texte → PCM 16 kHz, crête -3 dBFS ; télécharge la voix si besoin."""
    from piper import PiperVoice, SynthesisConfig
    from piper.download_voices import download_voice

    model = voices_dir / f"{name}.onnx"
    if not model.exists():
        logger.info("telechargement de la voix %s (une fois, ~60 Mo)…", name)
        voices_dir.mkdir(parents=True, exist_ok=True)
        download_voice(name, voices_dir)
    voice = PiperVoice.load(model)
    import numpy as np

    chunks = list(voice.synthesize(text, syn_config=SynthesisConfig(speaker_id=speaker)))
    audio = np.concatenate([chunk.audio_float_array for chunk in chunks])
    return resampler.convert(audio, chunks[0].sample_rate)  # normalisé : les deux voix au même niveau


def check_wake(settings: Settings, pcm: bytes) -> bool:
    """Passe le fichier dans le vrai détecteur du serveur ; False : le mot ne sera pas reconnu."""
    try:
        from voice_server.wake_word import WakeWordDetector

        detector = WakeWordDetector(settings.wake)
    except (ImportError, FileNotFoundError) as error:
        logger.warning("verification du mot de reveil impossible : %s", error)
        return True
    chunk = settings.audio.bytes_per_second * settings.audio.chunk_ms // 1000
    for start in range(0, len(pcm) - chunk + 1, chunk):
        detection = detector.process(pcm[start:start + chunk])
        if detection is not None:
            logger.info("mot de reveil reconnu : %s (%.2f)", detection.word, detection.score)
            return True
    logger.error("mot de reveil NON reconnu par le detecteur : essayez une autre --wake-voice")
    return False


def main(argv: list[str]) -> int:
    """Synthétise la phrase et écrit WAV (+ MP3 si ffmpeg est présent)."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("text", help="phrase a dire")
    parser.add_argument("--voice", default="fr_FR-tom-medium", help="voix Piper")
    parser.add_argument("--speaker", type=int, help="locuteur, pour les voix a plusieurs locuteurs")
    parser.add_argument("--wake", help="mot de reveil dit avant la phrase, par --wake-voice (ex. 'Hey Mycroft')")
    parser.add_argument("--wake-voice", default="en_GB-alan-medium", help="voix anglaise du mot de reveil")
    parser.add_argument("--out", type=Path, default=Path("recordings/voix-test/phrase.wav"))
    parser.add_argument("--config", type=Path, default=SERVER_DIR / "config.yaml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    settings = load_settings(args.config)
    voices_dir = settings.models_dir / "piper"
    resampler = AudioResampler(settings.audio)
    rate = settings.audio.sample_rate
    try:
        pieces = []
        if args.wake:
            pieces.append(synthesize(voices_dir, args.wake_voice, args.wake, None, resampler))
            pieces.append(bytes(2 * int(PAUSE_AFTER_WAKE_S * rate)))
        pieces.append(synthesize(voices_dir, args.voice, args.text, args.speaker, resampler))
    except ImportError as error:
        logger.error("%s : lancez 'make install-speech'", error)
        return 1
    edge = bytes(2 * int(EDGE_SILENCE_S * rate))
    pcm = edge + b"".join(pieces) + edge

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(args.out), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    said = f"{args.wake} ({args.wake_voice}), {args.text} ({args.voice})" if args.wake else f"{args.text} ({args.voice})"
    logger.info("« %s », %.1f s -> %s", said, len(pcm) / 2 / rate, args.out)

    if args.wake and not check_wake(settings, pcm):
        return 1
    if shutil.which("ffmpeg"):
        mp3 = args.out.with_suffix(".mp3")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(args.out), "-codec:a", "libmp3lame",
                        "-q:a", "2", str(mp3)], check=True)
        logger.info("pour le telephone : %s", mp3)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
