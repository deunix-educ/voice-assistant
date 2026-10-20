# Local voice assistant — ESP32 + Raspberry Pi

A **fully local** voice assistant, with no cloud service: an ESP32 captures
speech, streams raw PCM over MQTT to a Python server that transcribes,
interprets and answers with speech synthesis; the ESP32 plays the answer back
and executes the command.

*Version française : [README.md](README.md).*

---

## 1. Architecture

```
R503 (finger) --+
                |
INMP441 -> ESP32 -> MQTT -> Python server (Raspberry Pi / PC)
                                  |
                  person = the finger's name (fingerprint.users)
                                  |
                           faster-whisper
                                  |
                       Assistant + rights (access)
                                  |
                                Piper
                                  |
                             MQTT audio/out
                                  |
                        ESP32 -> MAX98357A -> speaker
```

You put an enrolled finger on the sensor, speak, and lift it: the session lasts as long as the finger.

| Stage | Question it answers | Answered by |
|---|---|---|
| R503 sensor | Who holds the board? | the recognised finger (sensor slot → name) |
| STT | What is being said? | faster-whisper, on the whole session |
| Rights | What may they ask for? | `access` in `conf/access.yaml`, per person |

> The finger says **who holds the board**, not who speaks: someone else can talk while your finger is down. Sensitive
> actions therefore ask for a confirmation with the same finger. The PTT button identifies nobody. There is no
> VAD, diarization, voice identification or wake word any more: they live in the `main` branch.

---

## 2. Hardware

| Item | Detail |
|---|---|
| ESP32-WROOM-32 | two I2S peripherals: I2S0 as receiver, I2S1 as transmitter |
| ESP32-S3 Mini (`fingerprint` branch) | same firmware, other pinout: [docs/BROCHAGE.md](docs/BROCHAGE.md) §1.5 (in French), environment `esp32s3mini` |
| INMP441 | I2S microphone, **3.3 V**, L/R pin to GND (left channel) |
| MAX98357A | mono class-D amplifier, **5 V**, SD tied to VIN, GAIN left floating (9 dB) |
| Button | push-to-talk between GPIO 4 and GND (internal pull-up) |
| LED | with a 330 Ω resistor on GPIO 2 |
| Server | Raspberry Pi 4/5 64-bit or Debian PC |

### Wiring

**Full diagram: [docs/wiring.svg](docs/wiring.svg)** (ESP32 WROOM, no sensor), **[docs/wiring-esp32-wroom-fingerprint.svg](docs/wiring-esp32-wroom-fingerprint.svg)**
(ESP32 WROOM with the R503 sensor, the fallback for the S3) or **[docs/wiring-esp32-s3.svg](docs/wiring-esp32-s3.svg)** (ESP32-S3 Mini, with the R503 sensor) — detailed pinout, power
budget, multimeter checks and moving from one board to the other (§1.6), all in French: **[docs/BROCHAGE.md](docs/BROCHAGE.md)**.

```
        ESP32                     INMP441 (microphone)
    3V3  ----------------------- VDD
    GND  ----------------------- GND, L/R
    GPIO 25 (SCK)  ------------- SCK
    GPIO 26 (WS)   ------------- WS
    GPIO 33 (SD)   ------------- SD

        ESP32                     MAX98357A (amplifier, step 2)
    5V (VIN) ------------------- VIN
    GND  ----------------------- GND
    GPIO 27 (BCLK) ------------- BCLK
    GPIO 14 (LRC)  ------------- LRC
    GPIO 22 (DIN)  ------------- DIN

        ESP32                     User interface
    GPIO 4  ------ button ------ GND
    GPIO 2  --[330 Ω]-- LED ---- GND
```

No strapping pin (GPIO 0, 2, 5, 12, 15) carries an audio signal: their level at
reset selects the boot mode. GPIO 2 drives the LED as an output only, which does
not affect booting.

---

## 3. Audio format, identical everywhere

| Parameter | Value |
|---|---|
| Sample rate | 16 000 Hz |
| Resolution | signed 16-bit, little-endian (`pcm_s16le`) |
| Channels | 1 (mono) |
| Bit rate | 32 000 bytes/s |
| Chunk | 50 ms = 800 samples = **1 600 bytes** |

No Base64: MQTT payloads carry raw PCM bytes.

---

## 4. Installation

### 4.0 Choosing your version

Two versions live in this repository, on two branches. They do not mix: the voice code was removed from `fingerprint`.

| | `main` | `fingerprint` |
|---|---|---|
| Who holds the board | the **voice** (voice profiles, diarization) | the **finger** placed on an R503 sensor |
| Session start | wake word "Hey Mycroft" or PTT button | finger placed, or PTT button (no identity) |
| Heavy dependencies | pyannote and torch (≈ 1.3 GB), openWakeWord | none |
| Boards | ESP32 WROOM | ESP32 WROOM or ESP32-S3 Mini |
| Configuration | versioned `server/config.yaml` | `config.example.yaml` template, ignored local copy, `server/conf/` |
| systemd service | no | yes (`make server-install`) |

**Getting the `fingerprint` version**

```bash
# new folder
git clone -b fingerprint https://github.com/deunix-educ/voice-assistant.git     # "-b main" for the voice version
cd voice-assistant

# repository already cloned
git fetch origin && git switch fingerprint && git pull
```

Then steps 4.1 and 4.2 below (`make install-system`, `make install-dev`, `make install-speech`, `make models`).
ESP32-S3 Mini: `make fw-upload FW_ENV=esp32s3mini SERIAL_PORT=/dev/ttyACM0`.

**Switching branches in the same folder: mind `server/config.yaml`.** On `fingerprint` it is ignored by git; on `main` it is
tracked. As a result `git switch main` **replaces your file, without warning**, with the one from `main` (git overwrites ignored
files), and `git switch fingerprint` then **deletes** it. Back it up first (`cp server/config.yaml
~/config.yaml.fingerprint`) and put it back when you return. After going back to `main`, its voice dependencies must be
reinstalled (`make install-diarization`, `make install-wakeword`).

**Safer: one folder per version**, leaving the other untouched:

```bash
git worktree add ../voice-assistant-main main      # a copy of main next to this one, with its own files
cd ../voice-assistant-main && make install-dev     # its venv, its server/.env (to fill in) and its models (make models)
```

### 4.1 ESP32 firmware

**Prerequisites:** a USB cable with data lines (not charge-only), port identified
(`ls /dev/ttyUSB*` or `/dev/ttyACM*` after plugging in).

```bash
make pio-venv          # PlatformIO in its own venv — once only
make fw-build          # compile without flashing
make fw-upload SERIAL_PORT=/dev/ttyUSB0
```

ESP32-S3 Mini: add `FW_ENV=esp32s3mini` and use the native USB port (`/dev/ttyACM0`), see step F5.

**First boot — WiFi configuration portal**

If no credentials have been configured yet, the board automatically opens an
access point:

1. The LED blinks rapidly (100 ms on / 100 ms off).
2. Connect a phone or PC to the **VoiceAssist-Config** Wi-Fi network.
3. Open `http://192.168.4.1` in a browser.
4. Enter the Wi-Fi network **(2.4 GHz required)** and the MQTT broker IP address.
5. Click **Save and restart** — credentials are saved to persistent memory (NVS)
   and the board reboots.

> **Reconfiguration:** hold the PTT button for 3 s at power-on to reopen the
> portal, even if credentials are already stored.

**Alternative — `secrets.h`** (for repeated development builds)

```bash
cp firmware/include/secrets.h.example firmware/include/secrets.h
# Edit: WIFI_SSID, WIFI_PASSWORD, MQTT_HOST
make fw-upload SERIAL_PORT=/dev/ttyUSB0
```

NVS takes priority over `secrets.h`. To start from scratch, force the portal
with the PTT button (or erase the NVS with `esptool.py erase_flash`).

**Raspberry Pi 5 Wi-Fi access point: WPA2 required**

If the board connects to the Pi's access point (NetworkManager, here `RPI5-AP`),
it must use **WPA2**. By default the ESP32 refuses any network below WPA2, and
an access point created by NetworkManager may be WPA1. Recommended fix, on the Pi:

```bash
nmcli -t -f NAME,TYPE con show --active        # name of the access point connection
sudo nmcli con modify "RPI5-AP" wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn \
     wifi-sec.pairwise ccmp wifi-sec.group ccmp \
     802-11-wireless.band bg 802-11-wireless.channel 6
sudo nmcli con down "RPI5-AP" && sudo nmcli con up "RPI5-AP"
```

`proto rsn` enforces WPA2; channel 6 avoids channels 12 and 13, which the ESP32
handles poorly. The Wi-Fi password does not change. On this network the Pi is
`10.42.0.1`: that is the broker address to enter in the portal.

Diagnosis on the serial monitor (`make fw-monitor`, or minicom at **921600 baud**):

| Board message | Cause | Fix |
|---|---|---|
| `Reason: 211` | access point uses WPA1 | switch it to WPA2 (above) |
| `Reason: 15 - 4WAY_HANDSHAKE_TIMEOUT` | wrong Wi-Fi password stored in the board | re-enter it in the portal; the Pi confirms it: `sudo journalctl -u wpa_supplicant \| grep PSK-MISMATCH` |
| `# mqtt : echec (code 5)` | MQTT account missing on the broker, or different password | `make mqtt-user NAME=esp32-01` on the Pi |
| unreadable bytes when the button is pressed | MQTT not connected: the session goes over the serial cable | check the `# wifi` and `# mqtt` lines at boot |

> In the portal, both password fields (Wi-Fi and MQTT) are always shown empty:
> retype them on every save, a field left empty erases the stored password.

---

### 4.2 Python server

**Prerequisites:** Python 3.10+, Docker (for the broker) or native Mosquitto.

```bash
# 1. System packages (mosquitto, ffmpeg, venv) + serial port access (dialout group)
make install-system
# → log out and back in to activate the "dialout" group

# 2. MQTT broker (choose one)
make mosquitto-docker          # Docker container (recommended)
make mosquitto-config          # native Debian Mosquitto service (installed via make install-system)

# 3. Python environment + dev tooling (pytest, pyright, PlatformIO)
make install-dev
# PlatformIO gets its own venv (.pio-venv): its dependencies clash with the
# voice server's.

# 4. Speech recognition + synthesis (~550 MB)
make install-speech
make models

# 5. Enroll a finger (step F2), then run the assistant
make finger-enroll NAME=Denis
make run
```

**Files to configure:**

| File | Contents |
|---|---|
| `server/.env` | `MQTT_USERNAME`, `MQTT_PASSWORD` |
| `server/config.yaml` | **your local copy** (ignored by git, created by `make install` from `config.example.yaml`): general settings (audio, MQTT, voices, log) and the list of included files |
| `server/conf/home.yaml` | rooms and the room of each board |
| `server/conf/machines.yaml` | Linux machines on the network and their actions |
| `server/conf/access.yaml` | rights per person (`access`) and the owner of each finger (`fingerprint`) |

For help on all targets: `make help`.

---

### 4.3 Linux agent (machines controlled by voice)

Each Linux machine to control (desktop PC, NAS…) runs a lightweight service that
executes **only** the actions listed in its own configuration file — no shell
exposed.

**On the target machine:**

```bash
# 1. Copy the agent/ directory from the Raspberry Pi
scp -r /home/rpi5/voice-assistant/agent/ user@desktop-pc:~/voice-agent-install/

# 2. Install the service (creates /opt/voice-agent/, /etc/voice-assistant/, systemd unit)
cd ~/voice-agent-install
sudo make install

# 3. Create the MQTT account on the server (if the broker is secured, step 15)
#    (run on the Raspberry Pi, not on the target machine)
make mqtt-user NAME=pc-bureau      # note the password
make mosquitto-docker              # or mosquitto-config — reloads the broker

# 4. Configure the agent on the target machine
sudo nano /etc/voice-assistant/agent.yaml
#   id: pc-bureau                    ← name used in MQTT topics
#   mqtt.host: 192.168.1.200         ← broker IP address
#   mqtt.username: pc-bureau         ← same value as id (empty if anonymous broker)
#   mqtt.password: <password>        ← generated in step 3 (empty if anonymous broker)
#   dry_run: false                   ← true to test without executing anything

# 4. Enable and start
sudo systemctl enable --now voice-agent
sudo systemctl status voice-agent
```

**On the server,** add the machine to `server/conf/machines.yaml`:

```yaml
machines:
  list:
    - id: pc-bureau
      name: "PC du bureau"        # spoken as: "le PC du bureau"
      mac: "AA:BB:CC:DD:EE:FF"    # MAC address for Wake-on-LAN
      ip: 192.168.1.50
```

**Try it without shutting anything down** (`dry_run: true` in `agent.yaml`):

```bash
make run        # server (terminal 1)
make agent-run  # agent in test mode on this machine (terminal 2)
# Finger down: "éteins le PC du bureau"
# → the agent prints: essai : systemctl poweroff (non exécuté)
```

Default available actions: `shutdown`, `reboot`, `lock`; `wake`
(power on over the network) is handled by the server via Wake-on-LAN.

---

## 5. Usage

```bash
make fw-build                            # build the firmware
make fw-upload SERIAL_PORT=/dev/ttyUSB0
make record OUT=recordings/test.wav      # record one session (step 1)
make play OUT=recordings/test.wav       # play it back (picks a working player)
make inspect OUT=recordings/test.wav    # analyse it without listening
make play-esp OUT=recordings/test.wav   # play it on the ESP32 speaker (step 2)
make tone-esp TONE=440                  # pure tone on the ESP32 speaker (step 2)
make run                                # assistant: answers every question (step 6)
make speech-check TEXT="Quelle heure est-il ?"  # recognition and synthesis, no ESP32
make say TEXT="Ouvre la porte du salon." VOICE=fr_FR-tom-medium  # WAV + MP3 to play at the microphone from a phone
make latency                            # Whisper transcription time on real recordings
make finger-enroll NAME=Denis           # enroll a finger (two scans); finger-list, finger-delete ID=2
make transcribe OUT=server/recordings/<file>.wav
make mqtt-watch                         # text messages voice/… and home/# (no audio)
make mqtt-user NAME=esp32-01            # MQTT account for a board, or the server (step 15)
make agent-run                          # Linux agent of this PC, test mode (step 15b)
make mqtt-agents                        # agent states, commands and reports
make fw-monitor                          # serial monitor
make test                                # Python tests
make lint                                # pyright type check
```

---

## 6. Progress

The exact state of the project, step by step, lives in **[CONTEXT.md](CONTEXT.md)**.

Current step: **done — steps 1 to 16 are validated** (`main` branch).
`fingerprint` branch: F1 to F4b, F5 (ESP32-S3 Mini), F6 (configuration in several files), F7 (server under systemd) and F8 (versioned configuration template, ignored local copy) validated

### `fingerprint` branch — step F8: a versioned configuration template, an ignored local copy

`server/config.example.yaml` is the **template** (versioned); `server/config.yaml` is **your copy** (ignored by git): the
server, the tools and the service read that one. No more risk of committing your broker address, or of losing it to a
"discard changes".

```bash
make install        # creates server/config.yaml from the template if missing (like server/.env), never overwriting it
make config         # the same, alone
make config-diff    # what differs between the template and your copy (re-read it when the template changes)
```

| Point | Detail |
|---|---|
| fresh clone | `make install` creates `server/config.yaml`; **check `mqtt.host`** (the template points at `10.42.0.1`, the Raspberry Pi) |
| the template changes | your copy is not touched: `make config-diff`, then carry the new keys over by hand |
| `server/conf/*.yaml` | stay versioned (starting values); only `config.yaml` is local |
| `make run`, service | a missing `config.yaml` gives `configuration invalide` and "`make config` creates it from config.example.yaml" |
| tests | they read the template: their results do not depend on your copy |

**Another machine that already has this repository** (the Raspberry Pi, a second PC): on the first `git pull` that brings this
step, git removes the `server/config.yaml` it used to track. Copy it before (`cp server/config.yaml ~/config.yaml.backup`)
and put it back afterwards; without local changes, `make config` recreates a file from the template.

1. `git pull` has nothing to do on your machine: your `server/config.yaml` stays (same content, `192.168.1.4`), and `git status`
   no longer shows it.
2. `make config`: "existe deja: rien a faire"; `make config-diff`: shows only your differences (broker address…).
3. Fresh clone (temporary folder): `make config` creates `server/config.yaml` from the template.

### `fingerprint` branch — step F7: the server starts with the machine (systemd)

The server becomes a service: it starts at boot (no login needed), is restarted if it crashes, and its log goes to
`journalctl`. Same pattern as the agent (`make agent-install`): a template unit in the repository, installed with `sudo`.

```bash
make server-install    # installs and starts the service (sudo); refuses while a "make run" is still running
make server-status     # service state
make server-log        # live log (Ctrl-C to quit)
sudo systemctl restart voice-server   # after changing config.yaml, conf/*.yaml, .env or the code
make server-unit       # prints the unit that would be installed, installs nothing
make server-uninstall  # removes the service (the repository and the recordings stay)
```

| Point | Detail |
|---|---|
| account and folder | the account of whoever runs `make server-install`, in this repository's `server/`: `config.yaml`, `conf/`, `.env`, `models/`, `recordings/` and `logs/` are the same as with `make run`; moving the repository means running `make server-install` again |
| start | at boot, once the network is online; if the broker is absent the server starts and reconnects on its own (1 to 10 s between tries); loading Whisper and Piper takes a few seconds |
| crash | restarted after 5 s |
| configuration or models to fix | **not restarted** (exit code 78): the state is `failed` and `make server-log` says what to fix |
| stop | `sudo systemctl stop voice-server`: clean disconnection from the broker |
| `make run` | refused while the service runs: two servers with the same MQTT identifier kick each other out of the broker in a loop |
| secrets | `.env` stays in `server/`, read by the server; nothing is copied into the unit or the log |

The service reads `server/config.yaml` **as it is at start-up**: check `mqtt.host` before installing (the template's value is
`10.42.0.1`, the Raspberry Pi).

1. Stop the server started by hand (Ctrl-C in the `make run` terminal), then `make server-install`: `active (running)`.
2. `make server-log`: `doigts : Denis (empreinte 1)`, then the broker connection; a session by finger gets its answer.
3. Simulated crash: `sudo systemctl kill -s SIGKILL voice-server`; the log shows the restart after 5 s.
4. Reboot the machine without logging in: `make server-status` says `active (running)` and the board gets its answer.
5. Configuration error: `sudo systemctl stop voice-server`, write `include: conf/absent.yaml` in `config.yaml`,
   `sudo systemctl start voice-server`: state `failed` (code 78), no restart loop; put the configuration back and restart.

### `fingerprint` branch — step F6: the configuration in several files

`server/config.yaml` keeps the general settings and **includes** the sections that grow or are personal:

```yaml
include:
    - conf/home.yaml          # home: rooms and boards
    - conf/machines.yaml      # machines: Linux machines and their actions
    - conf/access.yaml        # access: rights; fingerprint: owner of each finger
```

| Rule | Detail |
|---|---|
| paths | relative to the including file; an included file may include others (8 levels at most, loops refused) |
| patterns | `conf/machines/*.yaml` (one file per machine), `conf/**/*.yaml`: sorted by name; a pattern matching nothing gives a warning, a path without a pattern must exist |
| merging | **mappings add up** (`home.rooms` may come from several files); the same value (text, number, list) defined twice is an **error naming both files**, never a silent overwrite; lists are not concatenated |
| `mqtt` | stays in `config.yaml`: the Makefile reads `mqtt.host` there |

Example, one room per file: `include: 'conf/pieces/*.yaml'`, with `conf/pieces/garage.yaml` containing:

```yaml
home:
    rooms:
        garage: du garage
```

A `config.yaml` without `include` works as before. The tools (`make finger-*`, `say`, `speech-check`…) use the same loader: they
read the included files with no change. The loaded configuration is **identical** to the one before
the split (checked value by value): the sections only moved to other files.

1. `make test`: 300 tests (28 for the inclusion).
2. Stop and restart `make run`: the log must say `doigts : Denis (empreinte 1)` as before, and the LED, the rights and the machines
   behave as before.
3. Break a rule to see the message: define `access.default` twice (in `config.yaml` and in `conf/access.yaml`); the server refuses
   to start with `« access.default » est défini à la fois dans …`. Put it back.

### `fingerprint` branch — step F5: the ESP32-S3 Mini board

The same firmware runs on two boards: the **ESP32 WROOM** (environment `esp32dev`, the default) and the **ESP32-S3 Mini**
(18 pins, USB-C, 4 MB flash, environment `esp32s3mini`). Only the pinout changes
([firmware/include/pins_esp32_s3_mini.h](firmware/include/pins_esp32_s3_mini.h), wiring in §1.5 of
[docs/BROCHAGE.md](docs/BROCHAGE.md), in French) and the serial monitor: the USB-C port talks to the chip directly, with no
USB-serial chip.

```bash
make fw-check  FW_ENV=esp32s3mini                              # build
make fw-upload FW_ENV=esp32s3mini SERIAL_PORT=/dev/ttyACM0     # flash
make fw-monitor SERIAL_PORT=/dev/ttyACM0                       # monitor (a "baud rate" means nothing over native USB)
```

Without `FW_ENV`, every `fw-*` target still builds the WROOM. The board prints `# carte : ESP32-S3 Mini (esp32s3mini)` at
startup: if you read `ESP32 WROOM`, the wrong environment was flashed.

Wire and try **in stages**, with the board unplugged from USB for each addition:

1. Board alone: `# carte : …`, then the microphone self-test fails (no microphone): that is normal.
2. R503 sensor on GPIO 1, 2, 4: `poignee de main : ok`, `200 empreintes`, and the ring follows the finger.
3. INMP441 microphone (GPIO 5, 6, 7): the self-test passes, `micro pret`.
4. MAX98357A amplifier (GPIO 13, 12, 11): the 440 Hz startup beep.
5. Button (GPIO 10), then `make run`: a session by the button, then by an enrolled finger (F3).

A new board has **no network configuration**: it tries `secrets.h` (if it exists), otherwise the portal is needed. Hold PTT
for about 10 s at power-up (3 s for the board to wait, then 3 s of holding): the board prints `portail force`, the status LED
blinks fast, and the `VoiceAssist-Config` Wi-Fi gives access to http://192.168.4.1. Type the network name **exactly** (the
board only speaks **2.4 GHz**): a wrong name gives `NO_AP_FOUND` in a loop.

The board identifier stays `esp32-01` for both boards: connect only **one** to the broker at a time (two boards with the same
identifier kick each other out). Fingerprints are stored **in the sensor**: an R503 moved from one board to the other keeps its
fingers.

| Symptom | Likely cause |
|---|---|
| `/dev/ttyUSB0` missing | native USB is `/dev/ttyACM0` (group `dialout`) |
| the upload does not start | hold BOOT, press RESET, release RESET then BOOT, retry |
| nothing printed at startup | monitor opened too late: the port disappears at every reset; the board waits 3 s, then drops messages if no terminal is open |
| S3 "offline" until a terminal is opened | firmware older than 2026-10-20: `Serial.setTxTimeoutMs(0)` froze the boot; reflash |
| sensor `ABSENT` | yellow and brown wires on GPIO 1 and 2, swapped; 3.3 V |
| board no longer boots | a wire on GPIO 0, 3, 45 or 46 (boot pins) |
| distorted sound or noise | Philips framing (`AMP_FORMAT_PHILIPS`) is verified on the S3 (clean beep); otherwise try it set to `false` in `config.h` |
| reboot loop, `invalid header: 0xffffffff` | blank flash (new board): flash the firmware, this is normal before that |
| `NO_AP_FOUND` in a loop | network name mistyped in the portal, or a 5 GHz-only network |
| `chunks perdus faute de place` in the server log, misheard words | Wi-Fi signal too weak (measured: −87 dBm = 147 lost chunks, −73 dBm = none): move the board closer to the access point |
| the board reboots when the serial port is opened (home-made tool) | DTR/RTS lowered on open: leave the default values |

### `fingerprint` branch — step F4: rights follow the finger

The person is the one **whose finger opened the session** (`fingerprint.users` in `conf/access.yaml`); the voice identifies
nobody any more, and VAD, diarization and voice identification no longer run (the server starts without pyannote or
torch). Rights are still those of step 14, by name (`access.users`), ignoring case and accents.

| Who holds the board | Person | Level |
|---|---|---|
| finger named in `fingerprint.users` and listed in `access.users` | their name | the one in `access.users` |
| named finger, absent from `access.users` | their name | `access.default` (standard) |
| PTT button, or a recognized finger with no name | unknown | `access.unknown` (limited) |

A finger **unknown to the sensor** never reaches the server (F3). A sensitive action (`access.confirm`) asks for a "yes"
said with the **same finger** as the request: another finger of the same person, another person's finger or the button
cancels the request. At startup the server reports names that do not match (`droits: …`).

Without a VAD, a **silent** session (RMS below `stt.min_rms_dbfs`, −58 dBFS) is not transcribed: Whisper would make text
up. Measured on 180 sessions: background noise −63 dBFS, quietest speech −51 dBFS.

1. Stop and restart `make run` (a server started before F4 still applies voice-based rights). The log must say
   `doigts : Denis (empreinte 1)` and show no `droits: …` line.
2. Denis's finger: "turn on the light" → done; "open the door" → "Confirmez-vous…", put the **same** finger down again: "yes".
3. Another person's finger: `make finger-enroll NAME=Jessica`, add the printed line to `conf/access.yaml`, restart.
   Jessica: light → done; door → "Désolé Jessica, votre profil ne permet pas d'ouvrir…".
4. Denis asks to open the door, then Jessica's finger says "yes" → "Seul Denis peut confirmer. J'annule." ;
   another finger of Denis (`make finger-enroll NAME=Denis`) → "Confirmez avec le même doigt que pour la demande."
5. PTT button: light → "Je ne sais pas qui tient la carte…".
6. Finger held without speaking → "Je n'ai rien entendu.", and `silencieuse (RMS …)` in the log.

### `fingerprint` branch — step F3: the finger opens the session

No more wake word: **put an enrolled finger on the sensor, speak, lift it**. The ring shows where you are; the
PTT button still works (a session without identity).

| Ring | Meaning |
|---|---|
| solid purple | finger seen, identification in progress (a few hundred ms): start talking, the sound is kept |
| solid blue | finger recognized, the board is listening |
| off | finger lifted, the session has gone to the server |
| 3 red flashes | finger refused: unknown to the sensor, unusable image, or sensor fault |

1. `make fw-upload`, then `make run` on the PC (`wake.enabled: false` in `config.yaml`, already set on this branch).
2. Put an enrolled finger down, speak, lift it. Board side: `doigt reconnu, emplacement 2 (score …)`, then
   `session … terminee (doigt retire)`. Server side: `session … ouverte par Denis (empreinte 2)`.
3. Put an unknown finger down: red flashes, `fp_denied` in the server log. Lift it before trying again.

A finger already on the sensor at power-up, or put down during a button session, triggers nothing until it has left
the sensor. The timings (brush 40 ms, release 150 ms, identification 3 s) are in `firmware/include/config.h`
(`FP_*`). Rights follow the finger from step F4 (below).

### `fingerprint` branch — step F2: enrolling fingers

The sensor stores the fingerprints (200 slots); `conf/access.yaml` says who owns them. The voice server
does not need to run: the tool talks to the board directly over MQTT.

```bash
make finger-enroll NAME=Denis          # two scans of the SAME finger; the ring and the terminal guide you
make finger-enroll NAME=Denis ID=2     # a second finger of the same person
make finger-list                       # fingerprints in the sensor and their owner
make finger-delete ID=2                # or ALL=1 to erase everything
make finger-list MQTT_HOST=192.168.1.4 # a broker other than the one in config.yaml
```

Enrolling prints the line to copy into `server/conf/access.yaml`:

```yaml
fingerprint:
    users: {1: Denis, 2: Denis, 3: Jessica}   # slot in the sensor → name
```

Rights stay per name in `access.users`. Ring: blue breathing = place the finger; purple = scan taken,
remove it; blue flashes = enrolled; red flashes = failed. Slot 0 is never picked automatically. A finger
that is already enrolled is refused (with its number), and so are two scans of different fingers.

### `fingerprint` branch — step F1: the R503 fingerprint sensor alone

Simplified variant (no VAD, diarization or voice identification): a fingerprint sensor tells **who**
holds the box. Wiring: [docs/BROCHAGE.md](docs/BROCHAGE.md) §1.3b (GPIO 16, 17 and 34).

1. `make fw-upload`, then `make fw-monitor` (or minicom at 921600 baud), board restarted.
2. Expected lines: `# empreinte : poignee de main : ok`, the capacity (200 for an R503) and
   `0 empreinte(s) enregistree(s)`, then the ring goes red, blue and purple.
3. Put a finger on it: `# empreinte : doigt POSE`, purple ring; remove it: `doigt retire`.

| Message | Likely cause |
|---|---|
| `ABSENT (pas de reponse …)` | RX/TX wires swapped (cross them), no 3.3 V, wrong baud rate |
| `lue comme doigt POSE` with no finger, or "POSE" and "retiré" swapped | touch signal polarity: `FP_TOUCH_ACTIVE_LOW` in `config.h` (`true` for your sensor: high at rest, low when a finger is on it); otherwise blue wire badly connected |
| handshake ok but no `doigt POSE` | blue (WAKEUP) wire not connected, or white (3.3VT) wire without 3.3 V |
| `mot de passe refuse` | the sensor has a password: `FP_PASSWORD` in `config.h` |

### Step 16 — optimisation

> On the `fingerprint` branch (F4), diarization and the voice fingerprint are gone: what remains is the synthesis cache, the
> unbuffered sends and `make latency`, which now only measures Whisper. The measurements below come from the `main` branch.

Measure first, optimise next: `make latency` replays the latest recordings through the
old chain, then the new one. On 54 real commands (PC, 20 logical cores): **1.6 s → 1.1 s**
between the end of the sentence and the recognised text, same text 54 times out of 54.

| Optimisation | Measured gain | Setting |
|---|---|---|
| Short speech: direct voice embedding, no diarization | 0.40 s (0.42 → 0.02 s) | `diarization.direct_below_s: 4.0` |
| Short speech: Whisper without word timestamps | 0.10 s | automatic |
| Answers already spoken kept in memory (synthesis cache) | 0.03 to 0.14 s | automatic, 64 sentences |
| End of command checked every 100 ms (250 before) | 0.075 s on average | — |
| Immediate network sends (`TCP_NODELAY`: server, broker) | to be measured: `premier son a … ms` | `set_tcp_nodelay true` in `voice.conf` |

Measured, then **rejected**: diarization and Whisper in parallel (0.03 to 0.10 s only: both
computations saturate the processor, even in two processes), `beam_size: 1` (0.05 s, and one
different text out of 16), 16 threads for Whisper (slower than 4).

1. On the Raspberry Pi (broker): `git pull`, then `make mosquitto-config`.
2. `make run`, a few commands: the log shows `1 locuteur (parole courte, empreinte directe
   20 ms)` and `reponse prete en ~1.1 s` (1.5 s before).
3. `make latency`: before, after, gain, and the identifications that differ.

Speech shorter than 4 s is assumed to come from **a single person**. Two voices in a short
sentence are no longer separated (they were at step 10); beyond 4 s nothing changes.

### Step 15b — Linux machines on the network

"Éteins le PC du bureau": each machine runs an **agent**
([agent/voice_agent.py](agent/voice_agent.py)), a small MQTT client that only runs
the actions in **its own** list (`/etc/voice-assistant/agent.yaml`): fixed commands,
no shell. The server only sends an action name. "Allume" wakes a machine that is
off through **Wake-on-LAN** (a network packet received by the sleeping network card).

| Action | Words | Rights (conf/access.yaml) |
|---|---|---|
| `wake` (server) | allume, démarre, réveille | standard |
| `lock` | verrouille | standard |
| `shutdown` | éteins, arrête | full + confirmation |
| `reboot` | redémarre, relance | full + confirmation |

1. Try it on this PC without shutting anything down: `make agent-run` (creates `agent/agent.yaml` in test mode).
2. `make run`, then finger down: "éteins le PC du bureau" → confirmation → same finger: "oui":
   the agent prints `essai : systemctl poweroff (non execute)`.
3. Real machine: copy `agent/` to it, `make -C agent install`, edit
   `/etc/voice-assistant/agent.yaml`, and name it in `server/conf/machines.yaml` (`machines.list`).

**Anonymous broker**: any device on the network can then publish on `agent/<machine>/command`.
In production, use the accounts and rights of step 15 (`mosquitto/acl` has the agent rules).

### Step 15 — reliability

| What | How |
|---|---|
| MQTT accounts and rights | no anonymous access; a board reads and writes **its own** topics only ([docs/MOSQUITTO.md](docs/MOSQUITTO.md) §2) |
| Server presence | `voice/server/status`: retained `online`, `offline` last will; server gone = reported in the board's log |
| Watchdog | board frozen for more than 15 s → reboot, reason `watchdog` in the `boot` event |
| Network | TCP connection to the broker within 1 s; Wi-Fi restarted after 30 s without it |
| Log | copied to `server/logs/voice-server.log` (rotating files, 4 MB at most) |

Setup, **in this order**: a broker that is still open already accepts clients that have an account.

1. `make mqtt-user NAME=voice-server`, then `make mqtt-user NAME=esp32-01` (passwords: `openssl rand -hex 16`).
2. `server/.env`: `MQTT_USERNAME=voice-server`, `MQTT_PASSWORD=...`; `secrets.h`: `MQTT_PASSWORD="..."`.
3. `make fw-upload` and `make run`: everything works as before, the server announces its presence.
4. `make mosquitto-docker`: the broker now requires the accounts.

### Step 14 — per-profile rights

> On the `fingerprint` branch the "profile" is the person of the **finger** (step F4): see above. No voice score, no
> `full_min_score`.

| Level | Rights | Given to |
|---|---|---|
| **complet** (full) | everything; sensitive actions (open the door) after a "oui" **confirmation** with the same finger | people listed in `access.users` |
| **standard** | lights, shutters, closing the door | a person named by a finger, without a level (`access.default`) |
| **limite** (limited) | conversation only | the PTT button, or a finger with no name (`access.unknown`) |

Rules per device and action in `conf/access.yaml` (section `access`); an action missing from the
rules requires the full level. Names in `access.users` are those of `fingerprint.users`.

**Reminder**: a finger is not absolute proof (a copied fingerprint, or a finger put down by one person while another
talks). These rights prevent mistakes and guests' commands, not a determined attack.

### Step 13 — wake word (hands-free)

> **Removed on the `fingerprint` branch** (step F4b): the finger replaces the wake word and continuous listening. The code and its instructions are in the `main` branch.

### Step 12 — anti-jitter output buffer

The board plays nothing until it holds **300 ms of audio in reserve** (priming); the
Wi-Fi may then deliver chunks late or in bursts without it being heard. If the reserve
runs dry, playback stops cleanly and resumes once the reserve is rebuilt (re-priming)
instead of stuttering. The `played` report gives the delay to the first sample, the
**minimum margin** (network delay still absorbable) and the number of re-primings.

1. `make fw-upload SERIAL_PORT=/dev/ttyUSB0` (step 12 firmware).
2. `make tone-mqtt`: expected `reamorcages 0`, minimum margin ≈ 250 ms.
3. `make tone-mqtt JITTER=200`: a 200 ms stall every 500 ms; sound still continuous,
   `reamorcages 0`, minimum margin ≈ 100 ms.
4. `make tone-mqtt JITTER=400`: beyond the reserve; clean pauses (re-primings > 0),
   never any crackling.
5. `make run`: assistant answers, with `reamorcages 0` at the usual signal level.

### Step 11 — home automation

A command is three words: an **action** (allumer, éteindre, ouvrir, fermer), a **device**
(lumière, volets, porte) and a **room** (listed in `conf/home.yaml`, section `home`). Without a
room, the board's own room is used. The server publishes `home/<room>/<device>/set` for any
home automation system; when the room is the board's, the board also executes it: demo LED
on GPIO 21 for the light, simulated shutters and door (serial log).

1. Wire the LED (optional): GPIO 21 → 330 Ω → LED → GND ([docs/BROCHAGE.md](docs/BROCHAGE.md) §10).
2. `make fw-upload SERIAL_PORT=/dev/ttyUSB0` (step 11 firmware), then `make run`.
3. In another terminal: `make mqtt-watch` (voice/… as text and home/#).
4. "Allume la lumière" → "J'allume la lumière du salon.", LED on, `home/salon/light/set`
   and the confirmation `{"event":"device",...,"ok":true}`.
5. "Ferme les volets de la cuisine" → `home/cuisine/shutter/set` only (another room).
6. "Allume les volets" → "Je ne sais pas allumer les volets."; "Ouvre" → "Que dois-je ouvrir ?".

**Security**: voice identification is never proof. Do not connect a door strike or an
alarm to these commands; per-profile rights come in step 14.

### Step 10 — who said what

> **Removed on the `fingerprint` branch** (step F4b): a session has one person only, the finger's (the `transcript` message keeps one utterance). The code and its instructions are in the `main` branch.

### Step 9 — speaker identification

> **Removed on the `fingerprint` branch** (step F4b): the finger identifies the person, no longer the voice. The code and its instructions are in the `main` branch.

### Step 8 — diarization: who speaks when

> **Removed on the `fingerprint` branch** (step F4b): nobody is left to tell apart: a session is one finger's. The code and its instructions are in the `main` branch.

### Step 7 — voice activity detection (VAD)

> **Removed on the `fingerprint` branch** (step F4b): the finger bounds the session; a completely silent one is dropped by `stt.min_rms_dbfs`. The code and its instructions are in the `main` branch.

### Step 6 — MVP: the assistant answers

1. `make install-speech` then `make models` (faster-whisper `small`, Piper voice
   `fr_FR-siwis-medium`, about 550 MB in `server/models/`, once).
2. `make speech-check`: Piper says "Quelle heure est-il ?", Whisper reads it
   back, the assistant answers; no hardware needed.
3. `make transcribe OUT=server/recordings/<file>.wav`: transcribes a sentence
   recorded by the ESP32 in step 4.
4. `make run`: press the button, ask "Quelle heure est-il ?", release; the board
   answers. Recognised questions: time, date, hello, thanks, your name; anything
   else is repeated back as understood.

The log shows the time spent in each stage: `transcription 1.0 s, synthese
0.1 s, reponse prete en 1.1 s`. `make run-echo` keeps the step 5 echo mode,
without models.

### Step 5 — the server makes the ESP32 speak, over MQTT

1. `make fw-upload SERIAL_PORT=/dev/ttyUSB0` (step 5 firmware).
2. `make tone-mqtt`: 3 s of pure tone sent over MQTT; the board report is printed
   (`chunks 60/60, underruns 0`).
3. `make play-mqtt OUT=server/recordings/<file>.wav`: any WAV, converted to
   16 kHz mono, peak at −3 dBFS.
4. `make run-echo`: the server plays each sentence back on the board that said it.

### Step 4 — voice to the server, over MQTT

1. `make install` (server dependencies: paho-mqtt, PyYAML, numpy...).
2. `make run`: the server connects to the broker and waits for sessions.
3. `make fw-upload SERIAL_PORT=/dev/ttyUSB0`, then press the button, speak, release.
4. The server writes `server/recordings/esp32-01_<date>_<session>.wav` and logs a
   report: `complete`, received / announced chunks, peak and RMS.
5. `make play OUT=server/recordings/<file>.wav` or `make inspect OUT=...`.

If the broker is unreachable when the button is pressed, the session goes over
the serial link: `make record` remains available for troubleshooting.

### Step 3 — Wi-Fi and MQTT

1. Broker: see [docs/MOSQUITTO.md](docs/MOSQUITTO.md) (Debian service or container).
2. `cp firmware/include/secrets.h.example firmware/include/secrets.h`, then set
   the Wi-Fi (2.4 GHz) and the broker IP address.
3. `make mqtt-watch MQTT_HOST=<broker ip>` in a first terminal.
4. `make fw-upload SERIAL_PORT=/dev/ttyUSB0` in a second one.

Expected in the first terminal: `voice/esp32-01/state {"status":"online",...}`
and `voice/esp32-01/event {"event":"boot",...}`. After cutting the ESP32 power:
`voice/esp32-01/state {"status":"offline"}` within 15 s.

### Step 2 — test the audio output

1. Wire the MAX98357A amplifier and the speaker (§2), including C1 and C2.
2. `make fw-upload SERIAL_PORT=/dev/ttyUSB0`
3. At boot, a **440 Hz beep** plays: it tests the output on its own.
4. `make tone-esp`: 3 s of pure tone streamed from the PC in real time. Any
   dropout is heard as a click.
5. `make play-esp OUT=recordings/test.wav`: the step 1 recording played back on
   the ESP32 speaker.

Expected: clean tone, no clicks or crackle; the ESP32 report shows `0 refuses`
(rejected) and `0 sous-alimentations` (underruns).

### Step 1 — test the audio input

1. Wire microphone, button and LED as in §2.
2. `make fw-upload SERIAL_PORT=/dev/ttyUSB0`
3. `make record OUT=recordings/test.wav`
4. Press the button, say a sentence, release it.
5. Listen: `make play OUT=recordings/test.wav`
6. With no audio output: `make inspect OUT=recordings/test.wav` draws the signal
   envelope in ASCII and reports peak, RMS and DC offset.

Expected: the sentence is intelligible and not clipped; the script reports a peak
between 10 % and 90 % of full scale. Above 99 %, lower `MIC_GAIN` in
[firmware/include/config.h](firmware/include/config.h); below 2 %, raise it.

---

## 7. Repository layout

```
voice-assistant/
├── firmware/            ESP32 firmware (PlatformIO, Arduino)
│   ├── include/         config.h, secrets.h.example
│   └── src/             main.cpp + one .h/.cpp pair per class
├── server/              Python server (one module per class)
├── tools/               standalone test tools
├── mosquitto/           voice.conf
├── Makefile             every system operation
├── CONTEXT.md           progress log and frozen technical decisions
└── README.md / README-EN.md
```

---

## 8. License

MIT — see [LICENSE](LICENSE).
