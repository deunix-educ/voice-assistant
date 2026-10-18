#!/usr/bin/env python3
"""Fait dire une phrase par une voix Piper, en WAV et MP3 : pour la jouer au micro depuis un téléphone.

Sert à essayer l'assistant sans parler : on tient un doigt enregistré sur le capteur pendant que le
téléphone joue la phrase (étape F3), ce qui permet aussi de tester les droits des autres personnes
sans qu'elles soient là. Les voix sont téléchargées au premier usage dans server/models/piper/.
Voix françaises : fr_FR-siwis-medium, fr_FR-tom-medium, fr_FR-upmc-medium
(SPEAKER 0 = Jessica, 1 = Pierre), fr_FR-gilles-low.

0,5 s de silence avant et après : le temps d'identifier le doigt, puis de le lever. Sortie en 16 kHz,
comme le micro.

Exemples :
    python3 tools/say.py "Oui, je confirme." --voice fr_FR-upmc-medium --speaker 1
    python3 tools/say.py "Ouvre la porte du salon, s'il te plaît."
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
from voice_server.settings import load_settings  # noqa: E402

logger = logging.getLogger("say")

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


def main(argv: list[str]) -> int:
    """Synthétise la phrase et écrit WAV (+ MP3 si ffmpeg est présent)."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("text", help="phrase a dire")
    parser.add_argument("--voice", default="fr_FR-tom-medium", help="voix Piper")
    parser.add_argument("--speaker", type=int, help="locuteur, pour les voix a plusieurs locuteurs")
    parser.add_argument("--out", type=Path, default=Path("recordings/voix-test/phrase.wav"))
    parser.add_argument("--config", type=Path, default=SERVER_DIR / "config.yaml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    settings = load_settings(args.config)
    voices_dir = settings.models_dir / "piper"
    resampler = AudioResampler(settings.audio)
    rate = settings.audio.sample_rate
    try:
        phrase = synthesize(voices_dir, args.voice, args.text, args.speaker, resampler)
    except ImportError as error:
        logger.error("%s : lancez 'make install-speech'", error)
        return 1
    edge = bytes(2 * int(EDGE_SILENCE_S * rate))
    pcm = edge + phrase + edge

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(args.out), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    logger.info("« %s » (%s), %.1f s -> %s", args.text, args.voice, len(pcm) / 2 / rate, args.out)

    if shutil.which("ffmpeg"):
        mp3 = args.out.with_suffix(".mp3")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(args.out), "-codec:a", "libmp3lame",
                        "-q:a", "2", str(mp3)], check=True)
        logger.info("pour le telephone : %s", mp3)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
