# CONTEXT.md — état d'avancement du projet

> Fichier de reprise. À relire **en premier** en cas d'interruption, et à mettre à jour
> **à la fin de chaque étape**. Il décrit ce qui est fait, ce qui est en cours, et les
> décisions techniques déjà prises (pour ne pas les rediscuter).

---

## 1. Projet

Assistant vocal **100 % local** : ESP32 (micro INMP441) → MQTT → serveur Python
(VAD, faster-whisper, diarisation, Piper) → MQTT → ESP32 (ampli MAX98357A).

Prompt de référence : `prompt-assistant-vocal-esp32.md` (rôle, style, feuille de route, conventions).

---

## 2. Feuille de route et avancement

| # | Étape | État | Validé par l'utilisateur |
|---|---|---|---|
| 0 | Ossature du dépôt (arborescence, Makefile, git, README) | **fait** | en attente |
| 1 | Entrée audio ESP32 (I2S RX) + `tools/serial_to_wav.py` | **fait** | **oui (2026-09-29)** |
| 2 | Sortie audio ESP32 (I2S TX) + `tools/wav_to_serial.py` | **fait** | **oui (2026-09-29)** |
| 3 | Mosquitto + topics (Wi-Fi, MQTT, état retenu, testament) | **fait** | **oui (2026-09-29)** |
| 4 | ESP32 → MQTT → serveur (sessions reconstruites en WAV) | **fait** | **oui (2026-09-29)** |
| 5 | Serveur → MQTT → ESP32 (WAV serveur joué par l'ESP32, mode écho) | **fait** | **oui (2026-09-29)** |
| 6 | MVP : PTT → faster-whisper → réponse → Piper → ESP32 | **fait** (serveur seul, firmware inchangé) | **oui (2026-09-29)** |
| 7 | VAD (Silero) | **fait** (serveur seul) | **oui (2026-09-29)** |
| 8 | Diarisation (pyannote) | **fait**, test réel à deux voix réussi | **oui (2026-09-29)** |
| 9 | Identification (embeddings + cosinus + seuil) | **fait** (serveur seul) | **oui (2026-09-30)** |
| 10 | Association locuteur + texte | **fait** (serveur seul) | **oui (2026-09-30)** |
| 11 | Assistant / domotique | **fait** (serveur + firmware) | **oui (2026-09-30)** |
| 12 | Buffer de sortie anti-jitter | **fait** (firmware + banc de gigue) | **oui (2026-09-30)** |
| 13 | Wake word (openWakeWord) | **fait** (serveur + firmware) | **oui (2026-09-30)** |
| 14 | Multi-utilisateurs | **fait** (serveur seul) | **oui (2026-09-30)** |
| 15 | Fiabilisation | **fait** (broker + serveur + firmware) | **oui (2026-09-30)** |
| 15b | **Agents Linux** (ajout utilisateur) : commander des machines du réseau | **fait** (serveur + agent) | **oui (2026-10-06)** |
| 16 | Optimisation | **fait** (serveur + option broker ; firmware inchangé) | en attente |

**Règle absolue** : ne jamais démarrer l'étape N+1 avant validation explicite de l'étape N.

**Exigence ajoutée par l'utilisateur (2026-09-30)** : l'assistant doit aussi pouvoir **commander des
machines Linux du réseau**. Absente de la feuille de route du prompt. Proposition en discussion :
un agent MQTT par machine (liste blanche d'actions définie sur la machine), Wake-on-LAN par le
serveur, état retenu + testament comme l'ESP32 ; actions sensibles seulement après les droits par
profil (étape 14) et l'authentification MQTT (étape 15). **Placement décidé : étape 15b** (après droits par profil et authentification MQTT). Liste d'actions et machines à préciser.

---

## 3. Décisions techniques figées

| Sujet | Décision | Raison |
|---|---|---|
| Format audio unique | PCM `s16le`, 16 kHz, mono | compatible Whisper/Silero, simple pour l'ESP32 |
| Taille de chunk | 50 ms = 800 échantillons = **1600 octets** | compromis latence / surcoût protocole |
| Encodage MQTT | bytes bruts, **jamais de Base64** | −33 % de débit, pas de CPU perdu |
| Core Arduino-ESP32 | **3.x** (ESP-IDF 5.x), driver `driver/i2s_std.h` | ancien `driver/i2s.h` déprécié ; plateforme `pioarduino` |
| Plateforme PlatformIO | `pioarduino` 54.03.21 (fork maintenu pour core 3.x) | le paquet officiel `espressif32` est resté en core 2.0.x |
| PlatformIO isolé | venv dédié `.pio-venv`, avec `click==8.1.8` | click ≥ 8.3 casse l'esptool 5.0 du core 3.x (erreur `get_metavar() missing ctx`) ; évite aussi tout conflit avec les dépendances du serveur |
| Périphériques I2S | I2S0 = micro (RX), I2S1 = ampli (TX) | duplex séparé, pas de conflit d'horloges |
| Broches | voir `firmware/include/config.h` | aucune broche de strapping (0, 2, 5, 12, 15) sur l'audio |
| Broker de dev : conteneur `mosquitto_services-mosquitto-1`, compose `~/srv/docker-compose.mosquitto.yml`, config montée `~/srv/mosquitto/config/mosquitto.conf` ; **IP du PC = 192.168.1.104** (et non .0.104) | 2026-09-29 | constaté sur la machine |
| **Broker = Raspberry Pi 5, 192.168.1.200**, comptes exigés (anonyme refusé) ; un seul réglage : `mqtt.host` de `server/config.yaml`, relu par le Makefile (`MQTT_HOST`) | 2026-10-06 | décision utilisateur (« broker sur pi5 ») ; le conteneur local du PC de dev (192.168.1.104, anonyme) tourne encore mais n'est plus utilisé |
| Carte : Wi-Fi et broker en **NVS**, réglés par le portail `VoiceAssist-Config` (http://192.168.4.1, bouton PTT tenu 3 s au démarrage) ; `secrets.h` n'est plus qu'un repli si la NVS est vide (`WifiConfig`, ajouté par l'utilisateur) | 2026-10-06 | changer de broker = portail, pas recompilation |
| MQTT : état JSON **retenu** + testament `{"status":"offline"}` retenu QoS 1, keepalive 10 s | 2026-09-29 | `offline` en 15 s max après coupure ; serveur informé même s'il démarre après l'ESP32 |
| Wi-Fi : `setSleep(false)` | 2026-09-29 | le modem endormi ajoute 100 ms+ de latence : incompatible avec le flux audio |
| Partitions `huge_app.csv` (3 Mo d'application, pas d'OTA) | 2026-09-29 | Wi-Fi + MQTT = 74 % de la partition par défaut |
| Sortie par MQTT : START/END sur `control` (QoS 1), PCM sur `audio/out` (QoS 0), **300 ms d'avance** puis 1 chunk / 50 ms | 2026-09-29 | FIFO ESP32 de 512 ms ; la carte publie `played` (reçus/annoncés, sous-alimentations) |
| Tout audio envoyé à l'ESP32 : **16 kHz mono, crête à −3 dBFS** (`AudioResampler`, soxr) | 2026-09-29 | ESP32 simple ; règle le volume faible noté à l'étape 2 ; Piper (22,05 kHz) passera par là |
| Firmware : lecture regroupée dans `PlaybackStream` (série ou MQTT), commandes dans `CommandHandler` (ArduinoJson 7.2.1) | 2026-09-29 | un seul chemin de lecture ; les commandes s'enrichiront à l'étape 11 |
| Capture : micro → **tâche de capture** → FIFO 1 s → `loop()` → MQTT | 2026-09-29 | `publish()` peut bloquer sur un Wi-Fi faible ; le DMA du micro n'a que 75 ms de réserve |
| Perte détectée par le **nombre de chunks du END** (pas de numéro de séquence avant l'étape 16) | 2026-09-29 | audio en QoS 0 ; le serveur compare reçus / annoncés après 0,5 s de grâce |
| **Limite connue** : PubSubClient ne publie qu'en **QoS 0**, `event` compris (le prompt demande QoS 1) | 2026-09-29 | serveur tolérant (session sans END close après 5 s d'inactivité). **Revu à l'étape 15 : conservé** (aucune perte mesurée sur le réseau local ; esp-mqtt = réécriture de `MqttLink` et de ses rappels, sans gain observable) |
| Session sans broker → liaison série, format de l'étape 1 | 2026-09-29 | `make record` reste un outil de dépannage |
| Sortie : **stéréo, même échantillon sur les deux voies** | 2026-09-29 | le MAX98357A joue (G+D)/2 : niveau exact, inversion G/D sans effet ; on ne mise plus sur un mode mono implicite |
| Sortie : canal I2S1 **toujours actif**, `auto_clear` → silence quand rien à jouer | 2026-09-29 | broches de l'ampli jamais flottantes (cause du souffle initial) |
| Lecture : réception → **FIFO** (StreamBuffer statique 512 ms) → **tâche audio** → I2S | 2026-09-29 | architecture exigée pour MQTT (callback court) ; la tâche est rythmée par le DMA |
| FIFO : écriture **tout ou rien** par chunk | 2026-09-29 | un chunk coupé = octet impair = tout le reste désaligné |
| Protocole série **partagé** : `SerialProtocol.h` ↔ `tools/serial_protocol.py` | 2026-09-29 | une seule définition par langage ; test croisé encodeur Python → décodeur C++ |
| Passe-haut micro **80 Hz**, 1er ordre, calculé en **24 bits** (`MIC_HPF_SHIFT = 5`) | 2026-09-29 | 87 % de l'énergie d'un enregistrement était infrasonore (plaque qui fléchit sous le doigt, souffle) ; 0,6 % après filtre, voix 99 % |
| Cadrage micro : **MSB** (24 bits utiles), mesuré, pas supposé | 2026-09-29 | en Philips, le bit de signe déborde dans le mot précédent (23 bits + `00000001`) ; l'auto-test bascule seul si un autre module est Philips |
| Les horloges du micro ne s'arrêtent **jamais** après le démarrage | 2026-09-29 | l'INMP441 s'endort sans horloge ; chaque réveil = dérive lente de la composante continue (+23 % FS encore à 600 ms) |
| Lecture I2S stéréo : la **phase de trame** est suivie entre les appels | corrigé le 2026-09-29 | `i2s_channel_read()` peut rendre un nombre **impair** de mots ; sans suivi, les deux moitiés s'échangent en cours de route et on enregistre la moitié muette |
| Micro : SCK → **GPIO 25**, WS → **GPIO 26** | corrigé le 2026-09-29 | le schéma initial les avait inversés ; aucune raison technique ne départage 25 et 26, la doc a été alignée sur le banc réel |
| Liaison série étape 1 | **921 600 baud**, trames `VSA1` + longueur | 32 kB/s d'audio impossible à 115 200 baud |
| Conversion micro | mot I2S 32 bits → `>> 16`, gain ×4, offset DC glissant | l'INMP441 place 24 bits utiles en tête de mot |
| Licence | MIT | choix par défaut du prompt |
| Extra `speech` : **faster-whisper 1.2.1**, **piper-tts 1.8.0** (au lieu de 1.0.3 / 1.2.0) | 2026-09-29 | Python 3.13 : faster-whisper 1.0 exige `av<13` (compilation depuis les sources, échoue sans les en-têtes ffmpeg) ; piper-tts 1.2 dépend de `piper-phonemize` sans roue 3.13. piper-tts ≥ 1.3 embarque espeak-ng. API Piper : `PiperVoice.load()`, `synthesize()` → `AudioChunk.audio_float_array` |
| Modèles **locaux** dans `server/models/` : `whisper/<model>/`, `piper/<voice>.onnx(.json)`, via `make models` (`tools/download_models.py`) | 2026-09-29 | aucun accès réseau au démarrage du serveur ; ~550 Mo ; ignorés par git |
| STT : `small`, `int8`, `beam_size 5`, `condition_on_previous_text=False`, `vad_filter=False` (VAD = étape 7) | 2026-09-29 | mesuré sur ce PC (20 cœurs) : **≈ 1,0 s quelle que soit la phrase (1 à 3 s)**, le nombre de fils (0/8/16) ou le beam (1/5) : Whisper encode toujours une fenêtre de 30 s |
| Session de crête < **−45 dBFS** (`stt.min_peak_dbfs`) : Whisper n'est pas appelé, la carte dit « Je n'ai rien entendu. » | 2026-09-29 | mesuré : 2 s de silence numérique = **4,8 s** de calcul (repli sur les températures) |
| Hallucinations connues écartées (`KNOWN_HALLUCINATIONS` : Amara.org, sous-titres…) | 2026-09-29 | mesuré : 1 s de bruit gaussien → « Sous-titres réalisés par la communauté d'Amara.org » |
| Imports faster-whisper / Piper **différés** (dans `__init__`) | 2026-09-29 | `make run-echo` et les tests fonctionnent sans l'extra `speech` |
| Assistant à règles (`assistant.py`) : heure, date, nom, bonjour, merci ; sinon « Vous avez dit : … » ; heure écrite « une heure », « midi », « 19 heures 5 » | 2026-09-29 | Piper lit « 1 heure » = « un heure » ; Whisper écrit parfois « Quel heure » (mots-clés tolérants) |
| VAD : **Silero ONNX livré par faster-whisper** (`faster_whisper.vad.get_vad_model`), segmentation écrite dans `voice_activity.py` (hystérésis 0,5 / 0,35, pause 500 ms, parole ≥ 250 ms, marge 150 ms) | 2026-09-29 | pas de torch ; segmentation testable sans modèle ; 2 à 4 ms par session |
| VAD **sur la session entière** (après le END), pas en flux | 2026-09-29 | suffisant en PTT ; la détection de fin de parole en flux servira au mode mains libres (étape 13) |
| La VAD **remplace le seuil de crête** `stt.min_peak_dbfs` (supprimé) | 2026-09-29 | l'appui muet de l'étape 6 (crête −30,7 dBFS) passait le seuil ; la VAD le rejette |
| Diarisation : **pyannote.audio 4.0.7**, torch **2.14.0+cpu** / torchaudio 2.11.0+cpu (index pytorch.org), modèle `pyannote/speaker-diarization-community-1` (33 Mo) | 2026-09-29 | Python 3.13 ; sans l'index CPU, pip tire torch CUDA (plusieurs Go de `nvidia-*`) ; pyannote 3.3.2 antérieur à ce torch |
| **numpy 2.1.3 → 2.5.3** (dépendance de base) | 2026-09-29 | pyannote-metrics 4 exige numpy ≥ 2.2.2 ; 78 tests repassés avec 2.5.3 (soxr, onnxruntime, ctranslate2 OK) |
| **Télémétrie pyannote coupée** (`PYANNOTE_METRICS_ENABLED=false`, forcé par `disable_telemetry()` avant l'import) | 2026-09-29 | activée par défaut en 4.x : envoie durée et nombre de locuteurs à `otel.pyannote.ai` ; variable relue à chaque appel |
| Modèle pyannote **copié dans `models/pyannote/<nom>/`** par `make models` (`snapshot_download`, `HF_TOKEN` de `server/.env`), chargé depuis ce dossier | 2026-09-29 | les sous-modèles sont des `$model/…` relatifs : aucun appel au hub à l'exécution. `HF_HUB_OFFLINE` non utilisé : lu à l'import de huggingface_hub, que faster-whisper a déjà fait |
| Diarisation **sur `vad.speech_pcm`**, le même audio que Whisper, **en séquence** avant Whisper | 2026-09-29 | instants communs pour l'association locuteur/texte (étape 10) ; parallélisation → étape 16 |
| `diarization.enabled` dans config.yaml ; activée mais absente → le serveur refuse de démarrer avec la marche à suivre | 2026-09-29 | Raspberry Pi 4 : `false` ; pas de repli silencieux |
| pyannote 4 rend **une empreinte vocale par locuteur** (`speaker_embeddings`, WeSpeaker ResNet34), conservée dans `Diarization.embeddings` | 2026-09-29 | candidate pour l'identification (étape 9), à comparer à SpeechBrain ECAPA prévu par le prompt ; `speechbrain` retiré de l'extra `diarization` |
| Empreintes : **modèle `embedding/` du pipeline pyannote (WeSpeaker ResNet34, 256 dim.)** via `Inference(window="whole")` sur toute la parole de chaque locuteur (tours de `exclusive_speaker_diarization` mis bout à bout), 66 ms | 2026-09-29 | **les `speaker_embeddings` de pyannote sont NaN sous ~2 s de parole** : il ne garde que les fenêtres de 10 s où le locuteur parle seul ≥ 20 % (`filter_embeddings`, `min_active_ratio=0.2`) ; toutes les questions PTT étaient NaN |
| Tours de parole : version **exclusive** (sans chevauchement) | 2026-09-29 | une voix par empreinte ; prête pour le découpage du texte (étape 10) |
| Seuil cosinus **0,45** (remplace 0,75) | 2026-09-29 | mesuré : profil de 5 sessions contre 23 autres sessions de l'utilisateur **0,52–0,83** (0,52 = 0,9 s de parole) ; Piper direct ou par haut-parleur **−0,08 à 0,11**. À recalibrer avec de vraies autres personnes (étape 14) |
| Profils : `server/profiles/<nom>.json` (nom, modèle, nb de sessions, date, empreinte normalisée), écriture atomique, **relus quand le dossier change** | 2026-09-29 | JSON lisible (on sait ce qu'on stocke) ; un profil d'un autre modèle est ignoré ; enrôlement pris en compte sans redémarrer |
| Enrôlement (`make enroll NAME=`, `tools/enroll.py`) : ≥ 3 sessions de ≥ 1 s, une seule voix, **écart des sessions qui ne ressemblent pas aux autres** (cosinus avec la moyenne des autres < seuil) | 2026-09-29 | la session mélangée `093463` (voix + haut-parleur, vue comme 1 locuteur par pyannote) est écartée à 0,27–0,32 |
| Identification **seulement avec la diarisation** (pas d'empreinte sinon) | 2026-09-29 | choix de l'utilisateur (empreintes pyannote plutôt que SpeechBrain) |
| Qui a dit quoi : **`word_timestamps=True`** dans Whisper, chaque mot au tour de diarisation qui le recouvre le plus (sinon le plus proche), mots consécutifs d'un même locuteur regroupés | 2026-09-30 | mesuré : +0,07 à 0,14 s (5 à 10 %) ; les bornes des mots tombent sur celles des tours (même audio pour les deux) |
| Topic **`voice/<carte>/transcript`** (serveur → abonnés, QoS 1, non retenu) : `{"session", "utterances": [{"speaker", "text", "start", "end", "score"}]}` | 2026-09-30 | format de la feuille de route + instants et score pour la domotique (étape 11) et les droits (étape 14) ; le serveur ne s'y abonne pas |
| L'assistant reçoit le **nom de la dernière personne qui a parlé** (`reply(text, speaker)`) : « Bonjour Denis ! », « Avec plaisir, Denis. » | 2026-09-30 | c'est elle qui attend la réponse ; `None` si inconnue |
| Domotique : commandes sur **`home/<pièce>/<appareil>/set`** (QoS 1, **non retenu**) **et**, si la pièce est celle de la carte, `voice/<carte>/control` `{"cmd":"device",...}` exécuté par l'ESP32 (choix de l'utilisateur : « les deux », en simulation) | 2026-09-30 | toute domotique peut s'abonner ; l'ESP32 « exécute la commande éventuelle » (prompt) ; une commande retenue serait rejouée à un abonné tardif |
| Analyse par **mots-clés** (`home_control.py`) : action × appareil × pièce ; « allume/éteins » seuls = lumière ; sans pièce = pièce de la carte (`home.boards`) ; appareil/action incompatibles ou incomplets → question | 2026-09-30 | suffisant pour 3 appareils ; tolère « fermé » (Whisper) ; limites : 1 appareil et 1 pièce par phrase, faux positifs possibles (« je porte… ») |
| Identifiants en anglais dans les topics et le JSON (`light`, `shutter`, `door`, `on/off/open/close`) ; pièces = noms de config.yaml, en slug (`salle-de-bain`) | 2026-09-30 | mêmes chaînes côté Python et firmware |
| Firmware : **`DemoDevices`**, LED sur **GPIO 21** (330 Ω), volets et porte simulés ; confirmation `{"event":"device","ok":...,"state":...}` journalisée par le serveur | 2026-09-30 | GPIO 21 libre, hors strapping ; la boucle commande → exécution → confirmation est visible |
| La commande part **avant** la réponse parlée | 2026-09-30 | la lumière s'allume pendant que la carte le dit |
| `normalize` déplacé dans `text_normalize.py` | 2026-09-30 | commun à l'assistant et à la domotique, sans import circulaire |
| Météo : réponse honnête « je fonctionne sans Internet » | 2026-09-30 | plutôt que répéter la question |
| Anti-gigue : **amorçage à 300 ms** (`PLAYBACK_START_MS`) avant le premier échantillon, **réamorçage** après une famine (pause propre plutôt que bribes), son plus court que la réserve joué dès le END | 2026-09-30 | 300 ms = 6 chunks = avance envoyée d'emblée par le serveur : la lecture démarre dès qu'elle est arrivée ; valeur de l'exemple du prompt |
| `underruns` du bilan = **réamorçages** (famines en plein flux) ; nouveaux champs `start_ms` (délai du premier son) et `min_margin_ms` (plus petite réserve mesurée) | 2026-09-30 | l'ancien compteur comptait chaque bloc incomplet, y compris au démarrage : 6 à 10 par lecture sans aucune perte |
| Banc de gigue : `--jitter MS` / `make tone-mqtt JITTER=MS` retient **1 chunk sur 10** (toutes les 500 ms) pendant MS ms, puis rattrapage en rafale | 2026-09-30 | scénario déterministe : marge attendue ≈ 300 − MS ms, réamorçages seulement au-delà de 300 ms ; prouver plutôt qu'attendre un mauvais Wi-Fi |
| Mot de réveil : **openWakeWord 0.6.0 en ONNX**, installé `--no-deps` (exige `tflite-runtime`, absent pour Python 3.13) ; modèles `alexa`, `hey_jarvis`, `hey_mycroft` v0.1 + melspectrogram/embedding, depuis les releases GitHub v0.5.1 | 2026-09-30 | mesuré : « Alexa » (Piper FR) 0,92–1,0 ; « Hé Maïcrofte » 1,0 ; « Hey Djâr-viss » 0,74 ; lus à la française Jarvis/Mycroft ≈ 0 ; **0 fausse alerte sur 90 enregistrements réels** (max 0,07) ; ~1 ms par chunk |
| Flux continu **`voice/<carte>/audio/stream`** (QoS 0), activé par le serveur (`cmd listen`) à la connexion/redémarrage de la carte, coupé à l'arrêt du serveur ; **jamais pendant une réponse** (écho, Wi-Fi) ni pendant un appui (le bouton reprend la main) | 2026-09-30 | le serveur décide ; la carte reste simple |
| `HandsFreeListener` : VEILLE (préroll 500 ms **en mémoire seulement**) → COMMANDE (fin = 800 ms de silence après de la parole qui dépasse le mot de 0,3 s ; abandon après 4 s sans commande ; max 10 s) → RÉPONSE (jusqu'au bilan `played`, garde 20 s) ; la commande devient une `SessionResult` ordinaire | 2026-09-30 | aucune autre partie de la chaîne ne change |
| Délais d'une commande mesurés en **audio reçu**, pas à l'horloge | 2026-09-30 | trouvé en simulation : en rafale Wi-Fi, 2 s de son peuvent arriver en 10 ms ; l'horloge ne sert qu'aux gardes (réponse sans bilan, flux perdu 2 s) |
| LED : éclair 100 ms / 2 s = écoute continue ; allumée = commande en cours (`cmd capture`) ; `StatusLed::blink(period, onMs)` | 2026-09-30 | l'utilisateur voit quand le micro part sur le réseau |
| Droits : **complet > standard > limite** (`access_control.py`) ; inconnu = limite, enrôlé sans niveau = standard, `users: {Denis: complet}` | 2026-09-30 | niveaux du prompt ; conversation toujours permise |
| Niveau complet seulement si **score ≥ 0,6** (`full_min_score`), sinon rétrogradé en standard | 2026-09-30 | la voix n'est pas une preuve : une identification de justesse ne doit pas tout ouvrir |
| Règles par appareil/action ; **action absente = niveau complet** | 2026-09-30 | on ne permet jamais par oubli |
| Actions sensibles (`confirm: [door.open]`) : question, puis « oui »/« non » **de la même personne**, sur la même carte, en 15 s ; toute autre phrase abandonne la demande | 2026-09-30 | garde-fou pour la porte et pour les agents Linux (15b) |
| Confirmation : acceptée si la voix est reconnue comme le demandeur, **ou** si elle est sous le seuil mais que le demandeur reste le **profil le plus proche** avec `access.confirm_min_score` (**0,25**) ; voix reconnue comme quelqu'un d'autre : refusée | 2026-10-06 | mesuré : 6 « oui » seuls de Denis (~0,7 s de parole) à 0,29-0,47, 5 sous le seuil 0,45 ; autres profils face à Denis ≤ 0,12 (profils entiers, surtout voix de synthèse : à revoir avec de vraies personnes) |
| Étape 16 : **parole < `diarization.direct_below_s` (4 s) = une seule personne** → `Diarizer.embed()` au lieu de la diarisation, Whisper sans mots horodatés | 2026-10-06 | mesuré sur 113 sessions réelles : 0,02 s contre 0,42 s, écart de score médian +0,001, 4 verdicts changés (2 en mieux, 2 en moins bien, tous au seuil) ; aucune session récente à 2 voix. Contrepartie : deux voix dans une phrase courte ne sont plus séparées |
| Étape 16 : parallélisme diarisation / Whisper **écarté** | 2026-10-06 | mesuré : séquentiel 1,43-1,59 s, parallèle 1,34-1,56 s ; chacun ralentit l'autre (Whisper 1,1 → 1,4 s, diarisation 0,4 → 0,8 s), **même dans deux processus** : saturation du processeur, pas le verrou de Python |
| Étape 16 : `beam_size` 5 gardé, `stt.cpu_threads` 0 (défaut, 4 fils) | 2026-10-06 | beam 1 : −0,05 s et 1 texte différent sur 16 ; fils : 4 → 1,08 s, 8 → 0,98 s, 16 → 1,29 s sur le PC ; l'encodeur de Whisper (fenêtre fixe de 30 s) domine |
| Étape 16 : cache de synthèse (64 phrases), fin de commande cherchée toutes les 100 ms, `TCP_NODELAY` côté serveur (`on_socket_open`) et broker (`set_tcp_nodelay true`) | 2026-10-06 | cache : 0,03-0,14 s par réponse répétée ; l'effet de `TCP_NODELAY` sur « premier son a ~240 ms » est une **hypothèse** (Nagle + accusés retardés de l'ESP32), à confirmer sur la carte |
| Droits appliqués au **dernier locuteur** de la session | 2026-09-30 | c'est lui qui attend la réponse (cohérent avec l'étape 10) |
| Broker : `allow_anonymous false` + `password_file` + `acl_file` ; comptes `voice-server` (voice/# et home/# en lecture-écriture) et **un par carte, nom = DEVICE_ID** (`pattern ... voice/%u/...`) | 2026-09-30 | micro inaudible et commandes impossibles pour le reste du réseau ; une carte ne peut ni écouter ni usurper une autre (vérifié sur broker jetable) |
| Outils `make mqtt-*` : identifiants du serveur relus dans `server/.env` (`-include`), recettes en `@` | 2026-09-30 | pas de compte supplémentaire ; mot de passe jamais affiché par make ; mot de passe hexadécimal (`#`, `$` cassent make) |
| Présence serveur : `voice/server/status` retenu, `online` à chaque connexion, testament `offline`, `offline` publié à l'arrêt propre ; **seulement `MqttLink(presence=True)`** du vrai serveur | 2026-09-30 | ESP32 : serveur absent → `listenWanted=false`, plus de flux micro ; les outils (wav_to_mqtt) ne doivent pas faire croire au départ du serveur |
| Topic de présence hors motif `voice/+/<suffixe>` des abonnements serveur | 2026-09-30 | sinon le serveur se prendrait pour une carte (écoute activée sur voice/server/control) |
| Chien de garde ESP32 : TWDT reconfiguré **15 s, panique** ; loop (`enableLoopWDT`), tâches capture et audio inscrites ; réarmé entre deux envois réseau | 2026-09-30 | `NetworkClient::write` peut attendre 10 × 1 s (lu dans le core 3.x) : 5 s par défaut redémarrerait sur un Wi-Fi faible |
| MQTT reste dans `loop()` (pas de tâche dédiée) ; connexion TCP 1 s (`setConnectionTimeout`) + CONNACK 2 s = 3 s de blocage au pire, broker absent seulement | 2026-09-30 | PubSubClient pas prévu pour plusieurs tâches ; audio dans ses propres tâches |
| Wi-Fi relancé (`disconnect` + `begin`) après 30 s d'absence | 2026-09-30 | filet si la reconnexion automatique n'aboutit pas |
| Journal serveur copié dans `logging.file` (`RotatingFileHandler` 1 Mo × 4), `logs/` ignoré par git | 2026-09-30 | Raspberry Pi sans écran ; les transcriptions y figurent = données personnelles |
| Machines : **agent MQTT par machine** (`agent/voice_agent.py`, autonome : paho-mqtt + PyYAML), config YAML sur la machine | 2026-09-30 | demande utilisateur (config YAML) ; MQTT = transport unique (§3.7) ; SSH écarté : clés sur le serveur, liste blanche côté serveur seulement |
| Liste blanche **sur la machine** : action = commande fixe en **liste** (pas de shell), le serveur n'envoie qu'un **nom** ; nom inconnu refusé | 2026-09-30 | un message forgé ne peut rien lancer d'autre ; config refusée si `command` est une chaîne |
| Topics `agent/<id>/state` (retenu, **actions annoncées**, testament), `command` (QoS 1, non retenu, **session propre**), `result` | 2026-09-30 | le serveur sait si la machine répond et ce qu'elle accepte ; aucune commande rejouée au redémarrage |
| « Allume » = **Wake-on-LAN** envoyé par le serveur (paquet magique UDP port 9, `broadcast` configurable), si agent hors ligne et MAC connue | 2026-09-30 | machine éteinte = pas d'agent |
| Phrase qui **nomme une machine** traitée avant la domotique ; noms de plusieurs mots (« pc du bureau ») | 2026-09-30 | « éteins le PC du bureau » ≠ « éteins la lumière du bureau » (bureau est une pièce) |
| Droits : `access.rules.machine` (wake/lock standard, shutdown/reboot complet), `confirm: [machine.shutdown, machine.reboot]` ; `MachineCommand` porte ses phrases (`said`, `asked`) | 2026-09-30 | réutilise étape 14 sans changer `DeviceCommand` ; `CommandRouter` aiguille vers Home/MachineController |
| Service systemd **root** (`/opt/voice-agent`, `/etc/voice-assistant/agent.yaml` 0600), `Restart=always` ; `agent/` autonome (`make -C agent install`) | 2026-09-30 | poweroff/reboot exigent root ; il suffit de copier le dossier sur la machine |
| `dry_run` dans agent.yaml ; `make agent-run` crée `agent/agent.yaml` en mode essai | 2026-09-30 | tester « éteins le PC » sur le PC de développement sans l'éteindre |
| **TLS non activé** (option de l'étape 15) ; **numéros de séquence** reportés à l'étape 16 (§3.4 du prompt : « phase 16 ») | 2026-09-30 | comptes + ACL ferment l'accès au micro ; TLS = certificats + ~40 Ko de RAM ESP32, à décider par l'utilisateur |
| `make run` = **mode assistant** ; `make run-echo` = étape 5 | 2026-09-29 | une file + un fil de travail : les sessions sont traitées une à une, hors du fil réseau |

---

## 4. Fichiers existants (et qui les utilise)

```
docs/BROCHAGE.md                   brochage, consommation, vérifications multimètre
docs/wiring.svg                    schéma de câblage complet (source versionnable)
firmware/include/config.h          constantes matérielles + audio (étapes 1→16)
firmware/include/secrets.h.example modèle Wi-Fi / MQTT (utilisé à partir de l'étape 3)
firmware/src/MicCapture.*          I2S RX stéréo + conversion 32→16 bits       [étape 1]
firmware/src/MicSelfTest.*         auto-test du micro au démarrage             [étape 1]
firmware/src/PushButton.*          bouton PTT anti-rebond                       [étape 1]
firmware/src/StatusLed.*           LED d'état                                   [étape 1]
firmware/src/SerialProtocol.h      format des trames, partagé                   [étapes 1-2]
firmware/src/SerialFramer.*        émission des trames vers le PC               [étape 1]
firmware/src/SerialFrameReader.*   réception des trames du PC                   [étape 2]
firmware/src/AudioFifo.*           FIFO réception → tâche audio (StreamBuffer)  [étape 2]
firmware/src/AudioOutput.*         I2S1 TX vers MAX98357A, bip de test          [étape 2]
firmware/src/WifiLink.*            Wi-Fi station, non bloquant                  [étape 3]
firmware/src/MqttLink.*            PubSubClient, testament, reconnexion         [étape 3]
firmware/src/VoiceSession.*        START / chunks / END, MQTT ou série          [étape 4]
firmware/src/PlaybackStream.*      flux de lecture + tâche audio + bilan        [étape 5]
firmware/src/CommandHandler.*      JSON du topic control (start/end/ping)       [étape 5]
server/voice_server/audio_resampler.py  WAV quelconque -> 16 kHz mono -3 dBFS   [étape 5]
server/voice_server/audio_sender.py  START / chunks rythmés / END vers une carte [étape 5]
tools/wav_to_mqtt.py               WAV ou son pur -> carte, avec bilan          [étape 5]
server/voice_server/settings.py    config.yaml + .env                           [étape 4]
server/voice_server/mqtt_link.py   paho-mqtt 2.x, abonnement voice/+/...        [étape 4]
server/voice_server/audio_buffer.py  PCM borné, niveaux, écriture WAV           [étape 4]
server/voice_server/session_manager.py  sessions, pertes, délais, WAV           [étape 4]
server/voice_server/__main__.py    point d'entrée : make run                    [étape 4]
server/voice_server/speech_to_text.py  SpeechToText (faster-whisper) + Transcript   [étape 6]
server/voice_server/text_to_speech.py  TextToSpeech (Piper → 16 kHz) + Speech       [étape 6]
server/voice_server/assistant.py   Assistant à règles (heure, date…)            [étape 6]
server/voice_server/voice_pipeline.py  VoicePipeline : STT → réponse → TTS → envoi [étape 6]
tools/download_models.py           make models                                  [étape 6]
tools/speech_check.py              make speech-check / make transcribe          [étapes 6-7]
server/voice_server/voice_activity.py  VoiceActivityDetector (Silero), find_segments [étape 7]
tools/vad_check.py                 make vad / make vad-all                      [étape 7]
server/voice_server/diarization.py Diarizer (pyannote), SpeakerTurn, timeline   [étape 8]
tools/diarize_check.py             make diarize / make diarize-demo             [étapes 8-10]
server/voice_server/speaker_identifier.py  SpeakerIdentifier, profils, cosinus  [étape 9]
server/voice_server/speaker_attribution.py  attribute, Utterance, transcript_json [étape 10]
server/voice_server/home_control.py  parse_intent, decide, HomeController       [étape 11]
server/voice_server/text_normalize.py  normalize (commun)                       [étape 11]
firmware/src/DemoDevices.*         LED GPIO 21 + volets/porte simulés           [étape 11]
server/voice_server/wake_word.py   WakeWordDetector (openWakeWord ONNX)         [étape 13]
server/voice_server/hands_free.py  HandsFreeListener : veille / commande / réponse [étape 13]
server/voice_server/access_control.py  AccessPolicy : niveaux, refus, confirmation [étape 14]
tools/enroll.py                    make enroll / profiles / forget              [étape 9]
tools/latency_check.py             make latency : avant/après sur enregistrements réels [étape 16]
tools/say.py                       make say : phrase Piper en WAV + MP3 (téléphone) [étape 14]
docs/MOSQUITTO.md                  broker : installation, pièges, vérification  [étape 3]
firmware/src/main.cpp              boucle PTT → I2S → série                     [étape 1]
tools/serial_protocol.py           format des trames, partagé                   [étapes 1-2]
tools/serial_to_wav.py             réception des trames série → WAV             [étape 1]
tools/wav_inspect.py               enveloppe ASCII et niveaux d'un WAV          [étape 1]
tools/wav_to_serial.py             WAV ou son pur → ESP32, au rythme réel       [étape 2]
mosquitto/voice.conf               configuration du broker                      [étape 3]
mosquitto/acl                      droits par topic : serveur, une carte = ses topics [étape 15]
mosquitto/passwd                   comptes hachés (make mqtt-user), NON versionné [étape 15]
server/tests/test_mqtt_link.py     présence, refus du broker, journal fichier   [étape 15]
server/voice_server/machine_control.py  MachineController, phrases, Wake-on-LAN  [étape 15b]
server/voice_server/command_router.py   CommandRouter : domotique / machines     [étape 15b]
firmware/src/WifiConfig.*          Wi-Fi + broker en NVS, portail AP/HTTP (utilisateur) [2026-10-06]
agent/voice_agent.py               agent Linux autonome (liste blanche, dry_run) [étape 15b]
agent/agent.yaml.example           modèle de /etc/voice-assistant/agent.yaml         [étape 15b]
agent/voice-agent.service, agent/Makefile  service systemd, make -C agent install [étape 15b]
server/tests/test_machine_control.py, test_voice_agent.py                      [étape 15b]
server/                            squelette Python (modules à partir de l'étape 3)
Makefile                           toutes les opérations système
```

**Non encore écrits** (volontairement, par étape) : optimisation et numéros de séquence (16),
TLS (option de l'étape 15, non activée).

---

## 5. Reprise après incident

1. Lire ce fichier, puis `README.md`.
2. `make help` liste toutes les cibles.
3. Vérifier l'étape en cours dans le tableau du §2, et **ne rien faire au-delà**.
4. Contrôle rapide de l'environnement : `make test` (203 tests), `make lint`, `make fw-check`.

### État au 2026-09-30 au soir (fin de session)

- **Étapes 1 à 15 validées.** Étape **15b écrite**, vérifiée hors carte (broker jetable), **pas encore
  essayée par l'utilisateur**. Ne pas commencer l'étape 16 avant sa validation explicite.
- Vérifié à l'arrêt : 203 tests, pyright 0 erreur (tools/ et agent/ inclus), `make fw-check`
  SUCCESS 0 avertissement (RAM 36,7 %, Flash 31,5 %). Git : `main`, 1 commit, arbre propre.
  Aucun serveur, agent ni conteneur de test lancé (`voice-acl-test`, `voice-agent-test` supprimés).
- Broker : **Raspberry Pi 5, 192.168.1.200, comptes exigés** (depuis le 2026-10-06). Le serveur s'y
  connecte (`voice-server`, `server/.env`). Le conteneur local (192.168.1.104, anonyme) n'est plus utilisé.
- Firmware : la version **étape 13** est sur la carte (essais mains libres réussis) ; téléversement de
  la version **étape 15** (chien de garde, présence serveur) **non confirmé**. Les deux fonctionnent
  avec le serveur actuel, qui publie sa présence.
- `server/.venv` : extras `speech`, `diarization` (torch CPU), `wakeword` ; `server/models/` complet ;
  `HF_TOKEN` dans `server/.env` — **secret de l'utilisateur, ne jamais le modifier ni l'afficher**.
  Ne jamais toucher `firmware/include/secrets.h` (utiliser `make fw-check`).
- 6 profils vocaux : Denis (complet), Gilles, Jessica, Pierre, Siwis, Tom (standard). Phrases de
  test au téléphone : `recordings/voix-test/` (`make say`, `WAKE="Hey Mycroft"` = voix anglaise).
- Mot de réveil retenu par l'utilisateur : **« Hey Mycroft »** (prononcé à l'anglaise).

### Reprise de l'étape 15b (actions de l'utilisateur)

1. Terminal 1 : `make agent-run` (crée `agent/agent.yaml` en **mode essai**, `dry_run: true` ; vérifier
   `id: pc-bureau`, `mqtt.host: 192.168.1.200` et le compte MQTT `pc-bureau`).
2. Terminal 2 : `make run` → `agent pc-bureau : en ligne, actions lock, reboot, shutdown`.
3. « Hey Mycroft, verrouille le PC du bureau » → « Je verrouille le PC du bureau. » ; agent :
   `essai : loginctl lock-sessions (non execute, dry_run)`.
4. « Hey Mycroft, éteins le PC du bureau » → « Confirmez-vous… ? » → « Hey Mycroft, oui » → agent :
   `essai : systemctl poweroff`.
5. « Hey Mycroft, allume le PC du bureau » → « …est déjà en marche. »
6. Ctrl-C sur l'agent → serveur : `agent pc-bureau : hors ligne` ; « éteins le PC du bureau » →
   « …ne répond pas ».
7. Analyser les journaux collés, puis demander la validation de l'étape 15b avant l'étape 16.

### Points ouverts (ni oubliés, ni décidés)

- `machines.actions.*.words` : **un mot par entrée** (comparaison mot à mot) ; « lance la sauvegarde »
  ne peut pas être reconnu tel quel. Proposé à l'utilisateur (2026-09-30) : accepter des expressions
  de plusieurs mots. Pas de réponse encore. La négation n'est pas gérée (« n'éteins pas » = éteindre ;
  la confirmation limite le risque).
- Test instable (rare, ~2 échecs sur 15 lancements de la suite, jamais seul) :
  `test_diarization.py::test_embeddings_exist_for_a_short_question` (synthèse Piper aléatoire, existait
  avant le 2026-10-06). Cause exacte non recherchée.
- « le PC » seul n'est pas un nom de machine (`names` : « pc du bureau »…) : à ajouter si l'utilisateur
  le veut, ambigu avec plusieurs machines.
- Seuils du mot de réveil : réglage reporté (outil prêt : « mot presque reconnu »).
- Seuil d'identification 0,45 à recalibrer avec de vraies personnes.
- Whisper : hallucination sur 0,8 s de parole (« Je vous remercie de votre soutien… ») ; un « Ouvre »
  perdu une fois en tête de commande.
- Étape 16 : diarisation en parallèle de Whisper, `start_ms` ~214 ms (fenêtre TCP lwIP ?), numéros de
  séquence (§3.4 du prompt) ; TLS (option de l'étape 15) si l'utilisateur le demande.

---

## 6. Vérifications déjà passées (sans matériel)

| Contrôle | Commande | Résultat |
|---|---|---|
| Compilation du firmware | `make fw-build` | **SUCCESS**, 0 warning avec `-Wall` ; RAM 7,2 % (23 536 o), Flash 24,7 % (324 182 o) |
| Tests Python | `make test` | **162 tests** passés (dont le vrai pipeline pyannote et openWakeWord) (trames, sessions, sortie, assistant, boucle, VAD, diarisation, vrais modèles) |
| Typage Python | `make lint` | 0 erreur, 0 warning (pyright, mode `standard`) |
| Chaîne série → WAV | port série simulé, sinus 440 Hz 1 s | WAV 16 kHz / 16 bits / mono, 16 000 trames, crête 48,8 % |

Reste à valider **avec le matériel** : câblage, niveau du micro, intelligibilité.

## 7. Git

Dépôt initialisé le 2026-09-30 (branche `main`). Dépôt distant ajouté par l'utilisateur :
`origin` = https://github.com/deunix-educ/voice-assistant.git (commits et push : par l'utilisateur, ou à sa demande). Identité **locale au dépôt** :
Denis Defolie <denis.defolie@gmail.com>. Commits et push seulement à la demande de l'utilisateur.
Vérifié avant le premier commit : `secrets.h`, `.env`, `mosquitto/passwd`, `agent/agent.yaml`,
profils vocaux, enregistrements (wav/mp3), modèles, journaux et environnements virtuels sont ignorés.
`docs/test-suivi-perso.txt` (notes personnelles) ignoré à la demande de l'utilisateur ; ancien doublon
`firmware/secrets.h.example` (modèle périmé, sans vrai secret) supprimé à sa demande.

## 8. Journal

- **2026-09-27** — Création du dépôt, ossature complète, étape 1 écrite (I2S RX + `serial_to_wav.py`).
  Compilation, tests et typage vérifiés. En attente de validation matérielle par l'utilisateur.
- **2026-09-27** — Ajout de `docs/BROCHAGE.md` et `docs/wiring.svg` (schéma de câblage
  complet : micro, ampli, HP, bouton, LED, rails d'alimentation). Point ouvert reporté à
  l'étape 2 : si le niveau sonore est deux fois trop faible, forcer le mode « canal gauche »
  du MAX98357A par un 100 kΩ de `SD` vers GND.
- **2026-09-28** — `docs/BROCHAGE.md` §6 : types et valeurs des condensateurs
  (C1 = 470 µF 16 V électrolytique faible ESR, C2 = 100 nF X7R, C3 = 100 nF anti-rebond
  facultatif), bornes mini/maxi justifiées par le calcul et lecture du marquage à 3 chiffres.
  Schéma mis à jour : C1 et C2 dessinés côte à côte sur le `Vin` de l'ampli.
- **2026-09-29** — Matériel branché par l'utilisateur : le HP soufflait en permanence. Cause : à l'étape 1 les broches BCLK/LRC/DIN sont flottantes et le MAX98357A (SD tiré au + par 100 kΩ) amplifie ce bruit. Correctif : `silenceAmplifier()` dans `main.cpp`, appelé en tout premier dans `setup()`, force les trois broches à 0 → veille automatique de l'ampli. Firmware recompilé (SUCCESS). Reste le bruit pendant les ~300 ms de boot : option matérielle `SD` → GND, ou piloter `SD` par un GPIO à l'étape 2 (résout aussi le « pop » d'activation).
- **2026-09-29** — Premier enregistrement réel : crête sous 2 %. `MIC_GAIN` passé de 4 à **16** (dimensionnement : INMP441 à -26 dBFS/94 dB SPL, voix à 30 cm ≈ 80 dB SPL crête → -40 dBFS ≈ 1 % ; ×16 → ~16 %, soit 16 dB de marge). `tools/serial_to_wav.py` affiche désormais crête + RMS + offset continu en dBFS, distingue « aucun signal » (câblage) de « niveau faible » (gain) et propose le facteur correctif. 6 tests, pyright propre, firmware recompilé.
- **2026-09-29** — L'utilisateur confirme le câblage. Ajout d'un **auto-test matériel** dans le firmware : `MicCapture::probe()` / `stats()` relèvent min/max/zéros des **mots I2S bruts**, `reportMicHealth()` (main.cpp) imprime le verdict sur la série au démarrage (bruit ambiant) et à la fin de chaque session (parole, avec la valeur de `MIC_GAIN` conseillée). Distingue : aucun mot lu (I2S muet) / mots figés (L/R ou SD) / amplitude mesurable (question de gain). `make fw-upload && make fw-monitor` suffit désormais à diagnostiquer sans le PC côté WAV.
- **2026-09-29** — **Cause trouvée** : l'auto-test renvoyait 39 200 mots I2S *tous exactement nuls*, avec deux micros différents et un câblage vérifié. Signature de la lecture de la mauvaise moitié de trame : l'INMP441 sort des zéros pendant la moitié qu'il n'utilise pas, et la correspondance `L/R` ↔ `I2S_STD_SLOT_LEFT/RIGHT` n'est pas celle attendue sur l'ESP32 d'origine. Correctif : `MicCapture::useSlot()` (reconfiguration à chaud via `i2s_channel_reconfig_std_slot`) et `selectMicSlot()` dans `main.cpp`, qui écoute les deux moitiés au démarrage et garde celle qui porte du signal. **Décision figée : ne jamais supposer le mapping des slots I2S sur ESP32, le mesurer.**
- **2026-09-29** — Les deux moitiés de trame sont nulles → la piste « slot » ne suffit pas. Vérification faite dans les en-têtes ESP-IDF installés (`framework-arduinoespressif32-libs/esp32/include/esp_driver_i2s/.../i2s_std.h`) : la configuration Philips 32 bits mono est correcte pour l'ESP32, le bug n'est pas là. Deux corrections : (1) l'auto-test affichait `amplitude 0` sans distinguer « mots nuls » de « aucun mot lu » → il imprime désormais le **nombre de mots** ; (2) `selectMicConfiguration()` explore les **4 combinaisons** {SCK/WS normaux, permutés} × {slot gauche, droite}, car une permutation SCK/WS donne exactement le même symptôme (le micro reçoit 16 kHz au lieu de 1 MHz et n'émet rien). En cas d'échec des 4, le firmware imprime un protocole de mesure au voltmètre et laisse les horloges tourner sur les broches du schéma.
- **2026-09-29** — **Le micro capte.** Session réelle : 24 000 mots, amplitude 1 547 300 LSB24 (**-14 dBFS brut**), soit 6 044 LSB sur 16 bits avant gain. `MIC_GAIN` passé de 16 à **1** (crête ≈ 18 % de pleine échelle). Le calcul théorique datasheet (-26 dBFS @ 94 dB SPL → -40 dBFS attendus) sous-estimait le niveau réel de **26 dB**. **Décision figée : pour les niveaux audio, mesurer sur le banc, ne pas extrapoler du datasheet.** Point à confirmer : quelle combinaison a été retenue par `selectMicConfiguration()` (SCK/WS normaux ou permutés) — si permutés, corriger le câblage ou le schéma.
- **2026-09-29** — **Fausse piste corrigée.** L'auto-test avait retenu « SCK/WS permutés » avec une amplitude de 16,7 M LSB24, soit ~100 % de la pleine échelle 24 bits (8 388 608) **au repos** : ce n'était pas du signal mais du **bruit pleine échelle d'une ligne de données flottante**. Mon critère « garder la plus grande amplitude » était faux. Trois corrections : (1) `MicCapture` lit désormais en **stéréo** (`I2S_SLOT_MODE_STEREO`) et choisit la moitié de trame en logiciel — la sélection de slot par le périphérique est ambiguë sur l'ESP32 et testait probablement deux fois la même moitié ; (2) convention d'amplitude unifiée (crête = demi-écart crête à crête, en LSB24) ; (3) garde-fou `NOISE_FLOOR_LIMIT` = 50 % de pleine échelle : au repos, au-delà c'est une ligne flottante, la candidate est rejetée et le message désigne `SD`/alimentation. **Décision figée : un niveau proche de la pleine échelle au repos est un défaut, jamais un succès.**
- **2026-09-29** — Mesures utilisateur : **VDD = 3,29 V**, **GPIO 25 et 26 = 1,64 V** (≈ VDD/2, signature d'un carré 50 % → les deux horloges tournent et arrivent). Alimentation et horloges donc écartées ; seule reste la ligne `SD` → GPIO 33. Ajout de `MicCapture::probeDataLine()` : applique le pull-up puis le pull-down interne (~45 kΩ) et échantillonne la broche 4000 fois, horloges actives. Une sortie logique (quelques ohms) écrase le pull : si les lectures **suivent** le pull (100 % puis 0 %), la ligne flotte ; sinon elle est pilotée. `gpio_get_level()` fonctionne même quand la broche est routée vers l'I2S, d'où un test sans démonter la configuration.
- **2026-09-29** — Test électrique : « ligne PILOTEE ». Mais ce verdict confondait deux cas très différents : *ligne active* et *ligne figée à un niveau fixe* (un fil posé sur GND ou L/R donne aussi « pilotée »). Verdicts désormais séparés en quatre : flottante / tenue à 0 / tenue à 1 / porte des transitions. Ajout de `MicCapture::readRaw()` + `dumpRawFrames()` : vidage hexadécimal de 12 trames (les deux moitiés côte à côte). C'est la seule observation qui distingue un décalage de bits d'une ligne morte. **Les pourcentages pull-up/pull-down sont l'information clé et doivent être relevés.**
- **2026-09-29** — Lecture de la **structure** des relevés : en mode « permuté », une moitié de trame porte des données et l'autre est *exactement* nulle — signature d'un INMP441 correctement cadencé (`L/R` fixe la moitié d'émission). Du bruit de ligne flottante serait présent dans les deux moitiés. Donc le micro fonctionne, et les fils `SCK`/`WS` ne sont pas sur les broches du schéma. Deux corrections : (1) `MIC_SETTLE_MS = 250` — le transitoire de mise en route de l'INMP441 faussait toutes les mesures d'ambiance (0, puis -8, puis -15 dBFS d'un essai à l'autre) ; `begin()` jette désormais ce début ; (2) le firmware annonce les **GPIO réels** retenus pour SCK et WS au lieu du mot « permuté », et affiche les mots bruts si le niveau au repos dépasse 10 % de la pleine échelle. **Point ouvert : déterminer si c'est le câblage ou le schéma du dépôt qui doit changer.**
- **2026-09-29** — **Brochage corrigé.** Le banc est câblé `SCK`→GPIO 25 et `WS`→GPIO 26, soit l'inverse du schéma initial. GPIO 25 et 26 étant équivalents (aucune fonction de strapping, tous deux en sortie), la documentation a été alignée sur le matériel plutôt que l'inverse. Modifiés : `config.h`, `docs/wiring.svg`, `docs/wiring.png`, `docs/BROCHAGE.md` (§1.1, ASCII, §9, §10), `README.md`, `README-EN.md`, `tools/serial_to_wav.py`. Vérifs : firmware SUCCESS, 6 tests, pyright 0 erreur, SVG re-rendu et relu.
- **2026-09-29** — `aplay` échouait avec « unable to open slave » : rien à voir avec le WAV. Le PCM ALSA `default` passe par `dmix`, dont l'esclave est la **carte 0** = `C110`, un périphérique USB de **capture seule** (`pcm0c`, aucun `pcm*p`). D'où l'ENOENT. `paplay` (PulseAudio) fonctionne ; `aplay -D plughw:1,0` est refusé car PulseAudio tient la carte, et `aplay -D pulse` échoue (plugin `libasound2-plugins` absent). Ajout de `make play` (essaie paplay, pw-play, ffplay, aplay) et surtout de **`tools/wav_inspect.py`** + `make inspect` : enveloppe ASCII, crête/RMS/offset et détection d'alternance silence/parole — **valider un enregistrement sans écouter**, ce qui servira aussi sur le Raspberry Pi sans écran. 8 tests, pyright 0 erreur.
- **2026-09-29** — **Bug trouvé : perte d'alignement de trame.** L'auto-test voyait du signal mais le WAV enregistré était fait de zéros exacts. Cause : en lecture stéréo, je déduisais la moitié de trame de la **parité de l'indice dans le tampon** (`_scratch[i*2]` / `[i*2+1]`). Or `i2s_channel_read()` rend ce qui est disponible, pas forcément un nombre pair de mots — et l'ancien `flush()` (lectures à vide) pouvait s'arrêter au milieu d'une trame. Les deux moitiés s'échangeaient alors, et `read()` prenait la moitié muette. Corrections : (1) `_phase` mémorisée entre les appels et avancée **mot par mot** dans `read()`, `probe()` et `readRaw()` ; (2) `flush()` fait désormais `i2s_channel_disable()` + `enable()`, ce qui vide le DMA **et** garantit que le prochain mot est un début de trame ; (3) `begin()` termine par `flush()`. **Décision figée : ne jamais déduire un canal d'une parité d'indice sur une API en flux d'octets.**
- **2026-09-29** — Diagnostic enfin net et cohérent : test électrique **100 % / 0 %** → la ligne suit exactement le pull interne, donc **GPIO 33 flotte, rien ne le pilote**. Le « bruit pleine échelle » vu sur une moitié de trame est du couplage capacitif depuis les pistes d'horloge voisines, capté par une entrée non connectée — d'où son rejet par `NOISE_FLOOR_LIMIT`. Alimentation (3,29 V) et horloges (1,64 V) déjà validées au voltmètre : le seul maillon restant est le fil `SD` micro → GPIO 33. Correction d'un défaut de mon diagnostic : le vidage hexa s'exécutait sur la configuration *permutée* (dernière de la boucle) et montrait donc des zéros non représentatifs ; on revient maintenant au câblage documenté avant de diagnostiquer. Ajout d'un protocole de **continuité** (§8 de BROCHAGE.md), à mesurer sur la **pastille** du module et non au bout du fil.
- **2026-09-29** — Les 3 continuités sont bonnes (`SD` ↔ GPIO 33, `L/R` ↔ GND, pas de court-circuit). Toutes les hypothèses de câblage côté connexions sont donc épuisées. Deux pistes restantes : (a) les signaux atteignent l'embase ESP32 mais **pas les pastilles du module** (soudure sèche) — à vérifier en mesurant SCK/WS/VDD **sur le module** ; (b) l'horloge bit de 1,024 MHz (16 kHz × 64) est trop lente pour ce module : certains INMP441 de contrefaçon ne démarrent qu'au-dessus de ~2 MHz. L'auto-test balaie désormais **2 fréquences × 2 câblages × 2 moitiés**, et `startSession()` annonce la **fréquence réellement retenue** dans l'en-tête de session (le WAV reste donc correct si 48 kHz est nécessaire).
- **2026-09-29** — **Relecture complète de l'historique : j'avais tiré une conclusion fausse.**
  Le verdict « FLOTTE » (ligne SD) avait été mesuré alors que la configuration active était la
  dernière de la boucle d'essais, c'est-à-dire horloges **inversées** pour le câblage réel : micro
  non cadencé, donc muet. Le verdict « PILOTÉE » de l'essai précédent avait, lui, été mesuré
  horloges **correctes**. Conclusion juste : **le micro pilote bien sa ligne SD**. Les tests de
  continuité demandés ensuite étaient superflus (tous bons). J'avais repéré ce défaut pour le
  vidage hexa, pas pour le test électrique qui avait le même.
  Ce que tous les essais montrent de façon constante : **horloges correctes → une moitié de trame
  porte des valeurs énormes et erratiques au repos (0, -8, -14, -15 dBFS), l'autre est exactement
  nulle ; horloges inversées → tout est nul.** Hypothèses restantes, par ordre :
  (H1) **mot décalé d'un bit** (cadrage Philips vs MSB) : le bit 31 devient aléatoire et un signal
  faible ressemble à un bruit pleine échelle ;
  (H2) **quelques mots parasites** qui font exploser la mesure crête (max − min), métrique fragile ;
  (H3) **pull-down de 100 kΩ absent** sur SD, pourtant exigé par le datasheet (sortie haute impédance
  hors moitié de trame).
  Réponse firmware : nouvelle classe **`MicSelfTest`** (main.cpp passe de 437 à ~150 lignes) ;
  balayage {16 kHz, 48 kHz} × {Philips, MSB} × {2 moitiés} ; critère = **valeur efficace au repos**
  (< -20 dBFS) + **cohérence du bit de signe** (bit31 = bit30 dans > 99 % des mots), plus jamais
  l'amplitude crête ; **pull-down interne activé sur SD** (`MIC_SD_PULLDOWN`) ; analyse **bit à bit**
  (taux de 1 par position) + vidage hexa en configuration nominale ; test électrique refait
  **horloges correctes**. Le balayage SCK/WS permutés est supprimé : câblage vérifié.
- **2026-09-29** — **H1 observée directement : mot décalé d'un bit en cadrage Philips.**
  Analyse bit à bit (16 kHz Philips) : 1re moitié = `F806B200`… avec **9 zéros** en bas au lieu de 8
  (bit 8 à 0 sur 8 mots sur 8), 2e moitié = `00000001` en permanence. Lecture : le premier bit émis
  par le micro (signe) est échantillonné un coup d'horloge trop tard et **déborde dans le dernier bit
  du mot précédent** ; les 23 bits restants sont décalés d'un rang. Confirmation croisée : à 48 kHz
  en cadrage **MSB**, signe incohérent seulement 0,5 % et 2e moitié exactement nulle.
  **Bug de mon critère** : la 2e moitié constante `00000001` avait été retenue comme « plausible »
  (non nulle, RMS nulle après `>> 12`, signe cohérent) → un enregistrement aurait été silencieux.
  Corrections : refus des constantes (`minWord == maxWord`) ; statistiques séparant **composante
  continue** (`sum`) et **variations** (écart type) — seul l'écart type juge le bruit de repos ;
  plancher à -110 dBFS ; `MIC_SETTLE_MS` 250 → **600 ms** (l'INMP441 s'endort à chaque arrêt des
  horloges et chaque réveil laisse une composante continue lente, -30 dBFS encore visible à 250 ms) ;
  analyse bit à bit faite sur la configuration **retenue**, en régime établi (trames du milieu).
  **À confirmer par le prochain essai : cadrage MSB attendu comme configuration retenue.**
- **2026-09-29** — Essai suivant : **16 kHz Philips encore retenu à tort**, toujours décalé (9 zéros,
  `00000001` dans l'autre moitié). Mon test de cohérence du signe (bit31 = bit30) est **aveugle sur un
  signal faible** : toute la partie haute du mot vaut `1`, en perdre un ne change rien. Il ne détecte le
  décalage que sur un signal fort. Nouveau critère, indépendant de l'amplitude : **nombre de bits
  utiles** = 32 − ctz(OU de tous les mots) ; avec le pull-down, un INMP441 bien cadré donne
  **exactement 24**. Validé hors carte sur les mots réels du banc (g++) : Philips → 23 (décalé), mêmes
  échantillons recadrés MSB → 24. Valeurs recadrées ≈ −227 000 LSB24 (composante continue de réveil,
  ≈ −31 dBFS) avec des écarts de quelques milliers : audio sain. Mesures au repos en MSB : continu
  −43 dBFS, variations −44 dBFS (16 kHz). **Décision attendue : cadrage MSB retenu automatiquement.**
- **2026-09-29** — **Cadrage MSB confirmé** sur le banc : 24 bits utiles (8 zéros exacts), 2e moitié
  exactement nulle. Mais l'auto-test échouait : les mots au repos valaient +1 950 000 (**23 % de pleine
  échelle**) en montant d'échantillon en échantillon. Cause : **le micro se réveillait à chaque essai**.
  L'INMP441 s'endort dès que ses horloges s'arrêtent, et mon auto-test les arrêtait entre chaque
  configuration ; chaque mesure « au repos » mesurait donc un réveil, pas la pièce (d'où −43 dBFS un jour,
  −13 dBFS le suivant). Pire : `flush()` faisait `disable()`/`enable()`, donc **chaque session
  d'enregistrement aurait démarré par ce transitoire** (« boum » grave).
  Refonte : (1) **un seul réveil** au démarrage, lu en MSB, `MIC_SETTLE_MS` = 1500 ; (2) décision
  **purement structurelle** : moitié ni nulle ni constante, **24 bits utiles** → MSB ; **25** → micro
  Philips, bascule et revérification ; le niveau au repos n'est plus qu'une information ; (3) `flush()`
  vide le DMA **par lecture**, sans arrêter les horloges, en suivant la phase, et **amorce le filtre de
  continu** avec la moyenne mesurée ; (4) toutes les lectures traitent les mots reçus **avant** de tester
  l'erreur (une lecture interrompue ne décale plus la phase) ; (5) `readRaw()` rend toujours des trames
  entières ; (6) suppression du balayage 48 kHz et de l'incohérence de signe (détecteur aveugle au repos) ;
  (7) `delay(1500)` au démarrage pour que le moniteur série ne perde plus le début de l'auto-test.
  Hypothèse de robustesse notée : quand personne ne lit entre deux sessions, le DMA déborde ; les
  descripteurs contiennent des trames entières (200 × 2 mots) et les lectures sont de taille paire, la
  parité — donc l'alignement — est conservée.
- **2026-09-29** — **Auto-test réussi sur le banc** : 16 kHz, cadrage MSB, 1re moitié, 24 bits utiles,
  2e moitié strictement nulle, ligne SD pilotée. Mots bruts `FE466E00`… → échantillons ≈ −111 000 ± 2 000
  LSB24 (continu ≈ −37 dBFS). Niveau au repos mesuré −37 dBFS de variations pendant l'auto-test, mais
  l'analyse bit à bit faite juste après montre des échantillons confinés dans [−131 000 ; −98 000], soit
  ≈ −54 dBFS : le micro finissait de se stabiliser, 1,5 s après son réveil. Sans conséquence pour les
  sessions (horloges jamais arrêtées ensuite). **Reste à valider : enregistrement réel + `make inspect`.**
- **2026-09-29** — **Premier enregistrement audible** (entendu au casque ; `paplay` fonctionne, sortie
  analogique par défaut). Analyse spectrale du WAV (numpy) : **87 % de l'énergie sous 20 Hz**, voix
  réelle mais à −42 dBFS dans la bande 300–3 400 Hz. Chaîne de capture saine : aucun saut > 382 LSB,
  aucune longue suite de valeurs identiques, donc aucun glissement de phase. Cause la plus probable :
  mécanique (plaque d'essai qui fléchit sous le doigt tenant le bouton) + souffle. L'ancien filtre
  (`MIC_DC_SHIFT = 12`, 0,6 Hz) ne retirait que le continu. Simulation de plusieurs réglages sur le WAV
  réel : 1 étage à 80 Hz suffit (infrasons 0,6 %, voix 99,1 %) ; 2 étages n'apportent presque rien.
  Implémenté : `MIC_HPF_SHIFT = 5` (80 Hz), filtre calculé **en 24 bits** (en 16 bits, zone morte de
  31 LSB), gain appliqué avant la réduction 24 → 16 bits avec arrondi, **`MIC_GAIN = 4`** (mesuré après
  filtre). La recommandation de gain en fin de session porte désormais sur la **crête après passe-haut**
  (`RawStats::filteredPeak`), plus sur les mots bruts. Vérifié hors carte (g++, arithmétique identique au
  firmware) : écart à la théorie du 1er ordre < 0,3 dB, −29 dB à 3 Hz, < 0,4 dB au-dessus de 300 Hz.
  **Étape 1 : critère « audible, sans saturation » atteint ; confirmation attendue sur le firmware final.**
- **2026-09-29** — **Étape 1 validée par l'utilisateur** (« le son est vraiment bien »). Le WAV validé
  provient encore de l'ancien firmware (dérive continue +0,3 % → +4,6 %, impossible avec le passe-haut
  à 80 Hz) : le filtre 80 Hz sera vérifié sur le matériel avec le firmware de l'étape 2.
- **2026-09-29** — **Étape 2 écrite.** Firmware : `AudioOutput` (I2S1 TX, 16 bits stéréo dupliqué,
  Philips par défaut via `AMP_FORMAT_PHILIPS`, `auto_clear`, bip 440 Hz −12 dBFS au démarrage = test de
  la sortie seule), `AudioFifo` (StreamBuffer statique 16 Ko, tout ou rien), `SerialFrameReader`
  (machine à états), tâche audio statique cœur 1 priorité 5, `SerialProtocol.h` partagé ;
  `silenceAmplifier()` supprimé (l'I2S tient les broches dès la 1re ligne de `setup()`) ; `loop()` sert
  la lecture même si le micro est en défaut. PC : `serial_protocol.py` extrait, `wav_to_serial.py`
  (WAV ou `--tone`, avance 200 ms puis 1 chunk / 50 ms), cibles `make play-esp` et `make tone-esp`.
  Vérifs : firmware SUCCESS 0 warning (RAM 17,4 %) ; **test croisé** encodeur Python → décodeur C++
  compilé sur PC (texte parasite, faux mot magique, somme fausse, longueur excessive : 4 trames
  valides, 2 rejetées) ; outil PC sur pseudo-terminal : 4 chunks d'avance puis 50 ± 1 ms ; 15 tests
  pytest (dont un qui a trouvé un vrai bug : dernier chunk de longueur impaire), pyright 0 erreur.
  **Non vérifiable sans matériel** : cadrage Philips réel du MAX98357A côté ESP32, bruit de
  l'ampli cadencé en permanence sur le micro, absence de coupure sur la carte.
- **2026-09-29** — **Étape 2 validée par l'utilisateur** : bip, son pur sans clic, lecture de la voix,
  contrôle du micro — tout passe. Seul point : **niveau du HP faible mais audible**. Explication :
  bip à −12 dBFS par choix, et le WAV rejoué (étape 1, ancien firmware, gain 1) avait sa voix vers
  −30 dBFS crête. Pistes, sans code : mesurer `Vin` sur la pastille de l'ampli (≥ 4,5 V), rejouer un
  enregistrement fait avec le firmware actuel (gain 4 → +12 dB), `GAIN` à GND (+6 dB, 15 dB),
  enceinte pour le HP. Correctif de fond prévu : **normaliser côté serveur** l'audio envoyé (étape 5,
  puis la voix Piper à l'étape 6), l'ESP32 restant simple. Prochaine étape : 3 (Mosquitto + topics).
- **2026-09-29** — **Étape 3 écrite.** Constat en arrivant : le broker du conteneur
  `mosquitto_services-mosquitto-1` redémarrait en boucle (**378 redémarrages**). Sa configuration,
  écrite à la main hors du dépôt, déclarait **deux `listener 1883`** : `Error: Address in use`,
  reproduit dans un conteneur jetable. Le journal restait muet car `log_dest syslog` (dans MA
  `voice.conf`) n'écrit nulle part dans un conteneur ; `message_size_limit` était aussi obsolète.
  L'IP donnée par l'utilisateur (192.168.0.104) ne correspond pas à la machine : **192.168.1.104**.
  `voice.conf` corrigé (`log_dest stdout`, `max_packet_size`, pièges documentés) et **testé dans un
  broker jetable** : état `online` retenu, testament `offline` publié à la coupure brutale du
  client, et reçu par un abonné arrivé après coup. Le conteneur de l'utilisateur n'a **pas** été
  modifié (hors dépôt, fichiers de l'uid 1883) : cible `make mosquitto-docker` fournie.
  Firmware : `WifiLink`, `MqttLink` (PubSubClient 2.8 épinglé, tampon 2048, keepalive 10 s, socket
  2 s, essais toutes les 5 s), état JSON retenu toutes les 30 s (IP, RSSI, uptime, micro, ampli),
  événement `boot` avec la cause du redémarrage (dont `brownout`), abonnement `control` journalisé ;
  secrets transmis par constructeur, `#error` explicite si `secrets.h` manque ; Wi-Fi démarré
  **après** l'auto-test du micro. Partitions `huge_app` (Flash 74,5 % → 31 %). Build SUCCESS
  0 warning (avec un `secrets.h` temporaire, supprimé). Cibles `mqtt-watch`, `mqtt-control`
  (clients de l'image Docker si `mosquitto-clients` absent). `docs/MOSQUITTO.md` créé.
  Correction faite dans la doc : un reset d'ESP32 ne ferme PAS la connexion TCP (testament en ≤ 15 s).
- **2026-09-29** — Étape 3, premiers résultats : broker réparé par l'utilisateur **avec sa propre
  config** (pas `voice.conf` : `log_dest file`, `log_type all`, `message_size_limit`) — fonctionne,
  mais `log_type all` journalisera chaque chunk audio à l'étape 4 → passer à `voice.conf` avant.
  ESP32 : Wi-Fi « BPI_R3-2G » en 4,8 s, IP 192.168.1.23, **RSSI −76 à −78 dBm (faible)**, MQTT connecté.
  Vérifié directement sur le broker : un abonné tardif reçoit aussitôt l'état retenu (uptime 158 s),
  republié 30 s plus tard (188 s). Auto-test micro : variations au repos −46 dBFS (Wi-Fi démarré
  après). Restent à confirmer : message `control` reçu, `offline` après coupure d'alimentation.
- **2026-09-29** — **Étape 3 validée par l'utilisateur.** `mqtt-watch` : état périodique, `control`
  relayé par le broker, `offline` publié par le testament à la coupure d'alimentation, puis `online`
  (uptime 12 s) et `event boot reason=poweron` au retour. Avant l'étape 4 : passer le broker sur
  `voice.conf` (`make mosquitto-docker`) et améliorer le RSSI (−75 à −79 dBm, viser > −70).
- **2026-09-29** — **Étape 3 validée ; étape 4 écrite.** Firmware : `VoiceSession` (START / chunks /
  END en JSON sur `event`, PCM brut sur `audio/in`, id de session 6 hex), **tâche de capture** dédiée
  + FIFO de capture 32 Ko (1 s) : `loop()` ne lit plus le micro, il vide la FIFO vers MQTT ; `AudioFifo`
  à stockage fourni ; `MqttLink::publishBinary`. RAM 36,7 %, Flash 31,1 %, 0 warning.
  Serveur (premier code) : `settings`, `mqtt_link`, `audio_buffer`, `session_manager`, `__main__` ;
  `make run`. Verdicts : complete, lost_chunks, timeout, interrupted. 30 tests pytest, pyright 0.
  **Essai de bout en bout sur le vrai broker** avec une carte simulée (paho, rythme réel) :
  complete 40/40 ✓, timeout 20/? ✓, mais la session à 3 chunks perdus était classée `interrupted`
  (nouveau START arrivé pendant le délai de grâce) → **bug corrigé**, test de régression ajouté,
  scénario rejoué : `lost_chunks 37/40` + alerte, puis session suivante `complete`.
  Pas de `tools/mqtt_to_wav.py` : le serveur remplit ce rôle ; à réévaluer avec `wav_to_mqtt.py` (étape 5).
- **2026-09-29** — **Incident causé par moi : `firmware/include/secrets.h` de l'utilisateur détruit.**
  Mes compilations de contrôle faisaient `cp secrets.h.example secrets.h ; make fw-build ; rm -f secrets.h`.
  Sans conséquence à l'étape 3 (fichier pas encore créé) ; à l'étape 4, la copie a écrasé le vrai
  fichier puis `rm` l'a supprimé (dossier modifié à 17:02). Symptôme chez l'utilisateur :
  `'WIFI_SSID' was not declared` (le `#error` s'affiche bien, mais GCC continue et ajoute cette
  erreur). **Correctif de méthode** : cible `make fw-check`, secrets factices dans un `mktemp -d`
  via `PLATFORMIO_BUILD_FLAGS=-I<tmp>`, `firmware/include/` jamais touché. Règle aussi notée en
  mémoire. L'utilisateur doit recréer son `secrets.h` (SSID relevé dans ses journaux : « BPI_R3-2G »,
  broker 192.168.1.104 déjà dans le modèle).
- **2026-09-29** — **Étape 4 validée** : 4 phrases de 4 à 6 mots, intelligibles au casque. Analyse des
  4 WAV serveur : 1,8 à 4,35 s, nombres entiers de chunks (36, 50, 87, 56), crête −10,4 à −15,3 dBFS
  (gain ×4 dans la cible), **offset ≈ 0 %** et infrasons 0 à 0,3 % (contre +3 % et 87 % avant) :
  **passe-haut 80 Hz validé sur le matériel**. Voix 99,4 à 99,9 % de l'énergie.
  Prochaine étape : 5 (serveur → MQTT → ESP32).
- **2026-09-29** — **Étape 4 validée ; étape 5 écrite.** Firmware : `PlaybackStream` (lecture série ou
  MQTT, tâche audio, bilan `played` publié sur `event`), `CommandHandler` (start/end/ping, ArduinoJson
  7.2.1), abonnement `audio/out`. Build 0 warning, RAM 36,7 %. Serveur : `AudioResampler` (8/16/24/32
  bits, toute fréquence, mono, soxr HQ, crête −3 dBFS), `AudioSender` (300 ms d'avance, rythme réel),
  `MqttLink.publish/wait_connected`, `SessionManager(on_finished=...)`, `--echo` (fil de lecture dédié,
  hors fil paho). Outil `tools/wav_to_mqtt.py`, cibles `tone-mqtt`, `play-mqtt`, `run-echo`.
  41 tests (dont un qui a trouvé qu'un WAV tronqué levait `EOFError` au lieu du message ffmpeg), pyright 0.
  **Essais de bout en bout sur le broker réel** (carte simulée « esp32-test ») : son pur 40/40, écart max
  51 ms, crête −3,0 dBFS, bilan reçu ; écho : session 30/30 à −24,3 dBFS renvoyée à −3 dBFS, 30/30.
  **Incident causé par moi** : mon serveur de test a repris l'identifiant `voice-server` du `make run`
  de l'utilisateur (lancé à 17:16) → ~16 s d'éjections mutuelles ; son serveur a enregistré
  `server/recordings/esp32-test_20260929-173525_voix01.wav` (artefact de test, 0,5 s, supprimable).
  Correctifs : identifiant de test distinct, indice « identifiant en double ? » dans le journal de
  déconnexion, règle mémorisée. Le `make run` de l'utilisateur tourne encore l'ancien code (étape 4).
- **2026-09-29** — Étape 5, premier essai utilisateur : écho une fois (phrase coupée), puis rien. Journal
  serveur : session `39ecba` ouverte et close en **72 ms, 0 chunk** → le bouton est lu relâché pendant
  l'appui (contact intermittent sur la plaque ? carte déplacée pour le Wi-Fi ?). Le serveur **ignorait en
  silence** les sessions < 0,2 s pour l'écho (défaut de ma part). Ligne `en ligne` 12 s après la
  session : republication périodique ou **redémarrage** — indécidable, le journal n'affichait pas
  l'uptime. Instrumentation ajoutée : raison de fin de session (« bouton relache » / « duree
  maximale »), alerte si session < 300 ms, purge des événements de bouton périmés (un appui mémorisé
  pendant une session relançait une session fantôme), uptime dans le journal d'état serveur,
  événement `boot` journalisé avec sa cause (WARNING si brownout/panic/watchdog), écho ignoré annoncé.
- **2026-09-29** — Étape 5, second essai (instrumenté) : **diagnostic Wi-Fi confirmé par les chiffres**.
  RSSI −81 à −75 dBm. Session 833429 : ~108 chunks captés, 42 envoyés, **66 perdus faute de place**
  (FIFO de capture 1 s débordée : envoi Wi-Fi bloqué plusieurs secondes) ; écho 11/42 chunks, END
  perdu (reconnexion MQTT pendant l'écho : `offline` puis `online` 130 ms après, uptime continu 102→107 s,
  donc **pas de redémarrage**). Sessions suivantes 55/55 et 76/76 (4 perdus), échos complets mais 35 et
  107 sous-alimentations (≈ 2 s de trous sur 3,8 s). Serveur toujours à l'heure (retard d'envoi 0 ms).
  Bouton : plus aucun problème (appuis de 2 à 5 s). **Décision : ne pas masquer côté logiciel** (le
  tampon anti-gigue est l'étape 12) ; expérience demandée : ESP32 à 1–2 m du point d'accès. Cibles :
  > −65 dBm fiable, −65 à −72 sous-alimentations ponctuelles, < −72 pertes. Piste matérielle si
  l'emplacement ne peut pas changer : ESP32-WROOM-32U + antenne externe.
- **2026-09-29** — **Étape 5 validée** (« Echo est bon maintenant ») après **changement de SSID** par
  l'utilisateur. RSSI −69 à −72 dBm (à peine mieux que −75/−81), même IP 192.168.1.23 (même réseau :
  autre point d'accès ou autre canal). État stable 7 min, aucune reconnexion. Leçon : le canal / la
  congestion 2,4 GHz compte autant que le RSSI. Chiffres de lecture (`played`) non fournis.
  Prochaine étape : 6 (MVP : faster-whisper → réponse → Piper → ESP32).
- **2026-09-29** — **Étape 6 écrite** (serveur seul, firmware inchangé). `speech_to_text.py`,
  `text_to_speech.py`, `assistant.py`, `voice_pipeline.py` ; `__main__.py` : mode assistant par
  défaut, écho avec `--echo`. `config.yaml` : `stt.model small`, `beam_size`, `no_speech_threshold`,
  `min_peak_dbfs`, `tts.length_scale` (clé `output_sample_rate` retirée : c'est `audio.sample_rate`).
  Extra `speech` installé dans `server/.venv` et modèles téléchargés (549 Mo) par moi.
  Mesures : Piper 0,04 à 0,10 s pour 1 à 3 s de parole ; Whisper ≈ 1,0 s ; `make speech-check` →
  « Quelle heure est-il ? » relu exactement, réponse prête en 1,04 s de calcul. Les 3 derniers
  enregistrements réels de l'ESP32 (`server/recordings/`) transcrits sans faute (« Fermez le volet
  de la salle de bain. »). 65 tests (dont 3 avec les vrais modèles, ignorés sans `make models`),
  pyright 0 erreur. Délai perçu attendu ≈ 1,1 s de calcul + 0,3 s d'avance d'envoi. Pas d'essai
  bout en bout sur le broker réel (éviterait deux serveurs qui répondent à la même carte).
  En attente de validation matérielle par l'utilisateur (`make run`).
- **2026-09-29** — **Étape 6 validée** par l'utilisateur (« Dialogue très correct »). 9 questions
  d'affilée, toutes les sessions `complete`, toutes les lectures `chunks N/N` :
  heure, date, bonjour, nom → bonnes réponses ; phrases libres répétées. Réponse prête **0,95 à
  1,11 s** après la fin de session (Whisper 0,89–1,06 s, Piper 0,05–0,11 s), pire retard d'envoi 0 ms.
  Appui sans parler : crête −30,7 dBFS (bruit du bouton, au-dessus du seuil de −45) → Whisper appelé,
  hallucination « Amara.org » **écartée par le filtre**, réponse « Je n'ai rien entendu. ».
  Points notés pour plus tard :
  (a) **sous-alimentations 6 à 9 par lecture même sans perte**, 25 et 35 quand le RSSI tombe à −83 dBm.
  La base 6–9 vient probablement du démarrage : la tâche audio vide chaque chunk dès son arrivée
  (DMA encore vide) et compte chaque bloc incomplet ; il n'y a pas de seuil de démarrage → **étape 12**
  (lecture lancée seulement après l'avance de 300 ms, sous-alimentations comptées après) ;
  (b) session muette : RMS −46,8 dBFS contre −22 à −28 pour la parole → la VAD de l'**étape 7**
  remplacera le seuil de crête ; (c) « fermé la porte » pour « fermez » : homophone, l'**étape 11**
  devra tolérer ce genre d'écart ; (d) 7 chunks perdus faute de place à la capture (Wi-Fi).
  Prochaine étape : 7 (VAD Silero). Silero est déjà livré par faster-whisper
  (`faster_whisper/assets/silero_vad_v6.onnx`, exécuté par onnxruntime) : pas besoin de torch.
- **2026-09-29** — **Étape 7 écrite** (serveur seul, firmware inchangé). `voice_activity.py`
  (`find_segments`, `pad_segments`, `VoiceActivityDetector`, `envelope`), branché dans `VoicePipeline`
  avant Whisper ; `tools/vad_check.py` (`make vad`, `make vad-all`) ; `speech_check --wav` passe par la VAD.
  Config : section `vad` (clé `window_samples` retirée : 512 est imposé, constante `WINDOW_SAMPLES`),
  `stt.min_peak_dbfs` supprimé. Mesures sur les 27 enregistrements du serveur : 6 sans parole, tous
  à juste titre (3 sessions vides de l'étape 5, l'appui muet `0137fe`, mes 2 sons purs `esp32-test`) ;
  pour les 9 questions de l'étape 6, 8 à 37 % de silence retiré, l'utilisateur parle ~0,5 s après
  l'appui. **Gain inattendu** : `c5a22c`, transcrit « fermé la porte » à l'étape 6, devient
  « fermez la porte » une fois le silence retiré. Appui muet : réponse prête en 0,03 s au lieu de 1,11 s.
  `c5a22c` commence à 0,00 s : parole dès l'appui, début de mot possiblement coupé (piège à signaler).
  78 tests, pyright 0 erreur. Pas d'essai sur le broker : le `make run` de l'utilisateur (étape 6,
  lancé à 19:26) tournait encore ; vérification hors ligne de `make_assistant` sur 2 vraies sessions.
  En attente de validation par l'utilisateur.
- **2026-09-29** — **Étape 7 validée** par l'utilisateur (« Semble fonctionner »). 6 sessions :
  segments de parole trouvés partout (jusqu'à 3 segments, pauses de 0,6 à 0,95 s conservées en une
  seule transcription), appui muet (crête −32,4 dBFS) rejeté par la VAD, réponse prête en **0,07 s**.
  VAD 7 à 11 ms (25 ms au premier appel). Erreur « 4 jours, sommes-nous ? » pour « Quel jour
  sommes-nous ? » (`ce69b6`, pause de 0,6 s après « jour ») : **pas due à la VAD** — sur l'audio brut,
  Whisper écrit « qu'à jour, sommes-nous ». Essai `initial_prompt` (phrases types de l'assistant) :
  corrige `ce69b6` mais dégrade `a2428a` (« quelles jours ») → **non retenu** ; à reconsidérer à
  l'étape 11 (vocabulaire des intentions, règles tolérantes : « jour » + « sommes ») ou modèle
  `medium` à l'étape 16. Prochaine étape : 8 (diarisation pyannote).
- **2026-09-29** — **Étape 8 écrite** (serveur seul). Installé par moi dans `server/.venv` :
  pyannote.audio 4.0.7 + torch CPU (`.venv` = 1,9 Go), numpy monté à 2.5.3 ; `pip check` propre.
  `diarization.py` (`Diarizer`, `Diarization`, `SpeakerTurn`, `timeline`, `disable_telemetry`),
  branché dans `VoicePipeline` après la VAD ; `make install-diarization`, `make diarize`,
  `make diarize-demo` (voix de l'utilisateur + Piper + voix de l'utilisateur) ; `make models`
  télécharge le pipeline si `HF_TOKEN` est dans `server/.env`, sinon explique la démarche et rend 1.
  **Découverte** : pyannote 4 a une télémétrie active par défaut vers otel.pyannote.ai → coupée.
  **Non vérifié** : le vrai pipeline n'a pas tourné, `server/.env` n'existe pas (pas de jeton). J'ai
  relu le code de pyannote là où le mien s'y appuie : entrée `{"waveform": (1, n), "sample_rate"}`
  acceptée, silence → `DiarizeOutput` vide sans erreur, `speaker_embeddings` (n, dim).
  84 tests + 1 ignoré (modèle réel), pyright 0 erreur. En attente : jeton de l'utilisateur, puis validation.
- **2026-09-29** — Modèle pyannote obtenu : premier refus `GatedRepoError` car le compte HF du jeton
  (type « read », valide) n'avait pas accepté les conditions ; réglé par l'utilisateur. `make models`,
  `make test` (85, vrai pipeline compris) et `make diarize-demo` OK : **2 locuteurs** trouvés dans
  l'ordre vous / Piper / vous (0,08-1,55 · 1,75-5,58 · 5,82-7,25 s), **0,47 s** pour 7,35 s de parole.
  Avertissement torch `std(): degrees of freedom` (morceau trop court) masqué dans `Diarizer.diarize`.
  Question seule en `make run` : OK selon l'utilisateur. Reste : une session réelle à deux voix.
- **2026-09-29** — **Test réel à deux voix réussi** (session `453cce`, 11,85 s, au micro de l'ESP32) :
  l'utilisateur, une voix Piper jouée par un haut-parleur, l'utilisateur. VAD : 3 segments, 6,34 s de
  parole. Diarisation : SPEAKER_00 0,08-1,50 · SPEAKER_01 1,57-4,69 · SPEAKER_00 4,92-6,17 — les deux
  phrases de l'utilisateur portent la même étiquette, et les bornes coïncident avec celles de la VAD
  (1,61 et 4,89 s dans l'audio sans silences). Diarisation 0,46 s. **Délai : réponse prête en 2,04 s**
  (VAD 0,02 + diarisation 0,46 + Whisper 1,34 pour 6,3 s + Piper 0,21) : la diarisation s'ajoute à
  Whisper → candidate à la parallélisation (étape 16). « la roi de synthèse » : voix passée par un
  haut-parleur puis le micro. Validation explicite de l'étape 8 non encore donnée.
- **2026-09-29** — **Étape 8 validée** par l'utilisateur. Étape 9 lancée avec **les empreintes de
  pyannote** (choix de l'utilisateur, après comparaison avec SpeechBrain ECAPA : précision publiée
  du même ordre ; SpeechBrain n'apporterait que l'indépendance vis-à-vis de la diarisation, au prix
  d'un modèle, d'une dépendance et d'un risque de compatibilité avec torchaudio 2.11).
- **2026-09-29** — **Étape 9 écrite** (serveur seul). Mesure préalable : les empreintes rendues par
  pyannote sont **NaN pour toutes les questions courtes** (cf. décisions) → empreinte recalculée par
  locuteur avec le sous-modèle WeSpeaker. `speaker_identifier.py` (`SpeakerIdentifier`,
  `VoiceProfile`, `Identification`, `save_profile`, `load_profiles`), `tools/enroll.py`,
  `make enroll/profiles/forget`, identification affichée par `make diarize` ; `VoicePipeline` journalise
  `SPEAKER_00 = Nom (score)`. `diarize-demo` choisissait `453cce` (deux voix) puis `093463` (mélange) :
  il prend maintenant la question la plus récente de 1 à 4 s à une seule voix (`--voice` pour imposer).
  Avertissements numpy « Mean of empty slice » (empreintes NaN internes de pyannote) masqués.
  Essai complet **dans le scratchpad** (aucun profil écrit dans `server/profiles/`) : profil « Essai »
  sur 10 sessions → 6 retenues (2 à deux voix, 1 muette, 1 mélangée écartées), cohérence 0,79–0,86 ;
  `c5a22c` → Essai 0,74 ; `453cce` → Essai 0,72 / haut-parleur Inconnu −0,05 ; démo → Essai 0,84 /
  Piper Inconnu −0,02. Délai : + ~0,5 s de diarisation et d'empreinte (réponse prête 1,47 s pour une
  question de 1,3 s). 98 tests, pyright 0 erreur. En attente : enrôlement réel et validation.
- **2026-09-30** — Fichiers de test pour l'étape 9 (demande de l'utilisateur, à jouer depuis son
  téléphone) : `recordings/voix-test/{1-tom,2-pierre,3-jessica,4-gilles,5-siwis}.{wav,mp3}`, 5 voix
  Piper différentes (tom-medium, upmc-medium pierre et jessica, gilles-low, siwis-medium), 0,5 s de
  silence avant et après, crête −3 dBFS, toutes relues correctement par Whisper. Voix téléchargées
  dans le scratchpad puis supprimées (rien ajouté à `server/models/`). `fr_FR-mls-medium` écartée :
  aucun de ses 125 locuteurs n'est transcrit correctement. **Mesure à retenir** : Jessica et Siwis
  (deux voix féminines de synthèse) se ressemblent à **0,60** (fichier contre fichier), au-dessus du
  seuil de 0,45 ; les autres paires 0,01–0,37. Le seuil devra peut-être monter si une voix étrangère
  est reconnue comme l'utilisateur (ses propres sessions : 0,52–0,83, marge faible).
- **2026-09-30** — **Étape 9 validée** par l'utilisateur (« Tout semble marcher super bien »).
  6 profils enrôlés par l'utilisateur : Denis (5 sessions) et les 5 voix de synthèse jouées au
  téléphone (Gilles 3, Jessica 3, Pierre 4, Siwis 3, Tom 5). Essai réel : Denis 0,74 et 0,78 ;
  Tom 0,95, Gilles 0,96, Siwis 0,96, Jessica 0,93, Pierre 0,92 — chaque voix trouvée, Jessica et Siwis
  (0,60 entre elles) bien départagées par le meilleur score. Réponse prête en 1,5 à 1,8 s
  (diarisation + empreinte ≈ 0,45 s). **Limites à garder en tête** : (1) les scores de 0,92–0,96 des
  voix de synthèse sont gonflés — même fichier rejoué à l'enrôlement et au test, audio quasi identique ;
  le vrai test de généralisation est Denis (phrases nouvelles, 0,74–0,78) ; (2) le cas « Inconnu »
  n'a pas été rejoué en réel ce matin (toutes les voix étaient enrôlées) ; il a été vérifié hier
  (haut-parleur −0,05, Piper −0,02) ; (3) seuil 0,45 à recalibrer avec de vraies personnes (étape 14).
  « Quel ton fera-t-il » pour « temps » : Whisper sur une voix de téléphone. Prochaine étape : 10.
- **2026-09-30** — **Étape 10 écrite** (serveur seul). `Word` et `Transcript.words` (Whisper,
  `word_timestamps`), `speaker_attribution.py` (`turn_of`, `attribute`, `Utterance`,
  `transcript_json`), `VoicePipeline` publie sur `voice/<carte>/transcript` et passe le nom de la
  dernière personne à l'assistant ; `make diarize` affiche qui a dit quoi et le message MQTT.
  Essai hors ligne sur `453cce` avec les profils réels de l'utilisateur : Denis « Ouvrir la porte. »,
  **Siwis** (juste : c'était sa voix au haut-parleur) « Je suis la roi de synthèse… », Denis « Fermez la
  porte. », bornes 0,00-1,26 / 1,50-4,46 / 4,92-5,86 s. 110 tests, pyright 0 erreur.
  En attente : essai sur la carte et validation.
- **2026-09-30** — **Étape 10 validée** par l'utilisateur (« continuer maintenant »). Essai carte :
  Denis 0,68 / 0,79, Jessica 0,92, interventions et textes justes ; « Bonjour » seul (1,06 s de parole)
  → Inconnu 0,32 → salutation générique (piège annoncé : phrase trop courte pour une empreinte).
  Session à plusieurs voix non refaite sur la carte (vérifiée hors ligne sur `453cce`).
  `make mqtt-watch` affichait l'audio binaire (`voice/#`) : limité aux topics texte (state, event,
  control, transcript) ; ajout de `make mqtt-transcript`. Prochaine étape : 11 (intentions + domotique).
- **2026-09-30** — **Étape 11 écrite** (serveur + firmware). Choix utilisateur : commandes vers
  `home/…` **et** exécution par la carte pour sa pièce ; pas de domotique réelle (simulation).
  Serveur : `home_control.py`, `Assistant.respond()` → `Reply(text, command)`, `HomeController`,
  `MqttLink.publish_topic`, section `home` de config.yaml (pièces + `boards: esp32-01: salon`),
  journal des confirmations `device`. Firmware : `DemoDevices`, `CommandHandler` gère `cmd: device`,
  `PIN_DEMO_LIGHT = 21` ; `make fw-check` SUCCESS, 0 warning (RAM 36,7 %, Flash 31,4 %).
  `make mqtt-watch` inclut `home/#` ; `make mqtt-home`. `docs/BROCHAGE.md` : LED GPIO 21 + 330 Ω
  (non dessinée sur `wiring.svg`). Essai hors ligne sur 3 enregistrements réels : volets du salon
  fermés/ouverts → `home/salon/shutter/set` + `esp32-01/control` ; salle de bain → `home/…` seul.
  130 tests, pyright 0 erreur. **Firmware non téléversé** (à faire par l'utilisateur). En attente de validation.
- **2026-09-30** — **Étape 11 validée** par l'utilisateur (« Semble fonctionner correctement »),
  firmware téléversé. « Éteint la lumière » (graphie Whisper) et « Allume la lumière » →
  `home/salon/light/set` + `esp32-01/control`, **confirmation de la carte 40 ms après la commande**
  (`{"event":"device","ok":true,"state":"off"}`), puis la réponse parlée ; « Quel temps fait-il ? » →
  réponse « sans Internet ». Réponse prête en 1,41 à 1,55 s. `mqtt-watch` lisible : transcript,
  commande, confirmation, START/END de lecture, `played`. **Sous-alimentations toujours 6 à 10 par
  lecture, sans aucune perte** (`chunks 34/34`) : c'est l'objet de l'étape 12. Prochaine étape : 12.
- **2026-09-30** — Exigence utilisateur : commander des machines Linux → **étape 15b** (décidé).
- **2026-09-30** — **Étape 12 écrite**. Firmware : `PlaybackStream` devient une machine à états
  (Idle / Priming / Playing) dans la tâche audio ; `PLAYBACK_START_MS = 300` ; bilan `played` enrichi
  (`start_ms`, `min_margin_ms`, `underruns` = réamorçages) ; `make fw-check` SUCCESS, 0 warning
  (RAM 36,7 %, Flash 31,4 %). Cause probable des 6 à 10 « sous-alimentations » : la tâche jouait dès
  le premier chunk, sans attendre l'avance envoyée par le serveur, et comptait chaque bloc incomplet.
  Serveur : journal `played` enrichi (WARNING s'il y a des réamorçages) ; banc `JitteryLink` dans
  `tools/wav_to_mqtt.py`, `JITTER` dans le Makefile, `tone-mqtt` passe à 5 s. 131 tests, pyright
  0 erreur. **Non vérifié sur la carte** (je n'envoie pas de son sur le matériel de l'utilisateur) :
  firmware à téléverser, puis essais `JITTER=0/200/400`. En attente de validation.
- **2026-09-30** — Premiers essais de l'étape 12 sur la carte (`tone-mqtt` 5 s, JITTER 0 / 200 / 400) :
  marge mini **204 / 142 / 2 ms** (suit bien la gigue), réamorçages **1 / 1 / 2**, premier son à
  **214–218 ms**. Deux écarts avec la prévision : (1) **un réamorçage systématique**, même sans gigue,
  hors de la marge mesurée ; (2) l'avance de 300 ms n'arrive pas d'un bloc mais en ~200 ms (hypothèse :
  fenêtre TCP de lwIP ~5,7 Ko < 6 chunks de 1,6 Ko, la carte lit au rythme de PubSubClient).
  Instrumentation ajoutée plutôt que supposition : `gaps_ms` (instant de chaque réamorçage depuis le
  premier son) dans le bilan `played`, la série et le journal serveur. En attente d'un nouvel essai.
- **2026-09-30** — **Cause du réamorçage systématique trouvée grâce à `gaps_ms`** : 1er essai après
  démarrage 0 réamorçage ; 2e essai `gaps_ms:[28043]` = uptime de la carte, donc compté avant le premier
  son du flux (`_firstSoundMs` = 0). **Faux compte, inaudible** : après un flux, la tâche restait en
  Playing, bloquée ≤ 20 ms dans `read()` ; le START suivant arrivait pendant ce temps, le `read()`
  rendait 0 octet, `_endOfStream` venait d'être remis à faux → famine comptée pour un flux pas encore
  commencé. Correctif : passage à **Idle** quand la FIFO est vide après le END, famine comptée
  seulement par **compare-and-swap Playing → Priming** (échoue si `open()` a déjà changé l'état), et
  `open()` met l'état à Priming **avant** de remettre les compteurs à zéro. `make fw-check` SUCCESS.
  Premier son à 214 ms : l'avance arrive étalée (fenêtre TCP de lwIP probable) ; à revoir à l'étape 16
  (latence), sans effet sur la fluidité. Marge mini 188–204 ms sans gigue.
- **2026-09-30** — Essai final de l'étape 12 (firmware corrigé) : `tone-mqtt` ×3 → **0 réamorçage,
  `gaps_ms` vide** à chaque fois (le cas qui échouait) ; JITTER=200 → **0**, marge mini 162 ms ;
  JITTER=400 → 1 réamorçage **réel** à 364 ms du premier son (1er blocage), marge 2 ms. Premier son
  212–221 ms. Critères de l'étape 12 atteints ; validation explicite de l'utilisateur en attente.
- **2026-09-30** — **Étape 12 validée** par l'utilisateur. Étape 13 lancée : mots de réveil anglais
  prêts à l'emploi **alexa, hey_jarvis, hey_mycroft** (les trois ; alexa si un seul).
- **2026-09-30** — **Étape 13 écrite** (serveur + firmware). openWakeWord installé `--no-deps` dans
  `server/.venv`, modèles dans `server/models/openwakeword/` (`make models`). Serveur : `wake_word.py`,
  `hands_free.py`, `make_listener` dans `__main__.py` (état/événements composés, balayage, arrêt qui
  coupe l'écoute), section `wake` de config.yaml, extra `wakeword` + `make install-wakeword`.
  Firmware : `cmd listen` / `cmd capture`, flux continu dans `loop()` (`updateStreaming`,
  `stopStreaming`), LED `blink(2000, 100)` ; `make fw-check` SUCCESS, 0 warning. Simulation complète
  hors ligne (flux : phrase sans mot puis « Alexa, allume la lumière », Piper) : 1re phrase ignorée,
  aucun fichier ; mot 0,99 ; commande close 2,75 s après ; `home/salon/light/set` + carte ; réponse.
  142 tests (dont vie privée : rien écrit en veille ; rafale Wi-Fi), pyright 0 erreur.
  **Non vérifié sur la carte** : firmware à téléverser. Limite connue : broker anonyme → flux audio
  lisible par tout le réseau local (étape 15) ; si le serveur meurt sans `listen false`, la carte
  continue d'émettre (testament serveur à prévoir à l'étape 15). En attente de validation.
- **2026-09-30** — Retour utilisateur (essai réel de l'étape 13) : « Hey Jarvis » et « Alexa » pas
  toujours reconnus (prononciation anglaise nécessaire), **« Hey Mycroft » fiable en français**.
  Ajouts : **seuil propre à chaque mot** (`wake.thresholds`, vide par défaut) et **journal des mots
  presque reconnus** (pic de score ≥ 0,15 retombé sans atteindre le seuil) pour régler sur mesures.
  143 tests, pyright 0 erreur. Aucun seuil changé tant qu'on n'a pas les pics réels.
- **2026-09-30** — **Étape 13 validée** par l'utilisateur, qui retient **« Hey Mycroft »** comme mot
  principal (fiable prononcé en français) ; alexa et hey_jarvis restent actifs. Réglage des seuils par
  mot reporté (outil prêt : « presque reconnu »). Étape 14 lancée : droits par profil.
- **2026-09-30** — **Étape 14 écrite** (serveur seul, firmware inchangé). `access_control.py`
  (`AccessPolicy`, `Level`, `Verdict`, `Resolution`), section `access` de config.yaml,
  `VoicePipeline._decide` (confirmation en attente d'abord, puis droits, puis exécution),
  `home_control.announce/request`, élision « d'ouvrir ». Essai hors ligne avec les vrais profils :
  Siwis (standard, 0,59) « ouvre la porte » → refusé ; Denis (complet, 0,69) → confirmation demandée ;
  phrase suivante sans oui/non → demande abandonnée ; Denis ferme les volets → exécuté.
  162 tests, pyright 0 erreur. En attente de l'essai réel et de la validation.
- **2026-09-30** — Ajout de **`make say`** (`tools/say.py`) : fait dire `TEXT` par une voix Piper
  (`VOICE`, `SPEAKER` pour upmc : 0 = Jessica, 1 = Pierre), 0,5 s de silence avant/après, crête −3 dBFS,
  WAV + MP3 (ffmpeg) dans `SAY_OUT` (défaut `recordings/voix-test/phrase.wav`) ; voix téléchargée au
  premier usage. Pour tester droits et confirmation sans seconde personne (≠ `speech-check`, qui fait
  entendre la RÉPONSE). Générés : `recordings/voix-test/tom-porte` (« Hey Mycroft, ouvre la porte. »)
  et `pierre-lumiere` (« Hey Mycroft, allume la lumière. »).
- **2026-09-30** — Retour utilisateur : « Hey Mycroft » doit être prononcé à l'anglaise, sinon rien.
  Mesures (détecteur hey_mycroft réel, scratchpad) : voix FR (tom, upmc, siwis) en texte ou en graphies
  francisées → **0,00** ; en phonèmes bruts Piper `[[ˈɛj mˈajkʁɔft]]` → ~1 synthèse sur 2 atteint 0,5
  (Piper est aléatoire), mais retombe à 0 au moindre bruit/filtrage → inutilisable au téléphone. Voix
  anglaise **en_GB-alan-medium** → 1,00 partout, même bruit 15 dB + bande 300-4000 Hz + −12 dB (ryan et
  lessac moins stables). D'où `make say WAKE="Hey Mycroft"` (`--wake`, `--wake-voice`) : Alan dit le mot,
  0,6 s de pause, puis VOICE dit la commande ; chaque morceau normalisé à −3 dBFS, sortie **16 kHz** ;
  fichier vérifié par `WakeWordDetector` (échec → code 1). Identification : le serveur prend la
  **dernière** intervention, mais la pré-écoute (0,5 s avant la détection, qui tombe ~0,15 s avant la fin
  du mot) mêle la voix d'Alan : commande courte → Tom 0,42-0,46 (sous le seuil) ; commande ≥ 2 s
  (« …du salon, s'il te plaît. ») → Tom 0,57, Pierre 0,77 sur la découpe exacte du serveur. Refaits :
  `tom-porte` (« Ouvre la porte du salon, s'il te plaît. », détecteur 1,00) et `pierre-lumiere`
  (« Allume la lumière du salon, s'il te plaît. », 0,95). 162 tests, pyright 0 erreur.
- **2026-09-30** — **Essai réel de l'étape 14** (« satisfaisant, ma voix Denis OK »). Denis : lumière
  on/off exécutées (0,71-0,84), porte → confirmation → « oui » (bouton, ou « croft ? Oui. » en mains
  libres) → exécutée ; Tom (fichier téléphone, 0,68) porte → refusé ; Pierre (0,83) identifié.
  **Bug trouvé** : Pierre (standard) REFUSÉ pour allumer la lumière, « requis complet ». Cause : YAML 1.1
  lit `on`/`off` nus comme des **booléens** → règles `light.True/False`, action `on` absente → défaut
  complet. Corrigé : guillemets dans config.yaml, `settings._key()` rend True/False → on/off, et
  `AccessPolicy` **refuse au démarrage** une action inconnue (plus d'erreur silencieuse). Aussi : Denis
  à 0,59 (< full_min_score 0,6) recevait « votre profil ne permet pas » — faux ; message désormais
  « je ne reconnais pas assez nettement votre voix… Répétez, plus près du micro. ». Autres constats,
  non corrigés : 1re session hallucinée par Whisper (0,82 s de parole → « Je vous remercie de votre
  soutien… », 5,5 s de calcul) ; un « Ouvre » perdu en tête de commande (« la porte du salon… » → « Que
  dois-je faire avec la porte ? »), réussi au 2e essai. 165 tests, pyright 0 erreur.
- **2026-09-30** — **Étape 14 validée** par l'utilisateur. Étape 15 lancée.
- **2026-09-30** — **Étape 15 écrite** (broker + serveur + firmware). Broker : `voice.conf`
  (`allow_anonymous false`, `password_file`, `acl_file`), `mosquitto/acl`, `make mqtt-user`,
  `mosquitto-docker`/`mosquitto-config` installent acl + passwd (0600). Vérifié sur un broker **jetable**
  (conteneur `voice-acl-test`, port 18830, supprimé ensuite ; le broker de l'utilisateur n'a pas été
  touché) : anonyme et mauvais mot de passe refusés ; carte qui publie sous le nom d'une autre, lit le
  micro ou les commandes d'une autre, publie sur home/ → jeté ; carte lit ses commandes et la présence.
  Un broker **ouvert** accepte un client qui envoie des identifiants (vérifié) → migration : comptes,
  .env + secrets.h, firmware + serveur, broker en dernier. Serveur : `MqttLink(presence=True)`,
  `refusal_hint`, `setup_logging` ; vérifié : arrêt propre → `offline`, `kill -9` → testament
  immédiat, mauvais mot de passe → message clair, `make run-echo` avec config de test → journal
  fichier. Firmware : TWDT 15 s, `setConnectionTimeout(1000)`, Wi-Fi relancé à 30 s, présence
  serveur (micro coupé si absent), utilisateur MQTT = DEVICE_ID par défaut, diagnostic codes 4/5.
  `make fw-check` SUCCESS 0 avertissement (RAM 36,7 %, Flash 31,5 %). 170 tests, pyright 0 erreur.
  Non vérifié sur la carte : déclenchement réel du chien de garde (pas de commande de test).
  En attente de l'essai réel et de la validation.
- **2026-09-30** — **Étape 15 validée** par l'utilisateur, qui **garde le broker de développement
  anonyme** (`make mosquitto-docker` non lancé ; comptes et ACL à activer en production). Conséquence
  à rappeler : tant que le broker est anonyme, tout appareil du réseau peut publier sur les topics de
  commande (cartes, et agents Linux de l'étape 15b). Étape 15b lancée : agent MQTT sur chaque
  machine, configuration YAML.
- **2026-09-30** — **Étape 15b écrite** (serveur + agent ; firmware inchangé). Serveur :
  `machine_control.py` (parse/decide, `MachineController` : états des agents, interprétation, commande
  ou Wake-on-LAN), `command_router.py`, `MqttLink.on_topic` (filtres hors voice/), section `machines`
  de config.yaml (actions : mots + phrases ; `pc-bureau`), `AccessPolicy` et `Assistant` généralisés
  (`Command = DeviceCommand | MachineCommand`). Agent : `agent/voice_agent.py`, `agent.yaml.example`,
  service systemd, `agent/Makefile` ; cibles `agent-run`, `agent-install`, `agent-log`, `mqtt-agents` ;
  règles agents dans `mosquitto/acl` (pour la production). Essai de bout en bout sur broker **jetable**
  anonyme (port 18832, supprimé) : agent en `dry_run` annoncé avec 3 actions ; « éteins » et
  « verrouille » → agent → compte rendu ; « allume » → « déjà en marche » ; agent arrêté → hors ligne →
  « ne répond pas ». 202 tests, pyright 0 erreur (agent/ inclus). En attente de l'essai réel.
- **2026-09-30** — **Git initialisé** à la demande de l'utilisateur, avant l'essai de l'étape 15b :
  premier commit (108 fichiers) sur `main` ; contrôle des fichiers sensibles fait (§7).
- **2026-10-06** — Reprise sur le PC de dev. Entre-temps, 3 commits de l'utilisateur (`WifiConfig` :
  portail de configuration et NVS ; README agent ; agent installé sous `/etc/voice-assistant`) et un
  dépôt distant GitHub. Symptôme : « `make run` ne donne plus de réponse ». Cause : `mqtt.host` passé à
  192.168.1.200 (Pi 5, comptes exigés), mais **la carte n'est sur aucun broker** : absente de .200
  (aucun `voice/esp32-01/state`), `offline` sur .104. Serveur sain (connecté, agent `pc-bureau` vu en
  ligne, `dry_run`). À 15:50, serveur éjecté toutes les 2 s : un autre client `voice-server` sur .200
  (second serveur, arrêté depuis). Décision : broker sur le Pi 5. Fait : `MQTT_HOST` du Makefile relu
  dans `config.yaml`, modèles (`agent.yaml.example`, `secrets.h.example`, README) en .200, restes de
  `/etc/voice-agent` alignés sur `/etc/voice-assistant` (dont le défaut `--config` de l'agent).
  **Reste à faire par l'utilisateur** : compte `esp32-01` sur le Pi, puis portail de la carte
  (broker 192.168.1.200, utilisateur `esp32-01`, mot de passe). Étape 15b toujours en attente d'essai.
- **2026-10-06** — Carte reconnectée, chaîne complète rétablie sur le Pi 5. Réseau réel : point d'accès
  **`RPI5-AP`** du Pi (NetworkManager, réseau **10.42.0.0/24**, Pi = **10.42.0.1** ; le Pi est aussi
  192.168.1.200 en Ethernet, même broker). Carte : broker `10.42.0.1`, IP 10.42.0.73, signal −53 dBm.
  Trois causes successives, lues dans le moniteur série (**921600 bauds**) : (1) session envoyée en
  binaire sur le port série = MQTT non connecté (repli de l'étape 4) ; (2) `Reason: 211` : l'AP était
  en **WPA1**, le core ESP32 3.x exige **WPA2** par défaut (`_minSecurity`) → AP passé en
  `proto rsn`, CCMP, **canal 6** (était 13) ; (3) `Reason: 15 4WAY_HANDSHAKE_TIMEOUT` : mot de passe
  Wi-Fi enregistré dans la carte faux, prouvé par `AP-STA-POSSIBLE-PSK-MISMATCH` dans le journal
  `wpa_supplicant` du Pi → ressaisi dans le portail. Piège du portail `WifiConfig` : champs de mot de
  passe toujours vides, un champ laissé vide efface la valeur enregistrée (correction proposée à
  l'utilisateur, pas de réponse). Étape 15b toujours en attente d'essai et de validation.
- **2026-10-06** — Premiers essais réels de l'étape 15b (agent `pc-bureau` en `dry_run`, broker du Pi) :
  « verrouille le PC du bureau » exécuté deux fois (agent : `essai : loginctl lock-sessions`).
  (1) Mots anglais non reconnus (« Locke », « Reboot », « Shut down » → « Que dois-je faire… ») :
  ajoutés à `machines.actions.*.words` (lock/locke/locker/loque, reboot/reboote/rebooter,
  shutdown/shut). (2) Confirmation refusée : « OUI » de Denis à 0,35 = « voix inconnue » → règle du
  profil le plus proche (`confirm_min_score: 0.25`, `AccessPolicy.resolve(..., closest, score)`, le
  pipeline transmet `Identification.closest`). 214 tests, pyright 0 erreur. Serveur à relancer
  (config.yaml lu au démarrage). Étape 15b : essais en cours, pas encore validée.
- **2026-10-06** — **Étape 15b validée** par l'utilisateur (« Agents Linux ok, réponse dans les logs »).
  Étape 16 lancée : optimisation.
- **2026-10-06** — **Étape 16 écrite** (serveur seul, plus une option du broker ; firmware inchangé).
  Point de départ mesuré sur 63 échanges réels : réponse prête en 1,54 s (Whisper 1,06 + diarisation
  0,40 + synthèse 0,07). Bancs dans le scratchpad, sur les enregistrements réels en lecture seule.
  Retenu : empreinte directe pour une parole courte, Whisper sans mots dans ce cas, cache de synthèse,
  vérification de fin de commande à 100 ms, `TCP_NODELAY`. Écarté sur mesures : parallélisme, beam 1,
  16 fils. `make latency COUNT=60` : **1,62 s → 1,09 s (−0,53 s, 33 %)**, même texte 54/54, même
  identification 50/54. 220 tests, pyright 0 erreur. `voice.conf` validé sur un broker jetable
  (supprimé). **À faire par l'utilisateur** : `make mosquitto-config` sur le Pi pour `set_tcp_nodelay`.
  **Non fait, à décider** : numéros de séquence des chunks (§3.4 du prompt, change le protocole et le
  firmware) ; `end_silence_ms` (800 ms d'attente après la phrase, le plus gros délai restant).
