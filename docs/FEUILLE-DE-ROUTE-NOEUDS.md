# Nœuds domotiques — feuille de route (options A et B)

**Statut : préparée le 2026-10-19, rien n'est implémenté.** Elle sert à reprendre le sujet plus tard sans rien redécouvrir.
Même méthode que le reste du projet : une étape à la fois, validée par l'utilisateur avant la suivante, code en anglais,
commentaires en français, tests, documentation FR/EN tenue à jour.

- **Option A** : firmware de nœud maison (PlatformIO, PubSubClient, ArduinoJson), sur la même pile que la carte vocale.
- **Option B** : nœuds ESPHome (un YAML par nœud, code C++ généré, compilé avec ESP-IDF).
- **Étapes communes D0 à D3** : le contrat MQTT, la configuration et le serveur. Elles ne dépendent pas du choix A ou B, et
  A et B cohabitent ensuite sur le même contrat.

## 1. Point de départ (vérifié dans le dépôt le 2026-10-19)

| Élément | État |
|---|---|
| Commande | le serveur publie `home/<pièce>/<appareil>/set`, JSON `{"action","by","board","session"}`, QoS 1, **non retenu** (`home_control.py`) |
| Pièce | en minuscules sans accents (`salle-de-bain`, `entree`) ; appareils `light` (`on`/`off`), `shutter` et `door` (`open`/`close`) |
| Retour d'état | **aucun** : le serveur dit « J'allume » sans savoir si un nœud a agi ; seule la carte vocale confirme (`voice/<carte>/event`, pour sa propre pièce) |
| `DemoDevices` | appareils de démonstration de la carte vocale ; n'écoute **pas** `home/…/set` (voir le journal du 2026-10-19) |
| Serveur | `MqttLink.on(filtre, rappel)` accepte un filtre complet hors `voice/` (déjà utilisé pour `agent/+/state`) : lire `home/+/+/state` est facile |
| Broker | `mosquitto/acl` : le serveur a `home/#` ; **aucun droit prévu pour des nœuds** ; `make mqtt-user NAME=…` crée un compte |
| Firmware | `MqttLink` (firmware) fabrique `voice/<DEVICE_ID>/…` et le testament `voice/<id>/state` : à généraliser pour un nœud (A1) |
| Configuration | F6 : `conf/home.yaml` et ses motifs (`conf/pieces/*.yaml`) permettent un fichier par pièce |

## 2. Ordre conseillé

```
D0 contrat ─ D1 configuration ─ D2 serveur lit les états ─ D3 usages de l'état
                    │
                    ├─ Option B : B1 ─ B2 ─ B3 ─ B4 ─ B5      (le plus rapide pour avoir un vrai relais)
                    └─ Option A : A1 ─ A2 ─ A3 ─ A4 ─ A5 ─ A6 (le plus cohérent avec le dépôt, plus long)
```

Les étapes D se font d'abord (avec un nœud **simulé**, sans matériel). Ensuite, B ou A, ou les deux. Les premiers essais avec du
matériel se font en **basse tension** (LED, lampe 12 V), jamais directement sur le secteur.

## 3. Étapes communes

### D0 — Le contrat MQTT « domotique v2 » (sans code de nœud)
- **Objectif** : figer les topics et les droits, pour que A et B écrivent le même nœud.
- **Contrat proposé** :
  - commande (inchangée) : `home/<pièce>/<appareil>/set`, JSON, QoS 1, non retenu ;
  - état (nouveau) : `home/<pièce>/<appareil>/state`, texte brut `on` | `off` | `open` | `closed`, **retenu**, QoS 1, publié après
    chaque exécution et à chaque connexion ;
  - présence (nouveau) : `home/nodes/<nœud>/status`, `online` | `offline`, retenu, `offline` en testament (LWT).
- **Droits** (`mosquitto/acl`) : compte du nœud = **la pièce** (`salon`), ce qui permet un bloc générique :
  `pattern read home/%u/+/set`, `pattern write home/%u/+/state`, `pattern write home/nodes/%c/status` (`%c` = identifiant client = nom
  du nœud). Le serveur garde `home/#`.
- **Fichiers** : `mosquitto/acl`, `docs/MOSQUITTO.md`, `docs/FEUILLE-DE-ROUTE-NOEUDS.md` (ce contrat), `tools/home_node_sim.py` (un nœud
  **simulé** : il écoute `set`, publie `state` et `status`, avec un identifiant client distinct).
- **Tests** : le simulateur et un faux lien MQTT (comme pour `test_home_control.py`).
- **Validation** : le simulateur reçoit « allume la lumière du salon » du vrai serveur et répond ; un nœud sans droit ne peut ni
  lire la commande d'une autre pièce ni écrire l'état d'une autre.
- **Pièges** : Mosquitto jette en silence une publication interdite ; il prévient déjà au démarrage pour un pattern sans `%c` ni `%u`
  (`voice/server/status`, voulu, voir l'ACL), un avertissement du même genre est à attendre pour `home/nodes/…`.
- **Décision à prendre** : compte par pièce (défaut) ou par nœud (blocs ACL explicites, plus de réglages).

### D1 — Les appareils de chaque pièce dans la configuration
- **Objectif** : dire ce que chaque pièce possède, pour refuser « ferme la porte du salon » quand il n'y en a pas.
- **Fichiers** : `conf/home.yaml` (clé `home.devices`, ex. `salon: [light, shutter]`), `settings.py` (lecture et validation :
  appareils parmi `light`, `shutter`, `door` ; pièces connues), `home_control.py` (« Il n'y a pas de volets dans le salon. »),
  `config.example.yaml`/README. Compatible avec F6 : un fichier par pièce, `conf/pieces/*.yaml`.
- **Compatibilité** : `home.devices` absent = comportement actuel (tout est accepté).
- **Tests** : validation, message, absence de la clé, fusion de plusieurs fichiers de pièces.
- **Validation** : la phrase refusée dit pourquoi ; rien n'est publié.

### D2 — Le serveur lit les états et la présence
- **Objectif** : savoir, sans rien changer à la réponse vocale.
- **Fichiers** : nouveau `home_state.py` (cache `{pièce, appareil} → état, date` et `nœud → online/offline`), `__main__.py`
  (`link.on("home/+/+/state", …)`, `link.on("home/nodes/+/status", …)`), journal : « salon/light : on », « nœud salon : hors ligne ».
- **Tests** : états retenus reçus à la connexion, nœud qui disparaît (testament), état inconnu.
- **Validation** : avec le simulateur, le journal du serveur suit l'état ; un simulateur arrêté passe « hors ligne ».

### D3 — Ce que le serveur fait de l'état
- **Objectif** : des réponses vraies.
- **Contenu** (chaque ligne peut être validée séparément) :
  1. nœud connu hors ligne : « Je ne peux pas joindre le salon » au lieu de « J'allume » ;
  2. question : « la lumière du salon est-elle allumée ? » (nouvelle intention de lecture, sans action) ;
  3. vérification après coup : l'état attendu n'arrive pas en N secondes → avertissement au journal.
- **Décision à prendre** : réponse immédiate puis vérification (défaut, aucune latence ajoutée) ou attente de l'état, 1,5 s au plus,
  avant de répondre.
- **Droits** : inchangés (`access.rules` et `access.confirm` s'appliquent avant toute publication).

## 4. Option B — ESPHome

Essai déjà fait (2026-10-19) : ESPHome 2026.9.1 sur Python 3.13 ; un nœud qui suit le contrat est valide ; le projet généré est un projet
ESP-IDF en C++ (`src/main.cpp`, composants utilisés, `CMakeLists.txt`, `sdkconfig`). Le compilateur et le binaire n'ont **pas** été
essayés (le premier build télécharge la chaîne ESP-IDF).

### B1 — L'outil, isolé
- **Fichiers** : `nodes/requirements.txt` (`esphome==2026.9.1`, version épinglée), `.gitignore` (`.esphome-venv/`, `nodes/.esphome/`,
  `nodes/secrets.yaml`), `Makefile` : `esphome-venv`, `node-config NODE=…`, `node-compile`, `node-run` (USB ou `DEVICE=OTA`),
  `node-logs`. Environnement à part (`.esphome-venv`), comme `.pio-venv` : ESPHome fixe ses propres versions de dépendances.
- **Validation** : `make esphome-venv` puis `make node-config NODE=salon-lumiere` sur un modèle.
- **Piège** : le premier `node-compile` télécharge beaucoup (chaîne ESP-IDF) : à lancer avec l'accord de l'utilisateur.

### B2 — Les modèles de nœud
- **Fichiers** : `nodes/salon-lumiere.yaml` (lampe, relais GPIO), `nodes/volets.yaml` (`cover` temporisé, deux relais, verrouillage
  mutuel), `nodes/porte.yaml` (`switch` à impulsion, `restore_mode: ALWAYS_OFF`, arrêt automatique), `nodes/secrets.yaml.example`.
- **Contenu commun** : `mqtt:` avec `topic_prefix: home/nodes/<nœud>` (la présence tombe sur `home/nodes/<nœud>/status`),
  `client_id` = nom du nœud, `discovery: false`, `on_json_message` sur `home/<pièce>/<appareil>/set`, état retenu publié par
  `mqtt.publish`, topics MQTT par défaut des composants mis à `null`.
- **À valider à cette étape** : les déclencheurs `on_open` / `on_closed` d'un `cover`, le comportement exact du composant MQTT d'une
  `light` (il existe toujours même avec les topics à `null`).

### B3 — Tests (sans matériel)
- **Fichiers** : `server/tests/test_nodes_esphome.py` : chaque `nodes/*.yaml` passe `esphome config` (sauté si ESPHome est absent,
  comme le test de `systemd-analyze`) ; un chargeur YAML qui accepte `!secret` vérifie que **les topics du nœud correspondent** à
  `conf/home.yaml` (pièces et appareils) et au contrat D0 ; aucun mot de passe réel dans le dépôt.
- **Validation** : un nom de pièce faux dans un modèle fait échouer le test.

### B4 — Premier nœud réel
- **Matériel** : un ESP32-C3, un module relais, une charge **basse tension** ; compte MQTT du nœud (`make mqtt-user NAME=salon`) et
  ses droits (D0).
- **Validation de bout en bout** : « allume la lumière du salon » allume la charge ; l'état retenu apparaît ; le débranchement donne
  `offline` ; le serveur (D3) répond en conséquence.
- **Pièges** : broches de démarrage de l'ESP32-C3 (GPIO 2, 8 et 9, à vérifier sur la carte) ; un relais peut claquer au démarrage ;
  le port USB natif est `/dev/ttyACM0`, comme sur l'S3.

### B5 — Exploitation
- **Contenu** : mise à jour par Wi-Fi (`node-run DEVICE=OTA`, mot de passe OTA), politique de version (ESPHome publie chaque mois :
  épingler, puis monter la version volontairement), sauvegarde des YAML, `docs/NOEUDS-ESPHOME.md` (FR/EN dans les README).

## 5. Option A — firmware maison

### A1 — Préparer le code partagé
- **Objectif** : ne pas recopier le firmware vocal.
- **Travail** : généraliser `MqttLink` (le préfixe `voice/<id>/…` et le testament sont écrits en dur : ajouter la publication sur un
  topic quelconque et un testament configurable) ou écrire une petite classe `NodeLink` ; ajouter à `WifiConfig` (portail) un champ
  **pièce**. Un environnement PlatformIO `[env:node-esp32c3]` dans `firmware/platformio.ini` (`FW_ENV=node-esp32c3`) avec son
  `build_src_filter` et son `pins_node_esp32c3.h`. Le firmware vocal ne change pas de comportement.
- **Décision à prendre** : un nouvel environnement dans `firmware/` (une seule chaîne d'outils, recommandé) ou un projet `node/` séparé.

### A2 — Le modèle d'appareils
- **Fichiers** : `firmware/src/DeviceTable.h` (en-tête seul, testable sur le PC comme `FingerprintTrigger.h`) : appareil = nom, type,
  broches, niveau actif, durée d'impulsion, temps de course ; machine d'état des volets (ouvrir, fermer, arrêter, verrouillage mutuel,
  temps de course), impulsion de la porte, lumière.
- **Configuration** : à la compilation d'abord (`node_devices.h`) ; à la volée (NVS) seulement si le besoin apparaît.

### A3 — La couche MQTT du nœud
- **Contenu** : abonnement à `home/<pièce>/+/set` ; lecture de `"action"` avec ArduinoJson ; exécution ; état retenu publié ; testament
  `home/nodes/<nœud>/status` ; reconnexion ; journalisation de `by` et `session` ; appareil inconnu refusé sans effet.

### A4 — Sécurité et robustesse
- **Contenu** : état au démarrage défini (lumière éteinte, volets à l'arrêt, porte fermée) ; impulsion de porte bornée ; durée
  maximale pour un volet ; chien de garde ; la porte exige un `by` non vide (défense en profondeur : le serveur demande déjà une
  confirmation par le même doigt) ; droits MQTT du nœud (D0).

### A5 — Tests
- **Contenu** : tests de l'en-tête `DeviceTable.h` compilé avec g++ (états, verrouillage, temps de course, impulsions), tests de la
  lecture du JSON, essai sur le vrai broker avec le simulateur (identifiant client distinct), `make fw-check FW_ENV=node-esp32c3` sans
  avertissement.

### A6 — Portail, mise à jour à distance, documentation, matériel
- **Contenu** : portail avec le champ pièce ; OTA (ArduinoOTA protégée par mot de passe) ; `docs/BROCHAGE-NOEUDS.md` (relais,
  alimentation séparée de la bobine, schéma) ; première validation sur du matériel, comme B4.

## 6. Matériel et sécurité

- **Carte de nœud** : ESP32-C3 (peu chère, USB Serial/JTAG comme l'S3). L'ESP32-S3 reste la carte vocale.
- **Relais** : module à optocoupleur, alimentation de la bobine séparée de celle de l'ESP32 si possible, broches de commande
  choisies hors broches de démarrage.
- **Secteur 230 V** : seulement si vous êtes qualifié, avec un module et un boîtier prévus pour cela. Valider d'abord en basse tension.
- **Porte** : la confirmation vocale (`access.confirm`) ne suffit pas pour une vraie gâche ; prévoir une impulsion bornée, un état
  fermé au démarrage et, de préférence, un mode manuel ou un verrou mécanique de secours.
- **Hors périmètre** : Home Assistant, Matter, le cloud.

## 7. Décisions à prendre (valeur proposée entre parenthèses)

1. Compte MQTT par pièce (**oui**) ou par nœud.
2. Réponse vocale immédiate puis vérification (**oui**) ou attente de l'état.
3. `home.devices` déclaré par pièce (**oui**, absent = comportement actuel).
4. Par où commencer : B (**le plus rapide**), A, ou les deux dans l'ordre B puis A.
5. Premier nœud : ESP32-C3 avec une charge basse tension (**oui**) ; porte réelle (**non**, pour l'instant).

## 8. Critères de fin

- « Allume la lumière du salon » commande un vrai relais sur un nœud distinct de la carte vocale, et le serveur voit l'état.
- Un nœud débranché est signalé hors ligne ; le serveur ne ment plus (« J'allume » devient « Je ne peux pas joindre le salon »).
- Un nœud ne peut ni lire ni écrire hors de sa pièce (droits MQTT vérifiés).
- Les tests passent sans matériel, y compris pour les modèles ESPHome et le firmware maison.
