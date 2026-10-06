# Assistant vocal local — ESP32 + Raspberry Pi

Assistant vocal **100 % local**, sans aucun service cloud : un ESP32 capture la
voix, l'envoie en PCM brut par MQTT à un serveur Python qui transcrit, interprète
et répond en synthèse vocale ; l'ESP32 joue la réponse et exécute la commande.

*English version: [README-EN.md](README-EN.md).*

---

## 1. Architecture

```
INMP441 -> ESP32 -> MQTT -> Serveur Python (Raspberry Pi / PC)
                                  |
                        +---------+---------+
                        |                   |
                       VAD          Diarisation /
                        |           identification
                        +---------+---------+
                                  |
                           faster-whisper
                                  |
                              Assistant
                                  |
                                Piper
                                  |
                             MQTT audio/out
                                  |
                        ESP32 -> MAX98357A -> haut-parleur
```

Quatre traitements souvent confondus, à ne jamais mélanger :

| Bloc | Question |
|---|---|
| VAD | Est-ce que quelqu'un parle ? |
| Diarisation | Qui parle à quel moment ? (SPEAKER_00, SPEAKER_01…) |
| Identification | Qui est cette personne ? (Denis, Marie, Inconnu) |
| STT | Qu'est-ce qui est dit ? |

> L'identification vocale est **probabiliste**. Ce n'est jamais un mécanisme
> d'authentification ni de sécurité.

---

## 2. Matériel

| Élément | Détail |
|---|---|
| ESP32-WROOM-32 | deux périphériques I2S : I2S0 en réception, I2S1 en émission |
| INMP441 | micro I2S, **3,3 V**, broche L/R à GND (canal gauche) |
| MAX98357A | ampli classe D mono, **5 V**, SD à VIN, GAIN laissé libre (9 dB) |
| Bouton | Push-To-Talk entre GPIO 4 et GND (pull-up interne) |
| LED | + résistance 330 Ω sur GPIO 2 |
| Serveur | Raspberry Pi 4/5 64 bits ou PC Debian |

### Câblage

**Schéma complet : [docs/wiring.svg](docs/wiring.svg)** — brochage détaillé,
bilan de consommation et vérifications au multimètre : **[docs/BROCHAGE.md](docs/BROCHAGE.md)**.

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

### 4.1 Firmware ESP32

**Prérequis :** câble USB avec les données (pas charge seule), port identifié
(`ls /dev/ttyUSB*` ou `/dev/ttyACM*` après branchement).

```bash
make pio-venv          # PlatformIO dans son propre venv — une seule fois
make fw-build          # compile sans flasher
make fw-upload SERIAL_PORT=/dev/ttyUSB0
```

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

# 5. Diarisation + identification vocale (~1,3 Go) — optionnel, lourd sur RPi 4
#    Prérequis : compte Hugging Face + accepter les conditions sur
#    https://huggingface.co/pyannote/speaker-diarization-community-1
#    Créer un jeton « Read » sur https://huggingface.co/settings/tokens
cp server/.env.example server/.env    # puis HF_TOKEN=hf_…
make install-diarization
make models

# 6. Mot de réveil openWakeWord (~5 Mo) — optionnel
make install-wakeword
make models

# 7. Lancer l'assistant
make run
```

**Fichiers à configurer :**

| Fichier | Contenu |
|---|---|
| `server/.env` | `MQTT_USERNAME`, `MQTT_PASSWORD`, `HF_TOKEN` |
| `server/config.yaml` | pièces, voix, droits d'accès, liste des machines |

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

**Sur le serveur,** ajouter la machine dans `server/config.yaml` :

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
# Dire : « Hey Mycroft, éteins le PC du bureau »
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
make say WAKE="Hey Mycroft" TEXT="Ouvre la porte du salon." VOICE=fr_FR-tom-medium  # WAV + MP3 pour un téléphone
make transcribe OUT=server/recordings/<fichier>.wav
make vad OUT=server/recordings/<fichier>.wav   # parole trouvée par la VAD (étape 7)
make vad-all                           # VAD sur tous les enregistrements du serveur
make diarize OUT=server/recordings/<fichier>.wav  # qui parle quand, qui, qui a dit quoi (étapes 8-10)
make diarize-demo                      # deux voix : la vôtre et celle de Piper
make enroll NAME=Denis                 # profil vocal depuis les 5 dernières sessions (étape 9)
make profiles                          # liste des profils ; make forget NAME=Denis pour en supprimer un
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

Étape courante : **15b — machines Linux du réseau**. Les étapes 1 à 15 sont validées.

### Étape 15b — machines Linux du réseau

« Hey Mycroft, éteins le PC du bureau » : chaque machine fait tourner un **agent**
([agent/voice_agent.py](agent/voice_agent.py)), petit client MQTT qui n'exécute que
les actions de **sa** liste (`/etc/voice-assistant/agent.yaml`), commandes fixes, sans
shell. Le serveur n'envoie qu'un nom d'action. « Allume » réveille une machine
éteinte par **Wake-on-LAN** (paquet réseau reçu par la carte réseau en veille).

| Action | Mots | Droits (config.yaml) |
|---|---|---|
| `wake` (serveur) | allume, démarre, réveille | standard |
| `lock` | verrouille | standard |
| `shutdown` | éteins, arrête | complet + confirmation |
| `reboot` | redémarre, relance | complet + confirmation |

1. Essai sur ce PC, sans rien éteindre : `make agent-run` (crée `agent/agent.yaml` en mode essai).
2. `make run`, puis « Hey Mycroft, éteins le PC du bureau » → confirmation → « oui » :
   l'agent affiche `essai : systemctl poweroff (non execute)`.
3. Machine réelle : copier `agent/` dessus, `make -C agent install`, régler
   `/etc/voice-assistant/agent.yaml`, et la nommer dans `server/config.yaml` (`machines.list`).

**Broker anonyme** : tout appareil du réseau peut alors publier sur `agent/<machine>/command`.
En production, les comptes et droits de l'étape 15 (`mosquitto/acl` contient les règles des agents).

### Étape 15 — fiabilisation

| Quoi | Comment |
|---|---|
| Comptes MQTT et droits | broker sans accès anonyme ; une carte ne lit et n'écrit que **ses** topics ([docs/MOSQUITTO.md](docs/MOSQUITTO.md) §2) |
| Présence du serveur | `voice/server/status` : `online` retenu, testament `offline` ; serveur absent = micros des cartes coupés |
| Chien de garde (*watchdog*) | carte figée plus de 15 s → redémarrage, cause `watchdog` dans l'événement `boot` |
| Réseau | connexion TCP au broker en 1 s au plus ; Wi-Fi relancé après 30 s d'absence |
| Journal | copie dans `server/logs/voice-server.log` (fichiers tournants, 4 Mo au plus) |

Mise en place, **dans cet ordre** : un broker encore ouvert accepte déjà les clients qui ont un compte.

1. `make mqtt-user NAME=voice-server`, puis `make mqtt-user NAME=esp32-01` (mots de passe : `openssl rand -hex 16`).
2. `server/.env` : `MQTT_USERNAME=voice-server`, `MQTT_PASSWORD=...` ; `secrets.h` : `MQTT_PASSWORD="..."`.
3. `make fw-upload` et `make run` : tout marche comme avant, le serveur annonce sa présence.
4. `make mosquitto-docker` : le broker exige désormais les comptes.

### Étape 14 — droits par profil

| Niveau | Droits | Attribué à |
|---|---|---|
| **complet** | tout ; actions sensibles (ouvrir la porte) après **confirmation** « oui » | profils listés dans `access.users`, **si la voix est reconnue nettement** (score ≥ 0,6) |
| **standard** | lumières, volets, fermer la porte | profil enrôlé sans niveau (`access.default`) |
| **limite** | conversation seulement | voix inconnue (`access.unknown`) |

Règles par appareil et action dans `config.yaml` (section `access`) ; une action absente des
règles exige le niveau complet. Les noms de `access.users` sont ceux de `make profiles`.

1. `make run` (serveur seul, firmware inchangé).
2. « Hey Mycroft, allume la lumière » → exécuté (Denis, complet).
3. « Hey Mycroft, ouvre la porte » → « Confirmez-vous : ouvrir la porte du salon ? » →
   « Hey Mycroft, oui » → « J'ouvre la porte du salon. » ; « non » → « D'accord, j'annule. »
4. Une voix du téléphone (profil standard) : « ouvre la porte » → refus ; une voix non
   enrôlée : « allume la lumière » → « Je ne vous ai pas reconnu… ». Phrases prêtes :
   `recordings/voix-test/tom-porte.mp3`, `pierre-lumiere.mp3` ; d'autres avec `make say`. Le mot de réveil y est dit par une
   voix anglaise (`WAKE`) : prononcé à la française par une voix de synthèse, il n'est pas reconnu.

**Rappel** : la voix n'est jamais une preuve d'identité. Ces droits évitent les erreurs et les
commandes d'un invité, pas une attaque (enregistrement, imitation).

### Étape 13 — mot de réveil (mains libres)

Le serveur demande à la carte d'envoyer son micro en continu (`voice/<carte>/audio/stream`)
et guette le mot de réveil avec openWakeWord : **« Alexa »**, **« Hé Maïcrofte »** (Hey
Mycroft) ou **« Hey Djâr-viss »** (Hey Jarvis), prononcés à l'anglaise. Une fois le mot
entendu, il garde la commande jusqu'au silence final, puis la traite comme une session du
bouton. Le bouton reste utilisable à tout moment.

LED : **éclair bref toutes les 2 s** = écoute continue ; **allumée** = mot entendu, commande
en cours ; clignotement lent = prêt, sans écoute continue.

1. `make install-wakeword` puis `make models` (5 modèles ONNX, 5 Mo).
2. `make fw-upload SERIAL_PORT=/dev/ttyUSB0` (firmware de l'étape 13), puis `make run`.
3. « Alexa, allume la lumière » → LED allumée pendant la commande, puis réponse et commande.
4. Une phrase sans le mot → rien ne se passe, rien n'est enregistré.
5. « Alexa » seul, puis rien → retour en veille après 4 s.

**Vie privée** : avant le mot, seules les 500 dernières ms restent en mémoire, rien n'est
écrit sur disque (vérifié par un test). Mais le son circule en continu sur le réseau : tant
que le broker accepte les clients anonymes (étape 15), n'importe qui sur le réseau local peut
l'écouter. Couper l'écoute : `wake.enabled: false`, ou
`make mqtt-control MSG='{"cmd":"listen","enabled":false}'`.

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
**appareil** (lumière, volets, porte) et une **pièce** (liste dans `config.yaml`, section `home`).
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

Whisper donne l'instant de chaque mot, la diarisation celui de chaque tour de parole, sur
le même audio : chaque mot va au locuteur qui parlait à ce moment. Le serveur publie le
résultat sur `voice/<carte>/transcript` et l'assistant appelle la personne par son nom.

```json
{"session": "453cce", "utterances": [
  {"speaker": "Denis", "text": "Ouvrir la porte.", "start": 0.0, "end": 1.26, "score": 0.6},
  {"speaker": "Siwis", "text": "Je suis la voix de synthèse...", "start": 1.5, "end": 4.46, "score": 0.6},
  {"speaker": "Denis", "text": "Fermez la porte.", "start": 4.92, "end": 5.86, "score": 0.6}]}
```

1. `make diarize OUT=server/recordings/<fichier>.wav` : qui a dit quoi, et le message MQTT, sans carte.
2. `make mqtt-watch` dans un terminal, `make run` dans un autre.
3. « Bonjour » : la carte répond « Bonjour Denis ! » ; `voice/esp32-01/transcript` apparaît.
4. Une session à deux voix (vous, puis une voix du téléphone, puis vous) : trois interventions.

### Étape 9 — identification du locuteur

Chaque voix trouvée par la diarisation est résumée par une **empreinte** (256 nombres,
modèle WeSpeaker du pipeline pyannote), comparée par **similarité cosinus** aux profils
enrôlés. Au-dessus du seuil (0,45, mesuré sur ce banc), le nom ; sinon « Inconnu ».
**C'est une estimation, jamais une sécurité** : un enregistrement ou une imitation peut tromper.

1. `make run`, puis 5 phrases variées de 1 à 4 s, seul, à distance normale.
2. `make enroll NAME=Denis` : profil créé depuis ces 5 sessions ; les sessions trop
   courtes, à plusieurs voix ou qui ne ressemblent pas aux autres sont écartées.
3. Nouvelles questions (le serveur voit le profil sans redémarrer) : le journal affiche
   `SPEAKER_00 = Denis (0.74)` ; une autre personne ou une voix de haut-parleur donne
   `Inconnu (0.05, proche de Denis)`.
4. `make diarize-demo` : votre voix reconnue, la voix Piper « Inconnu ».

Les profils sont des **données biométriques** : `server/profiles/`, jamais versionné,
un fichier JSON lisible par personne ; `make forget NAME=...` le supprime.

### Étape 8 — diarisation : qui parle quand

La parole trouvée par la VAD est découpée en tours de parole : `SPEAKER_00` de 0,0 à
1,3 s, `SPEAKER_01` de 1,4 à 2,9 s... Les étiquettes ne valent que pour une session :
mettre un nom sur une voix, c'est l'étape 9. Modèle `pyannote/speaker-diarization-community-1`
(33 Mo), exécuté sur CPU, **télémétrie de pyannote coupée** (active par défaut en version 4).

1. Compte sur <https://huggingface.co>, puis **accepter les conditions** sur
   <https://huggingface.co/pyannote/speaker-diarization-community-1>.
2. Jeton de type « Read » sur <https://huggingface.co/settings/tokens>, copié dans
   `server/.env` : `HF_TOKEN=hf_...` (`cp server/.env.example server/.env` s'il n'existe pas).
3. `make install-diarization` puis `make models`. Ensuite, plus aucun accès à Internet.
4. `make diarize-demo` : votre voix, la voix Piper, votre voix → deux locuteurs attendus.
5. `make run` : une session où deux personnes parlent à tour de rôle ; le journal affiche
   `2 locuteur(s) : SPEAKER_00 0.00-1.30, SPEAKER_01 1.40-2.90`.

Sur Raspberry Pi 4, trop lourd : `diarization.enabled: false` dans `config.yaml`.

### Étape 7 — détection d'activité vocale (VAD)

Le serveur ne transcrit plus que la parole : silences de début et de fin retirés,
sessions sans parole (appui muet, clic) écartées sans appeler Whisper. Silero est
livré avec faster-whisper : rien de plus à installer.

1. `make vad-all` : une ligne par fenêtre de 32 ms (`#` parole, `_` silence) pour
   chaque enregistrement, et le nombre de sessions sans parole.
2. `make vad OUT=server/recordings/<fichier>.wav` puis `make play OUT=recordings/parole.wav` :
   la parole seule, sans les silences.
3. `make run` (à relancer : le code a changé), puis quelques questions et un appui
   sans parler. Le journal affiche `parole 1.36 s sur 2.00 s (segments 0.49-1.85 s)`,
   et pour l'appui muet `sans parole (VAD)`, avec une réponse immédiate.

Réglages dans `config.yaml`, section `vad` : seuil, pause minimale, durée minimale de parole, marge.

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
