"""Tests du décodeur de trames série utilisé à l'étape 1.

Ces tests ne nécessitent aucun matériel : un faux port série rejoue des octets.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from serial_protocol import (  # noqa: E402
    AudioFormat,
    Frame,
    FrameReader,
    FrameType,
    encode_frame,
)
from serial_to_wav import measure_levels, suggested_gain_factor  # noqa: E402
from wav_inspect import column_peaks, render, speech_ratio  # noqa: E402


class FakeSerial:
    """Faux port série : restitue une suite d'octets par petits morceaux."""

    def __init__(self, data: bytes, block_size: int = 7) -> None:
        self._data = data
        self._block_size = block_size
        self._position = 0

    def read(self, size: int = 1) -> bytes:
        """Retourne au plus block_size octets, puis des chaînes vides."""
        count = min(size, self._block_size, len(self._data) - self._position)
        if count <= 0:
            return b""
        chunk = self._data[self._position : self._position + count]
        self._position += count
        return chunk


def build_frame(frame_type: FrameType, payload: bytes) -> bytes:
    """Construit une trame avec l'encodeur partagé, celui qu'utilise wav_to_serial."""
    return encode_frame(frame_type, payload)


def descriptor(sample_rate: int = 16000, bits: int = 16, channels: int = 1) -> bytes:
    """Construit le descripteur de 8 octets de la trame START."""
    return (
        sample_rate.to_bytes(4, "little")
        + bits.to_bytes(2, "little")
        + channels.to_bytes(2, "little")
    )


def collect(data: bytes, expected: int) -> list[Frame]:
    """Décode le flux et retourne les `expected` premières trames."""
    reader = FrameReader(FakeSerial(data))
    frames: list[Frame] = []
    for frame in reader.frames():
        frames.append(frame)
        if len(frames) == expected:
            break
    return frames


def test_decodes_a_full_session() -> None:
    """START + deux chunks + END sont décodés dans l'ordre."""
    pcm = bytes(range(0, 200)) * 8  # 1600 octets
    stream = (
        build_frame(FrameType.START, descriptor())
        + build_frame(FrameType.AUDIO, pcm)
        + build_frame(FrameType.AUDIO, pcm)
        + build_frame(FrameType.END, b"")
    )

    frames = collect(stream, 4)

    assert [f.type for f in frames] == [
        FrameType.START,
        FrameType.AUDIO,
        FrameType.AUDIO,
        FrameType.END,
    ]
    assert frames[1].payload == pcm
    assert AudioFormat.from_payload(frames[0].payload) == AudioFormat(16000, 16, 1)


def test_skips_interleaved_text() -> None:
    """Les messages texte de l'ESP32 n'empêchent pas le décodage."""
    stream = (
        b"# micro pret\n"
        + build_frame(FrameType.START, descriptor())
        + b"\n# session terminee\n"
        + build_frame(FrameType.END, b"")
    )

    frames = collect(stream, 2)

    assert [f.type for f in frames] == [FrameType.START, FrameType.END]


def test_rejects_bad_checksum() -> None:
    """Une trame corrompue est ignorée, la suivante est retrouvée."""
    broken = bytearray(build_frame(FrameType.AUDIO, b"\x01\x02\x03\x04"))
    broken[-1] ^= 0xFF  # checksum faux
    stream = bytes(broken) + build_frame(FrameType.END, b"")

    frames = collect(stream, 1)

    assert frames[0].type is FrameType.END


def test_measure_levels_on_silence() -> None:
    """Un flux nul est signalé comme absence totale de signal."""
    level = measure_levels(b"\x00\x00" * 1000)

    assert level.peak == 0.0
    assert level.rms == 0.0


def test_measure_levels_on_known_amplitude() -> None:
    """Un carré à la moitié de l'échelle donne crête et RMS à 50 %."""
    pcm = (struct.pack("<h", 16384) + struct.pack("<h", -16384)) * 500

    level = measure_levels(pcm)

    assert abs(level.peak - 0.5) < 0.001
    assert abs(level.rms - 0.5) < 0.001
    assert abs(level.dc) < 0.001


def test_suggested_gain_factor() -> None:
    """Le facteur conseillé est une puissance de 2 qui vise 25 % de crête."""
    assert suggested_gain_factor(0.015) == 16
    assert suggested_gain_factor(0.12) == 2
    assert suggested_gain_factor(0.0) == 0


def test_envelope_follows_amplitude() -> None:
    """L'enveloppe dessine un creux entre deux bouffées de signal."""
    loud = struct.pack("<h", 16000) * 1000
    quiet = struct.pack("<h", 0) * 1000
    peaks = column_peaks(loud + quiet + loud, columns=3)

    assert peaks[0] > 0.4 and peaks[2] > 0.4
    assert peaks[1] == 0.0

    drawing = render(peaks)
    assert drawing[0] == drawing[2]
    assert drawing[1] != drawing[0]


def test_speech_ratio_detects_alternation() -> None:
    """Un signal constant n'est pas de la parole, une alternance oui."""
    steady = struct.pack("<h", 8000) * 3000
    assert speech_ratio(column_peaks(steady, columns=6)) == 0.0

    burst = struct.pack("<h", 0) * 2000 + struct.pack("<h", 8000) * 1000
    assert speech_ratio(column_peaks(burst, columns=6)) > 0.0


def test_audio_format_round_trip() -> None:
    """Le descripteur START s'encode et se décode sans perte."""
    fmt = AudioFormat(16000, 16, 1)
    assert AudioFormat.from_payload(fmt.to_payload()) == fmt


def test_encode_frame_rejects_oversized_payload() -> None:
    """Un payload plus grand que ce que l'ESP32 accepte est refusé dès l'envoi."""
    try:
        encode_frame(FrameType.AUDIO, bytes(4096))
    except ValueError:
        return
    raise AssertionError("un payload de 4096 octets aurait du etre refuse")
