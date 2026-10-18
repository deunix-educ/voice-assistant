"""Boucle complète d'une question (étapes 6 à 14, simplifiée par la branche fingerprint, F4).

Doigt reconnu par le capteur → personne (fingerprint.users) → transcription → réponse
(+ commande domotique ou machine, si les droits de la personne le permettent) → synthèse → envoi.

Plus de VAD, de diarisation ni d'identification de la voix : la session dure tant que le doigt est
posé, et c'est le doigt qui dit QUI tient la carte. Les sessions du bouton PTT n'ont pas de doigt :
la personne est inconnue.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from voice_server.access_control import Resolution, Verdict
from voice_server.assistant import NOTHING_HEARD, Reply
from voice_server.audio_buffer import read_pcm
from voice_server.audio_sender import Publisher
from voice_server.fingerprint_book import UNKNOWN
from voice_server.machine_control import Command
from voice_server.session_manager import SessionResult
from voice_server.speech_to_text import Transcript
from voice_server.text_to_speech import Speech

logger = logging.getLogger(__name__)

MIN_SESSION_S = 0.3  # en dessous, doigt posé trop brièvement pour contenir un mot


class Fingers(Protocol):
    """Ce que la boucle attend du carnet des empreintes (FingerprintBook)."""

    def person_of(self, finger_id: int | None) -> str | None: ...


class Policy(Protocol):
    """Ce que la boucle attend des droits par personne (AccessPolicy)."""

    def check(self, command: Command, person: str | None) -> Verdict: ...

    def ask(self, board: str, command: Command, person: str | None, finger: int | None) -> None: ...

    def resolve(self, board: str, text: str, person: str | None, finger: int | None) -> Resolution | None: ...


class Transcriber(Protocol):
    """Ce que la boucle attend de la transcription."""

    def transcribe(self, pcm: bytes, words: bool = True) -> Transcript: ...


class Responder(Protocol):
    """Ce que la boucle attend de l'assistant."""

    def respond(self, text: str, speaker: str | None = None, board: str = "") -> Reply: ...


class Controller(Protocol):
    """Ce que la boucle attend de la domotique (HomeController)."""

    def execute(self, board: str, command: Command, speaker: str | None, session: str) -> list[str]: ...


class Synthesizer(Protocol):
    """Ce que la boucle attend de la synthèse vocale."""

    def synthesize(self, text: str) -> Speech: ...


class Player(Protocol):
    """Ce que la boucle attend de l'envoi vers la carte (AudioSender)."""

    def send(self, device: str, pcm: bytes, session: str | None = None) -> str: ...


@dataclass(frozen=True)
class Turn:
    """Bilan d'un échange, pour le journal et les tests."""

    heard: str              # texte transcrit ; vide si rien de reconnu
    answer: str             # texte dit par la carte
    person: str | None      # personne dont le doigt a ouvert la session ; None : bouton ou doigt sans nom
    command: Command | None # commande exécutée : domotique (11), machine (15b)
    refused: bool           # commande refusée ou mise en attente de confirmation (étape 14)
    stt_s: float            # temps de transcription (0 si Whisper n'a pas été appelé)
    tts_s: float            # temps de synthèse
    ready_s: float          # de la fin de session au départ du premier chunk
    answer_s: float         # durée de la réponse parlée


class VoicePipeline:
    """Traite une session terminée et fait dire la réponse par la carte.

    L'appel est bloquant (transcription, puis envoi au rythme réel) : il
    s'exécute dans le fil de travail du serveur, jamais dans le fil réseau.
    """

    def __init__(
        self,
        stt: Transcriber,
        assistant: Responder,
        tts: Synthesizer,
        player: Player,
        fingers: Fingers,
        publisher: Publisher | None = None,
        controller: Controller | None = None,
        policy: Policy | None = None,
        read: Callable[[SessionResult], bytes] = lambda result: read_pcm(result.path),
        clock: Callable[[], float] = time.monotonic,
        silence_rms_dbfs: float = -58.0,
    ) -> None:
        """
        Args:
            fingers: carnet des empreintes : le doigt de la session dit qui tient la carte.
            publisher: publie « qui a dit quoi » sur voice/<carte>/transcript (None : journal seul).
            controller: exécute les commandes domotiques (None : elles sont seulement annoncées).
            policy: droits par personne (None : toute commande est permise).
            read: lecture du PCM d'une session, injectable pour les tests.
            clock: horloge, injectable pour les tests.
            silence_rms_dbfs: session plus faible que cela (RMS, en dBFS) : silence, Whisper n'est pas appelé.
                Sans VAD, c'est le seul filtre avant Whisper, qui invente du texte dans un silence complet.
        """
        self._stt = stt
        self._assistant = assistant
        self._tts = tts
        self._player = player
        self._fingers = fingers
        self._publisher = publisher
        self._controller = controller
        self._policy = policy
        self._read = read
        self._clock = clock
        self._silence_rms_dbfs = silence_rms_dbfs

    def handle(self, result: SessionResult) -> Turn | None:
        """Répond à la session ; None si elle était trop courte pour être une question."""
        began = self._clock()
        if result.duration_s < MIN_SESSION_S:
            logger.warning("%s : session %s trop courte (%.2f s) : doigt leve ou bouton relache trop tot ?",
                           result.device, result.session_id, result.duration_s)
            return None

        person = self._fingers.person_of(result.finger_id)
        if result.finger_id is None:
            logger.info("%s : session %s ouverte par le bouton : personne inconnue", result.device,
                        result.session_id)
        else:
            logger.info("%s : session %s : %s (empreinte %d, score %s)", result.device, result.session_id,
                        person or "personne inconnue", result.finger_id,
                        "?" if result.finger_score is None else result.finger_score)

        # Sans VAD, un silence complet irait à Whisper, qui en tirerait un texte inventé.
        if result.levels.rms_dbfs < self._silence_rms_dbfs:
            logger.warning("%s : session %s silencieuse (RMS %.1f dBFS, seuil %.1f) : rien a transcrire",
                           result.device, result.session_id, result.levels.rms_dbfs, self._silence_rms_dbfs)
            transcript = Transcript(text="", audio_s=0.0, elapsed_s=0.0)
        else:
            # Pas d'horodatage des mots : personne à départager, une seule voix tient la carte (étape 16).
            transcript = self._stt.transcribe(self._read(result), words=False)
        if transcript.dropped:
            logger.warning("%s : hallucination ecartee : %s", result.device, " | ".join(transcript.dropped))

        if transcript.text:
            logger.info("%s : %s : « %s »", result.device, person or UNKNOWN, transcript.text)
            if self._publisher is not None:
                self._publisher.publish(result.device, "transcript",
                                        transcript_json(result, person, transcript), 1)

        reply, refused = self._decide(result.device, transcript.text, person, result.finger_id)
        answer = reply.text
        if reply.command is not None and self._controller is not None:
            # La commande part avant la réponse parlée : la lumière s'allume pendant que la carte le dit.
            self._controller.execute(result.device, reply.command, person, result.session_id)
        speech = self._tts.synthesize(answer)
        ready_s = self._clock() - began

        logger.info("%s : entendu « %s » -> reponse « %s »", result.device, transcript.text, answer)
        logger.info("%s : transcription %.2f s (audio %.1f s), synthese %.2f s, reponse prete en %.2f s",
                    result.device, transcript.elapsed_s, result.duration_s, speech.elapsed_s, ready_s)
        self._player.send(result.device, speech.pcm, f"rep-{result.session_id}")
        return Turn(heard=transcript.text, answer=answer, person=person, command=reply.command, refused=refused,
                    stt_s=transcript.elapsed_s, tts_s=speech.elapsed_s, ready_s=ready_s, answer_s=speech.audio_s)

    def _decide(self, board: str, text: str, person: str | None,
                finger: int | None) -> tuple[Reply, bool]:
        """Réponse et commande autorisée ; True si une commande a été refusée ou mise en attente."""
        if not text:
            return Reply(NOTHING_HEARD), False
        if self._policy is not None:
            # Une confirmation attendue passe avant tout : « oui » n'est pas une requête.
            resolution = self._policy.resolve(board, text, person, finger)
            if resolution is not None:
                return Reply(resolution.answer, resolution.command), resolution.command is None
        reply = self._assistant.respond(text, person, board)
        if reply.command is None or self._policy is None:
            return reply, False
        verdict = self._policy.check(reply.command, person)
        if not verdict.allowed:
            return Reply(verdict.answer or "Je ne peux pas faire cela."), True
        if verdict.confirm:
            self._policy.ask(board, reply.command, person, finger)
            return Reply(verdict.answer or "Confirmez-vous ?"), True
        return reply, False


def transcript_json(result: SessionResult, person: str | None, transcript: Transcript) -> bytes:
    """Message publié sur voice/<carte>/transcript.

    {"session": "a1b2c3", "utterances": [{"speaker": "Denis", "text": "...", "start": 0.0,
     "end": 3.2, "score": null, "finger": 2}]}

    Une seule intervention par session : la personne dont le doigt a ouvert la session. « score » reste
    pour les lecteurs de l'ancien format (identification vocale, supprimée) ; « finger » est nouveau.
    """
    return json.dumps({
        "session": result.session_id,
        "utterances": [{"speaker": person or UNKNOWN, "text": transcript.text, "start": 0.0,
                        "end": round(result.duration_s, 2), "score": None, "finger": result.finger_id}],
    }, ensure_ascii=False).encode("utf-8")
