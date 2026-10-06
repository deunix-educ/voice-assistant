#!/usr/bin/env python3
"""Mesure la latence du serveur sur des enregistrements réels, avant et après l'étape 16.

Pour chaque enregistrement de server/recordings/ (lecture seule, rien n'est écrit) :

    avant   VAD → diarisation → identification → Whisper avec mots horodatés
    après   VAD → empreinte directe si la parole est courte (diarization.direct_below_s),
            diarisation sinon → identification → Whisper sans mots si parole courte

Le temps affiché est celui du calcul, de la fin de la session au texte reconnu :
il ne compte ni le réseau, ni la synthèse (quelques dizaines de ms, en cache
pour les réponses répétées). À lancer sur la machine qui fait tourner le serveur.

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
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from voice_server.audio_buffer import read_pcm  # noqa: E402
from voice_server.settings import load_settings  # noqa: E402

logger = logging.getLogger("latency_check")

T = TypeVar("T")


def timed(function: Callable[[], T]) -> tuple[float, T]:
    """Durée d'un appel, et son résultat."""
    began = time.monotonic()
    result = function()
    return time.monotonic() - began, result


def main(argv: list[str]) -> int:
    """Rejoue les enregistrements et affiche les médianes."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--last", type=int, default=30, help="nombre d'enregistrements, les plus recents")
    parser.add_argument("--config", type=Path, default=SERVER_DIR / "config.yaml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    warnings.filterwarnings("ignore")  # avertissements de torch sur les extraits très courts
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)  # une ligne par transcription sinon

    settings = load_settings(args.config)
    try:
        from voice_server.diarization import Diarizer
        from voice_server.speaker_identifier import SpeakerIdentifier
        from voice_server.speech_to_text import SpeechToText
        from voice_server.voice_activity import VoiceActivityDetector

        vad = VoiceActivityDetector(settings.vad, settings.audio.sample_rate)
        stt = SpeechToText(settings.stt, settings.audio.sample_rate)
        diarizer = Diarizer(settings.diarization, settings.audio.sample_rate)
    except (ImportError, FileNotFoundError) as error:
        logger.error("%s : lancez 'make install-speech', 'make install-diarization' et 'make models'", error)
        return 1
    identifier = SpeakerIdentifier(settings.speaker, settings.diarization.model)
    threshold = settings.diarization.direct_below_s

    files = sorted(settings.recordings_dir.glob("*.wav"), key=lambda path: path.stat().st_mtime)[-args.last:]
    clips = [result.speech_pcm for result in (vad.detect(read_pcm(path)) for path in files) if result.has_speech]
    if not clips:
        logger.error("aucun enregistrement avec de la parole dans %s", settings.recordings_dir)
        return 1
    diarizer.diarize(clips[0])  # premier appel plus lent : hors mesure
    diarizer.embed(clips[0])

    before: list[float] = []
    after: list[float] = []
    short = same_text = same_name = 0
    changes: list[str] = []
    for pcm in clips:
        speech_s = len(pcm) / 2 / settings.audio.sample_rate
        diar_s, diarization = timed(lambda: diarizer.diarize(pcm))
        words_s, with_words = timed(lambda: stt.transcribe(pcm))
        before.append(diar_s + words_s)
        old_name = (identifier.identify(diarization.embeddings[0]).label
                    if diarization.embeddings is not None and len(diarization.speakers) == 1 else None)
        if speech_s < threshold:
            short += 1
            embed_s, embedding = timed(lambda: diarizer.embed(pcm))
            plain_s, plain = timed(lambda: stt.transcribe(pcm, words=False))
            after.append(embed_s + plain_s)
            same_text += plain.text == with_words.text
            verdict = identifier.identify(embedding)
            same_name += verdict.label == old_name
            if verdict.label != old_name:
                changes.append(f"{old_name} -> {verdict.describe()} ({speech_s:.1f} s de parole)")
        else:
            after.append(diar_s + words_s)

    def median(values: list[float]) -> float:
        return statistics.median(values)

    logger.info("%d enregistrements avec parole (%.1f s de parole en mediane), dont %d sous %.1f s",
                len(clips), median([len(pcm) / 2 / settings.audio.sample_rate for pcm in clips]), short, threshold)
    logger.info("avant  : %.2f s (mediane), %.2f s (pire)", median(before), max(before))
    logger.info("apres  : %.2f s (mediane), %.2f s (pire)", median(after), max(after))
    logger.info("gain   : %.2f s, soit %.0f %%", median(before) - median(after),
                100 * (1 - median(after) / median(before)))
    if short:
        logger.info("paroles courtes : meme texte %d/%d, meme identification %d/%d", same_text, short,
                    same_name, short)
        for change in changes:  # attendu : des voix à la limite du seuil, dans les deux sens
            logger.info("  identification differente : %s", change)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
