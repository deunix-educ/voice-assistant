# Local voice assistant — ESP32 + Raspberry Pi

A **fully local** voice assistant, with no cloud service: an ESP32 captures
speech, streams raw PCM over MQTT to a Python server that transcribes,
interprets and answers with speech synthesis; the ESP32 plays the answer back
and executes the command.

*Version française : [README.md](README.md).*

---

## 1. Architecture

```
INMP441 -> ESP32 -> MQTT -> Python server (Raspberry Pi / PC)
                                  |
                        +---------+---------+
                        |                   |
                       VAD          Diarization /
                        |           identification
                        +---------+---------+
                                  |
                           faster-whisper
                                  |
                              Assistant
                                  |
                                Piper
                                  |
                             MQTT audio/out
                                  |
                        ESP32 -> MAX98357A -> speaker
```

Four often-confused stages, never to be mixed up:

| Stage | Question it answers |
|---|---|
| VAD | Is someone speaking? |
| Diarization | Who speaks when? (SPEAKER_00, SPEAKER_01…) |
| Identification | Who is this person? (Denis, Marie, Unknown) |
| STT | What is being said? |

> Voice identification is **probabilistic**. It is never an authentication or
> security mechanism.

---

## 2. Hardware

| Item | Detail |
|---|---|
| ESP32-WROOM-32 | two I2S peripherals: I2S0 as receiver, I2S1 as transmitter |
| INMP441 | I2S microphone, **3.3 V**, L/R pin to GND (left channel) |
| MAX98357A | mono class-D amplifier, **5 V**, SD tied to VIN, GAIN left floating (9 dB) |
| Button | push-to-talk between GPIO 4 and GND (internal pull-up) |
| LED | with a 330 Ω resistor on GPIO 2 |
| Server | Raspberry Pi 4/5 64-bit or Debian PC |

### Wiring

**Full diagram: [docs/wiring.svg](docs/wiring.svg)** — detailed pinout, power
budget and multimeter checks (in French): **[docs/BROCHAGE.md](docs/BROCHAGE.md)**.

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

### 4.1 ESP32 firmware

**Prerequisites:** a USB cable with data lines (not charge-only), port identified
(`ls /dev/ttyUSB*` or `/dev/ttyACM*` after plugging in).

```bash
make pio-venv          # PlatformIO in its own venv — once only
make fw-build          # compile without flashing
make fw-upload SERIAL_PORT=/dev/ttyUSB0
```

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

# 5. Diarization + speaker identification (~1.3 GB) — optional, heavy on RPi 4
#    Prerequisite: Hugging Face "Read" token in server/.env (HF_TOKEN=hf_…)
#    Accept the conditions at https://huggingface.co/pyannote/speaker-diarization-community-1
cp server/.env.example server/.env    # then HF_TOKEN=hf_…
make install-diarization
make models

# 6. Wake word openWakeWord (~5 MB) — optional
make install-wakeword
make models

# 7. Run the assistant
make run
```

**Files to configure:**

| File | Contents |
|---|---|
| `server/.env` | `MQTT_USERNAME`, `MQTT_PASSWORD`, `HF_TOKEN` |
| `server/config.yaml` | rooms, voices, access rights, machine list |

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

# 2. Install the service (creates /opt/voice-agent/, /etc/voice-agent/, systemd unit)
cd ~/voice-agent-install
sudo make install

# 3. Configure
sudo nano /etc/voice-assistant/agent.yaml
#   id: pc-bureau                    ← name used in MQTT topics
#   mqtt.host: 192.168.1.104         ← broker IP address
#   dry_run: false                   ← true to test without executing anything

# 4. Enable and start
sudo systemctl enable --now voice-agent
sudo systemctl status voice-agent
```

**On the server,** add the machine to `server/config.yaml`:

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
# Say: "Hey Mycroft, éteins le PC du bureau"
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
make say WAKE="Hey Mycroft" TEXT="Ouvre la porte du salon." VOICE=fr_FR-tom-medium  # WAV + MP3 for a phone
make transcribe OUT=server/recordings/<file>.wav
make vad OUT=server/recordings/<file>.wav     # speech found by the VAD (step 7)
make vad-all                            # VAD over every server recording
make diarize OUT=server/recordings/<file>.wav   # who speaks when, who, who said what (steps 8-10)
make diarize-demo                       # two voices: yours and Piper's
make enroll NAME=Denis                  # voice profile from the last 5 sessions (step 9)
make profiles                           # list profiles; make forget NAME=Denis deletes one
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

Current step: **15b — Linux machines on the network**. Steps 1 to 15 are validated.

### Step 15b — Linux machines on the network

"Hey Mycroft, éteins le PC du bureau": each machine runs an **agent**
([agent/voice_agent.py](agent/voice_agent.py)), a small MQTT client that only runs
the actions in **its own** list (`/etc/voice-assistant/agent.yaml`): fixed commands,
no shell. The server only sends an action name. "Allume" wakes a machine that is
off through **Wake-on-LAN** (a network packet received by the sleeping network card).

| Action | Words | Rights (config.yaml) |
|---|---|---|
| `wake` (server) | allume, démarre, réveille | standard |
| `lock` | verrouille | standard |
| `shutdown` | éteins, arrête | full + confirmation |
| `reboot` | redémarre, relance | full + confirmation |

1. Try it on this PC without shutting anything down: `make agent-run` (creates `agent/agent.yaml` in test mode).
2. `make run`, then "Hey Mycroft, éteins le PC du bureau" → confirmation → "oui":
   the agent prints `essai : systemctl poweroff (non execute)`.
3. Real machine: copy `agent/` to it, `make -C agent install`, edit
   `/etc/voice-assistant/agent.yaml`, and name it in `server/config.yaml` (`machines.list`).

**Anonymous broker**: any device on the network can then publish on `agent/<machine>/command`.
In production, use the accounts and rights of step 15 (`mosquitto/acl` has the agent rules).

### Step 15 — reliability

| What | How |
|---|---|
| MQTT accounts and rights | no anonymous access; a board reads and writes **its own** topics only ([docs/MOSQUITTO.md](docs/MOSQUITTO.md) §2) |
| Server presence | `voice/server/status`: retained `online`, `offline` last will; server gone = board microphones off |
| Watchdog | board frozen for more than 15 s → reboot, reason `watchdog` in the `boot` event |
| Network | TCP connection to the broker within 1 s; Wi-Fi restarted after 30 s without it |
| Log | copied to `server/logs/voice-server.log` (rotating files, 4 MB at most) |

Setup, **in this order**: a broker that is still open already accepts clients that have an account.

1. `make mqtt-user NAME=voice-server`, then `make mqtt-user NAME=esp32-01` (passwords: `openssl rand -hex 16`).
2. `server/.env`: `MQTT_USERNAME=voice-server`, `MQTT_PASSWORD=...`; `secrets.h`: `MQTT_PASSWORD="..."`.
3. `make fw-upload` and `make run`: everything works as before, the server announces its presence.
4. `make mosquitto-docker`: the broker now requires the accounts.

### Step 14 — per-profile rights

| Level | Rights | Given to |
|---|---|---|
| **complet** (full) | everything; sensitive actions (open the door) after a "oui" **confirmation** | profiles listed in `access.users`, **if the voice is clearly recognised** (score ≥ 0.6) |
| **standard** | lights, shutters, closing the door | enrolled profile without a level (`access.default`) |
| **limite** (limited) | conversation only | unknown voice (`access.unknown`) |

Rules per device and action in `config.yaml` (section `access`); an action missing from the
rules requires the full level. Names in `access.users` are those shown by `make profiles`.

1. `make run` (server only, firmware unchanged).
2. "Hey Mycroft, allume la lumière" → executed (Denis, full).
3. "Hey Mycroft, ouvre la porte" → "Confirmez-vous : ouvrir la porte du salon ?" →
   "Hey Mycroft, oui" → "J'ouvre la porte du salon."; "non" → "D'accord, j'annule."
4. A phone voice (standard profile): "ouvre la porte" → refused; a non-enrolled voice:
   "allume la lumière" → "Je ne vous ai pas reconnu…". Ready-made sentences:
   `recordings/voix-test/tom-porte.mp3`, `pierre-lumiere.mp3`; more with `make say`. The wake word is spoken by an English
   voice (`WAKE`): a synthetic voice saying it the French way is not recognised.

**Reminder**: a voice is never proof of identity. These rights prevent mistakes and guests'
commands, not an attack (recording, impersonation).

### Step 13 — wake word (hands-free)

The server asks the board to stream its microphone continuously (`voice/<board>/audio/stream`)
and listens for the wake word with openWakeWord: **"Alexa"**, **"Hey Mycroft"** or **"Hey
Jarvis"**, pronounced the English way. Once heard, it keeps the command until the final
silence, then handles it like a push-to-talk session. The button still works at any time.

LED: **short flash every 2 s** = continuous listening; **on** = wake word heard, command in
progress; slow blink = ready, no continuous listening.

1. `make install-wakeword` then `make models` (5 ONNX models, 5 MB).
2. `make fw-upload SERIAL_PORT=/dev/ttyUSB0` (step 13 firmware), then `make run`.
3. "Alexa, allume la lumière" → LED on during the command, then answer and command.
4. A sentence without the wake word → nothing happens, nothing is recorded.
5. "Alexa" alone, then silence → back to standby after 4 s.

**Privacy**: before the wake word, only the last 500 ms are kept in memory, nothing is
written to disk (checked by a test). But audio flows continuously over the network: as long
as the broker accepts anonymous clients (step 15), anyone on the LAN can listen to it.
Turn listening off: `wake.enabled: false`, or
`make mqtt-control MSG='{"cmd":"listen","enabled":false}'`.

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
(lumière, volets, porte) and a **room** (listed in `config.yaml`, section `home`). Without a
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

Whisper gives the time of each word, the diarization the time of each speaker turn, on
the same audio: each word goes to whoever was speaking at that moment. The server
publishes the result on `voice/<board>/transcript` and the assistant calls the person by name.

```json
{"session": "453cce", "utterances": [
  {"speaker": "Denis", "text": "Ouvrir la porte.", "start": 0.0, "end": 1.26, "score": 0.6},
  {"speaker": "Siwis", "text": "Je suis la voix de synthèse...", "start": 1.5, "end": 4.46, "score": 0.6},
  {"speaker": "Denis", "text": "Fermez la porte.", "start": 4.92, "end": 5.86, "score": 0.6}]}
```

1. `make diarize OUT=server/recordings/<file>.wav`: who said what, and the MQTT message, no board needed.
2. `make mqtt-watch` in one terminal, `make run` in another.
3. "Bonjour": the board answers "Bonjour Denis !"; `voice/esp32-01/transcript` shows up.
4. A two-voice session (you, a phone voice, you again): three utterances.

### Step 9 — speaker identification

Each voice found by the diarization is summarised by an **embedding** (256 numbers,
WeSpeaker model from the pyannote pipeline), compared by **cosine similarity** with the
enrolled profiles. Above the threshold (0.45, measured on this bench), the name;
otherwise "Inconnu". **It is an estimate, never a security feature**: a recording or an
impersonation can fool it.

1. `make run`, then 5 varied sentences of 1 to 4 s, alone, at a normal distance.
2. `make enroll NAME=Denis`: profile built from those 5 sessions; sessions that are too
   short, contain several voices or do not match the others are dropped.
3. New questions (the server picks up the profile without restarting): the log shows
   `SPEAKER_00 = Denis (0.74)`; another person or a loudspeaker voice gives
   `Inconnu (0.05, proche de Denis)`.
4. `make diarize-demo`: your voice recognised, Piper's voice "Inconnu".

Profiles are **biometric data**: `server/profiles/`, never versioned, one readable JSON
file per person; `make forget NAME=...` deletes it.

### Step 8 — diarization: who speaks when

The speech found by the VAD is split into speaker turns: `SPEAKER_00` from 0.0 to 1.3 s,
`SPEAKER_01` from 1.4 to 2.9 s... Labels only hold within one session: putting a name on
a voice is step 9. Model `pyannote/speaker-diarization-community-1` (33 MB), run on CPU,
**pyannote telemetry disabled** (enabled by default in version 4).

1. Create an account on <https://huggingface.co>, then **accept the conditions** at
   <https://huggingface.co/pyannote/speaker-diarization-community-1>.
2. Create a "Read" token at <https://huggingface.co/settings/tokens> and put it in
   `server/.env`: `HF_TOKEN=hf_...` (`cp server/.env.example server/.env` if missing).
3. `make install-diarization` then `make models`. No Internet access afterwards.
4. `make diarize-demo`: your voice, Piper's voice, your voice → two speakers expected.
5. `make run`: one session where two people take turns; the log shows
   `2 locuteur(s) : SPEAKER_00 0.00-1.30, SPEAKER_01 1.40-2.90`.

Too heavy for a Raspberry Pi 4: set `diarization.enabled: false` in `config.yaml`.

### Step 7 — voice activity detection (VAD)

The server now transcribes speech only: leading and trailing silence is removed,
sessions without speech (silent press, click) are dropped without calling Whisper.
Silero ships with faster-whisper: nothing more to install.

1. `make vad-all`: one character per 32 ms window (`#` speech, `_` silence) for
   each recording, plus the number of sessions without speech.
2. `make vad OUT=server/recordings/<file>.wav` then `make play OUT=recordings/parole.wav`:
   speech only, silences removed.
3. `make run` (restart it: the code changed), ask a few questions and press once
   without speaking. The log shows `parole 1.36 s sur 2.00 s (segments 0.49-1.85 s)`,
   and `sans parole (VAD)` for the silent press, answered immediately.

Settings in `config.yaml`, section `vad`: threshold, minimum pause, minimum speech, padding.

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
