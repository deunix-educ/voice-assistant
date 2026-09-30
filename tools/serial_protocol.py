"""Protocole série partagé entre le PC et l'ESP32 (étapes 1 et 2).

Même format dans les deux sens, et identique à firmware/src/SerialProtocol.h :

    "VSA1" | type (1 octet) | length (uint16 LE) | payload | checksum (XOR, 1 octet)

Les lignes de texte de l'ESP32 (commençant par « # ») peuvent s'intercaler entre
les trames : le décodeur se resynchronise sur le mot magique.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterator, Protocol

logger = logging.getLogger("serial_protocol")

MAGIC = b"VSA1"
HEADER_SIZE = 7  # magic (4) + type (1) + length (2)
MAX_PAYLOAD = 2048  # garde-fou, identique au firmware : un chunk légitime fait 1600 octets


class FrameType(IntEnum):
    """Types de trames."""

    START = 1
    AUDIO = 2
    END = 3


@dataclass(frozen=True)
class Frame:
    """Trame décodée."""

    type: FrameType
    payload: bytes


@dataclass(frozen=True)
class AudioFormat:
    """Format audio annoncé par la trame START."""

    sample_rate: int
    bits: int
    channels: int

    @classmethod
    def from_payload(cls, payload: bytes) -> "AudioFormat":
        """Décode le descripteur de 8 octets de la trame START."""
        if len(payload) != 8:
            raise ValueError(f"descripteur START invalide ({len(payload)} octets)")
        sample_rate = int.from_bytes(payload[0:4], "little")
        bits = int.from_bytes(payload[4:6], "little")
        channels = int.from_bytes(payload[6:8], "little")
        return cls(sample_rate=sample_rate, bits=bits, channels=channels)

    def to_payload(self) -> bytes:
        """Encode le descripteur de 8 octets de la trame START."""
        return (
            self.sample_rate.to_bytes(4, "little")
            + self.bits.to_bytes(2, "little")
            + self.channels.to_bytes(2, "little")
        )


def encode_frame(frame_type: FrameType, payload: bytes = b"") -> bytes:
    """Construit une trame complète, prête à écrire sur le port série."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload de {len(payload)} octets, maximum {MAX_PAYLOAD}")
    checksum = 0
    for byte in payload:
        checksum ^= byte
    header = MAGIC + bytes([int(frame_type)]) + len(payload).to_bytes(2, "little")
    return header + payload + bytes([checksum])


class ByteSource(Protocol):
    """Ce dont le décodeur a besoin d'un port série : lire des octets."""

    def read(self, size: int = 1) -> bytes: ...


class TextEcho:
    """Affiche ligne par ligne le texte émis par l'ESP32."""

    def __init__(self) -> None:
        self._pending = bytearray()

    def feed(self, data: bytes) -> None:
        """Ajoute des octets de texte ; journalise chaque ligne complète."""
        self._pending.extend(data)
        while True:
            end = self._pending.find(b"\n")
            if end < 0:
                break
            line = bytes(self._pending[:end]).decode("utf-8", errors="replace").strip()
            del self._pending[: end + 1]
            if line:
                logger.info("esp32 | %s", line)


class FrameReader:
    """Décode le flux série en trames, en tolérant du texte intercalé."""

    def __init__(self, port: ByteSource) -> None:
        self._port = port
        self._buffer = bytearray()
        self._echo = TextEcho()

    def frames(self) -> Iterator[Frame]:
        """Produit les trames au fil de leur arrivée (générateur infini)."""
        while True:
            chunk = self._port.read(1024)
            if chunk:
                self._buffer.extend(chunk)
            yield from self._drain()

    def _drain(self) -> Iterator[Frame]:
        """Extrait toutes les trames complètes présentes dans le tampon."""
        while True:
            index = self._buffer.find(MAGIC)

            if index < 0:
                # Pas de mot magique : tout est du texte, sauf les derniers octets
                # qui pourraient être un mot magique coupé en deux lectures.
                keep = len(MAGIC) - 1
                if len(self._buffer) > keep:
                    self._echo.feed(bytes(self._buffer[:-keep]))
                    del self._buffer[:-keep]
                return

            if index > 0:
                self._echo.feed(bytes(self._buffer[:index]))
                del self._buffer[:index]

            if len(self._buffer) < HEADER_SIZE:
                return  # en-tête incomplet : on attend la suite

            raw_type = self._buffer[4]
            length = int.from_bytes(self._buffer[5:7], "little")

            if raw_type not in (int(t) for t in FrameType) or length > MAX_PAYLOAD:
                # Fausse détection : on avance d'un octet et on cherche à nouveau.
                del self._buffer[:1]
                continue

            total = HEADER_SIZE + length + 1  # + checksum
            if len(self._buffer) < total:
                return  # trame incomplète : on attend la suite

            payload = bytes(self._buffer[HEADER_SIZE : HEADER_SIZE + length])
            checksum = self._buffer[total - 1]
            computed = 0
            for byte in payload:
                computed ^= byte

            if checksum != computed:
                logger.warning("checksum invalide, trame ignoree")
                del self._buffer[:1]
                continue

            del self._buffer[:total]
            yield Frame(type=FrameType(raw_type), payload=payload)
