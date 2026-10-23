// Banc d'essai du capteur d'empreinte R503 seul, sur ESP32 WROOM.
//
// Projet indépendant du firmware de l'assistant : aucun code partagé (le protocole est réécrit ici, en
// quelques lignes), pour qu'un défaut du firmware principal ne puisse pas fausser le diagnostic.
// Brochage et mode d'emploi : docs/BROCHAGE.md §1.8. Console série : 115200 bauds.
//
// Un cycle (environ 60 s, répété sans fin) :
//   1. niveaux au repos : la ligne RX, tirée vers le bas par la carte, reste-t-elle haute ? Si oui, quelque
//      chose la pilote : c'est le TX d'un capteur vivant. Niveau de la ligne de touche (fil bleu) ;
//   2. détecteur de doigt : posez un doigt, la ligne de touche doit passer à l'état bas ;
//   3. anneau seul à 57600 bauds : rouge, bleu, violet, éteint, trois fois (le capteur qui reçoit suit l'ordre) ;
//   4. dialogue : poignée de main (VfyPwd) puis anneau ROUGE allumé 1 s, à tous les débits du R503
//      (9600 x N, N = 1 à 12) ; si le capteur répond, il décrit sa configuration ;
//   5. écoute passive : tout octet émis spontanément (par exemple à la mise sous tension du capteur) ;
//   6. bilan : où chercher la panne.
//
// L'anneau départage deux pannes qui se ressemblent (aucune réponse du capteur) : s'il s'allume en rouge,
// la commande est arrivée, donc le capteur vit et c'est le chemin de retour (fil jaune, GPIO 16) qui est
// coupé ; s'il reste éteint, le capteur ne reçoit pas ou ne fonctionne plus.
#include <Arduino.h>

namespace {

// Brochage : les mêmes broches que firmware/include/pins_esp32_wroom.h, le câblage de l'étape F1 convient donc tel quel.
constexpr int PIN_RX = 16;     // RX2 de l'ESP32 : reçoit le fil JAUNE (TX du capteur)
constexpr int PIN_TX = 17;     // TX2 de l'ESP32 : envoie au fil MARRON (RX du capteur)
constexpr int PIN_TOUCH = 34;  // fil BLEU « WAKEUP » (entrée seule, sans résistance interne) : bas = doigt posé
constexpr int PIN_LED = 2;     // LED de la carte si elle existe : s'allume tant qu'un doigt est détecté

constexpr uint32_t CONSOLE_BAUD = 115200;

// Débits essayés : le R503 parle à 9600 x N (N = 1 à 12), 57600 par défaut. Fils croisés : les trois premiers seulement.
constexpr uint32_t BAUD_SCAN[] = {57600, 115200, 9600, 19200, 28800, 38400, 48000, 67200, 76800, 86400, 96000, 105600};
constexpr size_t BAUD_SCAN_COUNT = sizeof(BAUD_SCAN) / sizeof(BAUD_SCAN[0]);
constexpr size_t BAUD_SWAPPED_COUNT = 3;

constexpr uint32_t REPLY_WINDOW_MS = 250;
constexpr uint32_t RING_HOLD_MS = 1000;
constexpr uint32_t TOUCH_WINDOW_MS = 6000;
constexpr uint32_t LISTEN_MS = 8000;
constexpr uint32_t CYCLE_PAUSE_MS = 4000;

// Au plus deux octets nuls seuls : accident de ligne flottante (diaphonie, cassure), pas un capteur qui parle.
constexpr size_t MAX_NULL_GLITCH = 2;
constexpr size_t MAX_PACKET = 24;
constexpr size_t MAX_RX = 40;

// Données des commandes (fiche R503) : code d'instruction puis paramètres.
const uint8_t CMD_VERIFY_PASSWORD[] = {0x13, 0x00, 0x00, 0x00, 0x00};  // mot de passe 0, celui d'usine
const uint8_t CMD_RING_RED_ON[] = {0x35, 0x03, 0x00, 0x01, 0x00};      // anneau : fixe, vitesse 0, rouge
const uint8_t CMD_RING_OFF[] = {0x35, 0x04, 0x00, 0x01, 0x00};
const uint8_t CMD_RING_BLUE_ON[] = {0x35, 0x03, 0x00, 0x02, 0x00};
const uint8_t CMD_RING_PURPLE_ON[] = {0x35, 0x03, 0x00, 0x03, 0x00};
const uint8_t CMD_READ_PARAMETERS[] = {0x0F};
const uint8_t CMD_TEMPLATE_COUNT[] = {0x1D};

enum class Reply { None, Echo, Noise, BadSum, Valid };

struct Exchange {
    Reply kind = Reply::None;
    uint8_t code = 0;      // code de confirmation du capteur (kind == Valid)
    size_t frame = 0;      // position de la trame de réponse dans bytes (après un éventuel écho)
    size_t count = 0;
    uint32_t firstMs = 0;  // délai entre la fin de l'envoi et le premier octet reçu
    bool glitch = false;   // un ou deux octets nuls seuls : écartés, kind reste None
    uint8_t bytes[MAX_RX] = {};
};

/// Ce que le cycle a constaté ; le bilan en tire sa conclusion.
struct Findings {
    bool touchRestHigh = false;  // ligne de touche haute au repos : le capteur est alimenté
    bool touchReacted = false;   // la ligne de touche est passée à l'état bas pendant l'essai
    bool rxHigh = false;         // GPIO 16 tenue haute par autre chose que la carte
    bool txHigh = false;         // GPIO 17 tenue haute par autre chose que la carte
    bool valid = false;
    bool validSwapped = false;
    uint32_t validBaud = 0;
    uint8_t validCode = 0;
    bool echo = false;
    bool noise = false;  // octets reçus mais jamais une trame valide
};

uint16_t beWord(const uint8_t* data) {
    return static_cast<uint16_t>((data[0] << 8) | data[1]);
}

/// Trame de commande : EF01, adresse FFFFFFFF, type 01, longueur, instruction + paramètres, somme.
size_t buildPacket(uint8_t* out, const uint8_t* data, size_t size) {
    const uint16_t length = static_cast<uint16_t>(size + 2);
    size_t n = 0;
    out[n++] = 0xEF;
    out[n++] = 0x01;
    for (int i = 0; i < 4; i++) {
        out[n++] = 0xFF;
    }
    out[n++] = 0x01;
    out[n++] = static_cast<uint8_t>(length >> 8);
    out[n++] = static_cast<uint8_t>(length);
    uint16_t sum = static_cast<uint16_t>(0x01 + (length >> 8) + (length & 0xFF));
    for (size_t i = 0; i < size; i++) {
        out[n++] = data[i];
        sum = static_cast<uint16_t>(sum + data[i]);
    }
    out[n++] = static_cast<uint8_t>(sum >> 8);
    out[n++] = static_cast<uint8_t>(sum);
    return n;
}

/// Reconnaît ce qui est revenu : rien, écho de la commande, bruit, trame de confirmation (valide ou à somme fausse).
void classify(Exchange& x, const uint8_t* sent, size_t sentSize) {
    if (x.count == 0) {
        x.kind = Reply::None;
        return;
    }
    const bool echo = x.count >= sentSize && memcmp(x.bytes, sent, sentSize) == 0;
    bool allZero = true;
    for (size_t i = 0; i < x.count; i++) {
        allZero = allZero && x.bytes[i] == 0;
    }
    if (allZero && x.count <= MAX_NULL_GLITCH) {
        x.kind = Reply::None;
        x.glitch = true;
        return;
    }
    const size_t start = echo ? sentSize : 0;
    const uint8_t* r = x.bytes + start;
    const size_t n = x.count - start;
    x.kind = echo ? Reply::Echo : Reply::Noise;
    if (n < 12 || r[0] != 0xEF || r[1] != 0x01 || r[6] != 0x07) {
        return;
    }
    const size_t total = 9 + beWord(r + 7);
    if (total < 12 || total > n) {
        return;
    }
    uint16_t sum = 0;
    for (size_t i = 6; i < total - 2; i++) {
        sum = static_cast<uint16_t>(sum + r[i]);
    }
    x.frame = start;
    x.code = r[9];
    x.kind = sum == beWord(r + total - 2) ? Reply::Valid : Reply::BadSum;
}

/// Envoie une commande sur Serial2 et recueille la réponse pendant windowMs.
Exchange talk(const uint8_t* data, size_t size, uint32_t windowMs = REPLY_WINDOW_MS) {
    uint8_t packet[MAX_PACKET];
    const size_t length = buildPacket(packet, data, size);
    while (Serial2.available() > 0) {
        Serial2.read();
    }
    Serial2.write(packet, length);
    Serial2.flush();
    Exchange x;
    const uint32_t began = millis();
    while ((millis() - began) < windowMs && x.count < MAX_RX) {
        if (Serial2.available() > 0) {
            if (x.count == 0) {
                x.firstMs = millis() - began;
            }
            x.bytes[x.count++] = static_cast<uint8_t>(Serial2.read());
        } else {
            delay(1);
        }
    }
    classify(x, packet, length);
    return x;
}

const char* describeCode(uint8_t code) {
    switch (code) {
    case 0x00:
        return "ok";
    case 0x01:
        return "erreur de reception";
    case 0x13:
        return "mot de passe refuse";
    default:
        return "voir la fiche";
    }
}

void printHex(const uint8_t* data, size_t size) {
    for (size_t i = 0; i < size; i++) {
        Serial.printf("%s%02X", i == 0 ? "" : " ", data[i]);
    }
}

void report(const char* label, const Exchange& x) {
    Serial.printf("      %-16s : ", label);
    switch (x.kind) {
    case Reply::None:
        Serial.print(x.glitch ? "aucun octet utile (octet nul isole ecarte : ligne flottante ?)" : "aucun octet");
        break;
    case Reply::Echo:
        Serial.print("ECHO de la commande (TX relie a RX ?)");
        break;
    case Reply::Noise:
        Serial.printf("%u octet(s) sans trame valide (a +%lu ms)", static_cast<unsigned>(x.count), static_cast<unsigned long>(x.firstMs));
        break;
    case Reply::BadSum:
        Serial.print("trame de capteur, somme de controle fausse (ligne parasitee ?)");
        break;
    case Reply::Valid:
        Serial.printf("REPONSE VALIDE, code 0x%02X (%s), a +%lu ms", x.code, describeCode(x.code), static_cast<unsigned long>(x.firstMs));
        break;
    }
    if (x.count > 0) {
        Serial.print("  [");
        printHex(x.bytes, x.count);
        Serial.print("]");
    }
    Serial.println();
}

void note(Findings& f, const Exchange& x, uint32_t baud, bool swapped) {
    switch (x.kind) {
    case Reply::Valid:
        if (!f.valid) {
            f.valid = true;
            f.validBaud = baud;
            f.validSwapped = swapped;
            f.validCode = x.code;
        }
        break;
    case Reply::Echo:
        f.echo = true;
        break;
    case Reply::Noise:
    case Reply::BadSum:
        f.noise = true;
        break;
    case Reply::None:
        break;
    }
}

/// Pause qui garde la LED de la carte à jour : allumée tant que la ligne de touche est basse (doigt posé).
void waitMs(uint32_t ms) {
    const uint32_t began = millis();
    while ((millis() - began) < ms) {
        digitalWrite(PIN_LED, digitalRead(PIN_TOUCH) == LOW ? HIGH : LOW);
        delay(5);
    }
}

/// true si la broche reste haute malgré la résistance de rappel vers le bas : quelque chose la pilote.
bool drivenHigh(int pin) {
    pinMode(pin, INPUT_PULLDOWN);
    delay(5);
    int high = 0;
    for (int i = 0; i < 50; i++) {
        high += digitalRead(pin);
        delay(1);
    }
    Serial.printf("  GPIO %d (rappel vers le bas) : %d/50 a l'etat haut%s\n", pin, high, high >= 45 ? "  <== pilotee par quelque chose" : "");
    return high >= 45;
}

void stepLevels(Findings& f) {
    Serial.println("\n[1/5] Niveaux au repos");
    Serial2.end();
    f.rxHigh = drivenHigh(PIN_RX);
    f.txHigh = drivenHigh(PIN_TX);
    pinMode(PIN_TOUCH, INPUT);
    int high = 0;
    for (int i = 0; i < 20; i++) {
        high += digitalRead(PIN_TOUCH);
        delay(2);
    }
    f.touchRestHigh = high >= 18;
    Serial.printf("  GPIO %d (touche) : %d/20 a l'etat haut%s\n", PIN_TOUCH, high,
                  f.touchRestHigh ? "  (repos : le capteur est alimente)" : "  (basse ou flottante : fil blanc non alimente ?)");
}

void stepTouch(Findings& f) {
    Serial.printf("\n[2/5] Detecteur de doigt : posez un doigt sur le capteur puis retirez-le (%lu s)\n",
                  static_cast<unsigned long>(TOUCH_WINDOW_MS / 1000));
    pinMode(PIN_TOUCH, INPUT);
    uint32_t low = 0;
    bool previous = digitalRead(PIN_TOUCH) == LOW;
    const uint32_t began = millis();
    while ((millis() - began) < TOUCH_WINDOW_MS) {
        const bool finger = digitalRead(PIN_TOUCH) == LOW;
        digitalWrite(PIN_LED, finger ? HIGH : LOW);
        low += finger ? 1 : 0;
        if (finger != previous) {
            Serial.printf("  t=%.1f s : %s\n", (millis() - began) / 1000.0, finger ? "doigt detecte (ligne basse)" : "doigt retire (ligne haute)");
            previous = finger;
        }
        delay(10);
    }
    f.touchReacted = low >= 3;
    Serial.printf("  %s\n", f.touchReacted ? "le detecteur de doigt reagit" : "aucune reaction (doigt pose ? sinon : non teste)");
}

/// Décrit la configuration du capteur qui vient de répondre (même débit, mêmes fils).
void describeSensor() {
    const Exchange parameters = talk(CMD_READ_PARAMETERS, sizeof(CMD_READ_PARAMETERS));
    if (parameters.kind == Reply::Valid && beWord(parameters.bytes + parameters.frame + 7) >= 19) {
        const uint8_t* d = parameters.bytes + parameters.frame + 10;  // après le code de confirmation
        Serial.printf("      configuration    : capacite %u empreintes, securite %u, debit %lu bauds, adresse %02X%02X%02X%02X\n", beWord(d + 4),
                      beWord(d + 6), static_cast<unsigned long>(beWord(d + 14)) * 9600ul, d[8], d[9], d[10], d[11]);
    } else {
        report("configuration", parameters);
    }
    const Exchange count = talk(CMD_TEMPLATE_COUNT, sizeof(CMD_TEMPLATE_COUNT));
    if (count.kind == Reply::Valid && beWord(count.bytes + count.frame + 7) >= 5) {
        Serial.printf("      empreintes       : %u enregistree(s) dans le capteur\n", beWord(count.bytes + count.frame + 10));
    } else {
        report("empreintes", count);
    }
}

/// Ouvre l'UART2 vers le capteur. Une ligne série au repos est haute : on tire RX vers le haut avant de l'ouvrir (la broche
/// garde ce réglage), sinon le rappel vers le bas du test des niveaux la maintiendrait basse et l'UART y lirait un octet nul.
void openSensorPort(int rx, int tx, uint32_t baud) {
    Serial2.end();
    pinMode(rx, INPUT_PULLUP);
    Serial2.begin(baud, SERIAL_8N1, rx, tx);
}

/// Un débit, un sens de fils : poignée de main puis anneau rouge. true si le capteur a répondu.
bool probe(Findings& f, int rx, int tx, uint32_t baud, bool swapped) {
    openSensorPort(rx, tx, baud);
    delay(20);
    Serial.printf("  %6lu bauds\n", static_cast<unsigned long>(baud));
    const Exchange handshake = talk(CMD_VERIFY_PASSWORD, sizeof(CMD_VERIFY_PASSWORD));
    report("poignee de main", handshake);
    note(f, handshake, baud, swapped);
    Serial.println("      anneau ROUGE allume 1 s : regardez le capteur");
    const Exchange ring = talk(CMD_RING_RED_ON, sizeof(CMD_RING_RED_ON));
    report("commande anneau", ring);
    note(f, ring, baud, swapped);
    waitMs(RING_HOLD_MS);
    talk(CMD_RING_OFF, sizeof(CMD_RING_OFF), 50);
    const bool answered = handshake.kind == Reply::Valid || ring.kind == Reply::Valid;
    if (answered) {
        describeSensor();
    }
    return answered;
}

/// Anneau seul à 57600 bauds, fils normaux : rouge, bleu, violet, éteint, trois fois, une seconde chacun. Les couleurs
/// distinguent nos commandes d'une animation que le capteur ferait de lui-même (mise sous tension, redémarrage) :
/// un anneau qui suit la suite point par point reçoit nos commandes ; un anneau qui s'allume au hasard ne les reçoit pas
/// toutes (contact instable sur le marron, l'alimentation ou la masse) ou redémarre.
void stepRing(Findings& f) {
    Serial.println("\n[3/5] Anneau seul a 57600 bauds : ROUGE, BLEU, VIOLET, eteint (x3), 1 s chacun. Regardez et comptez");
    struct Color {
        const char* name;
        const uint8_t* data;
        size_t size;
    };
    const Color sequence[] = {{"ROUGE", CMD_RING_RED_ON, sizeof(CMD_RING_RED_ON)},
                              {"BLEU", CMD_RING_BLUE_ON, sizeof(CMD_RING_BLUE_ON)},
                              {"VIOLET", CMD_RING_PURPLE_ON, sizeof(CMD_RING_PURPLE_ON)},
                              {"eteint", CMD_RING_OFF, sizeof(CMD_RING_OFF)}};
    openSensorPort(PIN_RX, PIN_TX, 57600);
    delay(20);
    const uint32_t began = millis();
    for (int round = 1; round <= 3; round++) {
        for (const Color& color : sequence) {
            Serial.printf("  t=%4.1f s : commande %-6s", (millis() - began) / 1000.0, color.name);
            const Exchange reply = talk(color.data, color.size, 100);
            note(f, reply, 57600, false);
            if (reply.kind == Reply::None) {
                Serial.println();
            } else {
                Serial.print("  -> ");
                report("reponse", reply);
            }
            waitMs(900);
        }
    }
    Serial2.end();
}

void stepDialogue(Findings& f) {
    Serial.println("\n[4/5] Dialogue et anneau (jaune->GPIO 16, marron->GPIO 17)");
    // Jamais d'émission sur une ligne qu'un capteur vivant pourrait piloter : deux sorties face à face s'abîment.
    const bool wiresSwapped = f.txHigh && !f.rxHigh;  // le capteur pilote GPIO 17 : fils jaune et marron inversés
    const bool normalAllowed = !wiresSwapped;
    const bool swappedAllowed = !f.rxHigh;
    if (!normalAllowed) {
        Serial.println("  GPIO 17 est pilotee, GPIO 16 non : fils jaune et marron probablement inverses. Sens normal non essaye.");
    }
    for (int pass = 0; pass < 2; pass++) {
        const bool swapped = pass == 1;
        if (swapped ? !swappedAllowed : !normalAllowed) {
            if (swapped) {
                Serial.println("  Sens croise non essaye : GPIO 16 est pilotee par le capteur, on n'y emet pas.");
            }
            continue;
        }
        const int rx = swapped ? PIN_TX : PIN_RX;
        const int tx = swapped ? PIN_RX : PIN_TX;
        const size_t tries = swapped ? BAUD_SWAPPED_COUNT : BAUD_SCAN_COUNT;
        Serial.printf(" -- %s : RX=GPIO %d, TX=GPIO %d\n", swapped ? "FILS CROISES" : "sens normal", rx, tx);
        for (size_t i = 0; i < tries; i++) {
            if (probe(f, rx, tx, BAUD_SCAN[i], swapped)) {
                Serial2.end();
                return;  // le capteur répond : inutile d'essayer les autres débits
            }
        }
    }
    Serial2.end();
}

void stepListen(Findings& f) {
    Serial.printf("\n[5/5] Ecoute passive %lu s : debranchez puis rebranchez le fil ROUGE du capteur pour voir s'il parle a la mise sous tension\n",
                  static_cast<unsigned long>(LISTEN_MS / 1000));
    const bool swapped = f.txHigh && !f.rxHigh;
    openSensorPort(swapped ? PIN_TX : PIN_RX, swapped ? PIN_RX : PIN_TX, 57600);
    while (Serial2.available() > 0) {
        Serial2.read();
    }
    size_t count = 0;
    size_t nonZero = 0;
    const uint32_t began = millis();
    while ((millis() - began) < LISTEN_MS) {
        if (Serial2.available() > 0) {
            const int value = Serial2.read();
            Serial.printf("%s%02X", count == 0 ? "  octets recus : " : " ", value);
            count++;
            nonZero += value != 0 ? 1 : 0;
        }
        digitalWrite(PIN_LED, digitalRead(PIN_TOUCH) == LOW ? HIGH : LOW);
        delay(1);
    }
    Serial2.end();
    Serial.println(count == 0 ? "  rien recu" : "");
    f.noise = f.noise || nonZero > 0 || count > MAX_NULL_GLITCH;
}

void printReport(const Findings& f) {
    Serial.println("\n===== BILAN =====");
    Serial.printf("  Alimentation de la touche (ligne GPIO 34 haute) : %s\n", f.touchRestHigh ? "OK" : "ANORMALE");
    Serial.printf("  Detecteur de doigt                              : %s\n", f.touchReacted ? "OK (a reagi)" : "non teste ou HS");
    Serial.printf("  Processeur du capteur (reponse a une commande)  : %s\n", f.valid ? "OK" : "AUCUNE REPONSE");
    if (f.valid) {
        Serial.printf("  => CAPTEUR OK : reponse valide a %lu bauds, fils %s, code 0x%02X (%s).\n", static_cast<unsigned long>(f.validBaud),
                      f.validSwapped ? "CROISES" : "normaux", f.validCode, describeCode(f.validCode));
        if (f.validSwapped) {
            Serial.println("     Fils jaune et marron inverses : le firmware principal attend le jaune sur GPIO 16 (RX) et le marron sur GPIO 17 (TX).");
        }
        if (f.validBaud != 57600) {
            Serial.println("     Debit different de FP_BAUD (57600, firmware/include/config.h) : a corriger dans config.h ou a remettre dans le capteur.");
        }
        if (f.validCode == 0x13) {
            Serial.println("     Mot de passe refuse : le capteur a un mot de passe different de 0 (FP_PASSWORD dans config.h).");
        }
        if (!f.touchReacted) {
            Serial.println("     Detecteur de doigt non vu : posez un doigt pendant l'etape 2 au prochain cycle.");
        }
        Serial.println("     Fiabilite : debranchez puis rebranchez le fil ROUGE 3 fois, la reponse doit revenir a chaque fois.");
    } else if (f.echo) {
        Serial.println("  => ECHO : ce qui part sur GPIO 17 revient sur GPIO 16. Si 16 et 17 sont reliees expres, la carte est bonne.");
        Serial.println("     Sinon : fils jaune et marron en contact entre eux, ou TX relie a RX.");
    } else if (f.noise) {
        Serial.println("  => OCTETS RECUS SANS REPONSE VALIDE : parasite sur la ligne, ou capteur defectueux. Refaire avec d'autres duponts,");
        Serial.println("     fils courts, et sans autre module branche.");
    } else if (!f.touchRestHigh) {
        Serial.println("  => LIGNE DE TOUCHE BASSE : le fil BLANC (3V3T) n'est pas alimente, ou le fil bleu touche la masse.");
        Serial.println("     Mesurez la tension entre blanc et noir au connecteur du capteur (3,3 V attendus).");
    } else if (f.rxHigh) {
        Serial.println("  => LIGNE RX TENUE HAUTE mais aucune reponse : le capteur est vivant (son TX est au repos), mais ne comprend pas.");
        Serial.println("     Fil MARRON coupe (la commande n'arrive pas), ou adresse du module changee (autre que FFFFFFFF).");
    } else {
        Serial.println("  => SILENCE TOTAL : le processeur du capteur ne repond pas, et sa ligne TX n'est jamais a l'etat haut.");
        Serial.println("     La touche est alimentee (fil blanc), donc la carte, les fils bleu/blanc/noir et la masse sont bons.");
        Serial.println("     Departagez avec l'ANNEAU de l'etape 3 :");
        Serial.println("       - anneau ROUGE vu : le capteur recoit et vit. Le fil JAUNE est coupe ou mal serti, ou GPIO 16 est HS :");
        Serial.println("         ohmmetre bout a bout sur le jaune (carte <-> capteur), changez le dupont ;");
        Serial.println("       - anneau qui s'allume par moments, pas dans l'ordre rouge/bleu/violet : contact instable (marron, rouge ou noir) ou");
        Serial.println("         capteur qui redemarre : bougez les fils pendant l'etape 3, soudez ou changez le connecteur ;");
        Serial.println("       - anneau jamais allume : mesurez 3,3 V entre ROUGE et NOIR directement au connecteur du capteur.");
        Serial.println("         3,3 V presents et anneau mort : capteur defectueux, a remplacer. Pas de 3,3 V : fil rouge ou broche 3V3.");
    }
}

}  // namespace

void setup() {
    Serial.begin(CONSOLE_BAUD);
    pinMode(PIN_LED, OUTPUT);
    for (int i = 0; i < 3; i++) {  // trois clignotements : la carte tourne (et la LED GPIO 2 existe, si on la voit)
        digitalWrite(PIN_LED, HIGH);
        delay(100);
        digitalWrite(PIN_LED, LOW);
        delay(100);
    }
    delay(1000);
    Serial.println("\n# BANC D'ESSAI R503 (firmware independant, ne pas laisser sur la carte)");
    Serial.println("# Brochage : jaune->GPIO 16, marron->GPIO 17, bleu->GPIO 34, rouge+blanc->3V3, noir->GND");
}

void loop() {
    static uint32_t cycle = 0;
    Findings findings;
    Serial.printf("\n######## CYCLE %lu (depuis %lu s) ########\n", static_cast<unsigned long>(++cycle),
                  static_cast<unsigned long>(millis() / 1000));
    stepLevels(findings);
    stepTouch(findings);
    if (!(findings.txHigh && !findings.rxHigh)) {  // sens normal permis : voir stepDialogue
        stepRing(findings);
    }
    stepDialogue(findings);
    if (!findings.valid) {
        stepListen(findings);
    }
    printReport(findings);
    waitMs(CYCLE_PAUSE_MS);
}
