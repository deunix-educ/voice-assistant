"""Association locuteur + texte : qui a dit quoi (étape 10).

Whisper donne l'instant de chaque mot, la diarisation l'instant de chaque tour
de parole. Les deux ont reçu exactement le même audio (la parole trouvée par la
VAD) : leurs horloges coïncident. Chaque mot va au tour qui le recouvre le plus,
puis les mots consécutifs d'un même locuteur forment une intervention.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass

from voice_server.diarization import SpeakerTurn
from voice_server.speaker_identifier import UNKNOWN, Identification
from voice_server.speech_to_text import Word


@dataclass(frozen=True)
class Utterance:
    """Une intervention : ce qu'une personne a dit d'un seul tenant."""

    speaker: str            # nom, ou « Inconnu »
    text: str
    start_s: float          # instants dans la parole sans silences (ceux de Whisper et de la diarisation)
    end_s: float
    score: float | None     # similarité avec le profil (None : pas d'identification)
    label: str = ""         # étiquette de diarisation (SPEAKER_00), vide sans diarisation


def turn_of(word: Word, turns: Sequence[SpeakerTurn]) -> SpeakerTurn | None:
    """Le tour qui recouvre le plus le mot ; à défaut (mot dans un blanc), le plus proche."""
    if not turns:
        return None

    def overlap(turn: SpeakerTurn) -> float:
        return min(word.end_s, turn.end_s) - max(word.start_s, turn.start_s)

    best = max(turns, key=overlap)
    if overlap(best) > 0:
        return best
    middle = (word.start_s + word.end_s) / 2
    return min(turns, key=lambda turn: min(abs(middle - turn.start_s), abs(middle - turn.end_s)))


def attribute(words: Sequence[Word], turns: Sequence[SpeakerTurn],
              verdicts: dict[str, Identification]) -> list[Utterance]:
    """Regroupe les mots en interventions, une par changement de locuteur.

    Args:
        words: mots horodatés de Whisper.
        turns: tours de parole de la diarisation (vide : diarisation désactivée).
        verdicts: identification de chaque étiquette de diarisation.
    """
    utterances: list[Utterance] = []
    group: list[Word] = []
    current: str | None = None

    def close() -> None:
        if not group:
            return
        verdict = verdicts.get(current or "")
        score = verdict.score if verdict is not None and not math.isnan(verdict.score) else None
        utterances.append(Utterance(
            speaker=verdict.label if verdict is not None else UNKNOWN,
            text="".join(word.text for word in group).strip(),
            start_s=group[0].start_s,
            end_s=group[-1].end_s,
            score=score,
            label=current or "",
        ))

    for word in words:
        turn = turn_of(word, turns)
        label = turn.speaker if turn is not None else None
        if group and label != current:
            close()
            group = []
        current = label
        group.append(word)
    close()
    return utterances


def transcript_json(session: str, utterances: Sequence[Utterance]) -> bytes:
    """Message publié sur voice/<carte>/transcript.

    {"session": "a1b2c3", "utterances": [{"speaker": "Denis", "text": "...",
     "start": 0.0, "end": 1.66, "score": 0.74}, ...]}
    """
    return json.dumps({
        "session": session,
        "utterances": [
            {"speaker": u.speaker, "text": u.text, "start": round(u.start_s, 2), "end": round(u.end_s, 2),
             "score": None if u.score is None else round(u.score, 2)}
            for u in utterances
        ],
    }, ensure_ascii=False).encode("utf-8")
