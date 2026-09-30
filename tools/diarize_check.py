#!/usr/bin/env python3
"""Qui parle quand, qui est-ce, qui a dit quoi : VAD, diarisation, identification, texte (étapes 8-10).

Une ligne par locuteur, un caractère par 100 ms de parole (« # » : il parle).
Les instants sont ceux de la parole seule, silences retirés par la VAD : c'est
ce que reçoivent la diarisation et Whisper dans le serveur.

--demo fabrique une séquence à deux voix sans seconde personne : votre voix
(le dernier enregistrement de l'ESP32), la voix Piper, puis de nouveau la vôtre.
Elle est écrite dans --out pour l'écouter.

Exemples :
    python3 tools/diarize_check.py server/recordings/esp32-01_xxx.wav
    python3 tools/diarize_check.py --demo
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
from voice_server.diarization import Diarizer, timeline  # noqa: E402
from voice_server.settings import Settings, load_settings  # noqa: E402
from voice_server.speaker_attribution import attribute, transcript_json  # noqa: E402
from voice_server.speaker_identifier import SpeakerIdentifier  # noqa: E402
from voice_server.speech_to_text import SpeechToText  # noqa: E402
from voice_server.voice_activity import VoiceActivityDetector  # noqa: E402

logger = logging.getLogger("diarize_check")


def demo_sequence(settings: Settings, vad: VoiceActivityDetector, diarizer: Diarizer,
                  voice: Path | None) -> bytes:
    """Votre voix, la voix Piper, votre voix : deux locuteurs attendus."""
    from voice_server.text_to_speech import TextToSpeech

    # Par défaut, la question la plus récente : 1 à 4 s de parole, une seule voix. Les
    # sessions plus longues sont souvent des essais à plusieurs voix, que la diarisation
    # ne sépare pas toujours.
    recordings = [voice] if voice else sorted(settings.recordings_dir.glob("esp32-01_*.wav"), reverse=True)
    reader = AudioResampler(settings.audio, None)
    second = settings.audio.bytes_per_second
    mine = b""
    for path in recordings:
        mine = vad.detect(reader.from_wav(path)).speech_pcm
        if (voice or second <= len(mine) <= 4 * second) and len(diarizer.diarize(mine).speakers) == 1:
            logger.info("votre voix : %s", path.name)
            break
        mine = b""
    if not mine:
        raise FileNotFoundError(f"aucun enregistrement de l'ESP32 avec de la parole dans {settings.recordings_dir}")

    # Même niveau que votre enregistrement, pour que la diarisation juge la voix et non le volume.
    tts = TextToSpeech(settings.tts, AudioResampler(settings.audio, -10.0), settings.audio.sample_rate)
    piper = tts.synthesize("Bonjour, je suis la voix de synthèse, et je parle entre vos deux phrases.").pcm
    pause = bytes(settings.audio.bytes_per_second // 2)
    return mine + pause + piper + pause + mine


def main(argv: list[str]) -> int:
    """Diarise le fichier (ou la démonstration) et affiche la chronologie."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wav", type=Path, nargs="?", help="enregistrement a analyser")
    parser.add_argument("--demo", action="store_true", help="sequence a deux voix : vous et Piper")
    parser.add_argument("--voice", type=Path, help="votre voix pour --demo (sinon la derniere question)")
    parser.add_argument("--out", type=Path, default=Path("recordings/deux-voix.wav"),
                        help="WAV de la demonstration")
    parser.add_argument("--config", type=Path, default=SERVER_DIR / "config.yaml")
    args = parser.parse_args(argv)
    if (args.wav is None) == (not args.demo):
        parser.error("donnez soit un fichier WAV, soit --demo")

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = load_settings(args.config)
    audio = settings.audio
    vad = VoiceActivityDetector(settings.vad, audio.sample_rate)
    try:
        diarizer = Diarizer(settings.diarization, audio.sample_rate)
        if args.demo:
            pcm = demo_sequence(settings, vad, diarizer, args.voice)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(args.out), "wb") as wav:
                wav.setnchannels(audio.channels)
                wav.setsampwidth(audio.bits // 8)
                wav.setframerate(audio.sample_rate)
                wav.writeframes(pcm)
            logger.info("demonstration : make play OUT=%s", args.out)
        else:
            pcm = AudioResampler(audio, None).from_wav(args.wav)
    except (AudioFormatError, FileNotFoundError, ImportError) as error:
        logger.error("%s", error)
        return 1

    speech = vad.detect(pcm)
    if not speech.has_speech:
        print("\naucune parole : rien a diariser")
        return 0
    result = diarizer.diarize(speech.speech_pcm)

    print(f"\nparole {speech.speech_s:.2f} s (sur {speech.total_s:.2f} s), "
          f"{len(result.speakers)} locuteur(s), diarisation {result.elapsed_s:.2f} s")
    for line in timeline(result):
        print(f"  {line}")
    for turn in result.turns:
        print(f"  {turn.speaker}  {turn.start_s:6.2f} -> {turn.end_s:6.2f} s")

    identifier = SpeakerIdentifier(settings.speaker, settings.diarization.model)
    verdicts = {} if result.embeddings is None else {
        speaker: identifier.identify(embedding) for speaker, embedding in zip(result.speakers, result.embeddings)}
    for speaker, verdict in verdicts.items():
        print(f"  {speaker} = {verdict.describe()}")

    # Étape 10 : les mots de Whisper répartis entre les locuteurs.
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    transcript = SpeechToText(settings.stt, audio.sample_rate).transcribe(speech.speech_pcm)
    utterances = attribute(transcript.words, result.turns, verdicts)
    print(f"\nqui a dit quoi (transcription {transcript.elapsed_s:.2f} s) :")
    for utterance in utterances:
        print(f"  {utterance.start_s:5.2f}-{utterance.end_s:5.2f} s  {utterance.speaker:10s} « {utterance.text} »")
    print("\nmessage MQTT voice/<carte>/transcript :")
    print(f"  {transcript_json('essai', utterances).decode()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
