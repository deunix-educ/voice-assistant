"""Boucle complète d'une question (étapes 6 à 14).

VAD → diarisation → identification → transcription → qui a dit quoi → réponse
(+ commande domotique, si les droits de la personne le permettent) → synthèse → envoi.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from voice_server.access_control import Resolution, Verdict
from voice_server.assistant import NOTHING_HEARD, Reply
from voice_server.audio_buffer import read_pcm
from voice_server.audio_sender import Publisher
from voice_server.diarization import Diarization, SpeakerTurn
from voice_server.machine_control import Command
from voice_server.session_manager import SessionResult
from voice_server.speaker_attribution import Utterance, attribute, transcript_json
from voice_server.speaker_identifier import UNKNOWN, Identification
from voice_server.speech_to_text import Transcript
from voice_server.text_to_speech import Speech
from voice_server.voice_activity import VadResult

logger = logging.getLogger(__name__)

MIN_SPEECH_S = 0.3  # en dessous, appui trop bref pour contenir un mot


class SpeechDetector(Protocol):
    """Ce que la boucle attend de la détection d'activité vocale."""

    def detect(self, pcm: bytes) -> VadResult: ...


class SpeakerSplitter(Protocol):
    """Ce que la boucle attend de la diarisation."""

    def diarize(self, pcm: bytes) -> Diarization: ...


class Identifier(Protocol):
    """Ce que la boucle attend de l'identification du locuteur."""

    def identify(self, embedding: np.ndarray) -> Identification: ...


class Policy(Protocol):
    """Ce que la boucle attend des droits par profil (AccessPolicy)."""

    def check(self, command: Command, speaker: str | None, score: float | None) -> Verdict: ...

    def ask(self, board: str, command: Command, speaker: str | None) -> None: ...

    def resolve(self, board: str, text: str, speaker: str | None, closest: str | None = None,
                score: float | None = None) -> Resolution | None: ...


class Transcriber(Protocol):
    """Ce que la boucle attend de la transcription."""

    def transcribe(self, pcm: bytes) -> Transcript: ...


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
    speech_s: float         # parole trouvée par la VAD (0 : appui muet)
    speakers: int           # locuteurs trouvés par la diarisation (0 : non faite)
    names: tuple[str, ...]  # nom ou « Inconnu » de chaque locuteur, ordre de la diarisation
    utterances: tuple[Utterance, ...]  # qui a dit quoi (étape 10)
    command: Command | None            # commande exécutée : domotique (11), machine (15b)
    refused: bool                      # commande refusée ou mise en attente de confirmation (étape 14)
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
        vad: SpeechDetector,
        stt: Transcriber,
        assistant: Responder,
        tts: Synthesizer,
        player: Player,
        diarizer: SpeakerSplitter | None = None,
        identifier: Identifier | None = None,
        publisher: Publisher | None = None,
        controller: Controller | None = None,
        policy: Policy | None = None,
        read: Callable[[SessionResult], bytes] = lambda result: read_pcm(result.path),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            diarizer: diarisation, None pour s'en passer (Raspberry Pi 4).
            identifier: identification ; sans diarisation, elle n'a pas d'empreintes.
            publisher: publie « qui a dit quoi » sur voice/<carte>/transcript (None : journal seul).
            controller: exécute les commandes domotiques (None : elles sont seulement annoncées).
            policy: droits par profil (None : toute commande est permise).
            read: lecture du PCM d'une session, injectable pour les tests.
            clock: horloge, injectable pour les tests.
        """
        self._vad = vad
        self._stt = stt
        self._assistant = assistant
        self._tts = tts
        self._player = player
        self._diarizer = diarizer
        self._identifier = identifier
        self._publisher = publisher
        self._controller = controller
        self._policy = policy
        self._read = read
        self._clock = clock

    def handle(self, result: SessionResult) -> Turn | None:
        """Répond à la session ; None si elle était trop courte pour être une question."""
        began = self._clock()
        if result.duration_s < MIN_SPEECH_S:
            logger.warning("%s : session %s trop courte (%.2f s) : bouton relache trop tot ?",
                           result.device, result.session_id, result.duration_s)
            return None

        speakers = 0
        turns: tuple[SpeakerTurn, ...] = ()
        verdicts: dict[str, Identification] = {}
        # Sans parole, Whisper n'est pas appelé : 1 s de calcul pour un texte souvent inventé.
        vad = self._vad.detect(self._read(result))
        if vad.has_speech:
            logger.info("%s : parole %.2f s sur %.2f s (segments %s s), VAD %.0f ms", result.device,
                        vad.speech_s, vad.total_s, vad.bounds(), vad.elapsed_s * 1000)
            # La diarisation et Whisper reçoivent le même audio : leurs instants concordent (étape 10).
            if self._diarizer is not None:
                diarization = self._diarizer.diarize(vad.speech_pcm)
                speakers = len(diarization.speakers)
                logger.info("%s : %d locuteur(s) : %s, diarisation %.2f s", result.device, speakers,
                            diarization.describe() or "aucun tour", diarization.elapsed_s)
                turns = diarization.turns
                verdicts = self._identify(result.device, diarization)
            transcript = self._stt.transcribe(vad.speech_pcm)
        else:
            logger.warning("%s : session %s sans parole (VAD, crete %.1f dBFS)", result.device,
                           result.session_id, result.levels.peak_dbfs)
            transcript = Transcript(text="", audio_s=0.0, elapsed_s=0.0)
        if transcript.dropped:
            logger.warning("%s : hallucination ecartee : %s", result.device, " | ".join(transcript.dropped))

        utterances = self._who_said_what(result, transcript, turns, verdicts)
        # On répond à la dernière personne qui a parlé : c'est elle qui attend la réponse,
        # et ce sont ses droits qui s'appliquent.
        last = utterances[-1] if utterances else None
        speaker = last.speaker if last is not None and last.speaker != UNKNOWN else None
        score = last.score if last is not None else None
        verdict = verdicts.get(last.label) if last is not None else None
        closest = verdict.closest if verdict is not None else None
        reply, refused = self._decide(result.device, transcript.text, speaker, score, closest)
        answer = reply.text
        if reply.command is not None and self._controller is not None:
            # La commande part avant la réponse parlée : la lumière s'allume pendant que la carte le dit.
            self._controller.execute(result.device, reply.command, speaker, result.session_id)
        speech = self._tts.synthesize(answer)
        ready_s = self._clock() - began

        logger.info("%s : entendu « %s » -> reponse « %s »", result.device, transcript.text, answer)
        logger.info("%s : transcription %.2f s (audio %.1f s), synthese %.2f s, reponse prete en %.2f s",
                    result.device, transcript.elapsed_s, transcript.audio_s, speech.elapsed_s, ready_s)
        self._player.send(result.device, speech.pcm, f"rep-{result.session_id}")
        return Turn(heard=transcript.text, answer=answer, speech_s=vad.speech_s, speakers=speakers,
                    names=tuple(verdict.label for verdict in verdicts.values()), utterances=tuple(utterances),
                    command=reply.command, refused=refused,
                    stt_s=transcript.elapsed_s, tts_s=speech.elapsed_s, ready_s=ready_s,
                    answer_s=speech.audio_s)

    def _decide(self, board: str, text: str, speaker: str | None, score: float | None,
                closest: str | None = None) -> tuple[Reply, bool]:
        """Réponse et commande autorisée ; True si une commande a été refusée ou mise en attente."""
        if not text:
            return Reply(NOTHING_HEARD), False
        if self._policy is not None:
            # Une confirmation attendue passe avant tout : « oui » n'est pas une requête.
            resolution = self._policy.resolve(board, text, speaker, closest, score)
            if resolution is not None:
                return Reply(resolution.answer, resolution.command), resolution.command is None
        reply = self._assistant.respond(text, speaker, board)
        if reply.command is None or self._policy is None:
            return reply, False
        verdict = self._policy.check(reply.command, speaker, score)
        if not verdict.allowed:
            return Reply(verdict.answer or "Je ne peux pas faire cela."), True
        if verdict.confirm:
            self._policy.ask(board, reply.command, speaker)
            return Reply(verdict.answer or "Confirmez-vous ?"), True
        return reply, False

    def _identify(self, device: str, diarization: Diarization) -> dict[str, Identification]:
        """Un verdict (nom ou « Inconnu ») par étiquette de diarisation."""
        if self._identifier is None or diarization.embeddings is None:
            return {}
        verdicts = {speaker: self._identifier.identify(embedding)
                    for speaker, embedding in zip(diarization.speakers, diarization.embeddings)}
        logger.info("%s : %s", device, ", ".join(
            f"{speaker} = {verdict.describe()}" for speaker, verdict in verdicts.items()))
        return verdicts

    def _who_said_what(self, result: SessionResult, transcript: Transcript,
                       turns: tuple[SpeakerTurn, ...], verdicts: dict[str, Identification]) -> list[Utterance]:
        """Interventions de la session, journalisées et publiées sur voice/<carte>/transcript."""
        if not transcript.text:
            return []
        utterances = attribute(transcript.words, turns, verdicts)
        if not utterances:  # texte sans horodatage des mots : une seule intervention anonyme
            utterances = [Utterance(speaker=UNKNOWN, text=transcript.text, start_s=0.0,
                                    end_s=transcript.audio_s, score=None)]
        for utterance in utterances:
            logger.info("%s : %s : « %s »", result.device, utterance.speaker, utterance.text)
        if self._publisher is not None:
            self._publisher.publish(result.device, "transcript", transcript_json(result.session_id, utterances), 1)
        return utterances
