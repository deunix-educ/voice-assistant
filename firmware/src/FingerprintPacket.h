#pragma once

#include <stddef.h>
#include <stdint.h>

/**
 * @file FingerprintPacket.h
 * @brief Paquets du protocole UART des capteurs d'empreinte Grow (R503, R50x).
 *
 * Aucune dépendance à Arduino : ce fichier se compile et se teste sur le PC
 * (server/tests/test_fingerprint_packet.py). Format d'un paquet, octets en
 * grand-boutiste (poids fort d'abord) :
 *
 *   EF 01 | adresse (4) | PID (1) | longueur (2) | contenu | somme (2)
 *
 * La longueur compte le contenu ET la somme. La somme est celle des octets
 * PID, longueur et contenu, sur 16 bits. Inspiré du protocole documenté par la
 * bibliothèque R503 de mpagnoulle (Apache-2.0) et par la fiche du capteur.
 */
namespace FingerprintPacket {

static constexpr uint16_t START_CODE = 0xEF01;

/// Adresse d'usine du capteur.
static constexpr uint32_t DEFAULT_ADDRESS = 0xFFFFFFFF;

/// Identifiant de paquet (PID).
enum Pid : uint8_t {
    Command = 0x01,  ///< commande, du microcontrôleur vers le capteur
    Ack = 0x07,      ///< réponse du capteur
};

/// Octets avant le contenu : début (2) + adresse (4) + PID (1) + longueur (2).
static constexpr size_t HEADER_BYTES = 9;
static constexpr size_t CHECKSUM_BYTES = 2;

/// Contenu maximal reçu : la fiche produit R503 (46 octets + code de retour) est la plus longue.
static constexpr size_t MAX_PAYLOAD = 64;

/// Taille maximale d'un paquet complet.
static constexpr size_t MAX_PACKET = HEADER_BYTES + MAX_PAYLOAD + CHECKSUM_BYTES;

/**
 * @brief Écrit un paquet complet dans @p out.
 * @param address Adresse du capteur.
 * @param pid     Type de paquet.
 * @param payload Contenu (le code d'instruction en premier).
 * @param length  Taille du contenu.
 * @param out     Tampon de sortie.
 * @param outSize Taille du tampon.
 * @return Nombre d'octets écrits, 0 si le contenu est trop long ou le tampon trop petit.
 */
inline size_t build(uint32_t address, uint8_t pid, const uint8_t* payload, size_t length, uint8_t* out,
                    size_t outSize) {
    const size_t total = HEADER_BYTES + length + CHECKSUM_BYTES;
    if (length > MAX_PAYLOAD || total > outSize) {
        return 0;
    }
    out[0] = static_cast<uint8_t>(START_CODE >> 8);
    out[1] = static_cast<uint8_t>(START_CODE & 0xFF);
    for (int i = 0; i < 4; ++i) {
        out[2 + i] = static_cast<uint8_t>(address >> (24 - 8 * i));
    }
    out[6] = pid;
    const uint16_t declared = static_cast<uint16_t>(length + CHECKSUM_BYTES);
    out[7] = static_cast<uint8_t>(declared >> 8);
    out[8] = static_cast<uint8_t>(declared & 0xFF);
    uint16_t sum = static_cast<uint16_t>(pid + out[7] + out[8]);
    for (size_t i = 0; i < length; ++i) {
        out[HEADER_BYTES + i] = payload[i];
        sum = static_cast<uint16_t>(sum + payload[i]);
    }
    out[HEADER_BYTES + length] = static_cast<uint8_t>(sum >> 8);
    out[HEADER_BYTES + length + 1] = static_cast<uint8_t>(sum & 0xFF);
    return total;
}

/**
 * @brief Décodeur octet par octet d'un paquet reçu. Pas d'allocation, pas d'attente.
 *
 * Les octets qui précèdent le code de début sont ignorés : le capteur et la carte
 * peuvent se retrouver désynchronisés après un parasite.
 */
class Parser {
public:
    enum class Status : uint8_t {
        Incomplete,       ///< il faut encore des octets
        Done,             ///< paquet complet et valide : voir pid(), payload(), length()
        BadChecksum,      ///< somme de contrôle fausse
        TooLong,          ///< contenu annoncé plus grand que MAX_PAYLOAD
        AddressMismatch,  ///< le paquet n'est pas pour cette adresse
    };

    /// @param address Adresse attendue ; les paquets d'une autre adresse sont refusés.
    explicit Parser(uint32_t address = DEFAULT_ADDRESS) : _expected(address) {}

    /// Recommence un paquet.
    void reset() {
        _state = State::Start1;
        _count = 0;
    }

    /// Fournit l'octet suivant.
    Status feed(uint8_t byte) {
        switch (_state) {
        case State::Start1:
            if (byte == (START_CODE >> 8)) {
                _state = State::Start2;
            }
            return Status::Incomplete;
        case State::Start2:
            _state = (byte == (START_CODE & 0xFF)) ? State::Address
                                                   : (byte == (START_CODE >> 8) ? State::Start2 : State::Start1);
            _count = 0;
            _address = 0;
            return Status::Incomplete;
        case State::Address:
            _address = (_address << 8) | byte;
            if (++_count == 4) {
                _state = State::Pid;
            }
            return Status::Incomplete;
        case State::Pid:
            _pid = byte;
            _sum = byte;
            _state = State::LengthHigh;
            return Status::Incomplete;
        case State::LengthHigh:
            _declared = static_cast<uint16_t>(byte << 8);
            _sum = static_cast<uint16_t>(_sum + byte);
            _state = State::LengthLow;
            return Status::Incomplete;
        case State::LengthLow:
            _declared = static_cast<uint16_t>(_declared | byte);
            _sum = static_cast<uint16_t>(_sum + byte);
            if (_declared < CHECKSUM_BYTES || _declared - CHECKSUM_BYTES > MAX_PAYLOAD) {
                reset();
                return Status::TooLong;
            }
            _length = static_cast<size_t>(_declared - CHECKSUM_BYTES);
            _count = 0;
            _state = _length > 0 ? State::Payload : State::SumHigh;
            return Status::Incomplete;
        case State::Payload:
            _payload[_count++] = byte;
            _sum = static_cast<uint16_t>(_sum + byte);
            if (_count == _length) {
                _state = State::SumHigh;
            }
            return Status::Incomplete;
        case State::SumHigh:
            _received = static_cast<uint16_t>(byte << 8);
            _state = State::SumLow;
            return Status::Incomplete;
        case State::SumLow:
            _received = static_cast<uint16_t>(_received | byte);
            _state = State::Start1;
            if (_received != _sum) {
                return Status::BadChecksum;
            }
            return _address == _expected ? Status::Done : Status::AddressMismatch;
        }
        return Status::Incomplete;
    }

    uint8_t pid() const { return _pid; }
    const uint8_t* payload() const { return _payload; }
    size_t length() const { return _length; }

private:
    enum class State : uint8_t { Start1, Start2, Address, Pid, LengthHigh, LengthLow, Payload, SumHigh, SumLow };

    uint32_t _expected;
    State _state = State::Start1;
    uint32_t _address = 0;
    uint8_t _pid = 0;
    uint16_t _declared = 0;
    size_t _length = 0;
    size_t _count = 0;
    uint16_t _sum = 0;
    uint16_t _received = 0;
    uint8_t _payload[MAX_PAYLOAD] = {};
};

}  // namespace FingerprintPacket
