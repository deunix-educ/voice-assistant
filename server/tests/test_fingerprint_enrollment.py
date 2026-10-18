"""Tests de l'enregistrement d'une empreinte (branche fingerprint, étape F2).

Le déroulement (firmware/src/FingerprintEnrollment.h) est compilé avec g++ et joué contre un
faux capteur dont on scripte les doigts et les réponses. Sans g++, les tests sont ignorés.
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
#include <map>
#include <string>
#include <vector>

#include "FingerprintEnrollment.h"
#include "FingerprintPacket.h"

using Step = FingerprintEnrollment::Step;
using Port = FingerprintPort;

// Faux capteur : le doigt est posé pendant des intervalles [début, fin[ (ms), les réponses sont scriptées.
struct FakePort : Port {
    uint32_t now = 0;
    std::vector<std::pair<uint32_t, uint32_t>> touches;
    std::vector<Code> extract1, extract2;  // réponses successives ; OK quand la liste est épuisée
    Code searchCode = NO_MATCH_IN_LIBRARY;
    uint16_t searchId = 0;
    Code modelCode = OK, storeCode = OK;
    int getImages = 0;
    std::string aura;

    bool touched() const override {
        for (const auto& t : touches) if (now >= t.first && now < t.second) return true;
        return false;
    }
    Code getImage() override { ++getImages; return touched() ? OK : NO_FINGER; }
    Code extract(uint8_t buffer) override {
        auto& list = buffer == 1 ? extract1 : extract2;
        if (list.empty()) return OK;
        Code code = list.front();
        list.erase(list.begin());
        return code;
    }
    Code search(uint8_t, uint16_t& id, uint16_t& score) override { id = searchId; score = 90; return searchCode; }
    Code createModel() override { return modelCode; }
    Code store(uint8_t, uint16_t) override { return storeCode; }
    Code setAura(Aura effect, Color color, uint8_t, uint8_t) override {
        aura += "[" + std::to_string(static_cast<int>(effect)) + "/" + std::to_string(static_cast<int>(color)) + "]";
        return OK;
    }
};

static std::string events;

static void record(Step step, uint16_t id, const char* reason) {
    const char* names[] = {"start", "retry", "remove", "again", "done", "failed"};
    if (!events.empty()) events += ",";
    events += names[static_cast<int>(step)];
    if (reason) events += std::string(":") + reason;
    events += ":" + std::to_string(id);
}

static std::vector<std::pair<uint32_t, uint32_t>> parseTouches(const char* text) {
    std::vector<std::pair<uint32_t, uint32_t>> out;   // "100-600,1000-1600"
    const char* p = text;
    while (*p) {
        char* end;
        uint32_t a = strtoul(p, &end, 10);
        uint32_t b = strtoul(end + 1, &end, 10);
        out.push_back({a, b});
        p = *end == ',' ? end + 1 : end;
    }
    return out;
}

static std::vector<Port::Code> parseCodes(const char* text) {
    std::vector<Port::Code> out;
    for (const char* p = text; *p;) {
        char* end;
        out.push_back(static_cast<Port::Code>(strtoul(p, &end, 16)));
        p = *end == ',' ? end + 1 : end;
    }
    return out;
}

int main(int argc, char** argv) {
    const std::string mode = argv[1];
    if (mode == "index") {   // index <hex> <premier> <limite> <maxIds>
        std::vector<uint8_t> bitmap;
        for (const char* p = argv[2]; p[0] && p[1]; p += 2) {
            char pair[3] = {p[0], p[1], 0};
            bitmap.push_back(static_cast<uint8_t>(strtoul(pair, nullptr, 16)));
        }
        uint16_t ids[16] = {};
        const size_t found = FingerprintPacket::idsFromIndexTable(bitmap.data(), bitmap.size(), atoi(argv[3]),
                                                                  atoi(argv[4]), ids, atoi(argv[5]));
        printf("%zu:", found);
        for (size_t i = 0; i < found && i < static_cast<size_t>(atoi(argv[5])); ++i) printf("%s%u", i ? "," : "", ids[i]);
        printf("\n");
        return 0;
    }
    // run <id> <touches> <total ms> [extract1] [extract2] [search code] [search id] [model] [store] [cancel at ms]
    FakePort port;
    FingerprintEnrollment enrollment(port, record, 5000);
    const int id = atoi(argv[2]);
    port.touches = parseTouches(argv[3]);
    const uint32_t total = atoi(argv[4]);
    if (argc > 5) port.extract1 = parseCodes(argv[5]);
    if (argc > 6) port.extract2 = parseCodes(argv[6]);
    if (argc > 7) port.searchCode = static_cast<Port::Code>(strtoul(argv[7], nullptr, 16));
    if (argc > 8) port.searchId = atoi(argv[8]);
    if (argc > 9) port.modelCode = static_cast<Port::Code>(strtoul(argv[9], nullptr, 16));
    if (argc > 10) port.storeCode = static_cast<Port::Code>(strtoul(argv[10], nullptr, 16));
    const long cancelAt = argc > 11 ? atol(argv[11]) : -1;
    const bool first = enrollment.begin(id, 0);
    const bool second = mode == "twice" ? enrollment.begin(id + 1, 0) : true;
    for (port.now = 0; port.now <= total; port.now += 20) {
        if (cancelAt >= 0 && port.now == static_cast<uint32_t>(cancelAt)) enrollment.cancel();
        enrollment.update(port.now);
    }
    printf("%d%d %s busy=%d images=%d %s\n", first, second, events.c_str(), enrollment.busy(), port.getImages,
           port.aura.c_str());
    return 0;
}
"""


@pytest.fixture(scope="module")
def driver(tmp_path_factory: pytest.TempPathFactory) -> Callable[..., str]:
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("g++ absent : test de l'enregistrement ignore")
    directory = tmp_path_factory.mktemp("enrollment")
    source = directory / "driver.cpp"
    source.write_text(DRIVER, encoding="utf-8")
    binary = directory / "driver"
    subprocess.run([compiler, "-std=c++17", "-Wall", "-Werror", f"-I{FIRMWARE_SRC}", str(source), "-o", str(binary)],
                   check=True)

    def run(*arguments: str) -> str:
        return subprocess.run([str(binary), *arguments], check=True, capture_output=True, text=True).stdout.strip()

    return run


def events(output: str) -> str:
    """« 11 start:3,... busy=0 images=4 [1/2]… » → la liste des événements."""
    return output.split(" ")[1]


def ring(output: str) -> str:
    """Suite des commandes d'anneau : [effet/couleur] (effet 1 respiration, 2 éclairs, 3 fixe, 4 éteint)."""
    return output.split(" ")[4]


TWO_POSES = "100-600,1000-1600"


def test_two_poses_store_the_finger(driver: Callable[..., str]) -> None:
    output = driver("run", "3", TWO_POSES, "3000")
    assert events(output) == "start:3,remove:3,again:3,done:3"
    assert "busy=0" in output


def test_nothing_happens_before_the_finger_is_on_the_sensor(driver: Callable[..., str]) -> None:
    """Le capteur n'est interrogé que lorsque le signal de touche annonce un doigt."""
    assert "images=0" in driver("run", "3", "9000-9500", "3000").split(" ")


def test_removal_is_awaited_before_the_second_pose(driver: Callable[..., str]) -> None:
    """Un seul doigt posé tout le temps : la 2e pose n'est jamais prise pour une nouvelle."""
    assert events(driver("run", "3", "100-9000", "4500")) == "start:3,remove:3"


def test_known_finger_is_refused_and_reports_its_id(driver: Callable[..., str]) -> None:
    out = driver("run", "3", TWO_POSES, "3000", "", "", "00", "7")
    assert events(out) == "start:3,failed:already_enrolled:7"


def test_messy_capture_is_retried(driver: Callable[..., str]) -> None:
    out = driver("run", "3", "100-600,1000-1500,2000-2600", "4000", "06", "", "09")
    assert events(out) == "start:3,retry:messy:3,remove:3,again:3,done:3"


def test_too_many_bad_captures_give_up(driver: Callable[..., str]) -> None:
    touches = ",".join(f"{100 + 700 * i}-{400 + 700 * i}" for i in range(8))
    out = driver("run", "3", touches, "9000", "06,06,06,06,06,06,06")
    assert events(out).endswith("failed:messy:3")
    assert events(out).count("retry") == 5


def test_second_capture_failure_asks_for_the_pose_again(driver: Callable[..., str]) -> None:
    out = driver("run", "3", "100-600,1000-1500,2000-2600", "4000", "", "07")
    assert events(out) == "start:3,remove:3,again:3,retry:few_features:3,again:3,done:3"


def test_different_fingers_are_rejected(driver: Callable[..., str]) -> None:
    out = driver("run", "3", TWO_POSES, "3000", "", "", "09", "0", "0a")
    assert events(out) == "start:3,remove:3,again:3,failed:mismatch:3"


@pytest.mark.parametrize(("store", "reason"), [("0b", "bad_id"), ("18", "storage"), ("e9", "sensor")])
def test_storage_errors_are_named(driver: Callable[..., str], store: str, reason: str) -> None:
    out = driver("run", "3", TWO_POSES, "3000", "", "", "09", "0", "00", store)
    assert events(out) == f"start:3,remove:3,again:3,failed:{reason}:3"


def test_sensor_failure_while_searching(driver: Callable[..., str]) -> None:
    assert events(driver("run", "3", TWO_POSES, "3000", "", "", "e9")) == "start:3,failed:sensor:3"


def test_waiting_forever_times_out(driver: Callable[..., str]) -> None:
    out = driver("run", "3", "", "6000")
    assert events(out) == "start:3,failed:timeout:3" and "busy=0" in out


def test_never_removing_the_finger_times_out(driver: Callable[..., str]) -> None:
    assert events(driver("run", "3", "100-9000", "7000")) == "start:3,remove:3,failed:timeout:3"


def test_cancel_stops_the_enrollment(driver: Callable[..., str]) -> None:
    out = driver("run", "3", "", "2000", "", "", "09", "0", "00", "00", "500")
    assert events(out) == "start:3,failed:cancelled:3" and "busy=0" in out


def test_second_enrollment_is_refused_while_one_runs(driver: Callable[..., str]) -> None:
    assert driver("twice", "3", "", "100").startswith("10 ")  # begin() : vrai, puis faux


def test_ring_shows_waiting_then_success(driver: Callable[..., str]) -> None:
    """Respiration bleue en attente, violet fixe à la 1re pose, éteint au retrait, 3 éclairs bleus à la fin."""
    assert ring(driver("run", "3", TWO_POSES, "3000")) == "[1/2][3/3][4/2][1/2][2/2]"


def test_ring_flashes_red_on_failure(driver: Callable[..., str]) -> None:
    assert ring(driver("run", "3", "", "6000")).endswith("[2/1]")


@pytest.mark.parametrize(
    ("bitmap", "first", "limit", "maximum", "expected"),
    [
        ("00", 0, 200, 16, "0:"),
        ("01", 0, 200, 16, "1:0"),
        ("0280", 0, 200, 16, "2:1,15"),                  # bit de poids faible d'abord : 0x02 = emplacement 1
        ("FF", 8, 200, 16, "8:8,9,10,11,12,13,14,15"),   # 2e page : le 1er emplacement est `first`
        ("FF", 0, 5, 16, "5:0,1,2,3,4"),                 # au-delà de la capacité : ignorés
        ("FFFF", 0, 200, 4, "16:0,1,2,3"),               # tampon trop petit : le compte reste juste
    ],
)
def test_index_table_decoding(driver: Callable[..., str], bitmap: str, first: int, limit: int, maximum: int,
                              expected: str) -> None:
    assert driver("index", bitmap, str(first), str(limit), str(maximum)) == expected
