#!/usr/bin/env python3
"""Montre ce que la VAD (Silero) trouve dans des enregistrements (étape 7).

Pour chaque WAV : une lettre par fenêtre de 32 ms (« # » parole, « . » hésitation,
« _ » silence), les segments gardés et la part de silence retirée.
Avec un seul fichier, la parole seule est écrite dans --out, à écouter avec « make play ».

Exemples :
    python3 tools/vad_check.py server/recordings/esp32-01_xxx.wav
    python3 tools/vad_check.py server/recordings/*.wav
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

from voice_server.audio_resampler import AudioFormatError, AudioResampler  # noqa: E402
from voice_server.settings import load_settings  # noqa: E402
from voice_server.voice_activity import VoiceActivityDetector, envelope  # noqa: E402

logger = logging.getLogger("vad_check")


def main(argv: list[str]) -> int:
    """Analyse chaque fichier et affiche un bilan."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wavs", type=Path, nargs="+", help="enregistrements a analyser")
    parser.add_argument("--out", type=Path, default=Path("recordings/parole.wav"),
                        help="parole seule (un seul fichier analyse)")
    parser.add_argument("--config", type=Path, default=SERVER_DIR / "config.yaml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    settings = load_settings(args.config)
    audio = settings.audio
    detector = VoiceActivityDetector(settings.vad, audio.sample_rate)
    reader = AudioResampler(audio, None)  # niveau d'origine : la VAD voit ce que voit le serveur

    without_speech = 0
    last = None
    for path in args.wavs:
        try:
            result = detector.detect(reader.from_wav(path))
        except (AudioFormatError, FileNotFoundError) as error:
            logger.error("%s", error)
            continue
        last = result
        print(f"\n{path.name}")
        print(f"  {envelope(result.probabilities, settings.vad.threshold)}")
        if result.has_speech:
            removed = 100.0 * (1.0 - result.speech_s / result.total_s) if result.total_s else 0.0
            print(f"  parole {result.speech_s:.2f} s sur {result.total_s:.2f} s "
                  f"({removed:.0f} % retire), segments {result.bounds()} s, "
                  f"calcul {result.elapsed_s * 1000:.0f} ms")
        else:
            without_speech += 1
            print(f"  AUCUNE PAROLE ({result.total_s:.2f} s) : la session ne sera pas transcrite")

    print(f"\n{len(args.wavs)} fichier(s), {without_speech} sans parole")
    if len(args.wavs) == 1 and last is not None and last.has_speech:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(args.out), "wb") as wav:
            wav.setnchannels(audio.channels)
            wav.setsampwidth(audio.bits // 8)
            wav.setframerate(audio.sample_rate)
            wav.writeframes(last.speech_pcm)
        print(f"parole seule : make play OUT={args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
