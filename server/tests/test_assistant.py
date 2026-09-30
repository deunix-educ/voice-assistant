"""Tests de l'assistant à règles, étape 6 : réponses connues d'avance."""

from __future__ import annotations

from datetime import datetime

import pytest

from voice_server.assistant import NOTHING_HEARD, Assistant, normalize, say_date, say_time

NOW = datetime(2026, 9, 29, 19, 5)  # un mardi


@pytest.fixture
def assistant() -> Assistant:
    return Assistant(clock=lambda: NOW)


def test_normalize() -> None:
    """Minuscules, accents et ponctuation retirés."""
    assert normalize("  Quelle heure est-il ?") == "quelle heure est il"
    assert normalize("Ça marche, merci !") == "ca marche merci"


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 1, 1, 0, 0), "Il est minuit."),
        (datetime(2026, 1, 1, 1, 0), "Il est une heure."),
        (datetime(2026, 1, 1, 12, 30), "Il est midi 30."),
        (datetime(2026, 1, 1, 19, 5), "Il est 19 heures 5."),
    ],
)
def test_say_time(moment: datetime, expected: str) -> None:
    """L'heure est écrite pour être bien lue par Piper."""
    assert say_time(moment) == expected


def test_say_date() -> None:
    """« premier » pour le 1er du mois, jour de la semaine en toutes lettres."""
    assert say_date(datetime(2026, 10, 1)) == "Nous sommes le jeudi premier octobre 2026."
    assert say_date(NOW) == "Nous sommes le mardi 29 septembre 2026."


@pytest.mark.parametrize(
    ("heard", "expected"),
    [
        ("Quelle heure est-il ?", "Il est 19 heures 5."),
        ("Quel heure est-il ?", "Il est 19 heures 5."),        # faute fréquente de Whisper
        ("Bonjour, quelle heure est-il ?", "Il est 19 heures 5."),  # la règle précise gagne
        ("On est quel jour ?", "Nous sommes le mardi 29 septembre 2026."),
        ("Salut !", "Bonjour ! Que puis-je faire pour vous ?"),
        ("Merci beaucoup.", "Avec plaisir."),
        ("Allume la lumière.", "Vous avez dit : Allume la lumière."),
    ],
)
def test_rules(assistant: Assistant, heard: str, expected: str) -> None:
    """Chaque règle répond, sinon la phrase est répétée."""
    assert assistant.reply(heard) == expected


def test_whole_words_only(assistant: Assistant) -> None:
    """« salutations » ne déclenche pas la règle de « salut »."""
    assert assistant.reply("Salutations distinguées") == "Vous avez dit : Salutations distinguées"


@pytest.mark.parametrize("heard", ["", "   ", "?!"])
def test_nothing_heard(assistant: Assistant, heard: str) -> None:
    """Rien d'exploitable : l'assistant le dit plutôt que de se taire."""
    assert assistant.reply(heard) == NOTHING_HEARD


def test_known_speaker_is_greeted_by_name(assistant: Assistant) -> None:
    """Étape 10 : l'assistant sait qui parle."""
    assert assistant.reply("Bonjour", "Denis") == "Bonjour Denis ! Que puis-je faire pour vous ?"
    assert assistant.reply("Merci", "Denis") == "Avec plaisir, Denis."
    assert assistant.reply("Bonjour", None) == "Bonjour ! Que puis-je faire pour vous ?"
    assert assistant.reply("Quelle heure est-il ?", "Denis") == "Il est 19 heures 5."


def test_weather_is_honest(assistant: Assistant) -> None:
    """Étape 11 : sans Internet, l'assistant le dit plutôt que de répéter la question."""
    assert "sans Internet" in assistant.reply("Quel temps fera-t-il demain ?")
