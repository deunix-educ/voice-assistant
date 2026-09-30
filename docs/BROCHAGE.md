# Brochage et câblage — ESP32-WROOM-32 + INMP441 + MAX98357A

Document de référence du câblage. Schéma visuel : [wiring.svg](wiring.svg)
(clic droit → « Open Preview » dans VSCodium, ou ouverture dans un navigateur).

> **Avant de câbler** : ESP32 débranché de l'USB. On ne modifie jamais un câblage
> sous tension : une inversion VDD/GND détruit l'INMP441 en quelques secondes.

---

## 1. Tableau de câblage complet

Carte de référence : **DOIT ESP32 DEVKIT V1, 30 broches** (la plus répandue en TP).
Sur une carte 38 broches, les numéros de GPIO sont identiques, seule leur position
physique change : **fiez-vous toujours aux numéros sérigraphiés**, jamais au rang.

### 1.1 Micro INMP441 (I2S0, réception)

| INMP441 | ESP32 | Fil conseillé | Rôle |
|---|---|---|---|
| `VDD` | **3V3** | rouge | alimentation **3,3 V uniquement** |
| `GND` | **GND** | noir | masse |
| `SCK` | **GPIO 25** | bleu | horloge bit (BCLK), générée par l'ESP32 |
| `WS` | **GPIO 26** | vert | sélection de mot (word select / LRCL) |
| `SD` | **GPIO 33** | violet | donnée série, micro → ESP32 |
| `L/R` | **GND** | noir | choix du canal : GND = **gauche** |

`L/R` à GND est la référence de ce projet : gardez ce câblage.

Attention à un piège propre à l'ESP32 : l'INMP441 n'émet que pendant **une moitié
de trame** et sort des **zéros** pendant l'autre. Or la correspondance entre la
broche `L/R` et les constantes `I2S_STD_SLOT_LEFT` / `..._RIGHT` du pilote n'est
pas celle qu'on attend sur l'ESP32 d'origine. Écouter la mauvaise moitié donne un
flux **parfaitement nul**, alors que le câblage est correct — le symptôme le plus
déroutant de l'étape 1.

Le firmware tranche donc tout seul : au démarrage, la classe `MicSelfTest`
réveille le micro **une seule fois**, lit les deux moitiés de trame en cadrage
MSB et décide sur la **structure** des mots : la moitié utile est celle qui n'est
ni nulle ni constante, et elle doit porter exactement **24 bits utiles**. Si elle
en porte 25, le micro suit le cadrage Philips et le firmware bascule. Le niveau
au repos est affiché pour information, sans intervenir dans la décision.

### 1.2 Amplificateur MAX98357A (I2S1, émission) — étape 2

| MAX98357A | ESP32 | Fil | Rôle |
|---|---|---|---|
| `Vin` | **VIN (5 V)** | orange | alimentation 5 V (3,3 V possible mais 4× moins de puissance) |
| `GND` | **GND** | noir | masse |
| `BCLK` | **GPIO 27** | bleu | horloge bit |
| `LRC` | **GPIO 14** | vert | sélection de mot |
| `DIN` | **GPIO 22** | violet | donnée série, ESP32 → ampli |
| `GAIN` | *non connecté* | — | gain 9 dB par défaut (voir §4) |
| `SD` | *non connecté* | — | déjà tiré au + par 100 kΩ sur la carte → ampli actif |
| `+` / `−` | haut-parleur | — | sortie pontée : **jamais de masse sur `−`** |

### 1.3 Interface utilisateur

| Élément | ESP32 | Câblage |
|---|---|---|
| Bouton PTT | **GPIO 4** | une borne sur GPIO 4, l'autre sur **GND** — pull-up interne activé par le firmware |
| LED d'état | **GPIO 2** | GPIO 2 → résistance **330 Ω** → anode LED ; cathode → GND |

La carte DevKit V1 possède déjà une LED bleue sur GPIO 2 : la LED externe est
**optionnelle**, l'étape 1 est validable sans elle.

### 1.4 Haut-parleur

| Caractéristique | Valeur conseillée |
|---|---|
| Impédance | **4 Ω** (3 W) ou 8 Ω (2 W) |
| Diamètre | 40 à 66 mm |
| Câblage | directement sur `+` et `−` de l'ampli |

La sortie du MAX98357A est **pontée** (les deux fils sont actifs, en opposition
de phase). Relier `−` à la masse court-circuite l'étage de sortie.

---

## 2. Schéma ASCII

```
                           ESP32-WROOM-32 (DevKit V1, 30 broches)
                          +----------------------------------------+
   INMP441                |                                        |
  +---------+             |  EN                             GPIO23 |
  | VDD  o--|--rouge------|-------------------------------->  3V3  |  (broche 3V3)
  | GND  o--|--noir-------|--------------------------------->  GND |  (broche GND)
  | SD   o--|--violet---->| GPIO33                          GPIO22 |<--violet--+
  | SCK  o--|--bleu------>| GPIO25                           GPIO4 |<--+       |
  | WS   o--|--vert------>| GPIO26                           GPIO2 |---|--+    |
  | L/R  o--|--noir--> GND| GPIO27 -->--bleu----------+         .. |   |  |    |
  +---------+             | GPIO14 -->--vert--------+ |         .. |   |  |    |
   3,3 V SEULEMENT        |                         | |     VIN(5V)|   |  |    |
                          +-------------------------|-|------------+   |  |    |
                                                    | |                |  |    |
                                      +-------------+ |                |  |    |
                                      |   +-----------+                |  |    |
                                      v   v                            |  |    |
                            MAX98357A |   |                            |  |    |
                           +----------|---|-----+                      |  |    |
                           | LRC  o<--+   |     |                      |  |    |
                           | BCLK o<------+     |    HP 4 ohms / 3 W   |  |    |
                           | DIN  o<------------|----------------------|--|----+
                           | GAIN o  (non conn. = 9 dB)                |  |
                           | SD   o  (non conn. = actif)  + o---+      |  |
                           | GND  o---noir--> GND         - o---|--+   |  |
                           | Vin  o---orange--> VIN (5 V)       |  |   |  |
                           +------------------------------+     |  |   |  |
                                                                v  v   |  |
                                                            +---------+ |  |
                                                            |   ( ) HP| |  |
                                                            +---------+ |  |
                                                                        |  |
                   bouton PTT : GPIO4 ----[ /  ]---- GND  <-------------+  |
                   LED etat   : GPIO2 ----[330]---->|---- GND  <-----------+
```

---

## 3. Pourquoi ce choix de broches

| Contrainte | Conséquence |
|---|---|
| **Broches de strapping** GPIO 0, 2, 5, 12, 15 | leur niveau au reset choisit le mode de démarrage → aucun signal audio dessus. GPIO 2 ne porte qu'une LED, en sortie seule. |
| **GPIO 34-39 sont en entrée seule** | inutilisables pour SCK/WS (sorties d'horloge). |
| **GPIO 6-11** | reliées à la mémoire flash interne → jamais utilisables. |
| **Deux périphériques I2S séparés** | I2S0 pour le micro, I2S1 pour l'ampli. On ne partage **pas** les horloges : le micro tourne en 32 bits/slot et l'ampli en 16 bits, et surtout l'entrée et la sortie doivent pouvoir démarrer et s'arrêter indépendamment. Cinq fils au lieu de trois : c'est le prix de la simplicité logicielle. |
| **GPIO 1 / 3 (TX0/RX0)** | utilisées par l'USB-série → interdites. |

---

## 4. Réglages matériels du MAX98357A

### Broche `GAIN`

| Câblage de `GAIN` | Gain |
|---|---|
| direct à GND | 15 dB |
| à GND via 100 kΩ | 12 dB |
| **non connectée** | **9 dB** ← notre choix |
| à Vin via 100 kΩ | 6 dB |
| direct à Vin | 3 dB |

### Broche `SD` (shutdown + choix de canal)

La tension sur `SD` sélectionne le mode :

| Tension sur `SD` | Mode |
|---|---|
| < 0,16 V | ampli éteint |
| 0,16 – 0,77 V | canal droit seul |
| 0,77 – 1,4 V | canal gauche seul |
| **> 1,4 V** | **moyenne (gauche + droite) / 2** ← par défaut |

Les cartes de type Adafruit tirent `SD` au + par 100 kΩ : laissée libre, la broche
est donc au-dessus de 1,4 V et l'ampli fonctionne en mode moyenne.

Le firmware émet en **stéréo, le même échantillon sur les deux voies**
(`AudioOutput`). La moyenne (gauche + droite) / 2 redonne donc exactement le
signal : niveau correct, et une éventuelle inversion gauche/droite est sans effet.
On n'a pas misé sur le comportement implicite d'un mode « mono » : c'est la leçon
de l'étape 1, où une moitié de trame muette nous a longtemps égarés.

Pour couper le son par logiciel, on reliera plus tard `SD` à un GPIO
(niveau bas = silence). Inutile avant l'étape 15.

---

## 5. Bilan de consommation

| Consommateur | Courant moyen | Crête |
|---|---|---|
| ESP32 (Wi-Fi actif) | ~120 mA | ~500 mA pendant l'émission radio |
| INMP441 | 1,4 mA | — |
| MAX98357A au repos | 2,4 mA | — |
| MAX98357A à 3 W sur 4 Ω | ~670 mA | ~1,2 A sur les transitoires |
| **Total au volume maximal** | **~0,8 A** | **> 1,5 A** |

Conséquences pratiques :

1. Une alimentation USB 5 V / 1 A **ne suffit pas** au volume maximal : l'ESP32
   redémarre (brown-out). Prévoir **5 V / 2 A**.
2. Ajouter un condensateur **électrolytique 470 µF / 16 V** entre `Vin` et `GND`
   de l'ampli, au plus près de la carte : il fournit les crêtes que les fils de
   breadboard ne peuvent pas suivre. Types et valeurs limites : **§6**.
3. Alternative propre : alimenter l'ampli par une source 5 V **externe**, en
   reliant sa masse à celle de l'ESP32 (**masse commune obligatoire**).

---

## 6. Condensateurs : types et valeurs

### 6.1 Les trois condensateurs du montage

| Rep. | Où | Rôle | Type exact |
|---|---|---|---|
| **C1** | `Vin` / `GND` du MAX98357A | réservoir d'énergie pour les crêtes du haut-parleur | électrolytique aluminium radial, **faible ESR**, 105 °C |
| **C2** | `Vin` / `GND` du MAX98357A, collé à C1 | découplage haute fréquence (la classe D découpe à ~300 kHz) | céramique multicouche **X7R** ou X5R |
| **C3** | GPIO 4 / `GND` (**optionnel**) | anti-rebond matériel du bouton | céramique multicouche **X7R** |

### 6.2 Valeurs minimale, typique et maximale

| Rep. | Mini | **Typique** | Maxi | Tension de service |
|---|---|---|---|---|
| **C1** | 100 µF<br>`1 × 10⁸ pF` | **470 µF**<br>`4,7 × 10⁸ pF` | 1 000 µF<br>`1 × 10⁹ pF` | ≥ 10 V, **16 V conseillé** |
| **C2** | 10 nF<br>`10 000 pF` (code 103) | **100 nF**<br>`100 000 pF` (code **104**) | 1 µF<br>`1 000 000 pF` (code 105) | ≥ 25 V |
| **C3** | 10 nF<br>`10 000 pF` (code 103) | **100 nF**<br>`100 000 pF` (code **104**) | 220 nF<br>`220 000 pF` (code 224) | ≥ 16 V |

Les bornes ne sont pas arbitraires :

- **C1 mini 100 µF** — la chute de tension pendant une crête vaut `ΔV = I × Δt / C`.
  Pour 1 A pendant 100 µs : 470 µF → 0,21 V (sans effet) ; 100 µF → 1,0 V (limite,
  l'ESP32 décroche vers 4,0 V en entrée de son régulateur) ; 47 µF → 2 V, redémarrage garanti.
- **C1 maxi 1 000 µF** — au branchement, le condensateur vide est un court-circuit :
  `I = C × dV/dt`. Charger 1 000 µF à 5 V en 1 ms demande **5 A**, ce qui déclenche la
  protection du port USB ou du hub. Au-delà, il faut un circuit de pré-charge : hors sujet ici.
- **C2** — au-dessus de ~1 MHz, un électrolytique se comporte comme une self : il ne
  découple plus rien. Sous 10 nF, la céramique ne stocke plus assez pour les fronts de
  commutation. Au-dessus de 1 µF, aucun gain et le boîtier devient inutilement gros.
- **C3** — la constante de temps vaut `τ = R_pull-up × C`, avec le pull-up interne de
  l'ESP32 à ≈ 45 kΩ : 100 nF → τ = 4,5 ms, soit ~6 ms pour franchir le seuil logique.
  C'est bien en dessous des `BUTTON_DEBOUNCE_MS = 30 ms` du firmware. Avec 220 nF on
  atteint ~13 ms : encore acceptable. Au-delà, le filtre matériel et le filtre logiciel
  se cumulent et le bouton devient mou.

**C3 est facultatif** : l'anti-rebond est déjà fait en logiciel dans
[PushButton.cpp](../firmware/src/PushButton.cpp). Ne l'ajoutez que si le bouton est au
bout d'un fil long et capte du bruit. Dans ce cas, insérez aussi **100 Ω en série**
entre le GPIO et le bouton : cette résistance limite le courant de décharge du
condensateur dans le contact à chaque appui, ce qui préserve le bouton.

### 6.3 Lire le marquage — il est justement en picofarads

Les céramiques portent un **code à trois chiffres en pF** : deux chiffres significatifs,
puis le nombre de zéros à ajouter.

| Code imprimé | picofarads | nanofarads | microfarads |
|---|---|---|---|
| `102` | 1 000 pF | 1 nF | 0,001 µF |
| `103` | 10 000 pF | 10 nF | 0,01 µF |
| **`104`** | **100 000 pF** | **100 nF** | **0,1 µF** |
| `224` | 220 000 pF | 220 nF | 0,22 µF |
| `105` | 1 000 000 pF | 1 000 nF | 1 µF |
| `106` | 10 000 000 pF | 10 000 nF | 10 µF |

Une lettre suivante donne la tolérance : `J` = ±5 %, `K` = ±10 %, `M` = ±20 %.
Sur un découplage, la tolérance n'a aucune importance : `K` ou `M` conviennent.

Les électrolytiques, eux, portent la valeur en clair (`470µF 16V`) et une **bande
verticale marquant la borne négative**, en face de la patte la plus courte.

### 6.4 Pièges sur les condensateurs

1. **Polarité** — un électrolytique monté à l'envers chauffe, gonfle puis éclate en
   quelques secondes. La bande imprimée est le **−**, la patte longue est le **+**.
   Les céramiques, elles, ne sont pas polarisées.
2. **Diélectrique** — refusez les `Y5V` et `Z5U` : ils perdent jusqu'à 80 % de leur
   capacité avec la température et la tension appliquée. **X7R** (ou X5R) pour le
   découplage, `C0G`/`NP0` seulement pour les petites valeurs.
3. **Effet de la tension continue** — une céramique 10 µF / 6,3 V en boîtier 0603
   ne fait plus que 4 µF sous 5 V. C'est pourquoi C2 est spécifiée en **25 V minimum**.
4. **ESR** — un 470 µF « general purpose » peut présenter 1 Ω de résistance série :
   inutile pour des crêtes de 1 A. Cherchez la mention **low ESR** ou **105 °C**
   (typiquement < 0,3 Ω).
5. **Distance** — C1 et C2 à moins de 3 cm des broches `Vin` / `GND` de l'ampli.
   Au-delà, l'inductance des fils annule leur effet.
6. **C1 et C2 ne se remplacent pas** : le gros stocke l'énergie, le petit évacue le
   haut du spectre. On met toujours les deux, en parallèle.

Les modules INMP441 et MAX98357A du commerce embarquent déjà leur découplage local
(100 nF, et 10 µF côté ampli). C1 et C2 compensent **les fils de la platine d'essai**,
pas un manque des modules.

---

## 7. Précautions de câblage

1. **Longueur des fils I2S** : 15 cm maximum. Au-delà, l'horloge se déforme et le
   son devient bruité.
2. **Séparer** les fils du micro de ceux du haut-parleur : le courant du HP
   (jusqu'à 1 A commuté à 300 kHz) rayonne dans les fils du micro.
3. **Masse commune** entre tous les modules, sans exception.
4. Ne pas alimenter l'INMP441 en 5 V : il est prévu pour 1,8 à 3,3 V.
5. Le micro est sensible aux vibrations : le fixer, ne pas le laisser pendre
   contre le boîtier du haut-parleur (effet Larsen mécanique).
6. Sur breadboard, vérifier que les deux moitiés des rails d'alimentation sont
   bien reliées (beaucoup de breadboards coupent les rails au milieu).

---

## 8. Vérifications au multimètre, avant le premier démarrage

ESP32 **débranché**, en mode continuité puis en mode tension :

| # | Mesure | Attendu |
|---|---|---|
| 1 | continuité `INMP441 VDD` ↔ `ESP32 3V3` | passant |
| 2 | continuité `INMP441 GND` ↔ `ESP32 GND` | passant |
| 3 | continuité `INMP441 L/R` ↔ `GND` | passant |
| 4 | continuité `INMP441 VDD` ↔ `GND` | **coupé** (sinon court-circuit) |
| 5 | continuité `MAX98357A Vin` ↔ `ESP32 VIN` | passant |
| 6 | continuité `MAX98357A −` ↔ `GND` | **coupé** |
| 7 | continuité `GPIO 4` ↔ `GND`, bouton relâché | **coupé** |
| 8 | continuité `GPIO 4` ↔ `GND`, bouton appuyé | passant |

### Continuité de la ligne de données

Le défaut le plus tenace de l'étape 1 est une ligne `SD` qui n'arrive pas. Testez
en continuité, **carte hors tension**, et **sur la pastille du module**, jamais au
bout du fil : une soudure sèche laisse passer le fil mais pas le signal.

| # | Mesure | Attendu |
|---|---|---|
| 8b | `GPIO 33` ↔ pastille `SD` du module | passant |
| 8c | pastille `L/R` du module ↔ `GND` | passant |
| 8d | `GPIO 33` ↔ `GND` | **coupé** |

Puis USB branché :

| # | Mesure | Attendu |
|---|---|---|
| 9 | tension `3V3` / `GND` | 3,2 à 3,4 V |
| 10 | tension `VIN` / `GND` | 4,6 à 5,1 V |
| 11 | tension `GPIO 4` / `GND`, bouton relâché | ≈ 3,3 V (pull-up interne) |
| 12 | tension `GPIO 4` / `GND`, bouton appuyé | ≈ 0 V |

Le test 11 exige que le firmware soit déjà téléversé : le pull-up est activé par
`pinMode(INPUT_PULLUP)`.

---

## 9. Diagnostic rapide

| Symptôme | Cause la plus fréquente | Vérification |
|---|---|---|
| Auto-test : les deux moitiés « silence numerique » | le micro n'est pas cadencé ou n'émet rien | tensions sur les **pastilles du module** : `VDD` 3,3 V, `SCK` et `WS` ≈ 1,6 V |
| Auto-test : « un bit de retard (Philips) » | le micro suit le cadrage Philips | aucune : le firmware bascule et revérifie |
| Auto-test : « DECALE : bit de signe perdu » (23 bits) | cadrage faux, le premier bit déborde dans le mot précédent | aucune en MSB ; si ça persiste, envoyer l'analyse bit à bit |
| Auto-test : « queue du mot non nulle » | bits parasites après les 24 bits utiles | vérifier `MIC_SD_PULLDOWN = true` |
| Auto-test : « les DEUX moities portent des donnees » | `L/R` flottant, ou deux micros sur la ligne | continuité `L/R` ↔ GND sur la pastille |
| Auto-test : « bruit de fond eleve » au repos | pièce bruyante, ou micro pas encore stabilisé | sans effet sur le cadrage ; juger sur l'enregistrement |
| Ligne SD : « FLOTTE », horloges correctes | rien ne pilote GPIO 33 | continuité `SD` ↔ GPIO 33 sur la pastille |
| Auto-test série : amplitude mesurable mais crête faible | simple question de gain | appliquer la valeur de `MIC_GAIN` conseillée par l'auto-test |
| Crête à 0 %, silence total | `L/R` non relié à GND | test 3 |
| Crête à 0 %, silence total | `SD` du micro sur la mauvaise broche | test de continuité vers GPIO 33 |
| Bruit blanc continu | micro alimenté en 5 V | test 9 sur la broche `VDD` du micro |
| Son très faible même en criant | `SCK` et `WS` inversés | inverser GPIO 25 et GPIO 26 |
| Signal haché, craquements réguliers | fils I2S trop longs | raccourcir à 10 cm |
| L'ESP32 redémarre quand le son sort | alimentation insuffisante | §5, points 1 et 2 |
| Téléversement impossible | LED sur GPIO 2 qui perturbe le boot | retirer la LED externe, utiliser celle de la carte |
| Le HP souffle ou vibre en permanence dès la mise sous tension | broches I2S de sortie flottantes : l'ampli amplifie du bruit | firmware ≥ étape 1 : `silenceAmplifier()` les force à 0. Si le bruit persiste pendant le boot, relier `SD` de l'ampli à `GND`, ou ne pas l'alimenter avant l'étape 2 |
| Sifflement aigu permanent | masse commune absente entre ampli et ESP32 | §7, point 3 |

---

## 10. Récapitulatif des GPIO réservés

| GPIO | Usage | Étape |
|---|---|---|
| 2 | LED d'état | 1 |
| 4 | bouton PTT | 1 |
| 14 | MAX98357A `LRC` | 2 |
| 22 | MAX98357A `DIN` | 2 |
| 25 | INMP441 `SCK` | 1 |
| 26 | INMP441 `WS` | 1 |
| 27 | MAX98357A `BCLK` | 2 |
| 33 | INMP441 `SD` | 1 |
| 21 | LED de démonstration domotique (« la lumière » de la pièce) | 11 |

### LED de démonstration (étape 11, facultative)

```
GPIO21 ──[ 330 Ω ]──►|── GND        LED rouge ou verte : patte longue (anode) côté résistance
```

Courant : (3,3 V − 2,0 V) / 330 Ω ≈ 4 mA, bien sous les 12 mA conseillés par broche.
GPIO 21 est libre et sans rôle au démarrage (pas une broche de *strapping*). Elle
figure « la lumière » de la pièce de la carte ; volets et porte sont simulés
(état affiché sur la liaison série). Un module relais se brancherait au même endroit.
Cette LED n'est pas dessinée sur `wiring.svg`.

Les valeurs sont définies une seule fois, dans
[firmware/include/config.h](../firmware/include/config.h). Toute modification de
câblage se fait dans ce fichier, jamais dans le code des classes.
