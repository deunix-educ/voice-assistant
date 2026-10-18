"""Tests du carnet d'empreintes et de l'outil tools/finger.py (branche fingerprint, étape F2), sans carte."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import pytest

from voice_server.fingerprint_book import FingerprintBook
from voice_server.settings import FingerprintSettings, load_settings

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from finger import command_delete, command_enroll, command_list, enrollment_message  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"
BOOK = FingerprintBook(FingerprintSettings(users={1: "Denis", 2: "Denis", 5: "Jessica"}))


class FakeBoard:
    """Carte simulée : chaque commande reçue déclenche les événements scriptés pour elle."""

    def __init__(self, replies: dict[str, list[dict[str, Any]]]) -> None:
        self.replies = replies
        self.sent: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []

    def send(self, command: dict[str, Any]) -> None:
        self.sent.append(command)
        self._events.extend(self.replies.get(command["cmd"], []))

    def next_event(self, timeout_s: float) -> dict[str, Any] | None:
        return self._events.pop(0) if self._events else None


def listing(*ids: int, ok: bool = True) -> dict[str, Any]:
    return {"event": "fp_list", "ok": ok, "capacity": 200, "count": len(ids), "ids": list(ids)}


def enroll_steps(*steps: tuple[str, str | None]) -> list[dict[str, Any]]:
    return [{"event": "fp_enroll", "step": step, "id": 3, **({"reason": reason} if reason else {})}
            for step, reason in steps]


# --------------------------------------------------------------------------- carnet

def test_names_and_several_fingers_per_person() -> None:
    assert BOOK.name_of(2) == "Denis" and BOOK.name_of(9) is None
    assert BOOK.ids_of("Denis") == [1, 2] and BOOK.named_ids() == [1, 2, 5]
    assert BOOK.describe(5) == "Jessica (empreinte 5)"
    assert BOOK.describe(9) == "empreinte 9 (sans nom dans config.yaml)"


def test_free_id_skips_taken_and_named_slots_and_keeps_zero_free() -> None:
    assert BOOK.free_id([3], capacity=200) == 4        # 1, 2, 5 nommés ; 3 occupé dans le capteur
    assert FingerprintBook(FingerprintSettings(users={})).free_id([], capacity=200) == 1
    with pytest.raises(ValueError, match="plein"):
        FingerprintBook(FingerprintSettings(users={})).free_id([1, 2], capacity=3)


def test_project_config_loads() -> None:
    """Le config.yaml du dépôt se charge ; son contenu appartient à l'utilisateur (ses doigts)."""
    users = load_settings(CONFIG).fingerprint.users
    assert all(isinstance(number, int) and number >= 0 and name for number, name in users.items())


def config_with_fingerprint(tmp_path: Path, section: str) -> Path:
    """Copie de config.yaml dont la section fingerprint est remplacée (indépendant des doigts de l'utilisateur)."""
    text = CONFIG.read_text(encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(text[: text.index("\nfingerprint:")] + "\nfingerprint:\n" + section, encoding="utf-8")
    return path


def test_config_validation(tmp_path: Path) -> None:
    good = config_with_fingerprint(tmp_path, "    users: {1: Denis, 2: Denis}\n")
    assert load_settings(good).fingerprint.users == {1: "Denis", 2: "Denis"}
    assert load_settings(config_with_fingerprint(tmp_path, "    users: {}\n")).fingerprint.users == {}
    for wrong in ("{abc: Denis}", "{-1: Denis}", "{1: ''}"):
        with pytest.raises(ValueError, match="fingerprint.users"):
            load_settings(config_with_fingerprint(tmp_path, f"    users: {wrong}\n"))


# --------------------------------------------------------------------------- phrases

@pytest.mark.parametrize(
    ("step", "reason", "outcome", "fragment"),
    [
        ("start", None, None, "Posez le doigt de Denis"),
        ("remove_finger", None, None, "Retirez le doigt"),
        ("place_again", None, None, "MEME doigt"),
        ("retry", "messy", None, "trop brouillee"),
        ("done", None, True, "Empreinte 3 enregistree"),
        ("failed", "mismatch", False, "pas le meme doigt"),
        ("failed", "timeout", False, "temps ecoule"),
    ],
)
def test_messages_guide_the_person(step: str, reason: str | None, outcome: bool | None, fragment: str) -> None:
    message, result = enrollment_message(enroll_steps((step, reason))[0], "Denis")
    assert fragment in message and result is outcome


def test_known_finger_message_names_the_slot() -> None:
    event = {"event": "fp_enroll", "step": "failed", "id": 7, "reason": "already_enrolled"}
    assert "deja enregistre (c'est l'empreinte 7)" in enrollment_message(event, "Denis")[0]


# --------------------------------------------------------------------------- commandes

def test_list_names_the_owners(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    board = FakeBoard({"fp_list": [listing(1, 4)]})
    assert command_list(board, BOOK) == 0
    assert "Denis (empreinte 1)" in caplog.text and "empreinte 4 (sans nom dans config.yaml)" in caplog.text
    assert "config.yaml nomme l'empreinte 2" in caplog.text   # nommée mais absente du capteur


def test_list_without_answer_blames_the_board(caplog: pytest.LogCaptureFixture) -> None:
    assert command_list(FakeBoard({}), BOOK) == 1 and "la carte ne repond pas" in caplog.text


def test_enroll_picks_the_free_slot_and_prints_the_config_line(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    board = FakeBoard({"fp_list": [listing(3)],
                       "fp_enroll": enroll_steps(("start", None), ("remove_finger", None), ("place_again", None),
                                                 ("done", None))})
    assert command_enroll(board, BOOK, "Marie", None) == 0
    assert board.sent[1] == {"cmd": "fp_enroll", "id": 4}            # 1, 2, 5 nommés, 3 occupé
    assert "A ajouter dans server/config.yaml" in caplog.text and "4: Marie" in caplog.text


def test_enroll_reuses_a_slot_already_named_for_that_person_but_empty(caplog: pytest.LogCaptureFixture) -> None:
    """Doigt de Denis supprimé (emplacement 1 nommé mais vide) : on le réenregistre à l'emplacement 1."""
    board = FakeBoard({"fp_list": [listing(2)], "fp_enroll": enroll_steps(("start", None), ("done", None))})
    assert command_enroll(board, BOOK, "Denis", None) == 0
    assert board.sent[1] == {"cmd": "fp_enroll", "id": 1}
    other = FakeBoard({"fp_list": [listing(1, 2)], "fp_enroll": enroll_steps(("start", None), ("done", None))})
    assert command_enroll(other, BOOK, "Denis", None) == 0
    assert other.sent[1] == {"cmd": "fp_enroll", "id": 3}   # 1 et 2 occupés, 5 nommé pour Jessica : premier libre


def test_enroll_does_not_repeat_the_config_line_when_already_named(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    board = FakeBoard({"fp_list": [listing()], "fp_enroll": enroll_steps(("start", None), ("done", None))})
    assert command_enroll(board, BOOK, "Denis", 1) == 0
    assert "A ajouter" not in caplog.text


def test_enroll_refuses_an_occupied_slot_without_touching_it(caplog: pytest.LogCaptureFixture) -> None:
    board = FakeBoard({"fp_list": [listing(1)]})
    assert command_enroll(board, BOOK, "Denis", 1) == 1
    assert [c["cmd"] for c in board.sent] == ["fp_list"] and "deja occupe" in caplog.text


def test_enroll_failure_is_an_error(caplog: pytest.LogCaptureFixture) -> None:
    board = FakeBoard({"fp_list": [listing()],
                       "fp_enroll": enroll_steps(("start", None), ("failed", "mismatch"))})
    assert command_enroll(board, BOOK, "Denis", None) == 1 and "ECHEC" in caplog.text


def test_enroll_cancels_when_the_board_goes_silent() -> None:
    board = FakeBoard({"fp_list": [listing()], "fp_enroll": enroll_steps(("start", None))})
    assert command_enroll(board, BOOK, "Denis", None) == 1
    assert board.sent[-1] == {"cmd": "fp_cancel"}


def test_delete_one_and_all(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    board = FakeBoard({"fp_delete": [{"event": "fp_delete", "id": 5, "ok": True}]})
    assert command_delete(board, BOOK, 5, False) == 0 and board.sent == [{"cmd": "fp_delete", "id": 5}]
    assert "Jessica (empreinte 5) supprimee. Retirez sa ligne" in caplog.text
    everything = FakeBoard({"fp_delete": [{"event": "fp_delete", "all": True, "ok": True}]})
    assert command_delete(everything, BOOK, None, True) == 0 and everything.sent == [{"cmd": "fp_delete", "all": True}]


def test_delete_refused_by_the_sensor() -> None:
    board = FakeBoard({"fp_delete": [{"event": "fp_delete", "id": 5, "ok": False}]})
    assert command_delete(board, BOOK, 5, False) == 1
