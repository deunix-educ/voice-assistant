#!/usr/bin/env python3
"""Envoie de l'audio à l'ESP32 par le port série, pour lecture sur le haut-parleur.

Outil de validation de l'étape 2. Deux sources possibles :

    - un fichier WAV 16 kHz, 16 bits, mono (par exemple un enregistrement de l'étape 1) ;
    - un son pur généré ici (--tone) : la moindre coupure s'y entend comme un clic.

L'envoi suit le **rythme réel** : l'ESP32 ne peut pas jouer plus vite que 16 000
échantillons par seconde, et sa FIFO déborderait si on lui envoyait tout d'un coup.
On envoie d'abord une avance de quelques chunks (le « préremplissage »), puis un
chunk toutes les 50 ms.

Exemples :
    python3 tools/wav_to_serial.py --port /dev/ttyUSB0 recordings/essai.wav
    python3 tools/wav_to_serial.py --port /dev/ttyUSB0 --tone 440 --seconds 3
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
import wave
from pathlib import Path

import serial

from serial_protocol import AudioFormat, FrameType, TextEcho, encode_frame

logger = logging.getLogger("wav_to_serial")

EXPECTED = AudioFormat(sample_rate=16000, bits=16, channels=1)
CHUNK_MS = 50  # identique au firmware : 800 échantillons = 1600 octets
PREFILL_CHUNKS = 4  # 200 ms d'avance pour absorber les irrégularités d'envoi


class FormatError(ValueError):
    """Le fichier n'est pas dans le format unique du système."""


def read_wav(path: Path) -> bytes:
    """Lit un WAV et vérifie qu'il est en 16 kHz, 16 bits, mono."""
    with wave.open(str(path), "rb") as wav:
        found = AudioFormat(wav.getframerate(), wav.getsampwidth() * 8, wav.getnchannels())
        if found != EXPECTED:
            raise FormatError(
                f"{path} : {found.sample_rate} Hz, {found.bits} bits, {found.channels} canal(aux) ; "
                "attendu 16000 Hz, 16 bits, mono. Conversion : "
                f"ffmpeg -i {path} -ar 16000 -ac 1 -sample_fmt s16 converti.wav"
            )
        return wav.readframes(wav.getnframes())


def tone_pcm(frequency: float, seconds: float, amplitude: float = 0.25,
             rate: int = EXPECTED.sample_rate) -> bytes:
    """Génère un son pur en PCM s16le, avec fondus de 10 ms pour éviter les clics."""
    count = int(rate * seconds)
    fade = max(1, rate // 100)
    out = bytearray()
    for n in range(count):
        gain = min(1.0, n / fade, (count - 1 - n) / fade)
        value = amplitude * gain * math.sin(2.0 * math.pi * frequency * n / rate)
        out += int(round(value * 32767)).to_bytes(2, "little", signed=True)
    return bytes(out)


def split_chunks(pcm: bytes, chunk_bytes: int) -> list[bytes]:
    """Découpe le PCM en chunks ; le dernier peut être plus court, jamais impair."""
    even = pcm[: len(pcm) - (len(pcm) % 2)]  # un octet orphelin serait un demi-échantillon
    return [even[i : i + chunk_bytes] for i in range(0, len(even), chunk_bytes)]


def send_times(count: int, chunk_s: float, prefill: int) -> list[float]:
    """Instants d'envoi de chaque chunk, en secondes depuis le début.

    Les `prefill` premiers partent immédiatement ; chaque suivant part quand
    l'ESP32 a joué l'équivalent d'un chunk, ce qui maintient l'avance constante.
    """
    return [max(0.0, (index - prefill + 1) * chunk_s) for index in range(count)]


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Analyse la ligne de commande."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path, nargs="?", help="fichier WAV 16 kHz mono a jouer")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="port serie de l'ESP32")
    parser.add_argument("--baud", type=int, default=921600, help="debit serie")
    parser.add_argument("--tone", type=float, help="joue un son pur de cette frequence (Hz)")
    parser.add_argument("--seconds", type=float, default=3.0, help="duree du son pur")
    parser.add_argument("--amplitude", type=float, default=0.25,
                        help="amplitude du son pur, 0..1 (0.25 = -12 dBFS)")
    args = parser.parse_args(argv)
    if (args.wav is None) == (args.tone is None):
        parser.error("donnez soit un fichier WAV, soit --tone")
    return args


def main(argv: list[str]) -> int:
    """Point d'entrée : envoie l'audio au rythme réel, puis affiche le bilan de l'ESP32."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")

    try:
        if args.tone is not None:
            pcm = tone_pcm(args.tone, args.seconds, args.amplitude)
            logger.info("son pur %.0f Hz, %.1f s, amplitude %.0f %%", args.tone, args.seconds,
                        args.amplitude * 100)
        else:
            pcm = read_wav(args.wav)
            logger.info("fichier %s : %.2f s", args.wav, len(pcm) / 32000)
    except (OSError, wave.Error, FormatError) as error:
        logger.error("%s", error)
        return 1

    chunk_bytes = EXPECTED.sample_rate * CHUNK_MS // 1000 * 2
    chunks = split_chunks(pcm, chunk_bytes)
    schedule = send_times(len(chunks), CHUNK_MS / 1000.0, PREFILL_CHUNKS)

    try:
        port = serial.Serial(args.port, args.baud, timeout=0, write_timeout=1)
    except serial.SerialException as error:
        logger.error("port serie inaccessible : %s", error)
        return 1

    echo = TextEcho()
    with port:
        port.write(encode_frame(FrameType.START, EXPECTED.to_payload()))
        start = time.monotonic()
        late = 0.0

        for chunk, due in zip(chunks, schedule):
            wait = start + due - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            else:
                late = max(late, -wait)
            port.write(encode_frame(FrameType.AUDIO, chunk))
            echo.feed(port.read(port.in_waiting or 0))

        port.write(encode_frame(FrameType.END))
        logger.info("envoi termine : %d chunks, pire retard d'envoi %.0f ms", len(chunks), late * 1000)

        # L'ESP32 joue encore son avance, puis affiche son bilan : on l'attend.
        deadline = time.monotonic() + PREFILL_CHUNKS * CHUNK_MS / 1000.0 + 1.0
        while time.monotonic() < deadline:
            echo.feed(port.read(port.in_waiting or 0))
            time.sleep(0.05)
        echo.feed(b"\n")

    if late * 1000 > PREFILL_CHUNKS * CHUNK_MS:
        logger.warning("le PC a pris plus de retard que l'avance de l'ESP32 : coupure probable")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
