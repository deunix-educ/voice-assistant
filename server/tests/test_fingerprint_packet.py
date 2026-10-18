"""Tests du format de paquet du capteur d'empreinte R503 (branche fingerprint, étape F1).

Le code testé est celui du firmware (firmware/src/FingerprintPacket.h, sans dépendance à
Arduino) : un petit programme C++ le compile avec g++ et le test compare ses sorties à des
paquets de référence. Sans g++, les tests sont ignorés.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

FIRMWARE_SRC = Path(__file__).resolve().parents[2] / "firmware" / "src"

DRIVER = r"""
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "FingerprintPacket.h"

using namespace FingerprintPacket;

static std::vector<uint8_t> fromHex(const char* text) {
    std::vector<uint8_t> out;
    for (size_t i = 0; text[i] && text[i + 1]; i += 2) {
        char pair[3] = {text[i], text[i + 1], 0};
        out.push_back(static_cast<uint8_t>(strtoul(pair, nullptr, 16)));
    }
    return out;
}

static void printHex(const uint8_t* data, size_t length) {
    for (size_t i = 0; i < length; ++i) printf("%02X", data[i]);
}

int main(int argc, char** argv) {
    const std::string mode = argv[1];
    const uint32_t address = static_cast<uint32_t>(strtoul(argv[2], nullptr, 16));
    if (mode == "build") {
        const auto payload = fromHex(argv[4]);
        uint8_t out[MAX_PACKET];
        const size_t size = build(address, static_cast<uint8_t>(strtoul(argv[3], nullptr, 16)), payload.data(),
                                  payload.size(), out, argc > 5 ? static_cast<size_t>(atoi(argv[5])) : sizeof(out));
        printHex(out, size);
        printf("\n");
        return 0;
    }
    Parser parser(address);
    for (uint8_t byte : fromHex(argv[3])) {
        const auto status = parser.feed(byte);
        const char* name = nullptr;
        switch (status) {
        case Parser::Status::Incomplete: break;
        case Parser::Status::Done: name = "DONE"; break;
        case Parser::Status::BadChecksum: name = "BAD_CHECKSUM"; break;
        case Parser::Status::TooLong: name = "TOO_LONG"; break;
        case Parser::Status::AddressMismatch: name = "ADDRESS_MISMATCH"; break;
        }
        if (name) {
            printf("%s pid=%02X payload=", name, parser.pid());
            printHex(parser.payload(), status == Parser::Status::Done ? parser.length() : 0);
            printf("\n");
        }
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def driver(tmp_path_factory: pytest.TempPathFactory) -> Callable[..., str]:
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("g++ absent : test du format de paquet ignore")
    directory = tmp_path_factory.mktemp("fingerprint")
    source = directory / "driver.cpp"
    source.write_text(DRIVER, encoding="utf-8")
    binary = directory / "driver"
    subprocess.run([compiler, "-std=c++17", "-Wall", "-Werror", f"-I{FIRMWARE_SRC}", str(source), "-o", str(binary)],
                   check=True)

    def run(*arguments: str) -> str:
        return subprocess.run([str(binary), *arguments], check=True, capture_output=True, text=True).stdout.strip()

    return run


DEFAULT_ADDRESS = "FFFFFFFF"


def test_verify_password_packet_matches_the_reference(driver: Callable[..., str]) -> None:
    """Paquet « VfyPwd » mot de passe 0, adresse d'usine : l'exemple de la documentation Grow."""
    assert driver("build", DEFAULT_ADDRESS, "01", "1300000000") == "EF01FFFFFFFF0100071300000000001B"


def test_checksum_covers_pid_length_and_payload(driver: Callable[..., str]) -> None:
    # Aura : 0x35 contrôle 3 vitesse 0 couleur 2 répétitions 0 ; somme = 01 + 00 + 07 + 35 + 03 + 00 + 02 + 00 = 0x42
    assert driver("build", DEFAULT_ADDRESS, "01", "3503000200").endswith("0042")


def test_address_is_big_endian(driver: Callable[..., str]) -> None:
    assert driver("build", "12345678", "01", "40").startswith("EF0112345678")


def test_oversized_payload_or_buffer_is_refused(driver: Callable[..., str]) -> None:
    assert driver("build", DEFAULT_ADDRESS, "01", "13" * 65) == ""      # contenu trop long
    assert driver("build", DEFAULT_ADDRESS, "01", "13", "10") == ""     # tampon trop petit (12 octets utiles)


def test_acknowledgement_is_decoded(driver: Callable[..., str]) -> None:
    assert driver("parse", DEFAULT_ADDRESS, "EF01FFFFFFFF070003" + "00" + "000A") == "DONE pid=07 payload=00"


def test_templates_count_reply_carries_its_data(driver: Callable[..., str]) -> None:
    # code 00, puis 0x0005 : 5 empreintes. Somme = 07 + 00 + 05 + 00 + 00 + 05 = 0x11
    assert driver("parse", DEFAULT_ADDRESS, "EF01FFFFFFFF070005" + "00" + "0005" + "0011") == "DONE pid=07 payload=000005"


def test_noise_before_the_packet_is_skipped(driver: Callable[..., str]) -> None:
    packet = "EF01FFFFFFFF070003" + "00" + "000A"
    assert driver("parse", DEFAULT_ADDRESS, "00EFEF" + "55" + packet) == "DONE pid=07 payload=00"


def test_two_packets_in_a_row(driver: Callable[..., str]) -> None:
    packet = "EF01FFFFFFFF070003" + "00" + "000A"
    assert driver("parse", DEFAULT_ADDRESS, packet + packet).splitlines() == ["DONE pid=07 payload=00"] * 2


def test_corrupted_packet_is_rejected_then_the_next_one_is_read(driver: Callable[..., str]) -> None:
    good = "EF01FFFFFFFF070003" + "00" + "000A"
    bad = "EF01FFFFFFFF070003" + "00" + "000B"
    assert driver("parse", DEFAULT_ADDRESS, bad + good).splitlines() == ["BAD_CHECKSUM pid=07 payload=",
                                                                         "DONE pid=07 payload=00"]


def test_packet_for_another_address_is_refused(driver: Callable[..., str]) -> None:
    assert driver("parse", "00000001", "EF01FFFFFFFF070003" + "00" + "000A") == "ADDRESS_MISMATCH pid=07 payload="


def test_announced_length_beyond_the_buffer_is_refused(driver: Callable[..., str]) -> None:
    # longueur 0x0100 = 256 octets de contenu : plus que MAX_PAYLOAD
    assert driver("parse", DEFAULT_ADDRESS, "EF01FFFFFFFF070100").startswith("TOO_LONG")


def test_every_packet_built_is_decoded_back(driver: Callable[..., str]) -> None:
    """Aller-retour : ce que build() écrit, Parser le relit à l'identique (contenus de 0 à 64 octets)."""
    for size in (0, 1, 5, 17, 47, 64):
        payload = "".join(f"{(7 * i + 3) % 256:02X}" for i in range(size))
        packet = driver("build", "A1B2C3D4", "07", payload)
        assert driver("parse", "A1B2C3D4", packet) == f"DONE pid=07 payload={payload}"
