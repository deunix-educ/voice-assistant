#!/usr/bin/env python3
"""Enrôlement vocal : crée, liste ou supprime les profils de voix (étape 9).

Un profil est la moyenne des empreintes de plusieurs sessions d'une même
personne. Par défaut, on prend les N dernières sessions de l'ESP32 : dites
d'abord 5 phrases variées avec « make run », puis enrôlez.

Chaque session est contrôlée : assez de parole, une seule voix, et cohérente
avec les autres (une session où une autre voix s'est glissée est écartée).

Les profils sont des données biométriques : server/profiles/ n'est jamais
versionné, et « --forget » supprime un profil.

Exemples :
    python3 tools/enroll.py --name Denis                 # 5 dernières sessions
    python3 tools/enroll.py --name Denis a.wav b.wav c.wav
    python3 tools/enroll.py --list
    python3 tools/enroll.py --forget Denis
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from voice_server.audio_resampler import AudioFormatError, AudioResampler  # noqa: E402
from voice_server.diarization import Diarizer  # noqa: E402
from voice_server.settings import Settings, load_settings  # noqa: E402
from voice_server.speaker_identifier import (  # noqa: E402
    SpeakerIdentifier,
    load_profiles,
    normalized,
    profile_path,
    save_profile,
)
from voice_server.voice_activity import VoiceActivityDetector  # noqa: E402

logger = logging.getLogger("enroll")

MIN_SPEECH_S = 1.0   # en dessous, l'empreinte est trop instable pour servir de référence
MIN_SAMPLES = 3


def collect(settings: Settings, wavs: list[Path]) -> list[tuple[str, np.ndarray]]:
    """Une empreinte par session valable : assez de parole, une seule voix."""
    audio = settings.audio
    vad = VoiceActivityDetector(settings.vad, audio.sample_rate)
    diarizer = Diarizer(settings.diarization, audio.sample_rate)
    reader = AudioResampler(audio, None)
    samples: list[tuple[str, np.ndarray]] = []
    for path in wavs:
        try:
            speech = vad.detect(reader.from_wav(path))
        except (AudioFormatError, FileNotFoundError) as error:
            logger.warning("  %s ignore : %s", path.name, error)
            continue
        if speech.speech_s < MIN_SPEECH_S:
            logger.warning("  %s ignore : %.1f s de parole (minimum %.0f s)", path.name, speech.speech_s, MIN_SPEECH_S)
            continue
        diarization = diarizer.diarize(speech.speech_pcm)
        if len(diarization.speakers) != 1 or diarization.embeddings is None:
            logger.warning("  %s ignore : %d voix trouvees, une seule attendue", path.name, len(diarization.speakers))
            continue
        embedding = diarization.embeddings[0]
        if not np.all(np.isfinite(embedding)):
            logger.warning("  %s ignore : empreinte impossible", path.name)
            continue
        logger.info("  %s : %.1f s de parole", path.name, speech.speech_s)
        samples.append((path.name, normalized(embedding)))
    return samples


def drop_outliers(samples: list[tuple[str, np.ndarray]], threshold: float) -> list[tuple[str, np.ndarray]]:
    """Écarte les sessions qui ne ressemblent pas aux autres (autre voix, mélange)."""
    kept: list[tuple[str, np.ndarray]] = []
    for index, (name, embedding) in enumerate(samples):
        others = normalized(np.mean([e for i, (_, e) in enumerate(samples) if i != index], axis=0))
        score = float(others @ embedding)
        if score < threshold:
            logger.warning("  %s ecarte : ressemblance %.2f avec les autres sessions (seuil %.2f)",
                           name, score, threshold)
        else:
            kept.append((name, embedding))
    return kept


def enroll(settings: Settings, name: str, wavs: list[Path]) -> int:
    """Crée (ou remplace) le profil de name à partir des sessions données."""
    logger.info("enrolement de %s sur %d session(s) :", name, len(wavs))
    samples = collect(settings, wavs)
    if len(samples) >= MIN_SAMPLES:
        samples = drop_outliers(samples, settings.speaker.similarity_threshold)
    if len(samples) < MIN_SAMPLES:
        logger.error("%d session(s) valable(s), %d au minimum. Soit dites %d phrases seul avec 'make run' "
                     "puis relancez, soit remontez plus loin : 'make enroll NAME=%s LAST=%d'",
                     len(samples), MIN_SAMPLES, MIN_SAMPLES + 2, name, len(wavs) * 2)
        return 1

    # Avant d'écrire : ce nouveau profil ressemble-t-il trop à quelqu'un d'autre ?
    identifier = SpeakerIdentifier(settings.speaker, settings.diarization.model)
    mean = normalized(np.mean([e for _, e in samples], axis=0))
    for other in identifier.profiles:
        score = float(other.embedding @ mean)
        if other.name != name and score >= settings.speaker.similarity_threshold:
            logger.warning("attention : ressemble a %s (%.2f) ; meme personne, ou seuil trop bas ?",
                           other.name, score)

    profile = save_profile(settings.speaker.profiles_dir, name, [e for _, e in samples], settings.diarization.model)
    scores = [float(profile.embedding @ e) for _, e in samples]
    logger.info("profil %s enregistre : %d sessions, ressemblance %.2f a %.2f -> %s",
                name, profile.samples, min(scores), max(scores),
                profile_path(settings.speaker.profiles_dir, name))
    return 0


def main(argv: list[str]) -> int:
    """Aiguille entre enrôlement, liste et suppression."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wavs", type=Path, nargs="*", help="sessions a utiliser (sinon les --last dernieres)")
    parser.add_argument("--name", help="nom de la personne a enroler")
    parser.add_argument("--last", type=int, default=5, help="nombre de sessions recentes de l'ESP32")
    parser.add_argument("--device", default="esp32-01", help="carte dont on prend les sessions")
    parser.add_argument("--list", action="store_true", help="affiche les profils")
    parser.add_argument("--forget", metavar="NOM", help="supprime le profil de NOM")
    parser.add_argument("--config", type=Path, default=SERVER_DIR / "config.yaml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = load_settings(args.config)
    profiles_dir = settings.speaker.profiles_dir

    if args.list:
        profiles = load_profiles(profiles_dir, settings.diarization.model)
        for profile in profiles:
            print(f"  {profile.name:20s} {profile.samples} sessions, cree le {profile.created}")
        print(f"{len(profiles)} profil(s) dans {profiles_dir}")
        return 0
    if args.forget:
        path = profile_path(profiles_dir, args.forget)
        if not path.exists():
            logger.error("aucun profil %s (%s)", args.forget, path)
            return 1
        path.unlink()
        logger.info("profil %s supprime (%s)", args.forget, path)
        return 0
    if not args.name:
        parser.error("--name, --list ou --forget")

    wavs = args.wavs or sorted(settings.recordings_dir.glob(f"{args.device}_*.wav"))[-args.last:]
    if not wavs:
        logger.error("aucune session dans %s : parlez d'abord avec 'make run'", settings.recordings_dir)
        return 1
    try:
        return enroll(settings, args.name, wavs)
    except (FileNotFoundError, ImportError) as error:
        logger.error("%s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
