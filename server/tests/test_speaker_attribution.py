"""Tests de l'association locuteur + texte, étape 10 (sans modèle)."""

from __future__ import annotations

import json
import math

from voice_server.diarization import SpeakerTurn
from voice_server.speaker_attribution import Utterance, attribute, transcript_json, turn_of
from voice_server.speaker_identifier import Identification
from voice_server.speech_to_text import Word

# Mots et tours relevés sur la session réelle 453cce (vous, un haut-parleur, vous).
WORDS = [Word(" Ouvrir", 0.00, 0.56), Word(" la", 0.56, 0.76), Word(" porte.", 0.76, 1.26),
         Word(" Je", 1.50, 1.66), Word(" suis", 1.66, 1.78), Word(" là.", 1.78, 1.92),
         Word(" Fermez", 4.92, 5.38), Word(" la", 5.38, 5.52), Word(" porte.", 5.52, 5.86)]
TURNS = [SpeakerTurn("SPEAKER_00", 0.08, 1.50), SpeakerTurn("SPEAKER_01", 1.57, 4.69),
         SpeakerTurn("SPEAKER_00", 4.92, 6.17)]
VERDICTS = {"SPEAKER_00": Identification("Denis", 0.72, "Denis"),
            "SPEAKER_01": Identification(None, -0.05, "Denis")}


def test_word_goes_to_the_most_overlapping_turn() -> None:
    """« Je » (1,50-1,66) touche SPEAKER_00 sur 0 s et SPEAKER_01 sur 0,09 s."""
    turn = turn_of(Word(" Je", 1.50, 1.66), TURNS)
    assert turn is not None and turn.speaker == "SPEAKER_01"


def test_word_in_a_gap_goes_to_the_nearest_turn() -> None:
    """Un mot entre deux tours va au plus proche : jamais perdu."""
    before = turn_of(Word(" euh", 4.70, 4.76), TURNS)  # 0,04 s après la fin de SPEAKER_01
    after = turn_of(Word(" euh", 4.84, 4.90), TURNS)   # 0,05 s avant la reprise de SPEAKER_00
    assert before is not None and before.speaker == "SPEAKER_01"
    assert after is not None and after.start_s == 4.92


def test_real_session_is_split_by_speaker() -> None:
    utterances = attribute(WORDS, TURNS, VERDICTS)

    assert [(u.speaker, u.text) for u in utterances] == [
        ("Denis", "Ouvrir la porte."), ("Inconnu", "Je suis là."), ("Denis", "Fermez la porte.")]
    assert (utterances[0].start_s, utterances[0].end_s) == (0.0, 1.26)
    assert utterances[1].label == "SPEAKER_01" and utterances[1].score == -0.05


def test_without_diarization_everything_is_one_unknown_utterance() -> None:
    [utterance] = attribute(WORDS, [], {})

    assert utterance.speaker == "Inconnu" and utterance.score is None
    assert utterance.text == "Ouvrir la porte. Je suis là. Fermez la porte."


def test_speaker_with_too_little_speech_has_no_score() -> None:
    """Empreinte impossible (NaN) : « Inconnu », score null dans le JSON."""
    [utterance] = attribute(WORDS[:1], TURNS[:1], {"SPEAKER_00": Identification(None, math.nan, None)})
    assert utterance.speaker == "Inconnu" and utterance.score is None


def test_json_message() -> None:
    """Le format attendu par la feuille de route : {"speaker": ..., "text": ...}, accents lisibles."""
    payload = transcript_json("a1b2c3", [Utterance("Élodie", "Ferme la porte.", 0.0, 1.234, 0.7456)])

    assert json.loads(payload) == {"session": "a1b2c3", "utterances": [
        {"speaker": "Élodie", "text": "Ferme la porte.", "start": 0.0, "end": 1.23, "score": 0.75}]}
    assert "Élodie".encode() in payload
