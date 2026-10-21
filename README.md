# Assistant vocal local — ESP32 + Raspberry Pi

Assistant vocal **100 % local**, sans aucun service cloud : un ESP32 capture la
voix, l'envoie en PCM brut par MQTT à un serveur Python qui transcrit, interprète
et répond en synthèse vocale ; l'ESP32 joue la réponse et exécute la commande.

*English version: [README-EN.md](README-EN.md).*

---

## 1. Architecture

```
R503 (doigt) ---+
                |
INMP441 -> ESP32 -> MQTT -> Serveur Python (Raspberry Pi / PC)
                                  |
                   personne = nom du doigt (fingerprint.users)
                                  |
                           faster-whisper
                                  |
                       Assistant + droits (access)
                                  |
                                Piper
                                  |
                             MQTT audio/out
                                  |
                        ESP32 -> MAX98357A -> haut-parleur
```

On pose un doigt enregistré sur le capteur, on parle, on le lève : la session dure autant que le doigt.

| Bloc | Question | Qui répond |
|---|---|---|
| Capteur R503 | Qui tient la carte ? | le doigt reconnu (emplacement du capteur → nom) |
| STT | Qu'est-ce qui est dit ? | faster-whisper, sur toute la session |
| Droits | Qu'a-t-il le droit de demander ? | `access` de `conf/access.yaml`, par personne |

> Le doigt dit **qui tient la carte**, pas qui parle : quelqu'un d'autre peut parler pendant que votre doigt est
> posé. Les actions sensibles demandent donc une confirmation avec le même doigt. Le bouton PTT n'identifie personne.
> Il n'y a plus de VAD, de diarisation, d'identification de la voix ni de mot de réveil : ils sont dans la branche `main`.

---

## 2. Matériel

| Élément | Détail |
|---|---|
| ESP32-WROOM-32 | deux périphériques I2S : I2S0 en réception, I2S1 en émission. Une carte « ESP-32S » (NodeMCU-32S, 38 broches) convient aussi, mêmes GPIO : [docs/BROCHAGE.md](docs/BROCHAGE.md) §1.7 |
| ESP32-S3 Mini (branche `fingerprint`) | même firmware, autre brochage : [docs/BROCHAGE.md](docs/BROCHAGE.md) §1.5, environnement `esp32s3mini` |
| INMP441 | micro I2S, **3,3 V**, broche L/R à GND (canal gauche) |
| MAX98357A | ampli classe D mono, **5 V**, SD à VIN, GAIN laissé libre (9 dB) |
| Bouton | Push-To-Talk entre GPIO 4 et GND (pull-up interne) |
| LED | + résistance 330 Ω sur GPIO 2 |
| Serveur | Raspberry Pi 4/5 64 bits ou PC Debian |

### Câblage

**Schéma complet : [docs/wiring.svg](docs/wiring.svg)** (ESP32 WROOM, sans capteur), **[docs/wiring-esp32-wroom-fingerprint.svg](docs/wiring-esp32-wroom-fingerprint.svg)**
(ESP32 WROOM avec le capteur R503, repli de la S3) ou **[docs/wiring-esp32-s3.svg](docs/wiring-esp32-s3.svg)** (ESP32-S3 Mini, avec le capteur R503) — brochage détaillé,
bilan de consommation, vérifications au multimètre et passage d'une carte à l'autre (§1.6) : **[docs/BROCHAGE.md](docs/BROCHAGE.md)**.

```
        ESP32                     INMP441 (micro)
    3V3  ----------------------- VDD
    GND  ----------------------- GND, L/R
    GPIO 25 (SCK)  ------------- SCK
    GPIO 26 (WS)   ------------- WS
    GPIO 33 (SD)   ------------- SD

        ESP32                     MAX98357A (ampli, étape 2)
    5V (VIN) ------------------- VIN
    GND  ----------------------- GND
    GPIO 27 (BCLK) ------------- BCLK
    GPIO 14 (LRC)  ------------- LRC
    GPIO 22 (DIN)  ------------- DIN

        ESP32                     Interface
    GPIO 4  ------ bouton ------ GND
    GPIO 2  --[330 Ω]-- LED ---- GND
```

Aucune broche de strapping (GPIO 0, 2, 5, 12, 15) n'est utilisée pour l'audio :
leur niveau au démarrage change le mode de boot de l'ESP32. GPIO 2 est utilisé en
sortie seule pour la LED, ce qui reste sans effet sur le boot.

---

## 3. Format audio, unique dans tout le système

| Paramètre | Valeur |
|---|---|
| Fréquence | 16 000 Hz |
| Résolution | 16 bits signés, little-endian (`pcm_s16le`) |
| Canaux | 1 (mono) |
| Débit | 32 000 octets/s |
| Chunk | 50 ms = 800 échantillons = **1 600 octets** |

Pas de Base64 : les payloads MQTT contiennent des octets PCM bruts.

---

## 4. Installation

### 4.0 Choisir sa version

Deux versions vivent dans ce dépôt, sur deux branches. Elles ne se mélangent pas : le code de la voix a été retiré de `fingerprint`.

| | `main` | `fingerprint` |
|---|---|---|
| Qui tient la carte | la **voix** (profils vocaux, diarisation) | le **doigt** posé sur un capteur R503 |
| Début de session | mot de réveil « Hey Mycroft » ou bouton PTT | doigt posé, ou bouton PTT (sans identité) |
| Dépendances lourdes | pyannote et torch (≈ 1,3 Go), openWakeWord | aucune |
| Cartes | ESP32 WROOM | ESP32 WROOM ou ESP32-S3 Mini |
| Configuration | `server/config.yaml` versionné | modèle `config.example.yaml`, copie locale ignorée, `server/conf/` |
| Service systemd | non | oui (`make server-install`) |

**Obtenir la version `fingerprint`**

```bash
# dossier neuf
git clone -b fingerprint https://github.com/deunix-educ/voice-assistant.git     # « -b main » pour la version avec voix
cd voice-assistant

# dépôt déjà cloné
git fetch origin && git switch fingerprint && git pull
```

Puis les étapes 4.1 et 4.2 ci-dessous (`make install-system`, `make install-dev`, `make install-speech`, `make models`).
ESP32-S3 Mini : `make fw-upload FW_ENV=esp32s3mini SERIAL_PORT=/dev/ttyACM0`.

**Changer de branche dans le même dossier : attention à `server/config.yaml`.** Sur `fingerprint` il est ignoré par git ; sur
`main` il est suivi. Résultat : `git switch main` **remplace votre fichier, sans prévenir**, par celui de `main` (git écrase
les fichiers ignorés), et `git switch fingerprint` le **supprime** ensuite. Sauvegardez-le avant (`cp server/config.yaml
~/config.yaml.fingerprint`) et remettez-le au retour. Après un retour sur `main`, ses dépendances de voix sont à réinstaller
(`make install-diarization`, `make install-wakeword`).

**Plus sûr : un dossier par version**, sans rien changer dans l'autre :

```bash
git worktree add ../voice-assistant-main main      # une copie de main à côté, avec ses propres fichiers
cd ../voice-assistant-main && make install-dev     # son venv, son server/.env (à renseigner) et ses modèles (make models)
```

### 4.1 Firmware ESP32

**Prérequis :** câble USB avec les données (pas charge seule), port identifié
(`ls /dev/ttyUSB*` ou `/dev/ttyACM*` après branchement).

```bash
make pio-venv          # PlatformIO dans son propre venv — une seule fois
make fw-build          # compile sans flasher
make fw-upload SERIAL_PORT=/dev/ttyUSB0
```

ESP32-S3 Mini : ajouter `FW_ENV=esp32s3mini` et prendre le port USB natif (`/dev/ttyACM0`), voir l'étape F5.

**Premier démarrage — portail de configuration WiFi**

Si aucun identifiant n'a encore été configuré, la carte ouvre automatiquement
un point d'accès :

1. La LED clignote rapidement (100 ms on / 100 ms off).
2. Connectez un téléphone ou un PC au réseau **VoiceAssist-Config**.
3. Ouvrez `http://192.168.4.1` dans un navigateur.
4. Renseignez le Wi-Fi **(réseau 2,4 GHz obligatoire)** et l'adresse IP du
   broker MQTT.
5. Cliquez **Enregistrer et redémarrer** — les identifiants sont sauvegardés
   dans la mémoire permanente (NVS) et la carte redémarre.

> **Reconfiguration :** tenez le bouton PTT appuyé 3 s à la mise sous tension
> pour rouvrir le portail, même si des identifiants sont déjà enregistrés.

**Alternative — `secrets.h`** (pour les compilations répétées en développement)

```bash
cp firmware/include/secrets.h.example firmware/include/secrets.h
# Éditer : WIFI_SSID, WIFI_PASSWORD, MQTT_HOST
make fw-upload SERIAL_PORT=/dev/ttyUSB0
```

La NVS reste prioritaire sur `secrets.h`. Pour repartir de zéro, forcez le
portail avec le bouton PTT (ou effacez la NVS avec `esptool.py erase_flash`).

**Point d'accès Wi-Fi du Raspberry Pi 5 : WPA2 obligatoire**

Si la carte se connecte au point d'accès du Pi (NetworkManager, ici `RPI5-AP`),
celui-ci doit être en **WPA2**. L'ESP32 refuse par défaut tout réseau en dessous
de WPA2, et un point d'accès créé par NetworkManager peut être en WPA1.
Correction recommandée, sur le Pi :

```bash
nmcli -t -f NAME,TYPE con show --active        # nom de la connexion du point d'accès
sudo nmcli con modify "RPI5-AP" wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn \
     wifi-sec.pairwise ccmp wifi-sec.group ccmp \
     802-11-wireless.band bg 802-11-wireless.channel 6
sudo nmcli con down "RPI5-AP" && sudo nmcli con up "RPI5-AP"
```

`proto rsn` impose WPA2 ; le canal 6 évite les canaux 12 et 13, mal gérés par
l'ESP32. Le mot de passe du Wi-Fi ne change pas. Sur ce réseau, le Pi a
l'adresse `10.42.0.1` : c'est l'adresse du broker à saisir dans le portail.

Diagnostic au moniteur série (`make fw-monitor`, ou minicom à **921600 bauds**) :

| Message de la carte | Cause | Correctif |
|---|---|---|
| `Reason: 211` | point d'accès en WPA1 | le passer en WPA2 (ci-dessus) |
| `Reason: 15 - 4WAY_HANDSHAKE_TIMEOUT` | mot de passe Wi-Fi enregistré dans la carte faux | le ressaisir dans le portail ; le Pi le confirme : `sudo journalctl -u wpa_supplicant \| grep PSK-MISMATCH` |
| `# mqtt : echec (code 5)` | compte MQTT absent du broker, ou mot de passe différent | `make mqtt-user NAME=esp32-01` sur le Pi |
| des octets illisibles à l'appui du bouton | MQTT non connecté : la session part par le câble série | voir les lignes `# wifi` et `# mqtt` au démarrage |

> Dans le portail, les deux champs de mot de passe (Wi-Fi et MQTT) s'affichent
> toujours vides : retapez-les à chaque enregistrement, un champ laissé vide
> efface le mot de passe enregistré.

---

### 4.2 Serveur Python

**Prérequis :** Python 3.10+, Docker (pour le broker) ou Mosquitto natif.

```bash
# 1. Paquets système (mosquitto, ffmpeg, venv) + groupe dialout (port série)
make install-system
# → se déconnecter/reconnecter pour activer le groupe « dialout »

# 2. Broker MQTT (choisir l'une ou l'autre)
make mosquitto-docker          # conteneur Docker (recommandé)
make mosquitto-config          # service Mosquitto Debian natif (déjà installé via make install-system)

# 3. Environnement Python + outils de développement (pytest, pyright, PlatformIO)
make install-dev
# PlatformIO est dans son propre venv (.pio-venv) : ses dépendances entrent
# en conflit avec celles du serveur vocal.

# 4. Transcription + synthèse vocale (~550 Mo)
make install-speech
make models

# 5. Enregistrer un doigt (étape F2), puis lancer l'assistant
make finger-enroll NAME=Denis
make run
```

**Fichiers à configurer :**

| Fichier | Contenu |
|---|---|
| `server/.env` | `MQTT_USERNAME`, `MQTT_PASSWORD` |
| `server/config.yaml` | **votre copie locale** (ignorée par git, créée par `make install` depuis `config.example.yaml`) : réglages généraux (audio, MQTT, voix, journal) et liste des fichiers inclus |
| `server/conf/home.yaml` | pièces et pièce de chaque carte |
| `server/conf/machines.yaml` | machines Linux du réseau et leurs actions |
| `server/conf/access.yaml` | droits par personne (`access`) et propriétaire de chaque doigt (`fingerprint`) |

Pour l'aide sur toutes les cibles : `make help`.

---

### 4.3 Agent Linux (machines commandées par la voix)

Chaque machine Linux à commander (PC du bureau, NAS…) fait tourner un service
léger qui n'exécute **que** les actions listées dans son fichier de
configuration — aucun shell exposé.

**Sur la machine cible :**

```bash
# 1. Copier le répertoire agent/ (depuis le Raspberry Pi)
scp -r /home/rpi5/voice-assistant/agent/ user@pc-bureau:~/voice-agent/

# 2. Installer le service (crée /opt/voice-agent/, /etc/voice-assistant/, unité systemd)
cd ~/voice-agent
sudo make install

# 3. Créer le compte MQTT sur le serveur (si le broker est sécurisé, étape 15)
#    (à exécuter sur le Raspberry Pi, pas sur la machine cible)
make mqtt-user NAME=pc-bureau      # noter le mot de passe
make mosquitto-docker              # ou mosquitto-config — recharge le broker

# 4. Configurer l'agent sur la machine cible
sudo nano /etc/voice-assistant/agent.yaml
#   id: pc-bureau                    ← nom dans les topics MQTT
#   mqtt.host: 192.168.1.200         ← adresse IP du broker
#   mqtt.username: pc-bureau         ← même valeur que id (vide si broker anonyme)
#   mqtt.password: <mot-de-passe>    ← généré à l'étape 3 (vide si broker anonyme)
#   dry_run: false                   ← true pour tester sans rien exécuter

# 4. Activer et démarrer
sudo systemctl enable --now voice-agent
sudo systemctl status voice-agent
```

**Sur le serveur,** ajouter la machine dans `server/conf/machines.yaml` :

```yaml
machines:
  list:
    - id: pc-bureau
      name: "PC du bureau"        # dit à voix haute : « le PC du bureau »
      mac: "AA:BB:CC:DD:EE:FF"    # adresse MAC pour Wake-on-LAN
      ip: 192.168.1.50
```

**Essai sans rien éteindre** (`dry_run: true` dans `agent.yaml`) :

```bash
make run        # serveur (terminal 1)
make agent-run  # agent en mode essai sur cette machine (terminal 2)
# Doigt posé : « éteins le PC du bureau »
# → l'agent affiche : essai : systemctl poweroff (non exécuté)
```

Actions disponibles par défaut : `shutdown`, `reboot`, `lock` ; `wake`
(allumer depuis le réseau) est géré par le serveur via Wake-on-LAN.

---

## 5. Utilisation

```bash
make fw-build                       # compile le firmware
make fw-upload SERIAL_PORT=/dev/ttyUSB0
make record OUT=recordings/essai.wav   # enregistre une session (étape 1)
make play OUT=recordings/essai.wav     # joue le WAV (cherche un lecteur qui marche)
make inspect OUT=recordings/essai.wav  # analyse le WAV sans l'écouter
make play-esp OUT=recordings/essai.wav # joue le WAV sur le haut-parleur de l'ESP32 (étape 2)
make tone-esp TONE=440                 # son pur sur le haut-parleur de l'ESP32 (étape 2)
make run                               # assistant : répond à chaque question (étape 6)
make speech-check TEXT="Quelle heure est-il ?"  # transcription et synthèse, sans ESP32
make say TEXT="Ouvre la porte du salon." VOICE=fr_FR-tom-medium  # WAV + MP3 à jouer au micro depuis un téléphone
make latency                           # temps de transcription de Whisper sur de vrais enregistrements
make finger-enroll NAME=Denis          # enregistre un doigt (deux poses) ; finger-list, finger-delete ID=2
make transcribe OUT=server/recordings/<fichier>.wav
make mqtt-watch                        # messages texte voice/… et home/# (sans l'audio)
make mqtt-user NAME=esp32-01           # compte MQTT d'une carte, ou du serveur (étape 15)
make agent-run                         # agent Linux de ce PC, en mode essai (étape 15b)
make mqtt-agents                       # états, commandes et comptes rendus des agents
make fw-monitor                     # moniteur série
make test                           # tests Python
make lint                           # typage pyright
```

---

## 6. Avancement

L'état exact du projet, étape par étape, est dans **[CONTEXT.md](CONTEXT.md)**.

Étape courante : **terminée — les étapes 1 à 16 sont validées** (branche `main`).
Branche `fingerprint` : F1 à F4b, F5 (ESP32-S3 Mini), F6 (configuration en plusieurs fichiers), F7 (serveur sous systemd) et F8 (modèle de configuration versionné, copie locale ignorée) validées 

### Branche `fingerprint` — étape F8 : un modèle de configuration versionné, une copie locale ignorée

`server/config.example.yaml` est le **modèle** (versionné) ; `server/config.yaml` est **votre copie** (ignorée par git) : le serveur,
les outils et le service lisent celle-là. Plus de risque de commiter votre adresse de broker, ni de la perdre à un
« annuler les modifications ».

```bash
make install        # crée server/config.yaml depuis le modèle s'il manque (comme server/.env), sans jamais l'écraser
make config         # la même chose, seule
make config-diff    # ce qui diffère entre le modèle et votre copie (à relire quand le modèle change)
```

| Point | Détail |
|---|---|
| clone neuf | `make install` crée `server/config.yaml` ; **vérifiez `mqtt.host`** (le modèle vise `10.42.0.1`, le Raspberry Pi) |
| le modèle change | votre copie n'est pas touchée : `make config-diff`, puis reportez à la main les nouvelles clés |
| `server/conf/*.yaml` | restent versionnés (valeurs de départ) ; seul `config.yaml` est local |
| `make run`, service | un `config.yaml` absent donne `configuration invalide` et « `make config` le crée depuis config.example.yaml » |
| tests | ils lisent le modèle : leurs résultats ne dépendent pas de votre copie |

**Autre machine qui a déjà ce dépôt** (le Raspberry Pi, un second PC) : au premier `git pull` qui apporte cette étape, git retire
le `server/config.yaml` suivi jusque-là. Copiez-le avant (`cp server/config.yaml ~/config.yaml.sauvegarde`) puis remettez-le
après ; sans modification locale, `make config` recrée un fichier depuis le modèle.

1. `git pull` n'a rien à faire chez vous : votre `server/config.yaml` reste (même contenu, `192.168.1.4`), et `git status` ne le
   montre plus.
2. `make config` : « existe deja : rien a faire » ; `make config-diff` : montre seulement vos différences (adresse du broker…).
3. Clone neuf (dossier temporaire) : `make config` crée `server/config.yaml` depuis le modèle.

### Branche `fingerprint` — étape F7 : le serveur démarre avec la machine (systemd)

Le serveur devient un service : il démarre au boot (sans ouvrir de session), est relancé s'il plante, et son journal va dans
`journalctl`. Même schéma que l'agent (`make agent-install`) : une unité modèle dans le dépôt, installée avec `sudo`.

```bash
make server-install    # installe et démarre le service (sudo) ; refuse si un « make run » tourne encore
make server-status     # état du service
make server-log        # journal en direct (Ctrl-C pour quitter)
sudo systemctl restart voice-server   # après un changement de config.yaml, conf/*.yaml, .env ou du code
make server-unit       # affiche l'unité qui serait installée, sans rien installer
make server-uninstall  # retire le service (le dépôt et les enregistrements restent)
```

| Point | Détail |
|---|---|
| compte et dossier | le compte de celui qui lance `make server-install`, dans `server/` de ce dépôt : `config.yaml`, `conf/`, `.env`, `models/`, `recordings/` et `logs/` sont ceux de `make run` ; déplacer le dépôt demande de refaire `make server-install` |
| démarrage | au boot, une fois le réseau en ligne ; si le broker est absent, le serveur démarre et se reconnecte seul (1 à 10 s entre deux essais) ; le chargement de Whisper et de Piper prend quelques secondes |
| plantage | relancé après 5 s |
| configuration ou modèles à corriger | **pas relancé** (code de sortie 78) : l'état est `failed` et `make server-log` dit quoi corriger |
| arrêt | `sudo systemctl stop voice-server` : déconnexion propre du broker |
| `make run` | refusé tant que le service tourne : deux serveurs de même identifiant MQTT se chassent du broker en boucle |
| secrets | `.env` reste dans `server/`, lu par le serveur ; rien n'est copié dans l'unité ni dans le journal |

Le service lit `server/config.yaml` **tel qu'il est au démarrage** : vérifiez `mqtt.host` avant d'installer (celui du modèle est
`10.42.0.1`, le Raspberry Pi).

1. Arrêtez le serveur lancé à la main (Ctrl-C dans le terminal de `make run`), puis `make server-install` : `active (running)`.
2. `make server-log` : `doigts : Denis (empreinte 1)`, puis la connexion au broker ; une session au doigt reçoit sa réponse.
3. Plantage simulé : `sudo systemctl kill -s SIGKILL voice-server` ; le journal montre le redémarrage après 5 s.
4. Redémarrez la machine, sans ouvrir de session : `make server-status` dit `active (running)` et la carte reçoit sa réponse.
5. Erreur de configuration : `sudo systemctl stop voice-server`, écrivez `include: conf/absent.yaml` dans `config.yaml`,
   `sudo systemctl start voice-server` : état `failed` (code 78), sans boucle de relance ; remettez la configuration et redémarrez.

### Branche `fingerprint` — étape F6 : la configuration en plusieurs fichiers

`server/config.yaml` garde les réglages généraux et **inclut** les sections qui grossissent ou qui sont personnelles :

```yaml
include:
    - conf/home.yaml          # home : pièces et cartes
    - conf/machines.yaml      # machines : machines Linux et leurs actions
    - conf/access.yaml        # access : droits ; fingerprint : propriétaire de chaque doigt
```

| Règle | Détail |
|---|---|
| chemins | relatifs au fichier qui inclut ; un fichier inclus peut en inclure d'autres (8 niveaux au plus, boucle refusée) |
| motifs | `conf/machines/*.yaml` (un fichier par machine), `conf/**/*.yaml` : triés par nom ; un motif sans fichier donne un avertissement, un chemin sans motif doit exister |
| fusion | les **mappings s'additionnent** (`home.rooms` peut venir de plusieurs fichiers) ; une même valeur (texte, nombre, liste) définie deux fois est une **erreur qui nomme les deux fichiers**, jamais un écrasement silencieux ; les listes ne se concatènent pas |
| `mqtt` | reste dans `config.yaml` : le Makefile y lit `mqtt.host` |

Exemple, une pièce par fichier : `include: 'conf/pieces/*.yaml'`, avec dans `conf/pieces/garage.yaml` :

```yaml
home:
    rooms:
        garage: du garage
```

Un `config.yaml` sans `include` fonctionne comme avant. Les outils (`make finger-*`, `say`, `speech-check`…) passent par le même
chargeur : ils lisent les fichiers inclus sans rien changer. La configuration lue est **identique** à celle d'avant
le découpage (vérifié valeur par valeur) : les sections ont seulement changé de fichier.

1. `make test` : 300 tests (28 pour l'inclusion).
2. Arrêtez puis relancez `make run` : le journal doit dire `doigts : Denis (empreinte 1)` comme avant, et la LED, les droits et
   les machines se comportent comme avant.
3. Cassez une règle pour voir le message : définissez deux fois `access.default` (dans `config.yaml` et dans `conf/access.yaml`) ;
   le serveur refuse de démarrer avec `« access.default » est défini à la fois dans …`. Rétablissez.

### Branche `fingerprint` — étape F5 : la carte ESP32-S3 Mini

Le même firmware tourne sur deux cartes : l'**ESP32 WROOM** (environnement `esp32dev`, par défaut) et l'**ESP32-S3 Mini**
(18 broches, USB-C, flash 4 Mo, environnement `esp32s3mini`). Seuls changent le brochage
([firmware/include/pins_esp32_s3_mini.h](firmware/include/pins_esp32_s3_mini.h), câblage au §1.5 de
[docs/BROCHAGE.md](docs/BROCHAGE.md)) et le moniteur série : l'USB-C parle directement à la puce, sans puce USB-série.

```bash
make fw-check  FW_ENV=esp32s3mini                              # compile
make fw-upload FW_ENV=esp32s3mini SERIAL_PORT=/dev/ttyACM0     # téléverse
make fw-monitor SERIAL_PORT=/dev/ttyACM0                       # moniteur (le « débit » n'a pas de sens en USB natif)
```

Sans `FW_ENV`, toutes les cibles `fw-*` visent la WROOM, comme avant. La carte affiche `# carte : ESP32-S3 Mini (esp32s3mini)`
au démarrage : si vous lisez `ESP32 WROOM`, le mauvais environnement a été téléversé.

Câbler et essayer **par étapes**, carte débranchée de l'USB pour chaque ajout :

1. Carte seule : `# carte : …`, puis l'auto-test du micro échoue (pas de micro) : c'est normal.
2. Capteur R503 sur GPIO 1, 2, 4 : `poignee de main : ok`, `200 empreintes`, et l'anneau suit la pose du doigt.
3. Micro INMP441 (GPIO 5, 6, 7) : l'auto-test passe, `micro pret`.
4. Ampli MAX98357A (GPIO 13, 12, 11) : le bip de démarrage de 440 Hz.
5. Bouton (GPIO 10), puis `make run` : une session par le bouton, puis par un doigt enregistré (F3).

Une carte neuve n'a **aucune configuration réseau** : elle essaie `secrets.h` (s'il existe), sinon il faut le portail. Tenez PTT
enfoncé environ 10 s à la mise sous tension (3 s d'attente de la carte, puis 3 s de maintien) : la carte affiche `portail force`,
la LED d'état clignote vite, et le Wi-Fi `VoiceAssist-Config` donne accès à http://192.168.4.1. Recopiez le nom du réseau **à
l'identique** (la carte ne parle qu'en **2,4 GHz**) : un nom faux donne `NO_AP_FOUND` en boucle.

L'identifiant de la carte reste `esp32-01` pour les deux cartes : n'en branchez qu'**une** à la fois sur le broker (deux
cartes de même identifiant s'éjectent l'une l'autre). Les empreintes sont gardées **dans le capteur** : un R503 déplacé d'une
carte à l'autre garde ses doigts.

| Constat | Cause probable |
|---|---|
| `/dev/ttyUSB0` absent | l'USB natif s'appelle `/dev/ttyACM0` (groupe `dialout`) |
| le téléversement ne démarre pas | maintenir BOOT, appuyer sur RESET, relâcher RESET puis BOOT, relancer |
| aucune ligne au démarrage | moniteur ouvert trop tard : le port disparaît à chaque reset ; la carte attend 3 s, puis jette les messages sans terminal |
| S3 « hors ligne » tant qu'aucun terminal n'est ouvert | firmware d'avant le 2026-10-20 : `Serial.setTxTimeoutMs(0)` figeait le démarrage ; reflasher |
| `ABSENT` pour le capteur | fils jaune et marron sur GPIO 1 et 2, croisés ; 3,3 V |
| carte qui ne démarre plus | un fil sur GPIO 0, 3, 45 ou 46 (broches de démarrage) |
| son déformé ou bruit | le cadrage Philips (`AMP_FORMAT_PHILIPS`) est vérifié sur S3 (bip propre) ; sinon l'essayer à `false` dans `config.h` |
| redémarrages en boucle, `invalid header: 0xffffffff` | flash vierge (carte neuve) : téléverser le firmware, c'est normal avant |
| `NO_AP_FOUND` en boucle | nom du réseau mal saisi dans le portail, ou réseau en 5 GHz seulement |
| `chunks perdus faute de place` au journal du serveur, mots mal compris | signal Wi-Fi trop faible (mesuré : −87 dBm = 147 chunks perdus, −73 dBm = aucun) : rapprocher la carte du point d'accès |
| la carte redémarre à l'ouverture du port série (outil maison) | DTR/RTS abaissés à l'ouverture : laisser les valeurs par défaut |

### Branche `fingerprint` — étape F4 : les droits suivent le doigt

La personne est celle **dont le doigt a ouvert la session** (`fingerprint.users` de `conf/access.yaml`) ; la voix n'identifie
plus personne, et la VAD, la diarisation et l'identification vocale ne tournent plus (le serveur démarre sans pyannote
ni torch). Les droits restent ceux de l'étape 14, par nom (`access.users`), sans tenir compte de la casse ni des accents.

| Qui tient la carte | Personne | Niveau |
|---|---|---|
| doigt nommé dans `fingerprint.users`, présent dans `access.users` | son nom | celui de `access.users` |
| doigt nommé, absent de `access.users` | son nom | `access.default` (standard) |
| bouton PTT, ou doigt reconnu mais sans nom | inconnue | `access.unknown` (limite) |

Un doigt **inconnu du capteur** n'arrive jamais au serveur (F3). Une action sensible (`access.confirm`) demande « oui »,
dit avec le **même doigt** que la demande : un autre doigt de la même personne, le doigt d'une autre personne ou le bouton
annulent la demande. Au démarrage, le serveur signale les noms qui ne se correspondent pas (`droits : …`).

Sans VAD, une session **silencieuse** (niveau efficace sous `stt.min_rms_dbfs`, −58 dBFS) n'est pas transcrite : Whisper
y inventerait du texte. Mesuré sur 180 sessions : bruit de fond −63 dBFS, parole la plus faible −51 dBFS.

1. Arrêtez puis relancez `make run` (un serveur lancé avant F4 applique encore les droits par la voix). Le journal doit
   dire `doigts : Denis (empreinte 1)` et aucune ligne `droits : …`.
2. Doigt de Denis : « allume la lumière » → exécuté ; « ouvre la porte » → « Confirmez-vous… », reposez le **même** doigt : « oui ».
3. Un doigt d'une autre personne : `make finger-enroll NAME=Jessica`, ajoutez la ligne affichée dans `conf/access.yaml`, relancez.
   Jessica : « allume la lumière » → exécuté ; « ouvre la porte » → « Désolé Jessica, votre profil ne permet pas d'ouvrir… ».
4. Denis demande d'ouvrir la porte, puis le doigt de Jessica dit « oui » → « Seul Denis peut confirmer. J'annule. » ;
   un autre doigt de Denis (`make finger-enroll NAME=Denis`) → « Confirmez avec le même doigt que pour la demande. »
5. Bouton PTT : « allume la lumière » → « Je ne sais pas qui tient la carte… ».
6. Doigt tenu sans parler → « Je n'ai rien entendu. », et `silencieuse (RMS …)` dans le journal.

### Branche `fingerprint` — étape F3 : le doigt ouvre la session

Plus de mot de réveil : on **pose un doigt enregistré sur le capteur, on parle, on le lève**. L'anneau dit où
l'on en est ; le bouton PTT reste utilisable (session sans identité).

| Anneau | Sens |
|---|---|
| violet fixe | doigt vu, identification en cours (quelques centaines de ms) : parlez déjà, le son est gardé |
| bleu fixe | doigt reconnu, la carte vous écoute |
| éteint | doigt levé, la session est partie au serveur |
| 3 éclairs rouges | doigt refusé : inconnu du capteur, image inutilisable ou capteur en défaut |

1. `make fw-upload`, puis `make run` sur le PC (`wake.enabled: false` dans `config.yaml`, déjà fait sur cette branche).
2. Posez un doigt enregistré, parlez, levez-le. Côté carte : `doigt reconnu, emplacement 2 (score …)`, puis
   `session … terminee (doigt retire)`. Côté serveur : `session … ouverte par Denis (empreinte 2)`.
3. Posez un doigt inconnu : éclairs rouges, `fp_denied` dans le journal du serveur. Il faut le lever avant un nouvel essai.

Un doigt déjà posé au démarrage, ou posé pendant une session du bouton, ne déclenche rien tant qu'il n'a pas quitté
le capteur. Les durées (effleurement 40 ms, retrait 150 ms, identification 3 s) sont dans `firmware/include/config.h`
(`FP_*`). Les droits suivent le doigt depuis l'étape F4 (ci-dessous).

### Branche `fingerprint` — étape F2 : enregistrer les doigts

Le capteur garde les empreintes (200 emplacements) ; `conf/access.yaml` dit à qui elles appartiennent.
Le serveur vocal n'a pas besoin de tourner : l'outil parle directement à la carte par MQTT.

```bash
make finger-enroll NAME=Denis          # deux poses du MÊME doigt ; l'anneau guide, le terminal aussi
make finger-enroll NAME=Denis ID=2     # un deuxième doigt de la même personne
make finger-list                       # empreintes du capteur et propriétaire
make finger-delete ID=2                # ou ALL=1 pour tout effacer
make finger-list MQTT_HOST=192.168.1.4 # broker différent de celui de config.yaml
```

L'enregistrement affiche la ligne à copier dans `server/conf/access.yaml` :

```yaml
fingerprint:
    users: {1: Denis, 2: Denis, 3: Jessica}   # emplacement dans le capteur → nom
```

Les droits restent par nom dans `access.users`. Anneau : bleu qui respire = posez le doigt ; violet = pose
prise, retirez ; éclairs bleus = enregistré ; éclairs rouges = échec. L'emplacement 0 n'est jamais choisi
automatiquement. Un doigt déjà enregistré est refusé (avec son numéro) ; deux poses de doigts différents aussi.

### Branche `fingerprint` — étape F1 : le capteur d'empreinte R503 seul

Variante simplifiée (sans VAD, diarisation ni identification vocale) : un capteur d'empreinte dit **qui**
tient le boîtier. Câblage : [docs/BROCHAGE.md](docs/BROCHAGE.md) §1.3b (GPIO 16, 17 et 34).

1. `make fw-upload`, puis `make fw-monitor` (ou minicom à 921600 bauds), carte redémarrée.
2. Lignes attendues : `# empreinte : poignee de main : ok`, la capacité (200 pour un R503) et
   `0 empreinte(s) enregistree(s)`, puis l'anneau passe par le rouge, le bleu et le violet.
3. Posez un doigt : `# empreinte : doigt POSE`, anneau violet ; retirez-le : `doigt retire`. (Depuis F3, un doigt enregistré ouvre aussi une session : voir plus haut.)

| Message | Cause probable |
|---|---|
| `ABSENT (pas de reponse …)` | fils RX/TX inversés (les croiser), 3,3 V absent, mauvais débit |
| `lue comme doigt POSE` sans doigt, ou « POSE » et « retiré » inversés | polarité du signal de touche : `FP_TOUCH_ACTIVE_LOW` dans `config.h` (`true` pour votre capteur : haut au repos, bas quand le doigt est posé) ; sinon fil bleu mal branché |
| poignée de main ok mais pas de `doigt POSE` | fil bleu (WAKEUP) non branché, ou fil blanc (3,3VT) sans 3,3 V |
| `mot de passe refuse` | le capteur a un mot de passe : `FP_PASSWORD` dans `config.h` |

### Étape 16 — optimisation

> Sur la branche `fingerprint` (F4), la diarisation et l'empreinte vocale ont disparu : restent le cache de synthèse, les envois
> sans attente et `make latency`, qui ne mesure plus que Whisper. Les mesures ci-dessous datent de la branche `main`.

Mesurer d'abord, optimiser ensuite : `make latency` rejoue les derniers enregistrements
avec l'ancienne puis la nouvelle chaîne. Sur 54 commandes réelles (PC, 20 cœurs logiques) :
**1,6 s → 1,1 s** entre la fin de la phrase et le texte reconnu, même texte 54 fois sur 54.

| Optimisation | Gain mesuré | Réglage |
|---|---|---|
| Parole courte : empreinte vocale directe, sans diarisation | 0,40 s (0,42 → 0,02 s) | `diarization.direct_below_s: 4.0` |
| Parole courte : Whisper sans horodatage des mots | 0,10 s | automatique |
| Réponses déjà dites gardées en mémoire (cache de synthèse) | 0,03 à 0,14 s | automatique, 64 phrases |
| Fin de commande cherchée toutes les 100 ms (250 avant) | 0,075 s en moyenne | — |
| Envois réseau immédiats (`TCP_NODELAY` : serveur, broker) | à mesurer : `premier son a … ms` | `set_tcp_nodelay true` dans `voice.conf` |

Pistes mesurées puis **écartées** : diarisation et Whisper en parallèle (0,03 à 0,10 s seulement :
les deux calculs saturent le processeur, même dans deux processus), `beam_size: 1` (0,05 s, et
un texte différent sur 16), 16 fils pour Whisper (plus lent que 4).

1. Sur le Raspberry Pi (broker) : `git pull`, puis `make mosquitto-config`.
2. `make run`, quelques commandes : le journal affiche `1 locuteur (parole courte, empreinte
   directe 20 ms)` et `reponse prete en ~1,1 s` (1,5 s avant).
3. `make latency` : avant, après, gain, et les identifications qui diffèrent.

Une parole de moins de 4 s est supposée venir d'**une seule personne**. Deux voix dans une
phrase courte ne sont plus séparées (elles l'étaient à l'étape 10) ; au-delà de 4 s, rien ne change.

### Étape 15b — machines Linux du réseau

« Éteins le PC du bureau » : chaque machine fait tourner un **agent**
([agent/voice_agent.py](agent/voice_agent.py)), petit client MQTT qui n'exécute que
les actions de **sa** liste (`/etc/voice-assistant/agent.yaml`), commandes fixes, sans
shell. Le serveur n'envoie qu'un nom d'action. « Allume » réveille une machine
éteinte par **Wake-on-LAN** (paquet réseau reçu par la carte réseau en veille).

| Action | Mots | Droits (conf/access.yaml) |
|---|---|---|
| `wake` (serveur) | allume, démarre, réveille | standard |
| `lock` | verrouille | standard |
| `shutdown` | éteins, arrête | complet + confirmation |
| `reboot` | redémarre, relance | complet + confirmation |

1. Essai sur ce PC, sans rien éteindre : `make agent-run` (crée `agent/agent.yaml` en mode essai).
2. `make run`, puis doigt posé : « éteins le PC du bureau » → confirmation → même doigt : « oui » :
   l'agent affiche `essai : systemctl poweroff (non execute)`.
3. Machine réelle : copier `agent/` dessus, `make -C agent install`, régler
   `/etc/voice-assistant/agent.yaml`, et la nommer dans `server/conf/machines.yaml` (`machines.list`).

**Broker anonyme** : tout appareil du réseau peut alors publier sur `agent/<machine>/command`.
En production, les comptes et droits de l'étape 15 (`mosquitto/acl` contient les règles des agents).

### Étape 15 — fiabilisation

| Quoi | Comment |
|---|---|
| Comptes MQTT et droits | broker sans accès anonyme ; une carte ne lit et n'écrit que **ses** topics ([docs/MOSQUITTO.md](docs/MOSQUITTO.md) §2) |
| Présence du serveur | `voice/server/status` : `online` retenu, testament `offline` ; serveur absent = signalé dans le journal de la carte |
| Chien de garde (*watchdog*) | carte figée plus de 15 s → redémarrage, cause `watchdog` dans l'événement `boot` |
| Réseau | connexion TCP au broker en 1 s au plus ; Wi-Fi relancé après 30 s d'absence |
| Journal | copie dans `server/logs/voice-server.log` (fichiers tournants, 4 Mo au plus) |

Mise en place, **dans cet ordre** : un broker encore ouvert accepte déjà les clients qui ont un compte.

1. `make mqtt-user NAME=voice-server`, puis `make mqtt-user NAME=esp32-01` (mots de passe : `openssl rand -hex 16`).
2. `server/.env` : `MQTT_USERNAME=voice-server`, `MQTT_PASSWORD=...` ; `secrets.h` : `MQTT_PASSWORD="..."`.
3. `make fw-upload` et `make run` : tout marche comme avant, le serveur annonce sa présence.
4. `make mosquitto-docker` : le broker exige désormais les comptes.

### Étape 14 — droits par profil

> Sur la branche `fingerprint`, le « profil » est la personne du **doigt** (étape F4) : voir ci-dessus. Plus de score de voix,
> ni de `full_min_score`.

| Niveau | Droits | Attribué à |
|---|---|---|
| **complet** | tout ; actions sensibles (ouvrir la porte) après **confirmation** « oui » du même doigt | personnes listées dans `access.users` |
| **standard** | lumières, volets, fermer la porte | personne nommée par un doigt, sans niveau (`access.default`) |
| **limite** | conversation seulement | bouton PTT, ou doigt sans nom (`access.unknown`) |

Règles par appareil et action dans `conf/access.yaml` (section `access`) ; une action absente des
règles exige le niveau complet. Les noms de `access.users` sont ceux de `fingerprint.users`.

**Rappel** : un doigt n'est pas une preuve absolue (empreinte copiée, doigt posé par un tiers pendant que quelqu'un
d'autre parle). Ces droits évitent les erreurs et les commandes d'un invité, pas une attaque déterminée.

### Étape 13 — mot de réveil (mains libres)

> **Retirée sur la branche `fingerprint`** (étape F4b) : le doigt remplace le mot de réveil et l'écoute continue. Le code et son mode d'emploi sont dans la branche `main`.

### Étape 12 — tampon de sortie anti-gigue

La carte ne joue rien tant qu'elle n'a pas **300 ms de son en réserve** (amorçage) ; le
Wi-Fi peut alors livrer les chunks en retard ou en rafale sans que cela s'entende. Si la
réserve s'épuise, la lecture s'arrête proprement et reprend une fois la réserve refaite
(réamorçage), au lieu de hacher le son. Le bilan `played` donne le délai avant le premier
son, la **marge minimale** (retard encore absorbable) et le nombre de réamorçages.

1. `make fw-upload SERIAL_PORT=/dev/ttyUSB0` (firmware de l'étape 12).
2. `make tone-mqtt` : attendu `reamorcages 0`, marge mini ≈ 250 ms.
3. `make tone-mqtt JITTER=200` : un blocage de 200 ms toutes les 500 ms ; son toujours
   continu, `reamorcages 0`, marge mini ≈ 100 ms.
4. `make tone-mqtt JITTER=400` : au-delà de la réserve ; pauses franches (réamorçages > 0),
   jamais de grésillement.
5. `make run` : les réponses de l'assistant, avec `reamorcages 0` au signal habituel.

### Étape 11 — domotique

Une commande tient en trois mots : une **action** (allumer, éteindre, ouvrir, fermer), un
**appareil** (lumière, volets, porte) et une **pièce** (liste dans `conf/home.yaml`, section `home`).
Sans pièce, c'est celle de la carte. Le serveur publie `home/<pièce>/<appareil>/set` pour
toute domotique ; si la pièce est celle de la carte, la carte exécute aussi la commande :
LED de démonstration sur GPIO 21 pour la lumière, volets et porte simulés (liaison série).

1. Câbler la LED (facultatif) : GPIO 21 → 330 Ω → LED → GND ([docs/BROCHAGE.md](docs/BROCHAGE.md) §10).
2. `make fw-upload SERIAL_PORT=/dev/ttyUSB0` (firmware de l'étape 11), puis `make run`.
3. Terminal à part : `make mqtt-watch` (voice/… en texte et home/#).
4. « Allume la lumière » → « J'allume la lumière du salon. », LED allumée,
   `home/salon/light/set` et la confirmation `{"event":"device",...,"ok":true}`.
5. « Ferme les volets de la cuisine » → `home/cuisine/shutter/set` seulement (autre pièce).
6. « Allume les volets » → « Je ne sais pas allumer les volets. » ; « Ouvre » → « Que dois-je ouvrir ? ».

**Sécurité** : l'identification vocale n'est jamais une preuve. Ne branchez pas une gâche
de porte ou une alarme sur ces commandes ; les droits par profil arrivent à l'étape 14.

### Étape 10 — qui a dit quoi

> **Retirée sur la branche `fingerprint`** (étape F4b) : une session n'a plus qu'une personne, celle du doigt (le message `transcript` garde une intervention). Le code et son mode d'emploi sont dans la branche `main`.

### Étape 9 — identification du locuteur

> **Retirée sur la branche `fingerprint`** (étape F4b) : le doigt identifie la personne, plus la voix. Le code et son mode d'emploi sont dans la branche `main`.

### Étape 8 — diarisation : qui parle quand

> **Retirée sur la branche `fingerprint`** (étape F4b) : personne n'est à départager : la session est celle d'un seul doigt. Le code et son mode d'emploi sont dans la branche `main`.

### Étape 7 — détection d'activité vocale (VAD)

> **Retirée sur la branche `fingerprint`** (étape F4b) : le doigt borne la session ; un silence complet est écarté par `stt.min_rms_dbfs`. Le code et son mode d'emploi sont dans la branche `main`.

### Étape 6 — MVP : l'assistant répond

1. `make install-speech` puis `make models` (faster-whisper `small`, voix Piper
   `fr_FR-siwis-medium`, environ 550 Mo dans `server/models/`, une seule fois).
2. `make speech-check` : Piper dit « Quelle heure est-il ? », Whisper le relit,
   l'assistant répond ; aucun matériel nécessaire.
3. `make transcribe OUT=server/recordings/<fichier>.wav` : transcrit une phrase
   enregistrée par l'ESP32 à l'étape 4.
4. `make run` : appui sur le bouton, « Quelle heure est-il ? », relâcher ; la
   carte répond. Questions reconnues : l'heure, la date, bonjour, merci, ton nom ;
   sinon, la carte répète ce qu'elle a compris.

Le journal donne le temps de chaque maillon : `transcription 1.0 s, synthese
0.1 s, reponse prete en 1.1 s`. `make run-echo` garde le mode écho de l'étape 5,
sans modèles.

### Étape 5 — le serveur fait parler l'ESP32, par MQTT

1. `make fw-upload SERIAL_PORT=/dev/ttyUSB0` (firmware de l'étape 5).
2. `make tone-mqtt` : 3 s de son pur envoyées par MQTT ; le bilan de la carte
   s'affiche (`chunks 60/60, underruns 0`).
3. `make play-mqtt OUT=server/recordings/<fichier>.wav` : n'importe quel WAV,
   converti en 16 kHz mono, crête à −3 dBFS.
4. `make run-echo` : le serveur rejoue chaque phrase à la carte qui l'a dite.

### Étape 4 — la voix vers le serveur, par MQTT

1. `make install` (dépendances du serveur : paho-mqtt, PyYAML, numpy...).
2. `make run` : le serveur se connecte au broker et attend les sessions.
3. `make fw-upload SERIAL_PORT=/dev/ttyUSB0`, puis appui sur le bouton, une phrase, relâcher.
4. Le serveur écrit `server/recordings/esp32-01_<date>_<session>.wav` et affiche
   son bilan : `complete`, chunks reçus / annoncés, crête et RMS.
5. `make play OUT=server/recordings/<fichier>.wav` ou `make inspect OUT=...`.

Si le broker est injoignable à l'appui, la session part sur la liaison série :
`make record` reste utilisable pour le dépannage.

### Étape 3 — Wi-Fi et MQTT

1. Broker : voir [docs/MOSQUITTO.md](docs/MOSQUITTO.md) (service Debian ou conteneur).
2. `cp firmware/include/secrets.h.example firmware/include/secrets.h`, puis
   renseigner le Wi-Fi (2,4 GHz) et l'adresse IP du broker.
3. `make mqtt-watch MQTT_HOST=<ip du broker>` dans un premier terminal.
4. `make fw-upload SERIAL_PORT=/dev/ttyUSB0` dans un second.

Attendu dans le premier terminal : `voice/esp32-01/state {"status":"online",...}`
et `voice/esp32-01/event {"event":"boot",...}`. Après une coupure d'alimentation
de l'ESP32 : `voice/esp32-01/state {"status":"offline"}` en 15 s au plus.

### Étape 2 — tester la sortie audio

1. Câbler l'ampli MAX98357A et le haut-parleur (§2), sans oublier C1 et C2.
2. `make fw-upload SERIAL_PORT=/dev/ttyUSB0`
3. Au démarrage, un **bip de 440 Hz** retentit : il teste la sortie seule.
4. `make tone-esp` : 3 s de son pur envoyées par le PC, au rythme réel. La
   moindre coupure s'y entend comme un clic.
5. `make play-esp OUT=recordings/essai.wav` : l'enregistrement de l'étape 1
   rejoué sur le haut-parleur de l'ESP32.

Attendu : son pur, sans clic ni grésillement ; le bilan de l'ESP32 affiche
`0 refuses` et `0 sous-alimentations`.

### Étape 1 — tester l'entrée audio

1. Câbler le micro, le bouton et la LED comme au §2.
2. `make fw-upload SERIAL_PORT=/dev/ttyUSB0`
3. `make record OUT=recordings/essai.wav`
4. Appuyer sur le bouton, dire une phrase, relâcher.
5. Écouter : `make play OUT=recordings/essai.wav`
6. Sans sortie audio : `make inspect OUT=recordings/essai.wav` dessine l'enveloppe
   du signal en ASCII et donne crête, RMS et offset continu.

Attendu : la phrase est intelligible, sans saturation ; le script affiche une
crête entre 10 % et 90 % de la pleine échelle. Si la crête dépasse 99 %,
diminuer `MIC_GAIN` dans [firmware/include/config.h](firmware/include/config.h) ;
si elle reste sous 2 %, l'augmenter.

---

## 7. Arborescence

```
voice-assistant/
├── firmware/            firmware ESP32 (PlatformIO, Arduino)
│   ├── include/         config.h, secrets.h.example
│   └── src/             main.cpp + une paire .h/.cpp par classe
├── server/              serveur Python (un module par classe)
├── tools/               outils de test hors serveur
├── mosquitto/           voice.conf
├── Makefile             toutes les opérations système
├── CONTEXT.md           état d'avancement et décisions techniques
└── README.md / README-EN.md
```

---

## 8. Licence

MIT — voir [LICENSE](LICENSE).
