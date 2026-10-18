#!/usr/bin/env python3
"""Télécharge les modèles de config.yaml dans server/models/ (étape 6).

    models/whisper/<stt.model>/       modèle faster-whisper (CTranslate2)
    models/piper/<tts.voice>.onnx     voix Piper et sa configuration .onnx.json

Les modèles déjà présents ne sont pas retéléchargés. Ensuite, le serveur
fonctionne sans Internet : il ne lit que ces fichiers locaux.

Exemple :
    python3 tools/download_models.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from voice_server.settings import SttSettings, TtsSettings, load_settings  # noqa: E402

logger = logging.getLogger("download_models")


def download_whisper(stt: SttSettings) -> None:
    """Télécharge le modèle faster-whisper depuis Hugging Face (Systran)."""
    if (stt.model_dir / "model.bin").exists():
        logger.info("whisper %s deja present : %s", stt.model, stt.model_dir)
        return
    from faster_whisper import download_model

    logger.info("telechargement de whisper %s vers %s…", stt.model, stt.model_dir)
    download_model(stt.model, output_dir=str(stt.model_dir))


def download_piper(tts: TtsSettings) -> None:
    """Télécharge la voix Piper (.onnx et .onnx.json) depuis Hugging Face (rhasspy)."""
    config_path = tts.model_path.with_name(tts.model_path.name + ".json")
    if tts.model_path.exists() and config_path.exists():
        logger.info("voix piper %s deja presente : %s", tts.voice, tts.model_path)
        return
    from piper.download_voices import download_voice

    logger.info("telechargement de la voix piper %s vers %s…", tts.voice, tts.model_path.parent)
    tts.model_path.parent.mkdir(parents=True, exist_ok=True)
    download_voice(tts.voice, tts.model_path.parent)


def main(argv: list[str]) -> int:
    """Télécharge ce qui manque, puis affiche la taille totale."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=SERVER_DIR / "config.yaml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # une ligne par requête HTTP sinon

    settings = load_settings(args.config)
    try:
        download_whisper(settings.stt)
        download_piper(settings.tts)
    except ImportError as error:
        logger.error("%s : lancez d'abord 'make install-speech'", error)
        return 1

    size = sum(f.stat().st_size for f in settings.models_dir.rglob("*") if f.is_file())
    logger.info("modeles dans %s (%.0f Mo)", settings.models_dir, size / 1e6)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
