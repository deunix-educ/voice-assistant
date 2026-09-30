"""Tests du chemin de sortie, étape 5 : mise au format et envoi rythmé (sans broker)."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from voice_server.audio_resampler import AudioFormatError, AudioResampler, peak_dbfs
from voice_server.audio_sender import AudioSender, send_times
from voice_server.settings import AudioSettings

AUDIO = AudioSettings(sample_rate=16000, bits=16, channels=1, codec="pcm_s16le", chunk_ms=50)


def write_wav(path: Path, samples: np.ndarray, rate: int, width: int) -> None:
    """Écrit un WAV PCM entier de largeur donnée (1 à 4 octets) à partir de flottants."""
    channels = 1 if samples.ndim == 1 else samples.shape[1]
    scale = {1: 127, 2: 32767, 3: 8388607, 4: 2147483647}[width]
    ints = np.round(samples.reshape(-1) * scale).astype(np.int64)
    if width == 1:
        raw = (ints + 128).astype(np.uint8).tobytes()
    else:
        raw = b"".join(int(v).to_bytes(width, "little", signed=True) for v in ints)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(raw)


def sine(rate: int, seconds: float, amplitude: float = 0.1) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return amplitude * np.sin(2 * np.pi * 440 * t)


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_any_pcm_width_is_read(tmp_path: Path, width: int) -> None:
    """WAV 8, 16, 24 et 32 bits : lus, mis en 16 kHz, crête à -3 dBFS."""
    path = tmp_path / f"w{width}.wav"
    write_wav(path, sine(16000, 0.5), 16000, width)

    pcm = AudioResampler(AUDIO).from_wav(path)

    assert len(pcm) == 16000  # 0,5 s x 16 000 x 2 octets
    assert peak_dbfs(pcm) == pytest.approx(-3.0, abs=0.1)


def test_cd_stereo_is_converted_to_system_format(tmp_path: Path) -> None:
    """44,1 kHz stéréo -> 16 kHz mono ; durée conservée."""
    path = tmp_path / "cd.wav"
    stereo = np.stack([sine(44100, 1.0), sine(44100, 1.0)], axis=1)
    write_wav(path, stereo, 44100, 2)

    pcm = AudioResampler(AUDIO).from_wav(path)

    assert len(pcm) // 2 == pytest.approx(16000, abs=2)


def test_piper_rate_is_resampled(tmp_path: Path) -> None:
    """22 050 Hz (sortie habituelle de Piper, étape 6) -> 16 kHz, fréquence conservée."""
    pcm = AudioResampler(AUDIO).convert(sine(22050, 1.0), 22050)
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)

    spectrum = np.abs(np.fft.rfft(samples))
    dominant = np.fft.rfftfreq(samples.size, 1 / 16000)[int(np.argmax(spectrum))]
    assert dominant == pytest.approx(440.0, abs=2.0)


def test_normalization_can_be_disabled() -> None:
    """Sans normalisation, le niveau d'origine est gardé (-20 dBFS ici)."""
    pcm = AudioResampler(AUDIO, target_peak_dbfs=None).convert(sine(16000, 0.5, 0.1), 16000)

    assert peak_dbfs(pcm) == pytest.approx(-20.0, abs=0.1)


def test_unreadable_file_suggests_conversion(tmp_path: Path) -> None:
    """Un fichier qui n'est pas un WAV PCM : message avec la commande ffmpeg."""
    path = tmp_path / "faux.wav"
    path.write_bytes(b"RIFF....WAVEfmt pas vraiment")

    with pytest.raises(AudioFormatError, match="ffmpeg"):
        AudioResampler(AUDIO).from_wav(path)


class FakeLink:
    """Lien MQTT factice : mémorise les publications et l'instant de chacune."""

    def __init__(self, clock: "FakeTime") -> None:
        self.clock = clock
        self.sent: list[tuple[float, str, bytes, int]] = []

    def publish(self, device: str, suffix: str, payload: bytes, qos: int) -> None:
        self.sent.append((self.clock.now, suffix, payload, qos))


class FakeTime:
    """Horloge et sommeil simulés : le test dure 0 s quelle que soit la durée du son."""

    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_sender_protocol_and_pacing() -> None:
    """START (QoS 1), 10 chunks (QoS 0) dont 6 d'avance, END annonçant 10 chunks."""
    time = FakeTime()
    link = FakeLink(time)
    sender = AudioSender(link, AUDIO, prefill_chunks=6, sleep=time.sleep, clock=time.clock)

    session = sender.send("esp32-01", bytes(1600 * 9 + 801))  # 9 chunks pleins + 1 partiel

    kinds = [(suffix, qos) for _, suffix, _, qos in link.sent]
    assert kinds[0] == ("control", 1) and kinds[-1] == ("control", 1)
    audio = [(t, payload) for t, suffix, payload, _ in link.sent if suffix == "audio/out"]
    assert [len(p) for _, p in audio] == [1600] * 9 + [800]  # l'octet orphelin est retiré
    assert [t for t, _ in audio[:6]] == [0.0] * 6            # l'avance part d'un coup
    assert [round(t, 3) for t, _ in audio[6:]] == [0.05, 0.1, 0.15, 0.2]

    start = json.loads(link.sent[0][2])
    end = json.loads(link.sent[-1][2])
    assert (start["event"], start["rate"], start["session"]) == ("start", 16000, session)
    assert (end["event"], end["chunks"], end["session"]) == ("end", 10, session)


def test_send_times_matches_serial_tool() -> None:
    """Même calendrier que l'outil série de l'étape 2."""
    assert send_times(5, 0.05, 2) == pytest.approx([0.0, 0.0, 0.05, 0.10, 0.15])


def test_jitter_bench_holds_one_chunk_in_ten() -> None:
    """Étape 12 : le banc retient un chunk audio sur dix, jamais START/END."""
    from wav_to_mqtt import JitteryLink

    class Sink:
        def __init__(self) -> None:
            self.topics: list[str] = []

        def publish(self, device: str, suffix: str, payload: bytes, qos: int) -> None:
            self.topics.append(suffix)

    sink = Sink()
    bench = JitteryLink(sink, stall_ms=1.0)
    bench.publish("esp32-01", "control", b"{}", 1)
    for _ in range(25):
        bench.publish("esp32-01", "audio/out", bytes(1600), 0)

    assert bench.stalls == 2  # chunks 10 et 20
    assert len(sink.topics) == 26  # rien n'est perdu, seulement retardé
