#!/usr/bin/env python3
"""Télécharge les modèles de config.yaml dans server/models/ (étape 6).

    models/whisper/<stt.model>/       modèle faster-whisper (CTranslate2)
    models/piper/<tts.voice>.onnx     voix Piper et sa configuration .onnx.json
    models/pyannote/<nom>/            diarisation (étape 8) : jeton HF_TOKEN dans server/.env
    models/openwakeword/*.onnx        mots de réveil (étape 13)

Les modèles déjà présents ne sont pas retéléchargés. Ensuite, le serveur
fonctionne sans Internet : il ne lit que ces fichiers locaux.

Exemple :
    python3 tools/download_models.py
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from voice_server.settings import (  # noqa: E402
    DiarizationSettings,
    SttSettings,
    TtsSettings,
    WakeSettings,
    load_settings,
)
from voice_server.wake_word import model_files  # noqa: E402

OPENWAKEWORD_URL = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/{name}"

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


def download_pyannote(diarization: DiarizationSettings) -> bool:
    """Télécharge le pipeline de diarisation ; False si le jeton ou l'accord manque."""
    if not diarization.enabled:
        logger.info("diarisation desactivee dans config.yaml : rien a telecharger")
        return True
    if (diarization.model_dir / "config.yaml").exists():
        logger.info("diarisation %s deja presente : %s", diarization.model, diarization.model_dir)
        return True
    page = f"https://huggingface.co/{diarization.model}"
    token = os.environ.get("HF_TOKEN")  # chargé depuis server/.env par load_settings
    if not token:
        logger.error("HF_TOKEN absent de server/.env. Pour la diarisation :\n"
                     "  1. compte sur https://huggingface.co, puis accepter les conditions sur %s\n"
                     "  2. jeton « Read » sur https://huggingface.co/settings/tokens\n"
                     "  3. cp server/.env.example server/.env, y renseigner HF_TOKEN=..., relancer 'make models'",
                     page)
        return False
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import GatedRepoError, HfHubHTTPError

    logger.info("telechargement de %s vers %s…", diarization.model, diarization.model_dir)
    try:
        snapshot_download(diarization.model, local_dir=diarization.model_dir, token=token,
                          allow_patterns=["config.yaml", "*/pytorch_model.bin", "plda/*.npz"])
    except GatedRepoError:
        logger.error("acces refuse : acceptez les conditions du modele sur %s", page)
        return False
    except HfHubHTTPError as error:
        logger.error("echec du telechargement (%s) : jeton HF_TOKEN valide ?", error)
        return False
    return True


def download_openwakeword(wake: WakeSettings) -> None:
    """Télécharge les modèles ONNX d'openWakeWord (communs + un par mot) depuis GitHub."""
    if not wake.enabled:
        return
    from urllib.request import urlretrieve

    wake.model_dir.mkdir(parents=True, exist_ok=True)
    for name in model_files(wake):
        target = wake.model_dir / name
        if target.exists():
            continue
        logger.info("telechargement de %s", name)
        urlretrieve(OPENWAKEWORD_URL.format(name=name), target)
    logger.info("mots de reveil prets : %s", ", ".join(wake.words))


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
    download_openwakeword(settings.wake)
    diarization_ready = download_pyannote(settings.diarization)

    size = sum(f.stat().st_size for f in settings.models_dir.rglob("*") if f.is_file())
    logger.info("modeles dans %s (%.0f Mo)", settings.models_dir, size / 1e6)
    return 0 if diarization_ready else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
