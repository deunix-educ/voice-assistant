#!/usr/bin/env python3
"""Mesure le temps de transcription du serveur sur des enregistrements réels.

Pour chaque enregistrement de server/recordings/ (lecture seule, rien n'est écrit) : le temps
que Whisper met à transcrire la session entière, comme le fait le serveur (branche fingerprint,
F4 : sans VAD ni diarisation). Les sessions silencieuses (stt.min_rms_dbfs) ne sont pas transcrites
par le serveur : elles sont comptées à part.

Le temps affiché est celui du calcul, de la fin de la session au texte reconnu : il ne compte ni le
réseau, ni la synthèse (quelques dizaines de ms, en cache pour les réponses répétées). À lancer sur
la machine qui fait tourner le serveur.

Exemples :
    python3 tools/latency_check.py              # les 30 derniers enregistrements
    python3 tools/latency_check.py --last 100
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
import time
from pathlib import Path

import numpy as np

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from voice_server.audio_buffer import read_pcm  # noqa: E402
from voice_server.settings import load_settings  # noqa: E402

logger = logging.getLogger("latency_check")


def rms_dbfs(pcm: bytes) -> float:
    """Niveau efficace d'un PCM s16le, en dBFS (-inf pour le silence numérique)."""
    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float64) / 32768.0
    rms = float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0
    return 20.0 * float(np.log10(rms)) if rms > 0 else float("-inf")


def main(argv: list[str]) -> int:
    """Rejoue les enregistrements et affiche les médianes."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--last", type=int, default=30, help="nombre d'enregistrements, les plus recents")
    parser.add_argument("--config", type=Path, default=SERVER_DIR / "config.yaml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)  # une ligne par transcription sinon

    settings = load_settings(args.config)
    try:
        from voice_server.speech_to_text import SpeechToText

        stt = SpeechToText(settings.stt, settings.audio.sample_rate)
    except (ImportError, FileNotFoundError) as error:
        logger.error("%s : lancez 'make install-speech' et 'make models'", error)
        return 1

    files = sorted(settings.recordings_dir.glob("*.wav"), key=lambda path: path.stat().st_mtime)[-args.last:]
    clips = [read_pcm(path) for path in files]
    silent = [pcm for pcm in clips if rms_dbfs(pcm) < settings.stt.min_rms_dbfs]
    spoken = [pcm for pcm in clips if rms_dbfs(pcm) >= settings.stt.min_rms_dbfs]
    if not spoken:
        logger.error("aucun enregistrement audible dans %s", settings.recordings_dir)
        return 1

    durations: list[float] = []
    for pcm in spoken:
        began = time.monotonic()
        stt.transcribe(pcm)
        durations.append(time.monotonic() - began)

    rate = settings.audio.bytes_per_second
    logger.info("%d enregistrements transcrits (%.1f s d'audio en mediane), %d silencieux ecartes (sous %.0f dBFS)",
                len(spoken), statistics.median(len(pcm) / rate for pcm in spoken), len(silent),
                settings.stt.min_rms_dbfs)
    logger.info("transcription : %.2f s (mediane), %.2f s (pire)", statistics.median(durations), max(durations))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
