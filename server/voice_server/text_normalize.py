"""Normalisation du texte transcrit, commune à l'assistant et à la domotique."""

from __future__ import annotations

import re
import unicodedata


def normalize(text: str) -> str:
    """Minuscules, sans accents ni ponctuation : « Quelle heure est-il ? » → « quelle heure est il »."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    without_accents = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", without_accents).split())
