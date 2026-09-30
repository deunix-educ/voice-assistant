#!/usr/bin/env python3
"""Envoie un WAV (ou un son pur) à un ESP32 par MQTT, et affiche le bilan de lecture.

Outil de validation de l'étape 5. Il réutilise les classes du serveur :
AudioResampler (mise au format, crête à -3 dBFS) et AudioSender (rythme réel).
N'importe quel WAV PCM convient : il est converti en 16 kHz mono.

Étape 12 : --jitter MS simule un réseau irrégulier. Un chunk sur dix (toutes les
500 ms) est retenu MS millisecondes, comme lors d'un blocage Wi-Fi, puis l'envoi
rattrape son retard en rafale. Le bilan de la carte donne alors la marge minimale
(≈ 300 - MS ms) et le nombre de réamorçages (0 tant que MS < 300 ms).

Exemples :
    python3 tools/wav_to_mqtt.py server/recordings/esp32-01_xxx.wav
    python3 tools/wav_to_mqtt.py --tone 440 --seconds 3
    python3 tools/wav_to_mqtt.py --tone 440 --seconds 5 --jitter 200
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
import threading
import time
from pathlib import Path

import numpy as np

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from voice_server.audio_resampler import AudioFormatError, AudioResampler, peak_dbfs  # noqa: E402
from voice_server.audio_sender import AudioSender, Publisher  # noqa: E402
from voice_server.mqtt_link import MqttLink  # noqa: E402
from voice_server.settings import load_settings  # noqa: E402

logger = logging.getLogger("wav_to_mqtt")

STALL_EVERY = 10  # un chunk sur dix : un blocage toutes les 500 ms


class JitteryLink:
    """Lien MQTT qui retient certains chunks audio : banc d'essai du tampon anti-gigue."""

    def __init__(self, link: Publisher, stall_ms: float) -> None:
        self._link = link
        self._stall_s = stall_ms / 1000.0
        self._chunks = 0
        self.stalls = 0

    def publish(self, device: str, suffix: str, payload: bytes, qos: int) -> None:
        if suffix == "audio/out":
            self._chunks += 1
            if self._stall_s > 0 and self._chunks % STALL_EVERY == 0:
                time.sleep(self._stall_s)  # blocage : les chunks suivants partiront en rafale
                self.stalls += 1
        self._link.publish(device, suffix, payload, qos)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Analyse la ligne de commande."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path, nargs="?", help="fichier WAV a jouer")
    parser.add_argument("--device", default="esp32-01", help="carte destinataire")
    parser.add_argument("--config", type=Path, default=SERVER_DIR / "config.yaml")
    parser.add_argument("--tone", type=float, help="son pur de cette frequence (Hz)")
    parser.add_argument("--seconds", type=float, default=3.0, help="duree du son pur")
    parser.add_argument("--no-normalize", action="store_true", help="garder le niveau d'origine")
    parser.add_argument("--jitter", type=float, default=0.0,
                        help="retient un chunk sur dix pendant ce nombre de ms (etape 12)")
    args = parser.parse_args(argv)
    if (args.wav is None) == (args.tone is None):
        parser.error("donnez soit un fichier WAV, soit --tone")
    return args


def main(argv: list[str]) -> int:
    """Envoie l'audio, puis attend le bilan publié par la carte."""
    args = parse_args(argv)
    settings = load_settings(args.config)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")

    resampler = AudioResampler(settings.audio, None if args.no_normalize else -3.0)
    try:
        if args.tone is not None:
            t = np.arange(int(settings.audio.sample_rate * args.seconds)) / settings.audio.sample_rate
            fade = np.minimum(1.0, np.minimum(t, t[-1] - t) / 0.01)  # fondus de 10 ms
            pcm = resampler.convert(np.sin(2 * np.pi * args.tone * t) * fade, settings.audio.sample_rate)
        else:
            pcm = resampler.from_wav(args.wav)
    except (OSError, AudioFormatError) as error:
        logger.error("%s", error)
        return 1
    logger.info("audio pret : %.2f s, crete %.1f dBFS", len(pcm) / settings.audio.bytes_per_second,
                peak_dbfs(pcm))

    # Client distinct du serveur : deux clients ne doivent jamais partager un identifiant.
    link = MqttLink(dataclasses.replace(settings.mqtt, client_id="voice-wav-to-mqtt"))
    report = threading.Event()

    def on_event(device: str, payload: bytes) -> None:
        if device != args.device:
            return
        event = json.loads(payload)
        if event.get("event") in ("played", "rejected"):
            logger.info("bilan de %s : %s", device, event)
            report.set()

    link.on("event", on_event)
    link.start()
    if not link.wait_connected(5.0):
        logger.error("broker %s:%d injoignable", settings.mqtt.host, settings.mqtt.port)
        link.stop()
        return 1

    sender_link = JitteryLink(link, args.jitter)
    AudioSender(sender_link, settings.audio).send(args.device, pcm)
    if args.jitter > 0:
        logger.info("gigue simulee : %d blocages de %.0f ms ; marge attendue ~%.0f ms, reamorcage si <= 0",
                    sender_link.stalls, args.jitter, 300 - args.jitter)
    if not report.wait(5.0):
        logger.warning("aucun bilan de %s : carte hors ligne, ou firmware anterieur a l'etape 5",
                       args.device)
    link.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
