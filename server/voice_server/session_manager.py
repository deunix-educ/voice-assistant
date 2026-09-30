"""Reconstitution des sessions de parole reçues des ESP32 : START, chunks, END."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from voice_server.audio_buffer import AudioBuffer, Levels
from voice_server.settings import AudioSettings, SessionSettings

logger = logging.getLogger(__name__)


class Outcome(Enum):
    """Façon dont une session s'est terminée."""

    COMPLETE = "complete"        # END reçu, tous les chunks annoncés sont arrivés
    LOST_CHUNKS = "lost_chunks"  # END reçu, mais des chunks manquent (QoS 0 : perte réseau)
    TIMEOUT = "timeout"          # jamais de END : l'ESP32 a disparu en cours de session
    INTERRUPTED = "interrupted"  # un nouveau START est arrivé avant le END


@dataclass(frozen=True)
class SessionResult:
    """Bilan d'une session terminée."""

    device: str
    session_id: str
    outcome: Outcome
    path: Path
    duration_s: float
    chunks_received: int
    chunks_announced: int | None  # None si aucun END n'a été reçu
    levels: Levels


@dataclass
class _Session:
    """Session en cours de réception."""

    device: str
    session_id: str
    buffer: AudioBuffer
    started_at: datetime
    last_activity: float
    chunks_received: int = 0
    announced: int | None = None           # nombre de chunks annoncé par le END
    end_seen_at: float | None = None
    end_report: dict[str, Any] = field(default_factory=dict[str, Any])


class SessionManager:
    """Assemble les sessions de chaque carte et les écrit en WAV.

    Appelée depuis le fil réseau (messages MQTT) et depuis le fil principal
    (sweep) : un verrou protège l'état.

    Les messages audio sont en QoS 0 : ils peuvent se perdre. Le END annonce le
    nombre de chunks envoyés ; la comparaison avec le nombre reçu mesure la perte.
    Et comme le START et le END peuvent eux-mêmes se perdre (l'ESP32 ne sait
    publier qu'en QoS 0), une session sans END est close après un délai d'inactivité.
    """

    def __init__(
        self,
        audio: AudioSettings,
        sessions: SessionSettings,
        recordings_dir: Path,
        clock: Callable[[], float] = time.monotonic,
        on_finished: Callable[[SessionResult], None] | None = None,
    ) -> None:
        """
        Args:
            on_finished: appelé pour chaque session terminée, depuis le fil qui la
                termine (réseau ou principal) : il doit rendre la main aussitôt.
        """
        self._on_finished = on_finished
        self._audio = audio
        self._settings = sessions
        self._recordings_dir = recordings_dir
        self._clock = clock
        self._lock = threading.Lock()
        self._active: dict[str, _Session] = {}
        self.orphan_chunks = 0  # chunks reçus hors de toute session

    # ------------------------------------------------------------ messages reçus

    def on_event(self, device: str, payload: bytes) -> list[SessionResult]:
        """Traite un message JSON de voice/<device>/event."""
        try:
            event: dict[str, Any] = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("%s : evenement illisible : %r", device, payload[:80])
            return []

        kind = event.get("event")
        with self._lock:
            if kind == "start":
                return self._start(device, event)
            if kind == "end":
                return self._end(device, event)
        if kind == "boot":
            # Tout redémarrage autre qu'une mise sous tension mérite l'attention :
            # brownout = alimentation trop faible, panic = plantage, watchdog = blocage.
            reason = event.get("reason")
            log = logger.info if reason in ("poweron", "external", "software") else logger.warning
            log("%s : demarrage de la carte, cause : %s", device, reason)
        elif kind == "device":
            # Étape 11 : la carte confirme qu'elle a exécuté une commande de sa pièce.
            log = logger.info if event.get("ok") else logger.warning
            log("%s : commande %s %s %s, etat %s", device, event.get("device"), event.get("action"),
                "executee" if event.get("ok") else "REFUSEE", event.get("state"))
        elif kind == "played":
            # Étape 12 : « underruns » compte les réamorçages (réserve épuisée, pause propre) ;
            # la marge minimale dit combien de retard réseau restait absorbable.
            log = logger.warning if event.get("underruns") else logger.info
            log("%s : lecture %s jouee, chunks %s/%s, reamorcages %s %s, premier son a %s ms, marge mini %s ms",
                device, event.get("session"), event.get("chunks"), event.get("announced"),
                event.get("underruns"), event.get("gaps_ms") or "", event.get("start_ms"),
                event.get("min_margin_ms"))
        else:
            logger.info("%s : evenement %s", device, event)
        return []

    def on_audio(self, device: str, payload: bytes) -> None:
        """Traite un chunk PCM de voice/<device>/audio/in."""
        with self._lock:
            session = self._active.get(device)
            if session is None:
                self.orphan_chunks += 1
                return
            if len(payload) % 2 != 0:
                logger.warning("%s : chunk de %d octets ignore (impair)", device, len(payload))
                return
            session.buffer.append(payload)
            session.chunks_received += 1
            session.last_activity = self._clock()

    def sweep(self) -> list[SessionResult]:
        """Clôt les sessions dont le END est passé (délai de grâce) ou restées muettes."""
        now = self._clock()
        results: list[SessionResult] = []
        with self._lock:
            for device, session in list(self._active.items()):
                if session.end_seen_at is not None:
                    if now - session.end_seen_at >= self._settings.end_grace_s:
                        results.append(self._finish(device, self._outcome_after_end(session)))
                elif now - session.last_activity >= self._settings.idle_timeout_s:
                    results.append(self._finish(device, Outcome.TIMEOUT))
        return results

    # ------------------------------------------------------------ interne

    def _start(self, device: str, event: dict[str, Any]) -> list[SessionResult]:
        results: list[SessionResult] = []
        previous = self._active.get(device)
        if previous is not None:
            # END déjà reçu, délai de grâce en cours : la session précédente est
            # finie, pas interrompue. Son bilan se juge sur ce que le END annonçait.
            if previous.end_seen_at is not None:
                outcome = self._outcome_after_end(previous)
            else:
                outcome = Outcome.INTERRUPTED
            results.append(self._finish(device, outcome))

        announced = (event.get("rate"), event.get("bits"), event.get("channels"), event.get("codec"))
        expected = (self._audio.sample_rate, self._audio.bits, self._audio.channels, self._audio.codec)
        if announced != expected:
            logger.error("%s : session refusee, format %s au lieu de %s", device, announced, expected)
            return results

        session_id = str(event.get("session", "inconnue"))
        self._active[device] = _Session(
            device=device,
            session_id=session_id,
            buffer=AudioBuffer(self._audio, self._settings.max_seconds),
            started_at=datetime.now(),
            last_activity=self._clock(),
        )
        logger.info("%s : session %s ouverte", device, session_id)
        return results

    def _end(self, device: str, event: dict[str, Any]) -> list[SessionResult]:
        session = self._active.get(device)
        if session is None or event.get("session") != session.session_id:
            logger.warning("%s : END sans session correspondante (%s)", device, event.get("session"))
            return []

        session.announced = int(event.get("chunks", 0))
        session.end_report = event
        session.end_seen_at = self._clock()

        # Tout est arrivé : inutile d'attendre le délai de grâce.
        if session.chunks_received >= session.announced:
            return [self._finish(device, Outcome.COMPLETE)]
        return []

    @staticmethod
    def _outcome_after_end(session: _Session) -> Outcome:
        if session.announced is not None and session.chunks_received < session.announced:
            return Outcome.LOST_CHUNKS
        return Outcome.COMPLETE

    def _finish(self, device: str, outcome: Outcome) -> SessionResult:
        session = self._active.pop(device)
        stamp = session.started_at.strftime("%Y%m%d-%H%M%S")
        path = self._recordings_dir / f"{device}_{stamp}_{session.session_id}.wav"
        session.buffer.write_wav(path)

        result = SessionResult(
            device=device,
            session_id=session.session_id,
            outcome=outcome,
            path=path,
            duration_s=session.buffer.duration_s,
            chunks_received=session.chunks_received,
            chunks_announced=session.announced,
            levels=session.buffer.levels(),
        )
        self._log(result, session)
        if self._on_finished is not None:
            self._on_finished(result)
        return result

    @staticmethod
    def _log(result: SessionResult, session: _Session) -> None:
        announced = "?" if result.chunks_announced is None else str(result.chunks_announced)
        logger.info(
            "%s : session %s %s : %s, %.2f s, chunks %d/%s, crete %.1f dBFS, RMS %.1f dBFS",
            result.device, result.session_id, result.outcome.value, result.path.name,
            result.duration_s, result.chunks_received, announced,
            result.levels.peak_dbfs, result.levels.rms_dbfs,
        )
        report = session.end_report
        if report.get("send_failures") or report.get("capture_overruns"):
            logger.warning(
                "%s : cote ESP32, %s echecs d'envoi et %s chunks perdus faute de place",
                result.device, report.get("send_failures"), report.get("capture_overruns"),
            )
        if result.outcome is Outcome.LOST_CHUNKS and result.chunks_announced:
            lost = result.chunks_announced - result.chunks_received
            logger.warning("%s : %d chunks perdus en route (%.0f %%) : signal Wi-Fi ?",
                           result.device, lost, 100.0 * lost / result.chunks_announced)
        if session.buffer.truncated:
            logger.warning("%s : session tronquee a sa duree maximale", result.device)
