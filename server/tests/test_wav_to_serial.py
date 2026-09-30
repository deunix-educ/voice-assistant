"""Tests de l'outil de lecture de l'étape 2 (sans matériel)."""

from __future__ import annotations

import struct
import sys
import wave
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from wav_to_serial import FormatError, read_wav, send_times, split_chunks, tone_pcm  # noqa: E402


def test_send_times_keeps_constant_lead() -> None:
    """Les chunks d'avance partent tout de suite, puis un toutes les 50 ms."""
    times = send_times(count=7, chunk_s=0.05, prefill=4)

    assert times[:4] == [0.0, 0.0, 0.0, 0.0]
    assert times[4:] == pytest.approx([0.05, 0.10, 0.15])


def test_split_chunks_never_cuts_a_sample() -> None:
    """Tous les chunks ont une taille paire, le dernier compris."""
    chunks = split_chunks(bytes(3203), chunk_bytes=1600)

    assert [len(chunk) for chunk in chunks] == [1600, 1600, 2]


def test_tone_has_expected_length_and_level() -> None:
    """Un son pur de 0,5 s à 25 % : 8000 échantillons, crête proche de 8192."""
    pcm = tone_pcm(440.0, 0.5, amplitude=0.25)
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)

    assert len(samples) == 8000
    assert 8000 < max(samples) <= 8192
    assert abs(samples[0]) < 50 and abs(samples[-1]) < 50  # fondus : pas de clic


def test_read_wav_rejects_wrong_format(tmp_path: Path) -> None:
    """Un WAV 44,1 kHz stéréo est refusé, avec la commande de conversion."""
    path = tmp_path / "cd.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        wav.writeframes(bytes(400))

    with pytest.raises(FormatError, match="ffmpeg"):
        read_wav(path)


def test_read_wav_accepts_system_format(tmp_path: Path) -> None:
    """Un WAV 16 kHz, 16 bits, mono est lu tel quel."""
    path = tmp_path / "ok.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(bytes(320))

    assert len(read_wav(path)) == 320
