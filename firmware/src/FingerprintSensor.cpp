#include "FingerprintSensor.h"

#include "config.h"

namespace {
// Codes d'instruction du capteur (fiche R503).
constexpr uint8_t CMD_GET_IMAGE = 0x01;
constexpr uint8_t CMD_EXTRACT = 0x02;
constexpr uint8_t CMD_SEARCH = 0x04;
constexpr uint8_t CMD_CREATE_MODEL = 0x05;
constexpr uint8_t CMD_STORE = 0x06;
constexpr uint8_t CMD_DELETE = 0x0C;
constexpr uint8_t CMD_EMPTY = 0x0D;
constexpr uint8_t CMD_INDEX_TABLE = 0x1F;
constexpr uint8_t CMD_VERIFY_PASSWORD = 0x13;
constexpr uint8_t CMD_READ_PARAMETERS = 0x0F;
constexpr uint8_t CMD_TEMPLATE_COUNT = 0x1D;
constexpr uint8_t CMD_AURA = 0x35;
constexpr uint8_t CMD_HANDSHAKE = 0x40;

/// Entier grand-boutiste sur 16 bits.
uint16_t word(const uint8_t* data) {
    return static_cast<uint16_t>((data[0] << 8) | data[1]);
}
}  // namespace

FingerprintSensor::FingerprintSensor(HardwareSerial& serial, int rxPin, int txPin, int touchPin)
    : _serial(serial), _rxPin(rxPin), _txPin(txPin), _touchPin(touchPin) {}

bool FingerprintSensor::begin(uint32_t baud, uint32_t password) {
    pinMode(_touchPin, INPUT);
    _serial.begin(baud, SERIAL_8N1, _rxPin, _txPin);
    const uint8_t command[] = {CMD_VERIFY_PASSWORD, static_cast<uint8_t>(password >> 24),
                               static_cast<uint8_t>(password >> 16), static_cast<uint8_t>(password >> 8),
                               static_cast<uint8_t>(password)};
    _present = exchange(command, sizeof(command)) == OK;
    return _present;
}

FingerprintSensor::Code FingerprintSensor::handShake() {
    const uint8_t command[] = {CMD_HANDSHAKE};
    return exchange(command, sizeof(command));
}

FingerprintSensor::Code FingerprintSensor::readParameters(Parameters& parameters) {
    const uint8_t command[] = {CMD_READ_PARAMETERS};
    const Code code = exchange(command, sizeof(command));
    // Réponse : code (1) puis état (2), identifiant (2), capacité (2), sécurité (2), adresse (4),
    // taille de paquet (2, en code 0 à 3 = 32, 64, 128, 256 octets), débit (2, en multiples de 9600).
    if (code != OK || _parser.length() < 17) {
        return code == OK ? BAD_REPLY : code;
    }
    const uint8_t* data = _parser.payload() + 1;
    parameters.libraryCapacity = word(data + 4);
    _capacity = parameters.libraryCapacity;
    parameters.securityLevel = word(data + 6);
    parameters.address = (static_cast<uint32_t>(word(data + 8)) << 16) | word(data + 10);
    parameters.packetBytes = static_cast<uint16_t>(32u << (word(data + 12) & 0x3));
    parameters.baud = static_cast<uint32_t>(word(data + 14)) * 9600u;
    return OK;
}

FingerprintSensor::Code FingerprintSensor::templateCount(uint16_t& count) {
    const uint8_t command[] = {CMD_TEMPLATE_COUNT};
    const Code code = exchange(command, sizeof(command));
    if (code != OK || _parser.length() < 3) {
        return code == OK ? BAD_REPLY : code;
    }
    count = word(_parser.payload() + 1);
    return OK;
}

FingerprintSensor::Code FingerprintSensor::setAura(Aura effect, Color color, uint8_t speed, uint8_t times) {
    const uint8_t command[] = {CMD_AURA, static_cast<uint8_t>(effect), speed, static_cast<uint8_t>(color), times};
    return exchange(command, sizeof(command));
}

FingerprintSensor::Code FingerprintSensor::listTemplates(uint16_t* ids, size_t maxIds, size_t& found) {
    found = 0;
    // La table d'index tient en pages de 256 emplacements (32 octets, un bit chacun).
    for (uint16_t page = 0; page * 256u < _capacity; ++page) {
        const uint8_t command[] = {CMD_INDEX_TABLE, static_cast<uint8_t>(page)};
        const Code code = exchange(command, sizeof(command));
        if (code != OK || _parser.length() < 33) {
            return code == OK ? BAD_REPLY : code;
        }
        found += FingerprintPacket::idsFromIndexTable(_parser.payload() + 1, 32, static_cast<uint16_t>(page * 256u),
                                                      _capacity, ids + found, found < maxIds ? maxIds - found : 0);
    }
    return OK;
}

FingerprintSensor::Code FingerprintSensor::remove(uint16_t id) {
    const uint8_t command[] = {CMD_DELETE, static_cast<uint8_t>(id >> 8), static_cast<uint8_t>(id), 0x00, 0x01};
    return exchange(command, sizeof(command), FP_SLOW_REPLY_TIMEOUT_MS);
}

FingerprintSensor::Code FingerprintSensor::removeAll() {
    const uint8_t command[] = {CMD_EMPTY};
    return exchange(command, sizeof(command), FP_SLOW_REPLY_TIMEOUT_MS);
}

bool FingerprintSensor::touched() const {
    return (digitalRead(_touchPin) == HIGH) != FP_TOUCH_ACTIVE_LOW;
}

FingerprintSensor::Code FingerprintSensor::getImage() {
    const uint8_t command[] = {CMD_GET_IMAGE};
    return exchange(command, sizeof(command), FP_SLOW_REPLY_TIMEOUT_MS);
}

FingerprintSensor::Code FingerprintSensor::extract(uint8_t buffer) {
    const uint8_t command[] = {CMD_EXTRACT, buffer};
    return exchange(command, sizeof(command), FP_SLOW_REPLY_TIMEOUT_MS);
}

FingerprintSensor::Code FingerprintSensor::search(uint8_t buffer, uint16_t& id, uint16_t& score) {
    const uint8_t command[] = {CMD_SEARCH, buffer, 0x00, 0x00, static_cast<uint8_t>(_capacity >> 8),
                               static_cast<uint8_t>(_capacity)};
    const Code code = exchange(command, sizeof(command), FP_SLOW_REPLY_TIMEOUT_MS);
    if (code != OK) {
        return code;
    }
    if (_parser.length() < 5) {
        return BAD_REPLY;
    }
    id = word(_parser.payload() + 1);
    score = word(_parser.payload() + 3);
    return OK;
}

FingerprintSensor::Code FingerprintSensor::createModel() {
    const uint8_t command[] = {CMD_CREATE_MODEL};
    return exchange(command, sizeof(command), FP_SLOW_REPLY_TIMEOUT_MS);
}

FingerprintSensor::Code FingerprintSensor::store(uint8_t buffer, uint16_t id) {
    const uint8_t command[] = {CMD_STORE, buffer, static_cast<uint8_t>(id >> 8), static_cast<uint8_t>(id)};
    return exchange(command, sizeof(command), FP_SLOW_REPLY_TIMEOUT_MS);
}

const char* FingerprintSensor::describe(Code code) {
    switch (code) {
    case OK:
        return "ok";
    case NO_FINGER:
        return "pas de doigt";
    case IMAGE_MESSY:
        return "image brouillee";
    case FEATURES_TOO_FEW:
        return "pas assez de details sur le doigt";
    case NO_MATCH_IN_LIBRARY:
        return "empreinte inconnue";
    case MODEL_MISMATCH:
        return "les deux poses ne sont pas le meme doigt";
    case BAD_LOCATION:
        return "emplacement hors de la bibliotheque";
    case FLASH_ERROR:
        return "ecriture impossible dans le capteur";
    case WRONG_PASSWORD:
        return "mot de passe refuse";
    case BAD_CHECKSUM:
        return "reponse alteree (somme de controle)";
    case BAD_REPLY:
        return "reponse inattendue";
    case TIMEOUT:
        return "pas de reponse (cablage RX/TX, debit, alimentation 3,3 V)";
    default:
        return "code du capteur (voir la fiche)";
    }
}

FingerprintSensor::Code FingerprintSensor::exchange(const uint8_t* command, size_t length, uint32_t timeoutMs) {
    uint8_t packet[FingerprintPacket::MAX_PACKET];
    const size_t size = FingerprintPacket::build(_address, FingerprintPacket::Command, command, length, packet,
                                                 sizeof(packet));
    if (size == 0) {
        return BAD_REPLY;
    }
    while (_serial.available() > 0) {
        _serial.read();  // jette les octets en retard d'un échange précédent
    }
    _serial.write(packet, size);

    _parser = FingerprintPacket::Parser(_address);
    const uint32_t began = millis();
    Code code = TIMEOUT;
    while ((millis() - began) < timeoutMs) {
        bool finished = false;
        while (_serial.available() > 0 && !finished) {
            const auto status = _parser.feed(static_cast<uint8_t>(_serial.read()));
            if (status == FingerprintPacket::Parser::Status::Incomplete) {
                continue;
            }
            finished = true;
            if (status == FingerprintPacket::Parser::Status::Done) {
                code = (_parser.pid() != FingerprintPacket::Ack || _parser.length() < 1) ? BAD_REPLY
                                                                                          : _parser.payload()[0];
            } else {
                code = status == FingerprintPacket::Parser::Status::BadChecksum ? BAD_CHECKSUM : BAD_REPLY;
            }
        }
        if (finished) {
            break;
        }
        delay(1);
    }
    // Les codes attendus en fonctionnement normal restent muets ; tout le reste est dit, avec la commande
    // et le temps passé : c'est ce qui permet de comprendre un échec sans rien deviner.
    if (code != OK && code != NO_FINGER && code != NO_MATCH_IN_LIBRARY) {
        Serial.printf("# empreinte : commande 0x%02X -> code 0x%02X (%s) apres %lu ms\n", command[0], code,
                      describe(code), static_cast<unsigned long>(millis() - began));
    }
    return code;
}
