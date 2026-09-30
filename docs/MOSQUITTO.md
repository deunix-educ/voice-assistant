# Broker Mosquitto — installation et vérification

Le broker (serveur MQTT) relaie tous les messages entre l'ESP32 et le serveur
Python. Configuration unique : [mosquitto/voice.conf](../mosquitto/voice.conf).

---

## 1. Deux façons de le faire tourner

### Service Debian (Raspberry Pi, PC)

```bash
make install-system      # installe mosquitto et mosquitto-clients
make mosquitto-config    # copie voice.conf dans /etc/mosquitto/conf.d/ et redémarre
```

### Conteneur Docker

Monter `voice.conf` **à la place** de `mosquitto.conf` du conteneur, et publier le
port 1883 :

```yaml
services:
  mosquitto:
    image: eclipse-mosquitto:2.0
    restart: unless-stopped
    ports:
      - 1883:1883
    volumes:
      - ./mosquitto/config:/mosquitto/config    # contient voice.conf renommé mosquitto.conf
```

Si le conteneur existe déjà :

```bash
make mosquitto-docker MOSQ_DIR=~/srv/mosquitto MOSQ_COMPOSE=~/srv/docker-compose.mosquitto.yml
```

La cible sauvegarde l'ancienne configuration (`mosquitto.conf.bak`), installe
`voice.conf` avec le propriétaire du conteneur (uid 1883), relance le service et
affiche les dernières lignes du journal. Le `sudo` est nécessaire : les fichiers
appartiennent à l'utilisateur `mosquitto` du conteneur.

---

## 2. Comptes et droits (étape 15)

Sans authentification, n'importe quel appareil du réseau local peut s'abonner à
`voice/+/audio/stream` et **écouter le micro** en continu, ou publier une
commande. Depuis l'étape 15, le broker exige un compte, et un fichier de droits
(ACL, *Access Control List* : liste de qui peut lire ou écrire quel topic)
limite chaque compte :

| Compte | Qui | Droits ([mosquitto/acl](../mosquitto/acl)) |
|---|---|---|
| `voice-server` | le serveur, et les cibles `make mqtt-*`, `tone-mqtt`, `play-mqtt` | lire et écrire `voice/#` et `home/#` |
| `esp32-01` (= `DEVICE_ID`) | une carte | écrire **ses** `state`, `event`, `audio/in`, `audio/stream` ; lire **ses** `control`, `audio/out`, et `voice/server/status` |
| `pc-bureau` (= `id` de l'agent) | un agent Linux (étape 15b) | écrire **ses** `agent/<id>/state` et `result` ; lire **ses** `agent/<id>/command` |

Une carte ne peut donc ni écouter une autre carte, ni publier sous son nom, ni
envoyer de commande domotique. Vérifié sur un broker de test : connexion
anonyme et mauvais mot de passe refusés, usurpation d'une autre carte jetée.

```bash
make mqtt-user NAME=voice-server   # mot de passe demandé deux fois, stocké haché
make mqtt-user NAME=esp32-01       # un compte par carte, du nom de son DEVICE_ID
make mosquitto-docker              # installe voice.conf + acl + passwd, relance le broker
```

Les mots de passe en clair vont dans `server/.env` (`MQTT_USERNAME`,
`MQTT_PASSWORD`) et dans `firmware/include/secrets.h` (`MQTT_PASSWORD` ;
`MQTT_USER` vide = `DEVICE_ID`). `mosquitto/passwd` ne contient que des
empreintes (hachage : transformation à sens unique), et n'est pas versionné.

Le mot de passe circule encore **en clair** sur le réseau local : seul TLS
(chiffrement, port 8883) l'empêcherait. C'est l'option de l'étape 15, non
activée : sur un réseau Wi-Fi WPA2 domestique, l'écoute du trafic suppose déjà
d'avoir la clé du Wi-Fi.

---

## 3. Pièges rencontrés

| Symptôme | Cause | Correctif |
|---|---|---|
| L'ESP32 ne se connecte pas, `mosquitto_sub` sur le PC fonctionne | Mosquitto 2.x sans `listener` explicite n'écoute que sur `localhost` | `listener 1883 0.0.0.0` |
| Conteneur en état `restarting`, des centaines de redémarrages | deux lignes `listener 1883` : le second ne peut pas ouvrir le port (`Error: Address in use`) | **un seul** `listener` par port |
| Le broker plante sans rien dire | `log_dest syslog` : dans un conteneur, il n'y a pas de syslog | `log_dest stdout`, puis `docker logs <conteneur>` |
| `Note: It is recommended to replace message_size_limit` | option obsolète depuis Mosquitto 2.0 | `max_packet_size 8192` |
| L'ESP32 affiche `mqtt : echec (code -2)` | connexion TCP refusée : broker arrêté, mauvaise IP, pare-feu | `make mqtt-watch` depuis le PC ; vérifier `MQTT_HOST` dans `secrets.h` |
| Deux cartes se déconnectent l'une l'autre en boucle | même `DEVICE_ID`, donc même identifiant client MQTT | un `DEVICE_ID` différent par carte |
| `connexion refusee par le broker : Not authorized` (serveur), `code 5` (ESP32) | compte absent de `passwd`, mot de passe différent, ou `MQTT_PASSWORD` vide | `make mqtt-user NAME=...`, puis `make mosquitto-docker` ; recopier le même mot de passe |
| Tout est connecté, mais rien ne passe | droits : Mosquitto **jette en silence** une publication interdite et n'envoie rien à un abonnement interdit | nom d'utilisateur de la carte = son `DEVICE_ID` (la carte l'affiche au démarrage sinon) |
| Un autre programme de la maison ne se connecte plus au broker | `allow_anonymous false` vaut pour **tous** les clients | lui créer un compte, et ses lignes dans `mosquitto/acl` |
| `ACL pattern 'voice/server/status' does not contain '%c' or '%u'` au démarrage | avertissement : ce topic est commun à toutes les cartes, c'est voulu | rien à faire |

---

## 4. Vérifier le broker, sans ESP32

```bash
make mqtt-watch                              # terminal 1 : tout ce qui passe sur voice/#
make mqtt-control MSG='{"cmd":"ping"}'       # terminal 2 : un message sur control
```

Le terminal 1 doit afficher `voice/esp32-01/control {"cmd":"ping"}`.

Sans le paquet `mosquitto-clients`, ces cibles utilisent automatiquement les
clients inclus dans l'image Docker `eclipse-mosquitto:2.0`.

---

## 5. État retenu et testament

| Topic | Qui publie | Quand | Retenu |
|---|---|---|---|
| `voice/esp32-01/state` `{"status":"online",...}` | l'ESP32 | à chaque connexion, puis toutes les 30 s | oui |
| `voice/esp32-01/state` `{"status":"offline"}` | **le broker**, à la place de l'ESP32 | quand l'ESP32 disparaît sans prévenir | oui |
| `voice/esp32-01/event` `{"event":"boot","reason":...}` | l'ESP32 | une fois par démarrage | non |

Messages publiés par le serveur (étape 10) :

| Topic | Qui publie | Quand | Retenu |
|---|---|---|---|
| `voice/esp32-01/transcript` `{"session":...,"utterances":[{"speaker":"Denis","text":...}]}` | le serveur (QoS 1) | après chaque question transcrite | non |
| `home/<pièce>/<appareil>/set` `{"action":"close","by":"Denis","board":"esp32-01",...}` | le serveur (QoS 1), étape 11 | à chaque commande domotique | **non** : rejouée à un abonné tardif, elle ferait bouger un volet |
| `voice/esp32-01/control` `{"cmd":"device","device":"light","action":"on"}` | le serveur, étape 11 | commande pour la pièce de la carte | non |
| `voice/esp32-01/event` `{"event":"device",...,"ok":true,"state":"on"}` | l'ESP32, étape 11 | confirmation d'exécution | non |
| `voice/esp32-01/control` `{"cmd":"listen","enabled":true}` | le serveur, étape 13 | connexion ou redémarrage de la carte ; `false` à l'arrêt du serveur | non |
| `voice/esp32-01/audio/stream` (PCM binaire, QoS 0) | l'ESP32, étape 13 | en continu tant que l'écoute est active, sauf pendant une réponse ou un appui | non |
| `voice/esp32-01/control` `{"cmd":"capture","active":true}` | le serveur, étape 13 | mot de réveil entendu (LED allumée) ; `false` à la fin de la commande | non |
| `voice/server/status` `{"status":"online"}` | le serveur, étape 15 | à chaque connexion au broker | **oui** |
| `voice/server/status` `{"status":"offline"}` | le serveur à l'arrêt, ou **le broker** (testament du serveur) s'il meurt | arrêt ou disparition du serveur : les cartes coupent leur micro | **oui** |
| `agent/<machine>/state` `{"status":"online","actions":[...]}` | l'agent Linux, étape 15b ; `offline` par son testament | à sa connexion | **oui** |
| `agent/<machine>/command` `{"action":"shutdown","id":...,"by":"Denis"}` | le serveur (QoS 1) | commande autorisée (et confirmée) | **non** : l'agent se connecte en session propre, rien n'est rejoué |
| `agent/<machine>/result` `{"action":...,"ok":true,"code":0,"output":...}` | l'agent | après exécution | non |

- **Retenu** : le broker garde le dernier message du topic et le donne tout de
  suite à chaque nouvel abonné. Un serveur qui démarre après l'ESP32 connaît donc
  aussitôt son état.
- **Testament** (*Last Will*) : confié au broker à la connexion. Si l'ESP32
  disparaît sans se déconnecter proprement, c'est le broker qui publie `offline`.

Délai du testament :

| Disparition | Le broker s'en aperçoit | `offline` publié après |
|---|---|---|
| ESP32 : coupure de courant, reset, plantage, perte du Wi-Fi | quand l'ESP32 cesse de répondre : la puce ne prévient jamais | 1,5 × `MQTT_KEEPALIVE_S` = **15 s** au plus |
| client sur PC tué (`kill`, Ctrl-C) | immédiatement : le système d'exploitation ferme la connexion | < 1 s |

Après un simple reset, l'ESP32 se reconnecte en quelques secondes, souvent avant
l'expiration du keepalive. Le broker remplace alors l'ancienne session par la
nouvelle, et l'on peut voir passer un `offline` immédiatement suivi de `online`.
Pour observer le testament seul, il faut **couper l'alimentation** et attendre.

La cause du redémarrage (`reason`) vaut `poweron`, `external` (broche EN),
`software`, `panic` (plantage), `watchdog` ou **`brownout`** : chute de tension,
typiquement l'ampli qui tire trop sur une alimentation USB faible.
