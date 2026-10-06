"""Tests de la boucle complète, étapes 6-7 : modèles et carte remplacés par des doublures."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from voice_server.access_control import AccessPolicy
from voice_server.assistant import NOTHING_HEARD, Reply
from voice_server.audio_buffer import Levels
from voice_server.diarization import Diarization, SpeakerTurn
from voice_server.home_control import DeviceCommand
from voice_server.machine_control import Command
from voice_server.session_manager import Outcome, SessionResult
from voice_server.settings import AccessSettings, HomeSettings
from voice_server.speaker_identifier import Identification
from voice_server.speech_to_text import Transcript, Word
from voice_server.text_to_speech import Speech
from voice_server.voice_activity import Segment, VadResult
from voice_server.voice_pipeline import VoicePipeline

PCM = bytes(32000)  # 1 s de silence numérique : le contenu n'importe pas ici


class FakeVad:
    """Trouve la parole entre 0,25 et 0,75 s, ou rien si speech=False."""

    def __init__(self, speech: bool = True) -> None:
        self.speech = speech

    def detect(self, pcm: bytes) -> VadResult:
        segments = (Segment(4000, 12000),) if self.speech else ()
        kept = pcm[8000:24000] if self.speech else b""
        return VadResult(segments=segments, speech_pcm=kept, probabilities=np.zeros(0),
                         total_s=len(pcm) / 32000, speech_s=len(kept) / 32000, elapsed_s=0.003,
                         sample_rate=16000)


class FakeDiarizer:
    def __init__(self) -> None:
        self.lengths: list[int] = []
        self.embedded: list[int] = []

    def embed(self, pcm: bytes) -> np.ndarray:
        self.embedded.append(len(pcm))
        return np.array([1.0, 0.0], dtype=np.float32)  # la voix que FakeIdentifier reconnaît

    def diarize(self, pcm: bytes) -> Diarization:
        self.lengths.append(len(pcm))
        return Diarization(turns=(SpeakerTurn("SPEAKER_00", 0.0, 0.2), SpeakerTurn("SPEAKER_01", 0.2, 0.5)),
                           speakers=("SPEAKER_00", "SPEAKER_01"),
                           embeddings=np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
                           audio_s=0.5, elapsed_s=0.3)


class FakeIdentifier:
    """Reconnaît la première empreinte (valeur 1), pas les autres."""

    def identify(self, embedding: np.ndarray) -> Identification:
        if embedding[0] == 1.0:
            return Identification(name="Denis", score=0.8, closest="Denis")
        return Identification(name=None, score=0.1, closest="Denis")


class FakeStt:
    def __init__(self, text: str, dropped: tuple[str, ...] = (), words: tuple[Word, ...] = ()) -> None:
        self.text = text
        self.dropped = dropped
        self.words = words
        self.calls = 0
        self.lengths: list[int] = []
        self.with_words: list[bool] = []

    def transcribe(self, pcm: bytes, words: bool = True) -> Transcript:
        self.calls += 1
        self.lengths.append(len(pcm))
        self.with_words.append(words)
        return Transcript(text=self.text, audio_s=len(pcm) / 32000, elapsed_s=0.8, dropped=self.dropped,
                          words=self.words)


class EchoAssistant:
    def __init__(self, command: Command | None = None) -> None:
        self.speakers: list[str | None] = []
        self.command = command

    def respond(self, text: str, speaker: str | None = None, board: str = "") -> Reply:
        self.speakers.append(speaker)
        return Reply(f"réponse à {text}", self.command)


class FakeController:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[tuple[str, Command, str | None, str]] = []

    def execute(self, board: str, command: Command, speaker: str | None, session: str) -> list[str]:
        self.calls.append((board, command, speaker, session))
        self.events.append("commande")
        return ["home/salon/light/set"]


class FakePublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, bytes, int]] = []

    def publish(self, device: str, suffix: str, payload: bytes, qos: int) -> None:
        self.messages.append((device, suffix, payload, qos))


class FakeTts:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def synthesize(self, text: str) -> Speech:
        self.texts.append(text)
        return Speech(pcm=bytes(16000), audio_s=0.5, elapsed_s=0.1)


class FakePlayer:
    def __init__(self, events: list[str] | None = None) -> None:
        self.sent: list[tuple[str, int, str | None]] = []
        self.events = events if events is not None else []

    def send(self, device: str, pcm: bytes, session: str | None = None) -> str:
        self.sent.append((device, len(pcm), session))
        self.events.append("reponse parlee")
        return session or "x"


def session(duration_s: float = 1.5, peak_dbfs: float = -12.0) -> SessionResult:
    return SessionResult(device="esp32-01", session_id="a1b2c3", outcome=Outcome.COMPLETE,
                         path=Path("inutile.wav"), duration_s=duration_s, chunks_received=30,
                         chunks_announced=30, levels=Levels(peak_dbfs=peak_dbfs, rms_dbfs=-30.0))


def pipeline(stt: FakeStt, tts: FakeTts, player: FakePlayer, vad: FakeVad | None = None,
             diarizer: FakeDiarizer | None = None, identifier: FakeIdentifier | ShortYesIdentifier | None = None,
             publisher: FakePublisher | None = None, assistant: EchoAssistant | None = None,
             controller: FakeController | None = None, policy: AccessPolicy | None = None,
             direct_below_s: float = 0.0) -> VoicePipeline:
    ticks = iter([10.0, 11.2])  # début, puis réponse prête 1,2 s plus tard
    return VoicePipeline(vad or FakeVad(), stt, assistant or EchoAssistant(), tts, player, diarizer=diarizer,
                         identifier=identifier, publisher=publisher, controller=controller, policy=policy,
                         read=lambda result: PCM, clock=lambda: next(ticks), direct_below_s=direct_below_s)


def test_full_turn() -> None:
    """Question transcrite, réponse synthétisée puis envoyée à la bonne carte."""
    stt, tts, player = FakeStt("quelle heure"), FakeTts(), FakePlayer()

    turn = pipeline(stt, tts, player).handle(session())

    assert turn is not None
    assert (turn.heard, turn.answer) == ("quelle heure", "réponse à quelle heure")
    assert turn.ready_s == pytest.approx(1.2)
    assert stt.lengths == [16000]  # Whisper ne reçoit que la parole trouvée par la VAD
    assert turn.speech_s == pytest.approx(0.5)
    assert tts.texts == ["réponse à quelle heure"]
    assert player.sent == [("esp32-01", 16000, "rep-a1b2c3")]


def test_too_short_session_is_ignored() -> None:
    """Un appui trop bref ne déclenche ni transcription ni réponse."""
    stt, tts, player = FakeStt("x"), FakeTts(), FakePlayer()

    assert pipeline(stt, tts, player).handle(session(duration_s=0.1)) is None
    assert (stt.calls, player.sent) == (0, [])


def test_session_without_speech_skips_whisper() -> None:
    """Pas de parole selon la VAD : Whisper n'est pas appelé (lent, hallucinations), la carte le dit."""
    stt, tts, player = FakeStt("Sous-titres réalisés par Amara.org"), FakeTts(), FakePlayer()

    turn = pipeline(stt, tts, player, FakeVad(speech=False)).handle(session(peak_dbfs=-30.0))

    assert stt.calls == 0
    assert turn is not None and turn.answer == NOTHING_HEARD
    assert len(player.sent) == 1


def test_nothing_recognized_is_said() -> None:
    """Transcription vide (hallucination écartée) : « Je n'ai rien entendu. »"""
    stt, tts, player = FakeStt("", dropped=("Amara.org",)), FakeTts(), FakePlayer()

    turn = pipeline(stt, tts, player).handle(session())

    assert turn is not None and turn.answer == NOTHING_HEARD


def test_diarization_gets_the_same_audio_as_whisper() -> None:
    """Diarisation et transcription reçoivent la même parole : leurs instants concordent."""
    stt, tts, player, diarizer = FakeStt("bonjour"), FakeTts(), FakePlayer(), FakeDiarizer()

    turn = pipeline(stt, tts, player, diarizer=diarizer).handle(session())

    assert turn is not None and turn.speakers == 2
    assert diarizer.lengths == stt.lengths == [16000]


def test_no_diarization_without_speech() -> None:
    """Appui muet : ni diarisation ni transcription."""
    diarizer = FakeDiarizer()

    turn = pipeline(FakeStt("x"), FakeTts(), FakePlayer(), FakeVad(speech=False), diarizer).handle(session())

    assert turn is not None and turn.speakers == 0
    assert diarizer.lengths == []


def test_each_speaker_gets_a_name_or_unknown() -> None:
    """Étape 9 : un nom par locuteur de la diarisation, dans le même ordre."""
    turn = pipeline(FakeStt("bonjour"), FakeTts(), FakePlayer(), diarizer=FakeDiarizer(),
                    identifier=FakeIdentifier()).handle(session())

    assert turn is not None and turn.names == ("Denis", "Inconnu")


def test_no_identification_without_diarization() -> None:
    turn = pipeline(FakeStt("bonjour"), FakeTts(), FakePlayer(), identifier=FakeIdentifier()).handle(session())

    assert turn is not None and turn.names == ()


TWO_VOICES = (Word(" Ferme", 0.0, 0.1), Word(" la", 0.1, 0.18),       # SPEAKER_00 : 0,0-0,2
              Word(" Bonjour", 0.22, 0.4), Word(" Denis.", 0.4, 0.5))  # SPEAKER_01 : 0,2-0,5


def test_who_said_what_is_published() -> None:
    """Étape 10 : une intervention par locuteur, publiée en JSON sur voice/<carte>/transcript."""
    publisher = FakePublisher()
    turn = pipeline(FakeStt("Ferme la Bonjour Denis.", words=TWO_VOICES), FakeTts(), FakePlayer(),
                    diarizer=FakeDiarizer(), identifier=FakeIdentifier(), publisher=publisher).handle(session())

    assert turn is not None
    assert [(u.speaker, u.text) for u in turn.utterances] == [("Denis", "Ferme la"), ("Inconnu", "Bonjour Denis.")]
    [(device, suffix, payload, qos)] = publisher.messages
    assert (device, suffix, qos) == ("esp32-01", "transcript", 1)
    message = json.loads(payload)
    assert message["session"] == "a1b2c3"
    assert message["utterances"][0] == {"speaker": "Denis", "text": "Ferme la", "start": 0.0, "end": 0.18,
                                        "score": 0.8}


def test_assistant_answers_the_last_speaker_by_name() -> None:
    """L'assistant reçoit le nom de la dernière personne qui a parlé, None si elle est inconnue."""
    assistant = EchoAssistant()
    words = TWO_VOICES[:2]  # seul SPEAKER_00, reconnu comme Denis
    pipeline(FakeStt("Ferme la", words=words), FakeTts(), FakePlayer(), diarizer=FakeDiarizer(),
             identifier=FakeIdentifier(), assistant=assistant).handle(session())
    pipeline(FakeStt("Ferme la Bonjour Denis.", words=TWO_VOICES), FakeTts(), FakePlayer(),
             diarizer=FakeDiarizer(), identifier=FakeIdentifier(), assistant=assistant).handle(session())

    assert assistant.speakers == ["Denis", None]


def test_nothing_published_without_speech() -> None:
    publisher = FakePublisher()
    pipeline(FakeStt("x"), FakeTts(), FakePlayer(), FakeVad(speech=False), publisher=publisher).handle(session())

    assert publisher.messages == []


def test_text_without_word_times_is_one_unknown_utterance() -> None:
    """Sans diarisation ni horodatage, une seule intervention « Inconnu » : le JSON reste valide."""
    publisher = FakePublisher()
    turn = pipeline(FakeStt("bonjour"), FakeTts(), FakePlayer(), publisher=publisher).handle(session())

    assert turn is not None and [(u.speaker, u.text) for u in turn.utterances] == [("Inconnu", "bonjour")]
    assert json.loads(publisher.messages[0][2])["utterances"][0]["score"] is None


def test_command_is_executed_before_the_spoken_answer() -> None:
    """Étape 11 : la commande part d'abord, la lumière s'allume pendant que la carte le dit."""
    events: list[str] = []
    command = DeviceCommand(action="on", device="light", room="salon")
    controller = FakeController(events)
    turn = pipeline(FakeStt("allume"), FakeTts(), FakePlayer(events), assistant=EchoAssistant(command),
                    controller=controller).handle(session())

    assert turn is not None and turn.command == command
    assert controller.calls == [("esp32-01", command, None, "a1b2c3")]
    assert events == ["commande", "reponse parlee"]


def test_no_command_no_controller_call() -> None:
    events: list[str] = []
    controller = FakeController(events)
    pipeline(FakeStt("bonjour"), FakeTts(), FakePlayer(events), controller=controller).handle(session())

    assert controller.calls == []


HOME = HomeSettings(topic_prefix="home", rooms={"salon": "du salon"}, boards={"esp32-01": "salon"})
ACCESS = AccessSettings(unknown_level="limite", default_level="standard", users={"Denis": "complet"},
                        full_min_score=0.6, rules={"door": {"open": "complet"}}, confirm=("door.open",),
                        confirm_timeout_s=15.0)
DENIS_WORDS = (Word(" ouvre", 0.0, 0.1), Word(" la porte", 0.1, 0.18))  # tour de SPEAKER_00 = Denis (0,8)


def test_refused_command_is_not_executed() -> None:
    """Étape 14 : voix inconnue, pas de commande ; la carte dit pourquoi."""
    events: list[str] = []
    controller = FakeController(events)
    turn = pipeline(FakeStt("ouvre la porte"), FakeTts(), FakePlayer(events),
                    assistant=EchoAssistant(DeviceCommand("open", "door", "salon")),
                    controller=controller, policy=AccessPolicy(ACCESS, HOME)).handle(session())

    assert turn is not None and turn.refused and turn.command is None
    assert controller.calls == []
    assert turn.answer.startswith("Je ne vous ai pas reconnu")


def test_sensitive_command_waits_for_yes() -> None:
    """Denis demande d'ouvrir la porte : question, puis « oui » → exécution."""
    events: list[str] = []
    controller = FakeController(events)
    policy = AccessPolicy(ACCESS, HOME)
    door = DeviceCommand("open", "door", "salon")

    first = pipeline(FakeStt("ouvre la porte", words=DENIS_WORDS), FakeTts(), FakePlayer(events),
                     diarizer=FakeDiarizer(), identifier=FakeIdentifier(), assistant=EchoAssistant(door),
                     controller=controller, policy=policy).handle(session())
    assert first is not None and first.answer.startswith("Confirmez-vous") and controller.calls == []

    yes_words = (Word(" oui", 0.0, 0.1),)
    second = pipeline(FakeStt("oui", words=yes_words), FakeTts(), FakePlayer(events),
                      diarizer=FakeDiarizer(), identifier=FakeIdentifier(), assistant=EchoAssistant(),
                      controller=controller, policy=policy).handle(session())
    assert second is not None and second.command == door
    assert [call[1] for call in controller.calls] == [door]


class ShortYesIdentifier:
    """Un « oui » de 0,7 s : sous le seuil, mais Denis reste le profil le plus proche (ou non)."""

    def __init__(self, closest: str, score: float) -> None:
        self._verdict = Identification(name=None, score=score, closest=closest)

    def identify(self, embedding: np.ndarray) -> Identification:
        return self._verdict


@pytest.mark.parametrize(("closest", "score", "executed"), [("Denis", 0.35, True), ("Jessica", 0.35, False),
                                                           ("Denis", 0.10, False)])
def test_short_yes_confirms_only_from_the_closest_voice(closest: str, score: float, executed: bool) -> None:
    """Essai réel du 2026-10-06 : « OUI » de Denis à 0,35 refusé comme « voix inconnue »."""
    events: list[str] = []
    controller = FakeController(events)
    policy = AccessPolicy(ACCESS, HOME)
    door = DeviceCommand("open", "door", "salon")
    pipeline(FakeStt("ouvre la porte", words=DENIS_WORDS), FakeTts(), FakePlayer(events),
             diarizer=FakeDiarizer(), identifier=FakeIdentifier(), assistant=EchoAssistant(door),
             controller=controller, policy=policy).handle(session())

    second = pipeline(FakeStt("oui", words=(Word(" oui", 0.0, 0.1),)), FakeTts(), FakePlayer(events),
                      diarizer=FakeDiarizer(), identifier=ShortYesIdentifier(closest, score),
                      assistant=EchoAssistant(), controller=controller, policy=policy).handle(session())

    assert second is not None
    assert [call[1] for call in controller.calls] == ([door] if executed else [])
    assert second.answer == ("J'ouvre la porte du salon." if executed else "Seul Denis peut confirmer. J'annule.")


def test_short_speech_is_identified_without_diarization() -> None:
    """Étape 16 : 0,5 s de parole = une commande, une personne : empreinte directe, pas de diarisation."""
    stt, diarizer = FakeStt("allume la lumière"), FakeDiarizer()

    turn = pipeline(stt, FakeTts(), FakePlayer(), diarizer=diarizer, identifier=FakeIdentifier(),
                    direct_below_s=4.0).handle(session())

    assert turn is not None
    assert diarizer.lengths == [] and diarizer.embedded == [16000]  # même audio que Whisper
    assert stt.with_words == [False]                                 # mots non horodatés : inutiles ici
    assert (turn.speakers, turn.names) == (1, ("Denis",))
    assert [(u.speaker, u.text, u.score) for u in turn.utterances] == [("Denis", "allume la lumière", 0.8)]


def test_long_speech_still_goes_through_diarization() -> None:
    """Au-delà du seuil, plusieurs personnes ont pu parler : diarisation complète, mots horodatés."""
    stt, diarizer = FakeStt("bonjour", words=DENIS_WORDS), FakeDiarizer()

    turn = pipeline(stt, FakeTts(), FakePlayer(), diarizer=diarizer, identifier=FakeIdentifier(),
                    direct_below_s=0.4).handle(session())  # la fausse VAD garde 0,5 s de parole

    assert turn is not None and turn.speakers == 2
    assert diarizer.embedded == [] and diarizer.lengths == [16000] and stt.with_words == [True]


def test_short_speech_applies_rights_to_the_identified_person() -> None:
    """Le raccourci ne change rien aux droits : Denis reconnu, porte → confirmation demandée."""
    events: list[str] = []
    turn = pipeline(FakeStt("ouvre la porte"), FakeTts(), FakePlayer(events), diarizer=FakeDiarizer(),
                    identifier=FakeIdentifier(), assistant=EchoAssistant(DeviceCommand("open", "door", "salon")),
                    controller=FakeController(events), policy=AccessPolicy(ACCESS, HOME),
                    direct_below_s=4.0).handle(session())

    assert turn is not None and turn.answer.startswith("Confirmez-vous")
