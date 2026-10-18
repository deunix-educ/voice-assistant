"""Tests du déclencheur « doigt » (branche fingerprint, étape F3).

Le déroulement (firmware/src/FingerprintTrigger.h) est compilé avec g++ et joué contre un faux
capteur dont on scripte le doigt et les réponses. Sans g++, les tests sont ignorés.
"""

from __future__ import annotations

import re
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

#include "FingerprintTrigger.h"

using Event = FingerprintTrigger::Event;
using Port = FingerprintPort;
using Intervals = std::vector<std::pair<uint32_t, uint32_t>>;

static bool inside(const Intervals& list, uint32_t now) {
    for (const auto& t : list) if (now >= t.first && now < t.second) return true;
    return false;
}

// Faux capteur : le doigt est posé pendant des intervalles [début, fin[ (ms), les réponses sont scriptées.
struct FakePort : Port {
    uint32_t now = 0;
    Intervals touches;
    bool noImage = false;               // le capteur ne voit jamais le doigt
    std::vector<Code> images, extracts; // réponses successives ; OK quand la liste est épuisée
    Code searchCode = OK;
    uint16_t searchId = 2, searchScore = 90;
    int getImages = 0, searches = 0;
    std::string aura;

    static Code next(std::vector<Code>& list) {
        if (list.empty()) return OK;
        Code code = list.front();
        list.erase(list.begin());
        return code;
    }
    bool touched() const override { return inside(touches, now); }
    Code getImage() override { ++getImages; return noImage ? NO_FINGER : next(images); }
    Code extract(uint8_t) override { return next(extracts); }
    Code search(uint8_t, uint16_t& id, uint16_t& score) override {
        ++searches; id = searchId; score = searchScore; return searchCode;
    }
    Code createModel() override { return OK; }
    Code store(uint8_t, uint16_t) override { return OK; }
    Code setAura(Aura effect, Color color, uint8_t, uint8_t) override {
        aura += "[" + std::to_string(static_cast<int>(effect)) + "/" + std::to_string(static_cast<int>(color)) + "]";
        return OK;
    }
};

static FakePort port;
static std::string events;

static void record(Event event, uint16_t id, uint16_t score, const char* reason) {
    if (!events.empty()) events += ",";
    switch (event) {
    case Event::Touched: events += "touched"; break;
    case Event::Recognized: events += "recognized:" + std::to_string(id) + ":" + std::to_string(score); break;
    case Event::Rejected: events += std::string("rejected:") + reason; break;
    case Event::Released: events += "released"; break;
    }
    events += "@" + std::to_string(port.now);
}

static Intervals parseIntervals(const char* text) {
    Intervals out;   // "100-600,1000-1600"
    for (const char* p = text; *p;) {
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

// Arguments « clé=valeur » : touches, total, image, extract, search, id, score, noimage, blocked, skip.
int main(int argc, char** argv) {
    std::map<std::string, std::string> args;
    for (int i = 1; i < argc; ++i) {
        const char* equal = strchr(argv[i], '=');
        args[std::string(argv[i], equal - argv[i])] = equal + 1;
    }
    if (args.count("touches")) port.touches = parseIntervals(args["touches"].c_str());
    if (args.count("image")) port.images = parseCodes(args["image"].c_str());
    if (args.count("extract")) port.extracts = parseCodes(args["extract"].c_str());
    if (args.count("search")) port.searchCode = static_cast<Port::Code>(strtoul(args["search"].c_str(), nullptr, 16));
    if (args.count("id")) port.searchId = atoi(args["id"].c_str());
    if (args.count("score")) port.searchScore = atoi(args["score"].c_str());
    port.noImage = args.count("noimage") > 0;
    Intervals blocked;
    if (args.count("blocked")) blocked = parseIntervals(args["blocked"].c_str());
    Intervals skipped;   // le capteur sert à autre chose (enregistrement) : update() n'est pas appelé
    if (args.count("skip")) skipped = parseIntervals(args["skip"].c_str());
    const uint32_t total = atoi(args["total"].c_str());

    FingerprintTrigger::Timing timing;   // valeurs par défaut de l'en-tête : 40 / 150 / 3000 / 40 ms, 3 essais
    FingerprintTrigger trigger(port, record, timing);
    for (port.now = 0; port.now <= total; port.now += 20) {
        if (inside(skipped, port.now)) {
            trigger.requireLift();   // comme main.cpp pendant un enregistrement
            continue;
        }
        trigger.update(port.now, !inside(blocked, port.now));
    }
    printf("%s images=%d searches=%d busy=%d %s\n", events.empty() ? "-" : events.c_str(), port.getImages,
           port.searches, trigger.busy(), port.aura.empty() ? "-" : port.aura.c_str());
    return 0;
}
"""


@pytest.fixture(scope="module")
def driver(tmp_path_factory: pytest.TempPathFactory) -> Callable[..., str]:
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("g++ absent : test du declencheur ignore")
    directory = tmp_path_factory.mktemp("trigger")
    source = directory / "driver.cpp"
    source.write_text(DRIVER, encoding="utf-8")
    binary = directory / "driver"
    subprocess.run([compiler, "-std=c++17", "-Wall", "-Werror", f"-I{FIRMWARE_SRC}", str(source), "-o", str(binary)],
                   check=True)

    def run(*arguments: str) -> str:
        return subprocess.run([str(binary), *arguments], check=True, capture_output=True, text=True).stdout.strip()

    return run


def events(output: str, times: bool = False) -> str:
    """« touched@140,recognized:2:90@160 images=1 … » → la liste des événements (sans les instants, par défaut)."""
    listed = output.split(" ")[0]
    return listed if times else re.sub(r"@\d+", "", listed)


def field(output: str, name: str) -> int:
    match = re.search(rf"{name}=(\d+)", output)
    assert match is not None, output
    return int(match.group(1))


def ring(output: str) -> str:
    """Suite des commandes d'anneau : [effet/couleur] (effet 2 éclairs, 3 fixe, 4 éteint ; couleur 1 rouge, 2 bleu, 3 violet)."""
    return output.split(" ")[-1]


def test_recognized_finger_opens_then_closes_the_session(driver: Callable[..., str]) -> None:
    """Instants : posé à 100 → Touched après 40 ms ; identification au tour suivant ; retrait à 1000 + 150 ms."""
    out = driver("touches=100-1000", "total=2000")
    assert events(out, times=True) == "touched@140,recognized:2:90@160,released@1160"
    assert field(out, "busy") == 0


def test_the_id_and_score_come_from_the_sensor(driver: Callable[..., str]) -> None:
    assert events(driver("touches=100-1000", "total=2000", "id=7", "score=123")) == "touched,recognized:7:123,released"


def test_unknown_finger_is_rejected_and_never_released(driver: Callable[..., str]) -> None:
    out = driver("touches=100-1000", "total=2000", "search=09")
    assert events(out) == "touched,rejected:unknown"
    assert field(out, "busy") == 0


def test_unknown_finger_left_on_the_sensor_is_not_identified_again(driver: Callable[..., str]) -> None:
    out = driver("touches=100-5000", "total=4000", "search=09")
    assert events(out) == "touched,rejected:unknown"
    assert field(out, "searches") == 1
    assert field(out, "busy") == 1     # attend toujours que le doigt parte


def test_next_touch_after_a_rejection_is_identified(driver: Callable[..., str]) -> None:
    out = driver("touches=100-600,1000-1800", "total=3000", "search=09")
    assert events(out) == "touched,rejected:unknown,touched,rejected:unknown"
    assert field(out, "searches") == 2


def test_nothing_is_asked_to_the_sensor_without_a_finger(driver: Callable[..., str]) -> None:
    out = driver("total=2000")
    assert events(out) == "-" and field(out, "images") == 0


def test_a_brush_shorter_than_the_settle_delay_is_ignored(driver: Callable[..., str]) -> None:
    out = driver("touches=100-120", "total=1000")
    assert events(out) == "-" and field(out, "images") == 0 and ring(out) == "-"


def test_sensor_not_seeing_the_finger_yet_is_retried(driver: Callable[..., str]) -> None:
    """Le signal de touche précède la lecture : « pas de doigt » est répété, pas une erreur."""
    out = driver("touches=100-1000", "total=2000", "image=02,02,03,00")
    assert events(out) == "touched,recognized:2:90,released"
    assert field(out, "images") == 4


def test_messy_capture_is_retried_then_recognized(driver: Callable[..., str]) -> None:
    assert events(driver("touches=100-1000", "total=2000", "extract=06,07,00")) == "touched,recognized:2:90,released"


def test_too_many_messy_captures_give_up(driver: Callable[..., str]) -> None:
    out = driver("touches=100-1000", "total=2000", "extract=06,06,06")
    assert events(out) == "touched,rejected:image"
    assert field(out, "searches") == 0


def test_sensor_that_never_sees_the_finger_times_out(driver: Callable[..., str]) -> None:
    out = driver("touches=100-9000", "total=9500", "noimage=1")
    assert events(out, times=True) == "touched@140,rejected:image@3160"


def test_finger_lifted_during_identification_is_rejected(driver: Callable[..., str]) -> None:
    """Posé 300 ms sans que le capteur le voie, puis levé : rejeté après le délai de retrait (150 ms)."""
    out = driver("touches=100-400", "total=2000", "noimage=1")
    assert events(out, times=True) == "touched@140,rejected:lifted@560"
    assert field(out, "busy") == 0


@pytest.mark.parametrize("script", ["image=e9", "extract=e9", "search=e9", "image=18"])
def test_sensor_errors_are_named(driver: Callable[..., str], script: str) -> None:
    assert events(driver("touches=100-1000", "total=2000", script)) == "touched,rejected:sensor"


def test_short_lift_does_not_end_the_session(driver: Callable[..., str]) -> None:
    """40 ms de décollement : le doigt est toujours là. Fin 150 ms après le vrai retrait (1200)."""
    out = driver("touches=100-600,640-1200", "total=2500")
    assert events(out, times=True) == "touched@140,recognized:2:90@160,released@1360"


def test_lift_longer_than_the_release_delay_ends_the_session(driver: Callable[..., str]) -> None:
    out = driver("touches=100-600,900-1200", "total=2500")
    assert events(out) == "touched,recognized:2:90,released,touched,recognized:2:90,released"


def test_touch_while_another_session_runs_is_ignored(driver: Callable[..., str]) -> None:
    out = driver("touches=100-900", "total=2000", "blocked=0-5000")
    assert events(out) == "-" and field(out, "images") == 0


def test_touch_that_began_while_blocked_does_not_fire_when_unblocked(driver: Callable[..., str]) -> None:
    """Le bouton relâché alors que le doigt est encore posé : il faut d'abord le lever."""
    out = driver("touches=100-1500,2000-2600", "total=4000", "blocked=0-500")
    assert events(out, times=True).startswith("touched@2040")
    assert events(out).count("touched") == 1


def test_finger_already_on_the_sensor_at_power_up_is_ignored(driver: Callable[..., str]) -> None:
    out = driver("touches=0-600,1000-1600", "total=3000")
    assert events(out, times=True) == "touched@1040,recognized:2:90@1060,released@1760"


def test_require_lift_ignores_the_finger_present_at_that_moment(driver: Callable[..., str]) -> None:
    """Après un enregistrement, le doigt encore posé (jusqu'à 1500) n'ouvre pas de session."""
    out = driver("touches=100-1500,2000-2600", "total=4000", "skip=0-700")
    assert events(out, times=True).startswith("touched@2040")
    assert events(out).count("touched") == 1


def test_ring_shows_identification_then_the_recognized_finger(driver: Callable[..., str]) -> None:
    """Violet fixe pendant l'identification, bleu fixe pendant la session, éteint au retrait."""
    assert ring(driver("touches=100-1000", "total=2000")) == "[3/3][3/2][4/2]"


def test_ring_flashes_red_when_the_finger_is_refused(driver: Callable[..., str]) -> None:
    """Éclairs rouges ; l'anneau n'est pas éteint ensuite (il finit son clignotement seul)."""
    assert ring(driver("touches=100-1000", "total=2000", "search=09")) == "[3/3][2/1]"
