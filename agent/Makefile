# Installation de l'agent sur une machine Linux (étape 15b).
# Ce dossier suffit : copiez-le sur la machine, puis « make -C agent install ».
# Prérequis Debian : sudo apt install make python3-venv

SHELL       := /bin/bash
PREFIX      := /opt/voice-agent
CONFIG_DIR  := /etc/voice-agent
# Mêmes versions que le serveur (server/pyproject.toml).
DEPS        := paho-mqtt==2.1.0 PyYAML==6.0.2

.PHONY: help install uninstall log
.DEFAULT_GOAL := help

help:  ## Affiche cette aide
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'

install:  ## Installe l'agent comme service (sudo) ; garde une configuration existante
	sudo install -d -m 0755 $(PREFIX)
	sudo install -m 0644 voice_agent.py $(PREFIX)/voice_agent.py
	sudo python3 -m venv $(PREFIX)/.venv
	sudo $(PREFIX)/.venv/bin/pip install --quiet --disable-pip-version-check $(DEPS)
	sudo install -d -m 0700 $(CONFIG_DIR)
	@if sudo test -f $(CONFIG_DIR)/agent.yaml; then \
		echo "$(CONFIG_DIR)/agent.yaml existe : conserve"; \
	else \
		sudo install -m 0600 agent.yaml.example $(CONFIG_DIR)/agent.yaml; \
		echo "A FAIRE : sudo nano $(CONFIG_DIR)/agent.yaml (id, mqtt.host), puis sudo systemctl restart voice-agent"; \
	fi
	sudo install -m 0644 voice-agent.service /etc/systemd/system/voice-agent.service
	sudo systemctl daemon-reload
	sudo systemctl enable --now voice-agent
	sudo systemctl restart voice-agent
	@sleep 1; sudo systemctl --no-pager status voice-agent | head -n 5

uninstall:  ## Retire le service et le programme (garde la configuration)
	-sudo systemctl disable --now voice-agent
	sudo rm -f /etc/systemd/system/voice-agent.service
	sudo systemctl daemon-reload
	sudo rm -rf $(PREFIX)

log:  ## Journal de l'agent installé (Ctrl-C pour quitter)
	journalctl -u voice-agent -f
