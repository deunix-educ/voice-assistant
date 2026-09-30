#!/usr/bin/env python3
"""Reçoit une session audio de l'ESP32 par le port série et écrit un fichier WAV.

Outil de validation de l'étape 1 : il ne dépend ni de MQTT ni du serveur vocal.

Format des trames : voir tools/serial_protocol.py (identique au firmware).

Les lignes de texte émises par l'ESP32 (messages commençant par « # ») sont
affichées telles quelles : le lecteur se resynchronise sur le mot magique.

Exemple :
    python3 tools/serial_to_wav.py --port /dev/ttyUSB0 --baud 921600 \
        --output recordings/test.wav
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

import serial

from serial_protocol import AudioFormat, FrameReader, FrameType

logger = logging.getLogger("serial_to_wav")

class WavSessionRecorder:
    """Assemble les chunks PCM d'une session et les écrit dans un fichier WAV."""

    def __init__(self, output: Path) -> None:
        self._output = output
        self._chunks: list[bytes] = []
        self._format: AudioFormat | None = None

    def begin(self, audio_format: AudioFormat) -> None:
        """Démarre une session avec le format annoncé."""
        self._format = audio_format
        self._chunks = []
        logger.info(
            "session ouverte : %d Hz, %d bits, %d canal/canaux",
            audio_format.sample_rate,
            audio_format.bits,
            audio_format.channels,
        )

    def add(self, pcm: bytes) -> None:
        """Ajoute un chunk PCM à la session en cours."""
        self._chunks.append(pcm)

    @property
    def active(self) -> bool:
        """true si une session est ouverte."""
        return self._format is not None

    @property
    def byte_count(self) -> int:
        """Nombre d'octets PCM accumulés."""
        return sum(len(chunk) for chunk in self._chunks)

    def finish(self) -> Path:
        """Écrit le fichier WAV et retourne son chemin."""
        if self._format is None:
            raise RuntimeError("aucune session ouverte")

        pcm = b"".join(self._chunks)
        duration = self.byte_count / (
            self._format.sample_rate * self._format.channels * self._format.bits / 8
        )

        self._output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(self._output), "wb") as wav:
            wav.setnchannels(self._format.channels)
            wav.setsampwidth(self._format.bits // 8)
            wav.setframerate(self._format.sample_rate)
            wav.writeframes(pcm)

        logger.info("ecrit %s : %d octets, %.2f s", self._output, len(pcm), duration)
        diagnose(measure_levels(pcm))

        self._format = None
        return self._output


@dataclass(frozen=True)
class LevelReport:
    """Mesures de niveau d'un flux PCM, normalisées dans [0, 1]."""

    peak: float  # crête absolue
    rms: float  # valeur efficace (perception du volume)
    dc: float  # composante continue résiduelle


def measure_levels(pcm: bytes) -> LevelReport:
    """Mesure crête, valeur efficace et offset continu d'un flux PCM s16le."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if sys.byteorder == "big":
        samples.byteswap()  # le flux est little-endian par convention

    if len(samples) == 0:
        return LevelReport(peak=0.0, rms=0.0, dc=0.0)

    peak = max(max(samples), -min(samples))
    total = 0
    squares = 0
    for sample in samples:
        total += sample
        squares += sample * sample

    count = len(samples)
    return LevelReport(
        peak=peak / 32768.0,
        rms=math.sqrt(squares / count) / 32768.0,
        dc=(total / count) / 32768.0,
    )


def _dbfs(value: float) -> str:
    """Convertit un niveau normalisé en décibels pleine échelle."""
    if value <= 0.0:
        return "-inf dBFS"
    return f"{20.0 * math.log10(value):+.1f} dBFS"


def suggested_gain_factor(peak: float, target: float = 0.25) -> int:
    """Facteur (puissance de 2) à appliquer a MIC_GAIN pour viser `target` de crête."""
    if peak <= 0.0:
        return 0
    exponent = round(math.log2(target / peak))
    return int(min(64, max(2, 2**exponent)))


def diagnose(level: LevelReport) -> None:
    """Journalise le niveau mesuré et le correctif à appliquer."""
    logger.info(
        "niveau : crete %.2f %% (%s) | RMS %s | offset continu %+.3f %%",
        level.peak * 100.0,
        _dbfs(level.peak),
        _dbfs(level.rms),
        level.dc * 100.0,
    )

    if level.peak <= 0.0:
        logger.error("AUCUN signal : tous les echantillons sont nuls.")
        logger.error("  -> le micro ne transmet rien. Verifiez dans cet ordre :")
        logger.error("     1. L/R de l'INMP441 relie a GND (sinon silence total)")
        logger.error("     2. SD du micro sur GPIO 33, SCK sur 25, WS sur 26")
        logger.error("     3. VDD du micro sur 3V3 (jamais 5 V) et GND commun")
        return

    if level.peak < 0.02:
        factor = suggested_gain_factor(level.peak)
        logger.warning(
            "niveau tres faible : multipliez MIC_GAIN par %d dans firmware/include/config.h,",
            factor,
        )
        logger.warning("puis 'make fw-upload'. Parlez a 20-30 cm du micro.")
        return

    if level.peak > 0.99:
        logger.warning("SATURATION : divisez MIC_GAIN par 2 dans firmware/include/config.h.")
        return

    if abs(level.dc) > 0.02:
        logger.warning(
            "offset continu residuel eleve (%+.2f %%) : le micro vient peut-etre de demarrer.",
            level.dc * 100.0,
        )

    logger.info("niveau correct (cible : crete entre 10 % et 90 %).")


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Analyse la ligne de commande."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyUSB0", help="port serie de l'ESP32")
    parser.add_argument("--baud", type=int, default=921600, help="debit serie")
    parser.add_argument(
        "--output", type=Path, default=Path("recordings/test.wav"), help="fichier WAV de sortie"
    )
    parser.add_argument(
        "--sessions", type=int, default=1, help="nombre de sessions a enregistrer avant de quitter"
    )
    parser.add_argument("--verbose", action="store_true", help="journalisation detaillee")
    return parser.parse_args(argv)


def output_path(base: Path, index: int, total: int) -> Path:
    """Numérote le fichier de sortie si plusieurs sessions sont demandées."""
    if total <= 1:
        return base
    return base.with_name(f"{base.stem}-{index:02d}{base.suffix}")


def main(argv: list[str]) -> int:
    """Point d'entrée : lit le port série jusqu'à la fin des sessions demandées."""
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("ouverture de %s a %d baud", args.port, args.baud)
    try:
        port = serial.Serial(args.port, args.baud, timeout=0.2)
    except serial.SerialException as error:
        logger.error("port serie inaccessible : %s", error)
        return 1

    recorder: WavSessionRecorder | None = None
    completed = 0

    logger.info("en attente d'une session : appuyez sur le bouton PTT et parlez.")
    try:
        with port:
            for frame in FrameReader(port).frames():
                if frame.type is FrameType.START:
                    recorder = WavSessionRecorder(
                        output_path(args.output, completed + 1, args.sessions)
                    )
                    recorder.begin(AudioFormat.from_payload(frame.payload))

                elif frame.type is FrameType.AUDIO:
                    if recorder is None or not recorder.active:
                        logger.debug("chunk recu hors session, ignore")
                        continue
                    recorder.add(frame.payload)

                elif frame.type is FrameType.END:
                    if recorder is None or not recorder.active:
                        continue
                    recorder.finish()
                    recorder = None
                    completed += 1
                    if completed >= args.sessions:
                        return 0
                    logger.info("en attente de la session suivante.")
    except KeyboardInterrupt:
        logger.info("interruption clavier")
        if recorder is not None and recorder.active and recorder.byte_count > 0:
            recorder.finish()
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
