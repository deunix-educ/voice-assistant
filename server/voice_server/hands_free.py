"""Écoute mains libres : du flux continu du micro à une session de parole (étape 13).

Chaque carte suit trois états :

    VEILLE    le flux passe dans le détecteur de mot de réveil ; seules les
              dernières preroll_ms restent en mémoire, RIEN n'est écrit sur disque ;
    COMMANDE  mot entendu : on garde l'audio jusqu'au silence qui suit la commande
              (VAD de l'étape 7), puis on en fait une session ordinaire ;
    RÉPONSE   la carte parle : on n'écoute plus (elle s'entendrait elle-même)
              jusqu'à son bilan « played », ou jusqu'au délai de garde.

La session produite est identique à celle d'un appui sur le bouton : la suite
de la chaîne (VAD, diarisation, Whisper, assistant) ne change pas.

Les durées d'une commande (silence final, abandon, durée maximale) se mesurent
en AUDIO REÇU, pas à l'horloge : le Wi-Fi livre souvent les chunks en rafale, et
2 s de son arrivées en 10 ms restent 2 s de parole. L'horloge ne sert qu'aux
délais de garde (réponse sans bilan, flux interrompu).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Protocol

from voice_server.audio_buffer import AudioBuffer
from voice_server.audio_sender import Publisher
from voice_server.session_manager import Outcome, SessionResult
from voice_server.settings import AudioSettings, WakeSettings
from voice_server.voice_activity import VadResult
from voice_server.wake_word import Detection

logger = logging.getLogger(__name__)

# Recherche du silence final toutes les 100 ms d'audio (250 ms avant l'étape 16) : la fin de la
# commande est vue en moyenne 75 ms plus tôt, pour une VAD de quelques ms sur le tampon.
CHECK_EVERY_CHUNKS = 2
COMMAND_AFTER_WAKE_S = 0.3  # parole qui continue au moins 0,3 s après le mot : il y a une commande
STREAM_LOST_S = 2.0         # plus aucun chunk pendant une commande : la carte a décroché


class Detector(Protocol):
    """Ce que l'écoute attend du détecteur de mot de réveil."""

    def process(self, pcm: bytes) -> Detection | None: ...

    def reset(self) -> None: ...


class SpeechDetector(Protocol):
    """Ce que l'écoute attend de la VAD."""

    def detect(self, pcm: bytes) -> VadResult: ...


class Mode(Enum):
    """État d'une carte."""

    STANDBY = "veille"
    COMMAND = "commande"
    ANSWER = "reponse"


@dataclass
class _Board:
    """État d'écoute d'une carte."""

    detector: Detector
    preroll: deque[bytes]
    mode: Mode = Mode.STANDBY
    buffer: bytearray = field(default_factory=bytearray)
    wake_offset: int = 0            # octets du tampon qui précèdent la détection
    word: str = ""
    session_id: str = ""
    started_at: datetime = field(default_factory=datetime.now)
    last_chunk_time: float = 0.0   # horloge : détecte un flux interrompu
    unchecked: int = 0             # chunks reçus depuis la dernière recherche du silence final
    chunks: int = 0
    answer_until: float = 0.0
    last_uptime: int = -1


class HandsFreeListener:
    """Transforme le flux continu des cartes en sessions, sur mot de réveil."""

    def __init__(
        self,
        wake: WakeSettings,
        audio: AudioSettings,
        vad: SpeechDetector,
        recordings_dir: Path,
        link: Publisher,
        on_command: Callable[[SessionResult], None],
        detector_factory: Callable[[str], Detector],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            on_command: reçoit chaque commande, comme une session du bouton ; doit rendre la main vite.
            detector_factory: un détecteur neuf par carte (historique audio propre à chacune) ;
                reçoit le nom de la carte.
        """
        self._wake = wake
        self._audio = audio
        self._vad = vad
        self._recordings_dir = recordings_dir
        self._link = link
        self._on_command = on_command
        self._new_detector = detector_factory
        self._clock = clock
        self._lock = threading.Lock()
        self._boards: dict[str, _Board] = {}
        chunk_bytes = audio.bytes_per_second * audio.chunk_ms // 1000
        self._preroll_chunks = max(1, wake.preroll_ms * audio.bytes_per_second // 1000 // chunk_bytes)

    # ------------------------------------------------------------ cartes

    def on_state(self, device: str, payload: bytes) -> None:
        """État publié par une carte : on (ré)active son écoute à la connexion ou au redémarrage."""
        try:
            state = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if state.get("status") != "online":
            with self._lock:
                self._boards.pop(device, None)  # la carte réactivera l'écoute à son retour
            return
        uptime = int(state.get("uptime_s", 0))
        with self._lock:
            board = self._board(device)
            first_time = board.last_uptime < 0 or uptime < board.last_uptime  # nouvelle carte ou redémarrage
            board.last_uptime = uptime
        if first_time:
            self._send(device, {"cmd": "listen", "enabled": True})
            logger.info("%s : ecoute mains libres activee (mots : %s)", device, ", ".join(self._wake.words))

    def stop(self) -> None:
        """Arrêt du serveur : les cartes cessent d'envoyer leur micro."""
        with self._lock:
            devices = list(self._boards)
        for device in devices:
            self._send(device, {"cmd": "listen", "enabled": False})

    # ------------------------------------------------------------ flux audio

    def on_stream(self, device: str, payload: bytes) -> None:
        """Chunk du flux continu (voice/<carte>/audio/stream). Fil réseau : ~1 ms par chunk."""
        if len(payload) % 2 != 0:
            return
        result: SessionResult | None = None
        with self._lock:
            board = self._board(device)
            if board.mode is Mode.STANDBY:
                board.preroll.append(payload)  # seule mémoire de ce qui précède le mot
                detection = board.detector.process(payload)
                if detection is not None:
                    self._start_command(device, board, detection)
            elif board.mode is Mode.COMMAND:
                board.buffer.extend(payload)
                board.chunks += 1
                board.unchecked += 1
                board.last_chunk_time = self._clock()
                result = self._check_command(device, board)
            # Mode.ANSWER : la carte parle ; ce que capte son micro est ignoré.
        if result is not None:
            self._on_command(result)

    def on_event(self, device: str, payload: bytes) -> None:
        """Bilan « played » d'une carte : sa réponse est finie, on réécoute."""
        try:
            event = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if event.get("event") not in ("played", "rejected"):
            return
        with self._lock:
            board = self._boards.get(device)
            if board is not None and board.mode is Mode.ANSWER:
                self._standby(board)

    def sweep(self) -> list[SessionResult]:
        """Fil principal : fin de réponse sans bilan, commande dont le flux s'est arrêté."""
        now = self._clock()
        results: list[SessionResult] = []
        with self._lock:
            for device, board in self._boards.items():
                if board.mode is Mode.ANSWER and now >= board.answer_until:
                    logger.warning("%s : pas de bilan de lecture, reprise de l'ecoute", device)
                    self._standby(board)
                elif board.mode is Mode.COMMAND and now - board.last_chunk_time > STREAM_LOST_S:
                    result = self._finish(device, board, "flux interrompu")
                    if result is not None:
                        results.append(result)
        for result in results:
            self._on_command(result)
        return results

    def mode(self, device: str) -> Mode | None:
        """État d'écoute d'une carte (journal, tests)."""
        with self._lock:
            board = self._boards.get(device)
            return board.mode if board is not None else None

    # ------------------------------------------------------------ interne (verrou tenu)

    def _board(self, device: str) -> _Board:
        board = self._boards.get(device)
        if board is None:
            board = _Board(detector=self._new_detector(device), preroll=deque(maxlen=self._preroll_chunks))
            self._boards[device] = board
        return board

    def _start_command(self, device: str, board: _Board, detection: Detection) -> None:
        board.mode = Mode.COMMAND
        board.buffer = bytearray(b"".join(board.preroll))
        board.preroll.clear()
        board.wake_offset = len(board.buffer)
        board.word = detection.word
        board.session_id = f"w{uuid.uuid4().hex[:5]}"
        board.started_at = datetime.now()
        board.last_chunk_time = self._clock()
        board.unchecked = 0
        board.chunks = self._preroll_chunks
        logger.info("%s : mot de reveil « %s » (%.2f), session %s : j'ecoute la commande",
                    device, detection.word, detection.score, board.session_id)
        self._send(device, {"cmd": "capture", "active": True})

    def _check_command(self, device: str, board: _Board) -> SessionResult | None:
        """Commande finie ? Silence après la parole, rien dit, ou durée maximale (en audio reçu)."""
        elapsed = (len(board.buffer) - board.wake_offset) / self._audio.bytes_per_second
        if elapsed >= self._wake.max_command_s:
            return self._finish(device, board, "duree maximale")
        if board.unchecked < CHECK_EVERY_CHUNKS:
            return None
        board.unchecked = 0

        vad = self._vad.detect(bytes(board.buffer))
        rate = self._audio.sample_rate
        wake_sample = board.wake_offset // 2
        after_wake = [s for s in vad.segments if s.end > wake_sample + COMMAND_AFTER_WAKE_S * rate]
        if not after_wake:
            if elapsed >= self._wake.no_speech_timeout_s:
                logger.info("%s : mot de reveil sans commande, retour en veille", device)
                self._send(device, {"cmd": "capture", "active": False})
                self._standby(board)
            return None
        silence_s = (len(board.buffer) // 2 - after_wake[-1].end) / rate
        if silence_s * 1000 >= self._wake.end_silence_ms:
            return self._finish(device, board, "fin de phrase")
        return None

    def _finish(self, device: str, board: _Board, reason: str) -> SessionResult | None:
        """La commande devient une session ordinaire, écrite en WAV comme celles du bouton."""
        audio = AudioBuffer(self._audio, self._wake.max_command_s + 1)
        audio.append(bytes(board.buffer))
        stamp = board.started_at.strftime("%Y%m%d-%H%M%S")
        path = self._recordings_dir / f"{device}_{stamp}_{board.session_id}.wav"
        audio.write_wav(path)
        result = SessionResult(device=device, session_id=board.session_id, outcome=Outcome.COMPLETE,
                               path=path, duration_s=audio.duration_s, chunks_received=board.chunks,
                               chunks_announced=None, levels=audio.levels())
        logger.info("%s : commande %s finie (%s), %.2f s apres « %s » -> %s", device, board.session_id,
                    reason, audio.duration_s, board.word, path.name)
        self._send(device, {"cmd": "capture", "active": False})
        board.mode = Mode.ANSWER
        board.buffer = bytearray()
        board.answer_until = self._clock() + self._wake.cooldown_s
        return result

    def _standby(self, board: _Board) -> None:
        board.mode = Mode.STANDBY
        board.buffer = bytearray()
        board.preroll.clear()
        board.detector.reset()  # sinon l'historique du mot précédent pourrait redéclencher

    def _send(self, device: str, message: dict[str, object]) -> None:
        self._link.publish(device, "control", json.dumps(message).encode(), 1)
