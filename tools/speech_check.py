#!/usr/bin/env python3
"""Vérifie VAD, transcription, réponse et synthèse sans ESP32 ni broker (étapes 6-7).

Deux usages :
    texte → Piper → Whisper → Assistant → Piper        (la chaîne se relit elle-même)
    WAV   → VAD → Whisper → Assistant → Piper      (un enregistrement de l'ESP32, comme le serveur)

La réponse synthétisée est écrite dans --out, à écouter avec « make play ».

Exemples :
    python3 tools/speech_check.py "Quelle heure est-il ?"
    python3 tools/speech_check.py --wav server/recordings/esp32-01_xxx.wav
"""

from __future__ import annotations

import argparse
import logging
import sys
import wave
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from voice_server.assistant import Assistant  # noqa: E402
from voice_server.audio_resampler import AudioFormatError, AudioResampler  # noqa: E402
from voice_server.settings import load_settings  # noqa: E402
from voice_server.speech_to_text import SpeechToText  # noqa: E402
from voice_server.text_to_speech import TextToSpeech  # noqa: E402
from voice_server.voice_activity import VoiceActivityDetector  # noqa: E402

logger = logging.getLogger("speech_check")


def main(argv: list[str]) -> int:
    """Déroule la chaîne et affiche le temps de chaque maillon."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("text", nargs="?", help="phrase a synthetiser puis transcrire")
    parser.add_argument("--wav", type=Path, help="enregistrement a transcrire")
    parser.add_argument("--out", type=Path, default=Path("recordings/reponse.wav"),
                        help="WAV de la reponse synthetisee")
    parser.add_argument("--config", type=Path, default=SERVER_DIR / "config.yaml")
    args = parser.parse_args(argv)
    if (args.text is None) == (args.wav is None):
        parser.error("donnez soit une phrase, soit --wav")

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    settings = load_settings(args.config)
    audio = settings.audio
    try:
        tts = TextToSpeech(settings.tts, AudioResampler(audio), audio.sample_rate)
        stt = SpeechToText(settings.stt, audio.sample_rate)
    except FileNotFoundError as error:
        logger.error("%s", error)
        return 1

    if args.wav is not None:
        try:
            # Sans normalisation : Whisper doit entendre le niveau réel du micro.
            pcm = AudioResampler(audio, None).from_wav(args.wav)
        except (AudioFormatError, FileNotFoundError) as error:
            logger.error("%s", error)
            return 1
        vad = VoiceActivityDetector(settings.vad, audio.sample_rate).detect(pcm)
        logger.info("VAD : parole %.2f s sur %.2f s, segments %s", vad.speech_s, vad.total_s,
                    f"{vad.bounds()} s" if vad.has_speech else "aucun")
        pcm = vad.speech_pcm  # sans parole, Whisper reçoit un audio vide et ne rend rien
    else:
        spoken = tts.synthesize(args.text)
        logger.info("synthese de la question : %.2f s de calcul pour %.2f s de parole",
                    spoken.elapsed_s, spoken.audio_s)
        pcm = spoken.pcm

    transcript = stt.transcribe(pcm)
    logger.info("transcription : %.2f s de calcul pour %.2f s d'audio", transcript.elapsed_s, transcript.audio_s)
    if transcript.dropped:
        logger.warning("hallucination ecartee : %s", " | ".join(transcript.dropped))
    answer = Assistant().reply(transcript.text)
    speech = tts.synthesize(answer)
    logger.info("synthese de la reponse : %.2f s de calcul pour %.2f s de parole",
                speech.elapsed_s, speech.audio_s)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(args.out), "wb") as wav:
        wav.setnchannels(audio.channels)
        wav.setsampwidth(audio.bits // 8)
        wav.setframerate(audio.sample_rate)
        wav.writeframes(speech.pcm)

    print(f"\n  entendu : « {transcript.text} »")
    print(f"  reponse : « {answer} »")
    print(f"  delai   : {transcript.elapsed_s + speech.elapsed_s:.2f} s de calcul avant le premier son")
    print(f"  ecoute  : make play OUT={args.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
