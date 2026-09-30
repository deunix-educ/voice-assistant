#include "MicSelfTest.h"

#include <math.h>

#include "config.h"

namespace {

/// Pleine échelle d'un échantillon 24 bits.
constexpr float FULL_SCALE_24 = 8388608.0F;

/// Résolution de l'INMP441 : bien cadré, le mot porte exactement ce nombre de bits.
constexpr int MIC_DATA_BITS = 24;

/// Au repos, des variations au-dessus de ce seuil méritent une remarque.
constexpr int NOISY_ROOM_DBFS = -30;

}  // namespace

MicSelfTest::MicSelfTest(MicCapture& mic) : _mic(mic) {}

bool MicSelfTest::run() {
    Serial.println("# auto-test du micro : silence, ne parlez pas (2 s)...");

    // Un seul réveil du micro, lu en cadrage MSB. begin() inclut la stabilisation.
    _mic.end();
    if (!_mic.begin(AUDIO_SAMPLE_RATE, MicCapture::Format::Msb)) {
        Serial.println("# ERREUR : initialisation I2S impossible");
        return false;
    }

    Serial.println("#  cadrage moitie | continu  | variations | bits | verdict");
    MicCapture::FrameStats frames;
    MicCapture::Half half = MicCapture::Half::First;
    const MicCapture::RawStats* active = measure(frames, half);

    if (active == nullptr) {
        reportFailure("aucune moitie de trame ne porte de donnees.");
        return false;
    }

    // 25 bits en lecture MSB : tout est décalé d'un rang vers le bas, le micro
    // émet avec le retard d'un bit du format Philips. On bascule et on vérifie.
    if (usedBits(*active) == MIC_DATA_BITS + 1) {
        Serial.println("# 25 bits utiles en MSB : le micro suit le cadrage Philips, bascule.");
        _mic.end();
        if (!_mic.begin(AUDIO_SAMPLE_RATE, MicCapture::Format::Philips)) {
            Serial.println("# ERREUR : initialisation I2S impossible");
            return false;
        }
        active = measure(frames, half);
    }

    if (active == nullptr || usedBits(*active) != MIC_DATA_BITS) {
        reportFailure("pas de moitie de trame a 24 bits utiles.");
        return false;
    }

    _mic.useHalf(half);

    Serial.printf("# configuration retenue : %lu Hz, cadrage %s, moitie %s\n",
                  static_cast<unsigned long>(_mic.sampleRate()), formatName(_mic.format()),
                  halfName(half));

    const int variations = dbfs(acRmsLsb24(*active));
    Serial.printf("# niveau au repos : continu %d dBFS, variations %d dBFS\n",
                  dbfs(fabsf(dcLsb24(*active))), variations);
    if (variations > NOISY_ROOM_DBFS) {
        Serial.println("#   NB : bruit de fond eleve, ou micro pas encore stabilise.");
        Serial.println("#   Sans effet sur le cadrage ; a surveiller sur les enregistrements.");
    }

    if (MIC_SELFTEST_VERBOSE) {
        dumpBits("configuration retenue");
        reportDataLine();
    }
    return true;
}

const MicCapture::RawStats* MicSelfTest::measure(MicCapture::FrameStats& frames,
                                                 MicCapture::Half& activeHalf) {
    frames = _mic.probe(MIC_PROBE_MS);

    const MicCapture::RawStats* halves[] = {&frames.first, &frames.second};
    const MicCapture::RawStats* active = nullptr;
    int withData = 0;

    for (int index = 0; index < 2; ++index) {
        const MicCapture::Half half =
            (index == 0) ? MicCapture::Half::First : MicCapture::Half::Second;
        const MicCapture::RawStats& stats = *halves[index];

        Serial.printf("# %-7s %s    | %4d dBFS | %5d dBFS | %4d | %s\n",
                      formatName(_mic.format()), halfName(half), dbfs(fabsf(dcLsb24(stats))),
                      dbfs(acRmsLsb24(stats)), usedBits(stats), verdictFor(stats));

        if (carriesData(stats)) {
            ++withData;
            active = &stats;
            activeHalf = half;
        }
    }

    // Le micro n'émet que dans une moitié : deux moitiés actives signalent un
    // L/R flottant ou un second micro sur la ligne.
    if (withData != 1) {
        if (withData == 2) {
            Serial.println("# les DEUX moities portent des donnees : L/R du micro bien a GND ?");
        }
        return nullptr;
    }
    return active;
}

void MicSelfTest::reportFailure(const char* reason) {
    Serial.printf("# ECHEC : %s\n", reason);
    dumpBits("echec");
    reportDataLine();
    Serial.println("#   Envoyez ce bloc complet : l'analyse bit a bit dit ou se trouvent");
    Serial.println("#   les bits du micro dans le mot recu.");
}

void MicSelfTest::reportSession(const MicCapture::RawStats& stats) const {
    if (stats.wordCount == 0) {
        Serial.println("# parole : aucun mot lu");
        return;
    }

    // La crête retenue est celle qui sort du passe-haut : le grondement
    // infrasonore est exclu, c'est bien la voix qu'on mesure.
    const int32_t peak24 = stats.filteredPeak;
    const int64_t peak16 = (static_cast<int64_t>(peak24) * MIC_GAIN) >> 8;

    Serial.printf("# parole : crete %d dBFS apres passe-haut, %d bits utiles\n",
                  dbfs(static_cast<float>(peak24)), usedBits(stats));
    Serial.printf("#   apres gain x%d : crete a %ld %% de la pleine echelle\n", MIC_GAIN,
                  static_cast<long>((peak16 * 100) / 32768));

    if (peak24 <= 0) {
        return;
    }

    // Plus grand gain (puissance de 2) plaçant la crête sous 25 % de pleine
    // échelle : peak24 x gain / 256 <= 8192, soit gain <= 2^21 / peak24.
    const int32_t wanted = static_cast<int32_t>((1L << 21) / peak24);
    int gain = 1;
    while (gain * 2 <= wanted && gain < 64) {
        gain *= 2;
    }
    if (gain != MIC_GAIN) {
        Serial.printf("# -> reglez MIC_GAIN = %d dans firmware/include/config.h\n", gain);
    } else {
        Serial.println("# -> MIC_GAIN est bien regle.");
    }
}

void MicSelfTest::dumpBits(const char* label) {
    constexpr size_t WORDS = 2048;  // 1024 trames, 64 ms à 16 kHz
    static int32_t words[WORDS];    // statique : 8 Kio hors de la pile

    _mic.flush();
    const size_t got = _mic.readRaw(words, WORDS);  // words[0] est une 1re moitié

    // Les trames du milieu du relevé sont représentatives du régime établi.
    const size_t firstShown = (got / 4) & ~static_cast<size_t>(1);

    Serial.printf("# --- analyse bit a bit, %s (%s) ---\n", label, formatName(_mic.format()));
    Serial.println("# mots bruts, regime etabli, deux mots par trame :");
    for (size_t frame = 0; frame < 8 && firstShown + (frame * 2) + 1 < got; ++frame) {
        const size_t index = firstShown + (frame * 2);
        Serial.printf("#   %08lX %08lX\n", static_cast<unsigned long>(words[index]),
                      static_cast<unsigned long>(words[index + 1]));
    }

    // Proportion de 1 à chaque position de bit, du bit 31 (gauche) au bit 0.
    for (size_t column = 0; column < 2; ++column) {
        uint32_t ones[32] = {};
        size_t count = 0;
        for (size_t i = column; i < got; i += 2) {
            const uint32_t bits = static_cast<uint32_t>(words[i]);
            for (int bit = 0; bit < 32; ++bit) {
                ones[bit] += (bits >> bit) & 1U;
            }
            ++count;
        }

        char pattern[33];
        for (int bit = 31; bit >= 0; --bit) {
            const uint32_t tenth = (count == 0) ? 0 : (ones[bit] * 10U) / (count + 1);
            pattern[31 - bit] = static_cast<char>('0' + tenth);
        }
        pattern[32] = '\0';

        Serial.printf("# colonne %u, taux de 1 par bit (31..0) : %s\n",
                      static_cast<unsigned>(column + 1), pattern);
    }
    Serial.println("# lecture : 0 = toujours 0, 9 = toujours 1.");
    Serial.println("#   24 bits utiles bien cadres = exactement 8 zeros a droite.");
}

void MicSelfTest::reportDataLine() {
    const MicCapture::DataLineReport report = _mic.probeDataLine();
    if (report.samples == 0) {
        return;
    }

    const uint32_t up = (report.onesWithPullUp * 100) / report.samples;
    const uint32_t down = (report.onesWithPullDown * 100) / report.samples;

    Serial.printf("# ligne SD : pull-up %lu %% de 1, pull-down %lu %% de 1",
                  static_cast<unsigned long>(up), static_cast<unsigned long>(down));
    if (up >= 98 && down <= 2) {
        Serial.println(" -> FLOTTE : rien ne la pilote");
    } else if (up <= 2 && down <= 2) {
        Serial.println(" -> tenue a 0");
    } else if (up >= 98 && down >= 98) {
        Serial.println(" -> tenue a 1");
    } else {
        Serial.println(" -> PILOTEE : le micro emet");
    }
}

const char* MicSelfTest::verdictFor(const MicCapture::RawStats& stats) {
    if (stats.wordCount == 0) {
        return "aucun mot lu";
    }
    if (stats.zeroCount == stats.wordCount) {
        return "silence numerique";
    }
    if (stats.minWord == stats.maxWord) {
        return "valeur CONSTANTE";
    }

    const int bits = usedBits(stats);
    if (bits == MIC_DATA_BITS) {
        return "CADRE : 24 bits";
    }
    if (bits == MIC_DATA_BITS + 1) {
        return "un bit de retard (Philips)";
    }
    if (bits < MIC_DATA_BITS) {
        return "DECALE : bit de signe perdu";
    }
    return "queue du mot non nulle";
}

bool MicSelfTest::carriesData(const MicCapture::RawStats& stats) {
    return stats.wordCount > 0 && stats.zeroCount < stats.wordCount &&
           stats.minWord != stats.maxWord;
}

float MicSelfTest::dcLsb24(const MicCapture::RawStats& stats) {
    if (stats.wordCount == 0) {
        return 0.0F;
    }
    // sum porte sur (mot >> 12) ; en LSB 24 bits (mot >> 8), c'est x16.
    return static_cast<float>(static_cast<double>(stats.sum) /
                              static_cast<double>(stats.wordCount) * 16.0);
}

float MicSelfTest::acRmsLsb24(const MicCapture::RawStats& stats) {
    if (stats.wordCount == 0) {
        return 0.0F;
    }
    // Variance = moyenne des carrés - carré de la moyenne.
    const double count = static_cast<double>(stats.wordCount);
    const double mean = static_cast<double>(stats.sum) / count;
    const double variance = static_cast<double>(stats.sumSquares) / count - mean * mean;
    if (variance <= 0.0) {
        return 0.0F;
    }
    return static_cast<float>(sqrt(variance) * 16.0);
}

int MicSelfTest::dbfs(float lsb24) {
    if (lsb24 <= 0.0F) {
        return -999;
    }
    return static_cast<int>(20.0F * log10f(lsb24 / FULL_SCALE_24));
}

int MicSelfTest::usedBits(const MicCapture::RawStats& stats) {
    if (stats.orBits == 0) {
        return 0;
    }
    return 32 - __builtin_ctz(stats.orBits);  // ctz : nombre de zéros à droite
}

const char* MicSelfTest::formatName(MicCapture::Format format) {
    return (format == MicCapture::Format::Philips) ? "Philips" : "MSB";
}

const char* MicSelfTest::halfName(MicCapture::Half half) {
    return (half == MicCapture::Half::First) ? "1re" : "2e ";
}
