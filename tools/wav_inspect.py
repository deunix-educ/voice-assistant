#!/usr/bin/env python3
"""Analyse un fichier WAV et affiche son enveloppe en ASCII.

Permet de valider un enregistrement **sans écouter** : indispensable sur une
machine sans sortie audio, et plus tard sur un Raspberry Pi sans écran.

Exemple :
    python3 tools/wav_inspect.py recordings/essai.wav
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import wave
from array import array
from pathlib import Path

from serial_to_wav import LevelReport, measure_levels

logger = logging.getLogger("wav_inspect")

# Du plus faible au plus fort : huit niveaux de remplissage.
BLOCKS = " ▁▂▃▄▅▆▇█"

# Plage affichée par l'enveloppe, en décibels pleine échelle.
FLOOR_DBFS = -60.0

# Un segment dépassant le plancher de bruit de cette marge est considéré parlé.
SPEECH_MARGIN_DB = 12.0


def read_pcm(path: Path) -> tuple[bytes, int]:
    """Lit un WAV PCM 16 bits mono et retourne (octets, fréquence)."""
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError(f"{path} : {wav.getsampwidth() * 8} bits, 16 attendus")
        if wav.getnchannels() != 1:
            raise ValueError(f"{path} : {wav.getnchannels()} canaux, 1 attendu")
        return wav.readframes(wav.getnframes()), wav.getframerate()


def column_peaks(pcm: bytes, columns: int) -> list[float]:
    """Découpe le flux en `columns` tranches et retourne la crête de chacune."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if sys.byteorder == "big":
        samples.byteswap()

    total = len(samples)
    if total == 0 or columns <= 0:
        return []

    peaks: list[float] = []
    for index in range(columns):
        start = (index * total) // columns
        stop = max(start + 1, ((index + 1) * total) // columns)
        window = samples[start:stop]
        peak = max(max(window), -min(window))
        peaks.append(peak / 32768.0)
    return peaks


def to_dbfs(value: float) -> float:
    """Convertit un niveau normalisé en dBFS, borné au plancher d'affichage."""
    if value <= 0.0:
        return FLOOR_DBFS
    return max(FLOOR_DBFS, 20.0 * math.log10(value))


def render(peaks: list[float]) -> str:
    """Dessine l'enveloppe : un caractère par tranche, échelle logarithmique."""
    glyphs: list[str] = []
    for peak in peaks:
        ratio = (to_dbfs(peak) - FLOOR_DBFS) / (0.0 - FLOOR_DBFS)
        index = int(round(ratio * (len(BLOCKS) - 1)))
        glyphs.append(BLOCKS[min(len(BLOCKS) - 1, max(0, index))])
    return "".join(glyphs)


def speech_ratio(peaks: list[float]) -> float:
    """Proportion de tranches nettement au-dessus du plancher de bruit."""
    if not peaks:
        return 0.0
    quiet = min(to_dbfs(peak) for peak in peaks)
    loud = [peak for peak in peaks if to_dbfs(peak) > quiet + SPEECH_MARGIN_DB]
    return len(loud) / len(peaks)


def describe(level: LevelReport, peaks: list[float], duration: float) -> None:
    """Journalise le verdict sur l'enregistrement."""
    ratio = speech_ratio(peaks)
    logger.info("duree %.2f s | crete %.1f %% | RMS %.1f %% | offset %+.3f %%",
                duration, level.peak * 100.0, level.rms * 100.0, level.dc * 100.0)
    logger.info("tranches actives : %.0f %% de la duree", ratio * 100.0)

    if level.peak <= 0.0:
        logger.error("fichier totalement silencieux.")
    elif level.peak > 0.99:
        logger.warning("saturation : diminuez MIC_GAIN.")
    elif level.peak < 0.02:
        logger.warning("niveau tres faible : augmentez MIC_GAIN.")
    elif ratio < 0.05:
        logger.warning("aucune variation nette : bruit de fond plutot que parole ?")
    elif ratio > 0.95:
        logger.warning("signal actif partout : bruit continu plutot que parole ?")
    else:
        logger.info("alternance silence / parole coherente avec une phrase.")


def main(argv: list[str]) -> int:
    """Point d'entrée : analyse le fichier passé en argument."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path, help="fichier WAV a analyser")
    parser.add_argument("--columns", type=int, default=64, help="largeur de l'enveloppe")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    try:
        pcm, rate = read_pcm(args.wav)
    except (OSError, wave.Error, ValueError) as error:
        logger.error("lecture impossible : %s", error)
        return 1

    duration = len(pcm) / (2.0 * rate) if rate else 0.0
    peaks = column_peaks(pcm, args.columns)

    print(f"\n{args.wav}  ({rate} Hz, 16 bits, mono)")
    print(f"  0 dBFS  |{'-' * len(peaks)}|")
    print(f"          |{render(peaks)}|")
    print(f"{FLOOR_DBFS:.0f} dBFS  |{'-' * len(peaks)}|")
    print(f"          0 s{' ' * max(0, len(peaks) - 8)}{duration:.2f} s\n")

    sys.stdout.flush()  # le dessin doit sortir avant les lignes de journalisation
    describe(measure_levels(pcm), peaks, duration)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
