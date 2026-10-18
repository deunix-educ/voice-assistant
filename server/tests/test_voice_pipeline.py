"""Tests de la boucle complète (étapes 6-14, par doigt depuis F4) : modèles et carte remplacés par des doublures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_server.access_control import AccessPolicy
from voice_server.assistant import NOTHING_HEARD, Reply
from voice_server.audio_buffer import Levels
from voice_server.fingerprint_book import FingerprintBook
from voice_server.home_control import DeviceCommand
from voice_server.machine_control import Command
from voice_server.session_manager import Outcome, SessionResult
from voice_server.settings import AccessSettings, FingerprintSettings, HomeSettings
from voice_server.speech_to_text import Transcript
from voice_server.text_to_speech import Speech
from voice_server.voice_pipeline import VoicePipeline

PCM = bytes(64000)  # 2 s de silence numérique : le contenu n'importe pas ici

# Emplacements du capteur : 1 et 3 sont deux doigts de Denis, 2 est Jessica, 4 n'a pas de nom.
FINGERS = FingerprintBook(FingerprintSettings(users={1: "Denis", 2: "Jessica", 3: "denis"}))
DENIS, JESSICA, DENIS_OTHER_FINGER, UNNAMED = 1, 2, 3, 4


class FakeStt:
    def __init__(self, text: str, dropped: tuple[str, ...] = ()) -> None:
        self.text = text
        self.dropped = dropped
        self.lengths: list[int] = []
        self.with_words: list[bool] = []

    def transcribe(self, pcm: bytes, words: bool = True) -> Transcript:
        self.lengths.append(len(pcm))
        self.with_words.append(words)
        return Transcript(text=self.text, audio_s=len(pcm) / 32000, elapsed_s=0.8, dropped=self.dropped)


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


def session(finger: int | None = DENIS, duration_s: float = 2.0, rms_dbfs: float = -30.0) -> SessionResult:
    """Session d'un doigt (par défaut celui de Denis) ; finger=None : bouton PTT."""
    return SessionResult(device="esp32-01", session_id="a1b2c3", outcome=Outcome.COMPLETE,
                         path=Path("inutile.wav"), duration_s=duration_s, chunks_received=40,
                         chunks_announced=40, levels=Levels(peak_dbfs=-12.0, rms_dbfs=rms_dbfs),
                         finger_id=finger, finger_score=None if finger is None else 120)


def pipeline(stt: FakeStt, tts: FakeTts, player: FakePlayer, publisher: FakePublisher | None = None,
             assistant: EchoAssistant | None = None, controller: FakeController | None = None,
             policy: AccessPolicy | None = None) -> VoicePipeline:
    ticks = iter([10.0, 11.2])  # début, puis réponse prête 1,2 s plus tard
    return VoicePipeline(stt, assistant or EchoAssistant(), tts, player, FINGERS, publisher=publisher,
                         controller=controller, policy=policy, read=lambda result: PCM,
                         clock=lambda: next(ticks))


def test_full_turn() -> None:
    """Question transcrite, réponse synthétisée puis envoyée à la bonne carte."""
    stt, tts, player = FakeStt("quelle heure"), FakeTts(), FakePlayer()

    turn = pipeline(stt, tts, player).handle(session())

    assert turn is not None
    assert (turn.heard, turn.answer, turn.person) == ("quelle heure", "réponse à quelle heure", "Denis")
    assert turn.ready_s == pytest.approx(1.2)
    assert tts.texts == ["réponse à quelle heure"]
    assert player.sent == [("esp32-01", 16000, "rep-a1b2c3")]


def test_whisper_gets_the_whole_session_without_word_times() -> None:
    """Sans VAD, tout l'audio de la session va à Whisper ; sans diarisation, aucun mot n'a besoin d'instant."""
    stt = FakeStt("quelle heure")
    pipeline(stt, FakeTts(), FakePlayer()).handle(session())

    assert stt.lengths == [len(PCM)] and stt.with_words == [False]


def test_too_short_session_is_ignored() -> None:
    stt, player = FakeStt("x"), FakePlayer()

    assert pipeline(stt, FakeTts(), player).handle(session(duration_s=0.1)) is None
    assert stt.lengths == [] and player.sent == []


def test_silent_session_skips_whisper() -> None:
    """Doigt tenu sans parler (RMS du bruit de fond ≈ -63 dBFS) : Whisper inventerait du texte."""
    stt, tts = FakeStt("Sous-titrage ST'501"), FakeTts()

    turn = pipeline(stt, tts, FakePlayer()).handle(session(rms_dbfs=-63.0))

    assert turn is not None and turn.heard == "" and turn.answer == NOTHING_HEARD
    assert stt.lengths == []
    assert tts.texts == [NOTHING_HEARD]


def test_quiet_speech_is_still_transcribed() -> None:
    """La parole la plus faible des 171 enregistrements mesurés : RMS -50,8 dBFS, au-dessus du seuil (-58)."""
    stt = FakeStt("ferme les volets")
    pipeline(stt, FakeTts(), FakePlayer()).handle(session(rms_dbfs=-50.8))

    assert stt.lengths == [len(PCM)]


def test_nothing_recognized_is_said() -> None:
    tts = FakeTts()

    turn = pipeline(FakeStt(""), tts, FakePlayer()).handle(session())

    assert turn is not None and turn.answer == NOTHING_HEARD and tts.texts == [NOTHING_HEARD]


def test_hallucination_is_dropped() -> None:
    turn = pipeline(FakeStt("", dropped=("Amara.org",)), FakeTts(), FakePlayer()).handle(session())

    assert turn is not None and turn.heard == ""


def test_the_finger_names_the_person() -> None:
    """L'assistant et la domotique reçoivent la personne du doigt ; bouton ou doigt sans nom : None."""
    assistant = EchoAssistant()
    for finger in (DENIS, JESSICA, DENIS_OTHER_FINGER, UNNAMED, None):
        pipeline(FakeStt("bonjour"), FakeTts(), FakePlayer(), assistant=assistant).handle(session(finger))

    assert assistant.speakers == ["Denis", "Jessica", "denis", None, None]


def test_unnamed_finger_is_logged_as_an_oversight(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        pipeline(FakeStt("bonjour"), FakeTts(), FakePlayer()).handle(session(UNNAMED))

    assert "empreinte 4" in caplog.text and "sans nom" in caplog.text


def test_transcript_is_published_with_the_person_and_the_finger() -> None:
    publisher = FakePublisher()
    pipeline(FakeStt("quelle heure"), FakeTts(), FakePlayer(), publisher=publisher).handle(session(JESSICA))

    [(device, suffix, payload, qos)] = publisher.messages
    assert (device, suffix, qos) == ("esp32-01", "transcript", 1)
    assert json.loads(payload) == {"session": "a1b2c3", "utterances": [
        {"speaker": "Jessica", "text": "quelle heure", "start": 0.0, "end": 2.0, "score": None, "finger": 2}]}


def test_button_session_is_published_as_unknown() -> None:
    publisher = FakePublisher()
    pipeline(FakeStt("quelle heure"), FakeTts(), FakePlayer(), publisher=publisher).handle(session(None))

    utterance = json.loads(publisher.messages[0][2])["utterances"][0]
    assert (utterance["speaker"], utterance["finger"]) == ("Inconnu", None)


def test_nothing_published_without_text() -> None:
    publisher = FakePublisher()
    pipeline(FakeStt(""), FakeTts(), FakePlayer(), publisher=publisher).handle(session())

    assert publisher.messages == []


def test_command_is_executed_before_the_spoken_answer() -> None:
    """Étape 11 : la commande part d'abord, la lumière s'allume pendant que la carte le dit."""
    events: list[str] = []
    command = DeviceCommand(action="on", device="light", room="salon")
    controller = FakeController(events)
    turn = pipeline(FakeStt("allume"), FakeTts(), FakePlayer(events), assistant=EchoAssistant(command),
                    controller=controller).handle(session(JESSICA))

    assert turn is not None and turn.command == command
    assert controller.calls == [("esp32-01", command, "Jessica", "a1b2c3")]
    assert events == ["commande", "reponse parlee"]


def test_no_command_no_controller_call() -> None:
    events: list[str] = []
    controller = FakeController(events)
    pipeline(FakeStt("bonjour"), FakeTts(), FakePlayer(events), controller=controller).handle(session())

    assert controller.calls == []


HOME = HomeSettings(topic_prefix="home", rooms={"salon": "du salon"}, boards={"esp32-01": "salon"})
ACCESS = AccessSettings(unknown_level="limite", default_level="standard", users={"Denis": "complet"},
                        rules={"light": {"on": "standard"}, "door": {"open": "complet"}},
                        confirm=("door.open",), confirm_timeout_s=15.0)
DOOR = DeviceCommand("open", "door", "salon")
LIGHT = DeviceCommand("on", "light", "salon")


def rights(command: Command, finger: int | None) -> tuple[bool, str]:
    """(commande exécutée, réponse dite) quand ce doigt demande cette commande."""
    controller = FakeController([])
    turn = pipeline(FakeStt("fais-le"), FakeTts(), FakePlayer(), assistant=EchoAssistant(command),
                    controller=controller, policy=AccessPolicy(ACCESS, HOME)).handle(session(finger))
    assert turn is not None
    return bool(controller.calls), turn.answer


def test_button_session_cannot_command() -> None:
    """Étape 14 : le bouton n'identifie personne, donc niveau « limite » : conversation seulement."""
    executed, answer = rights(LIGHT, None)
    assert not executed and answer.startswith("Je ne sais pas qui tient la carte")


def test_unnamed_finger_cannot_command() -> None:
    executed, answer = rights(LIGHT, UNNAMED)
    assert not executed and answer.startswith("Je ne sais pas qui tient la carte")


def test_standard_person_controls_lights_not_the_door() -> None:
    assert rights(LIGHT, JESSICA)[0]
    executed, answer = rights(DOOR, JESSICA)
    assert not executed and answer == "Désolé Jessica, votre profil ne permet pas d'ouvrir la porte du salon."


def test_rights_follow_the_person_not_the_slot_or_the_case() -> None:
    """Slot 3 est nommé « denis » (minuscule) : mêmes droits que « Denis » dans access.users."""
    answer = rights(DOOR, DENIS_OTHER_FINGER)[1]
    assert answer.startswith("Confirmez-vous")


def confirm(first_finger: int, second_finger: int | None) -> tuple[list[Command], str]:
    """Un doigt demande d'ouvrir la porte, puis un doigt dit « oui » ; commandes exécutées, dernière réponse."""
    controller = FakeController([])
    policy = AccessPolicy(ACCESS, HOME)
    first = pipeline(FakeStt("ouvre la porte"), FakeTts(), FakePlayer(), assistant=EchoAssistant(DOOR),
                     controller=controller, policy=policy).handle(session(first_finger))
    assert first is not None and first.answer.startswith("Confirmez-vous") and controller.calls == []
    second = pipeline(FakeStt("oui"), FakeTts(), FakePlayer(), assistant=EchoAssistant(),
                      controller=controller, policy=policy).handle(session(second_finger))
    assert second is not None
    return [call[1] for call in controller.calls], second.answer


def test_sensitive_command_waits_for_yes_from_the_same_finger() -> None:
    commands, answer = confirm(DENIS, DENIS)
    assert commands == [DOOR] and answer == "J'ouvre la porte du salon."


def test_another_finger_of_the_same_person_does_not_confirm() -> None:
    """Le doigt exact : le slot 3 est aussi Denis (« denis »), mais ce n'est pas le doigt de la demande."""
    commands, answer = confirm(DENIS, DENIS_OTHER_FINGER)
    assert commands == [] and answer == "Confirmez avec le même doigt que pour la demande. J'annule."


@pytest.mark.parametrize("other", [JESSICA, UNNAMED, None])
def test_yes_from_another_person_or_the_button_is_refused(other: int | None) -> None:
    commands, answer = confirm(DENIS, other)
    assert commands == [] and answer == "Seul Denis peut confirmer. J'annule."
