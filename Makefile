# Makefile — toutes les opérations système du projet assistant vocal.
# Cible par défaut : help.

SHELL       := /bin/bash
VENV        := server/.venv
PY          := $(VENV)/bin/python
PIP         := $(VENV)/bin/pip
# PlatformIO vit dans son propre environnement : ses dépendances entrent sinon en
# conflit avec celles du serveur vocal (constaté avec click / esptool).
PIO_VENV    := .pio-venv
PIO         := $(PIO_VENV)/bin/pio
FW_DIR      := firmware
SERIAL_PORT ?= /dev/ttyUSB0
BAUD        ?= 921600
OUT         ?= recordings/test.wav
TONE        ?= 440
TEXT        ?= Quelle heure est-il ?
LAST        ?= 5
JITTER      ?= 0
VOICE       ?= fr_FR-tom-medium
SPEAKER     ?=
SAY_OUT     ?= recordings/voix-test/phrase.wav
WAKE        ?=
WAKE_VOICE  ?= en_GB-alan-medium
MQTT_HOST   ?= 192.168.1.104
MSG         ?= {"cmd":"ping"}
MOSQ_DIR    ?= $(HOME)/srv/mosquitto
MOSQ_COMPOSE ?= $(HOME)/srv/docker-compose.mosquitto.yml
# Clients MQTT : ceux du système s'ils sont installés, sinon ceux de l'image Docker.
MOSQ_RUN    := $(shell command -v mosquitto_sub >/dev/null 2>&1 || echo "docker run --rm --network host eclipse-mosquitto:2.0")
# Comptes MQTT (étape 15) : fichier de mots de passe hachés, jamais versionné.
MOSQ_PASSWD := $(shell command -v mosquitto_passwd >/dev/null 2>&1 && echo "mosquitto_passwd mosquitto/passwd" \
                 || echo "docker run --rm -it --user $$(id -u):$$(id -g) -v $(CURDIR)/mosquitto:/work eclipse-mosquitto:2.0 mosquitto_passwd /work/passwd")
# Les cibles mqtt-* prennent les identifiants du serveur dans server/.env. Leurs
# recettes commencent par @ : make n'affiche pas la ligne, donc pas le mot de passe.
-include server/.env
MQTT_AUTH    = $(if $(MQTT_USERNAME),-u '$(MQTT_USERNAME)' -P '$(MQTT_PASSWORD)')
MQTT_AS      = @echo "(compte MQTT : $(if $(MQTT_USERNAME),$(MQTT_USERNAME) de server/.env,anonyme))"

.DEFAULT_GOAL := help
.PHONY: help install-system mosquitto-config mosquitto-docker mqtt-user agent-run agent-install agent-log mqtt-agents mqtt-watch mqtt-transcript mqtt-home mqtt-control venv install install-dev install-speech install-diarization install-wakeword models \
        say speech-check transcribe vad vad-all diarize diarize-demo enroll profiles forget run test lint record play inspect play-esp tone-esp play-mqtt tone-mqtt run-echo pio-venv fw-build fw-check fw-upload \
        fw-monitor clean

## ------------------------------------------------------------------ aide

help:  ## Affiche cette aide
	@echo "Assistant vocal local ESP32 — cibles disponibles :"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Variables : SERIAL_PORT=$(SERIAL_PORT)  BAUD=$(BAUD)  OUT=$(OUT)  TONE=$(TONE)  MQTT_HOST=$(MQTT_HOST)  JITTER=$(JITTER)"
	@echo "            TEXT=\"$(TEXT)\""

## ------------------------------------------------------- installation système

install-system:  ## Installe les paquets Debian nécessaires (sudo)
	sudo apt-get update
	sudo apt-get install -y \
		mosquitto mosquitto-clients \
		ffmpeg \
		python3 python3-venv python3-pip \
		git
	@echo "Ajout de l'utilisateur au groupe dialout (accès au port série)."
	sudo usermod -aG dialout $$USER
	@echo "→ Déconnectez/reconnectez votre session pour activer le groupe dialout."

mosquitto-config: mosquitto/passwd  ## Installe voice.conf, acl et passwd dans /etc/mosquitto et redémarre le broker
	sed 's|/mosquitto/config/|/etc/mosquitto/|' mosquitto/voice.conf | sudo tee /etc/mosquitto/conf.d/voice.conf >/dev/null
	sudo install -o mosquitto -g mosquitto -m 0600 mosquitto/acl /etc/mosquitto/acl
	sudo install -o mosquitto -g mosquitto -m 0600 mosquitto/passwd /etc/mosquitto/passwd
	sudo systemctl restart mosquitto
	sudo systemctl --no-pager status mosquitto | head -n 5

mosquitto-docker: mosquitto/passwd  ## Installe voice.conf, acl et passwd dans le conteneur Mosquitto et le relance
	@test -f $(MOSQ_COMPOSE) || { echo "Fichier compose introuvable : MOSQ_COMPOSE=$(MOSQ_COMPOSE)"; exit 1; }
	-sudo cp $(MOSQ_DIR)/config/mosquitto.conf $(MOSQ_DIR)/config/mosquitto.conf.bak
	sudo install -o 1883 -g 1883 -m 0644 mosquitto/voice.conf $(MOSQ_DIR)/config/mosquitto.conf
	sudo install -o 1883 -g 1883 -m 0600 mosquitto/acl $(MOSQ_DIR)/config/acl
	sudo install -o 1883 -g 1883 -m 0600 mosquitto/passwd $(MOSQ_DIR)/config/passwd
	docker compose -f $(MOSQ_COMPOSE) restart
	@sleep 2
	docker compose -f $(MOSQ_COMPOSE) logs --tail 5

mosquitto/passwd:
	@echo "mosquitto/passwd absent : creez d'abord les comptes (make mqtt-user NAME=voice-server, puis NAME=esp32-01)"; exit 1

mqtt-user:  ## Crée ou change le compte MQTT NAME (voice-server, ou le DEVICE_ID d'une carte) dans mosquitto/passwd
	@test -n "$(NAME)" || { echo "usage : make mqtt-user NAME=voice-server   puis   make mqtt-user NAME=esp32-01"; exit 1; }
	@touch mosquitto/passwd && chmod 600 mosquitto/passwd
	@echo "mot de passe de $(NAME) (idee : openssl rand -hex 16), a reporter dans $(if $(filter voice-server,$(NAME)),server/.env,$(if $(findstring esp32,$(NAME)),firmware/include/secrets.h,/etc/voice-assistant/agent.yaml)) :"
	@$(MOSQ_PASSWD) $(NAME)
	@echo "compte $(NAME) enregistre, hache, dans mosquitto/passwd. Installation : make mosquitto-docker (ou mosquitto-config)."

## ------------------------------------------------------------- agents Linux (étape 15b)

agent-run: agent/agent.yaml  ## Lance l'agent de CETTE machine au premier plan (agent/agent.yaml, essai)
	$(PY) agent/voice_agent.py --config agent/agent.yaml

agent/agent.yaml:
	sed 's/^dry_run: false/dry_run: true/' agent/agent.yaml.example > $@
	@echo "agent/agent.yaml cree en MODE ESSAI (dry_run: true) : verifiez id et mqtt.host"

agent-install:  ## Installe l'agent comme service sur CETTE machine (autre machine : copier agent/, make -C agent install)
	$(MAKE) -C agent install

agent-log:  ## Journal de l'agent installé sur cette machine
	$(MAKE) -C agent log

mqtt-agents:  ## Affiche états, commandes et comptes rendus des agents : agent/#
	$(MQTT_AS)
	@$(MOSQ_RUN) mosquitto_sub -h $(MQTT_HOST) $(MQTT_AUTH) -v -t 'agent/#'

mqtt-watch:  ## Affiche les messages texte voice/+/... et home/# (sans l'audio binaire), Ctrl-C pour quitter
	$(MQTT_AS)
	@$(MOSQ_RUN) mosquitto_sub -h $(MQTT_HOST) $(MQTT_AUTH) -v -t 'voice/+/state' -t 'voice/+/event' \
		-t 'voice/+/control' -t 'voice/+/transcript' -t 'voice/server/status' -t 'home/#'

mqtt-home:  ## Affiche seulement les commandes domotiques home/# (étape 11)
	$(MQTT_AS)
	@$(MOSQ_RUN) mosquitto_sub -h $(MQTT_HOST) $(MQTT_AUTH) -v -t 'home/#'

mqtt-transcript:  ## Affiche seulement « qui a dit quoi » : voice/+/transcript (étape 10)
	$(MQTT_AS)
	@$(MOSQ_RUN) mosquitto_sub -h $(MQTT_HOST) $(MQTT_AUTH) -v -t 'voice/+/transcript'

mqtt-control:  ## Publie MSG sur voice/esp32-01/control
	$(MQTT_AS)
	@$(MOSQ_RUN) mosquitto_pub -h $(MQTT_HOST) $(MQTT_AUTH) -q 1 -t voice/esp32-01/control -m '$(MSG)'

## ----------------------------------------------------------- environnement Python

venv:  ## Crée l'environnement virtuel Python
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip setuptools wheel

install: venv  ## Installe le serveur Python (dépendances de base)
	$(PIP) install -e server
	@test -f server/.env || { cp server/.env.example server/.env; echo "server/.env créé depuis .env.example — renseigner MQTT_USERNAME/PASSWORD et HF_TOKEN si besoin."; }

install-dev: install pio-venv  ## Installe en plus les outils de développement (pyright, pytest, platformio)
	$(PIP) install -e "server[dev]"

install-speech: install  ## Installe faster-whisper et Piper (étape 6)
	$(PIP) install -e "server[speech]"

install-diarization: install-speech  ## Installe pyannote et torch, version CPU (étape 8, ~1,3 Go)
	$(PIP) install --index-url https://download.pytorch.org/whl/cpu \
		--extra-index-url https://pypi.org/simple -e "server[diarization]"

install-wakeword: install-speech  ## Installe openWakeWord, en mode ONNX (étape 13)
	$(PIP) install -e "server[wakeword]"
	# Sans dépendances : openwakeword exige tflite-runtime, absent pour Python 3.13 (inutile en ONNX).
	$(PIP) install --no-deps "openwakeword==0.6.0"

models:  ## Télécharge les modèles de config.yaml dans server/models (étapes 6, 8 et 13)
	$(PY) tools/download_models.py

say:  ## Fait dire [WAKE (voix anglaise) puis] TEXT par VOICE (SPEAKER) : WAV + MP3 dans SAY_OUT, pour un téléphone
	$(PY) tools/say.py "$(TEXT)" --voice $(VOICE) $(if $(SPEAKER),--speaker $(SPEAKER)) \
		$(if $(WAKE),--wake "$(WAKE)" --wake-voice $(WAKE_VOICE)) --out $(SAY_OUT)

speech-check:  ## Synthèse, transcription et RÉPONSE de l'assistant à TEXT, sans ESP32 (étape 6)
	$(PY) tools/speech_check.py "$(TEXT)"

transcribe:  ## VAD, transcription et réponse du WAV OUT, sans ESP32 (étapes 6-7)
	$(PY) tools/speech_check.py --wav $(OUT)

vad:  ## Montre la parole trouvée par la VAD dans OUT, écrit la parole seule (étape 7)
	$(PY) tools/vad_check.py $(OUT)

vad-all:  ## Passe la VAD sur tous les enregistrements du serveur (étape 7)
	$(PY) tools/vad_check.py server/recordings/*.wav

diarize:  ## Qui parle quand dans OUT, qui est-ce, qui a dit quoi (étapes 8-10)
	$(PY) tools/diarize_check.py $(OUT)

diarize-demo:  ## Diarise une séquence à deux voix : la vôtre et celle de Piper (étape 8)
	$(PY) tools/diarize_check.py --demo

enroll:  ## Crée le profil vocal de NAME avec ses LAST dernières sessions (étape 9)
	@test -n "$(NAME)" || { echo "Usage : make enroll NAME=Denis [LAST=5]"; exit 1; }
	$(PY) tools/enroll.py --name "$(NAME)" --last $(LAST)

profiles:  ## Liste les profils vocaux (étape 9)
	$(PY) tools/enroll.py --list

forget:  ## Supprime le profil vocal de NAME : donnée biométrique (étape 9)
	@test -n "$(NAME)" || { echo "Usage : make forget NAME=Denis"; exit 1; }
	$(PY) tools/enroll.py --forget "$(NAME)"

## ----------------------------------------------------------------- serveur

run:  ## Lance l'assistant vocal : répond à chaque session (étape 6, Ctrl-C pour arrêter)
	cd server && .venv/bin/python -m voice_server --config config.yaml

run-echo:  ## Serveur en mode echo : chaque session est rejouee par l'ESP32 (etape 5)
	cd server && .venv/bin/python -m voice_server --config config.yaml --echo

test:  ## Exécute les tests Python
	$(VENV)/bin/pytest server/tests -v

lint:  ## Vérifie le typage Python avec pyright
	$(VENV)/bin/pyright --project server/pyrightconfig.json

## ----------------------------------------------------------------- outils

play:  ## Joue OUT en cherchant un lecteur qui fonctionne
	@for player in paplay pw-play ffplay aplay; do \
		command -v $$player >/dev/null 2>&1 || continue; \
		echo "-> $$player $(OUT)"; \
		case $$player in \
			ffplay) ffplay -autoexit -nodisp -loglevel error "$(OUT)" && exit 0 ;; \
			*) $$player "$(OUT)" && exit 0 ;; \
		esac; \
	done; \
	echo "Aucun lecteur n'a abouti. Utilisez 'make inspect' pour verifier sans ecouter."; \
	exit 1

inspect:  ## Analyse OUT sans le jouer : enveloppe ASCII et niveaux
	$(PY) tools/wav_inspect.py $(OUT)

play-esp:  ## Joue OUT sur le haut-parleur de l'ESP32, au rythme reel (etape 2)
	$(PY) tools/wav_to_serial.py --port $(SERIAL_PORT) --baud $(BAUD) $(OUT)

tone-esp:  ## Joue un son pur de TONE Hz sur le haut-parleur de l'ESP32 (etape 2)
	$(PY) tools/wav_to_serial.py --port $(SERIAL_PORT) --baud $(BAUD) --tone $(TONE) --seconds 3

play-mqtt:  ## Joue OUT sur l'ESP32, par MQTT, avec bilan ; JITTER=ms simule un Wi-Fi irrégulier (étapes 5, 12)
	$(PY) tools/wav_to_mqtt.py --jitter $(JITTER) $(OUT)

tone-mqtt:  ## Joue un son pur de TONE Hz sur l'ESP32, par MQTT ; JITTER=ms simule un Wi-Fi irrégulier (étapes 5, 12)
	$(PY) tools/wav_to_mqtt.py --tone $(TONE) --seconds 5 --jitter $(JITTER)

record:  ## Enregistre une session depuis l'ESP32 vers un WAV (étape 1)
	@mkdir -p $$(dirname $(OUT))
	$(PY) tools/serial_to_wav.py --port $(SERIAL_PORT) --baud $(BAUD) --output $(OUT)

## ---------------------------------------------------------------- firmware

pio-venv:  ## Installe PlatformIO dans son propre environnement virtuel
	python3 -m venv $(PIO_VENV)
	$(PIO_VENV)/bin/pip install --upgrade pip
	$(PIO_VENV)/bin/pip install platformio
	# click 8.3+ casse l'esptool 5.0 livré avec le core Arduino 3.x : on épingle 8.1.
	$(PIO_VENV)/bin/pip install "click==8.1.8"

fw-build:  ## Compile le firmware ESP32
	@test -x $(PIO) || { echo "PlatformIO absent : lancez 'make pio-venv'."; exit 1; }
	$(PIO) run -d $(FW_DIR)

fw-check:  ## Compile sans secrets.h personnel (secrets factices hors du dépôt)
	@test -x $(PIO) || { echo "PlatformIO absent : lancez 'make pio-venv'."; exit 1; }
	@if [ -f $(FW_DIR)/include/secrets.h ]; then \
		echo "secrets.h present : compilation normale"; $(PIO) run -d $(FW_DIR); \
	else \
		tmp=$$(mktemp -d); cp $(FW_DIR)/include/secrets.h.example $$tmp/secrets.h; \
		echo "secrets.h absent : secrets factices dans $$tmp (firmware/include/ n'est pas touche)"; \
		PLATFORMIO_BUILD_FLAGS="-I$$tmp" $(PIO) run -d $(FW_DIR); status=$$?; rm -rf $$tmp; exit $$status; \
	fi

fw-upload:  ## Compile et téléverse le firmware
	$(PIO) run -d $(FW_DIR) -t upload --upload-port $(SERIAL_PORT)

fw-monitor:  ## Ouvre le moniteur série (Ctrl-C pour quitter)
	$(PIO) device monitor -p $(SERIAL_PORT) -b $(BAUD)

## ------------------------------------------------------------------ nettoyage

clean:  ## Supprime les artefacts de compilation et les caches
	rm -rf $(FW_DIR)/.pio
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf server/*.egg-info .pytest_cache
