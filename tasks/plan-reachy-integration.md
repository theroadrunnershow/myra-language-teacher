# Reachy Mini Integration Plan

**Robot**: Reachy Mini Wireless (Raspberry Pi 5, 8GB RAM)
**Goal**: Robot acts as an interactive language teacher for Myra — speaks words, listens to her replies, and reacts with head/antenna animations
**Status**: Planning

---

## Overview

The Myra app is already a REST API. The robot integration is a new Python script (`robot_teacher.py`) that runs on the Pi and drives the lesson loop by calling existing API endpoints. No changes to the server code are needed.

There are two deployment options depending on how much you want to set up on the Pi:

| | Option A | Option B |
|--|---------|---------|
| **Where Myra server runs** | GCP Cloud Run (already deployed) | On the Pi itself |
| **Laptop needed?** | No | No |
| **Internet needed?** | Yes (WiFi on robot) | Yes (gTTS needs Google) |
| **Setup effort** | Low — only install robot SDK on Pi | Medium — full app install on Pi |
| **Whisper runs on** | GCP (1 vCPU, fast) | Pi 5 CPU (~1–3s per word) |
| **Works offline?** | No | No (gTTS still needs internet) |
| **Best for** | Getting started quickly | Production / classroom use |

---

## Option A: Pi + GCP Cloud Run (Recommended Starting Point)

### How it works

```
┌─────────────────────────────┐         ┌──────────────────────────┐
│     Reachy Mini (Pi 5)      │         │  GCP Cloud Run           │
│                             │  HTTPS  │  kiddos-telugu-teacher   │
│  robot_teacher.py  ─────────┼────────►│  .com                    │
│  (lesson loop,              │         │                          │
│   animations,               │         │  /api/word               │
│   audio bridge)             │         │  /api/tts                │
│                             │         │  /api/recognize          │
│  4 mics  │  speaker         │         │  /api/dino-voice         │
│  head    │  antennas        │         │  /health                 │
└─────────────────────────────┘         └──────────────────────────┘
```

The Pi only runs `robot_teacher.py` and the Reachy Mini SDK. All speech recognition (Whisper) and TTS (gTTS) happen on the existing production server.

### Pi setup

```bash
# SSH into the robot
ssh pollen@reachy-mini.local   # default password: root

# Install the robot SDK (Python 3.10+ required — Pi OS ships with it)
pip install reachy-mini requests numpy pydub scipy soundfile

# Copy only robot_teacher.py to the Pi (not the full app)
scp robot_teacher.py pollen@reachy-mini.local:/home/pollen/
```

### robot_teacher.py config for Option A

```python
SERVER_URL = "https://kiddos-telugu-teacher.com"   # existing production server
# No server management needed — no subprocess, no port conflict
```

### Running

```bash
# On the Pi
python robot_teacher.py --language telugu --categories animals,colors --words 10
```

### Pros
- No app installation on Pi
- Fast recognition (GCP's full CPU)
- Uses the already-deployed, production-hardened server
- Simplest path to a working robot

### Cons
- Needs WiFi at all times (lesson stops if network drops)
- GCP Cloud Run has cold-start delay (~30s after idle) — first word may be slow
- Calls count toward your GCP budget

---

## Option B: Fully On Pi (No External Dependency)

### How it works

```
┌────────────────────────────────────────────────────────────┐
│                    Reachy Mini (Pi 5)                      │
│                                                            │
│  robot_teacher.py          myra server (port 8765)         │
│  ─────────────────         ──────────────────────────────  │
│  lesson loop       ◄─────► /health                         │
│  animations   HTTP         /api/word                       │
│  audio bridge              /api/tts        (gTTS→Google)   │
│                            /api/recognize                  │
│                              └─ Whisper tiny (39MB, CPU)   │
│                            /api/dino-voice                 │
│                                                            │
│  ReachyMini daemon (port 8000 — must not conflict!)        │
│  4 mics (16kHz)  │  speaker  │  head motors  │  antennas  │
└────────────────────────────────────────────────────────────┘
```

`robot_teacher.py` starts the Myra server as a subprocess on port **8765** (not 8000, which the Reachy Mini daemon uses).

### Pi setup

```bash
# SSH into the robot
ssh pollen@reachy-mini.local

# Install system deps
sudo apt-get update && sudo apt-get install -y ffmpeg

# Verify GStreamer (pre-installed on Reachy Mini OS)
gst-inspect-1.0 --version

# Copy the full Myra app to the Pi
scp -r /path/to/myra-language-teacher pollen@reachy-mini.local:/home/pollen/
# OR clone from git:
# git clone <repo-url> /home/pollen/myra-language-teacher

# Install all dependencies
cd /home/pollen/myra-language-teacher
pip install -r requirements.txt -r requirements-robot.txt

# Pre-warm Whisper model (downloads ~39MB on first run — do this over WiFi)
python -c "from faster_whisper import WhisperModel; WhisperModel('tiny', device='cpu', compute_type='int8'); print('Ready')"
```

### robot_teacher.py config for Option B

```python
SERVER_URL = "http://localhost:8765"   # local server on Pi
APP_DIR = "/home/pollen/myra-language-teacher"  # where to start server from
```

### Running

```bash
# On the Pi — robot_teacher.py starts the server automatically
python robot_teacher.py --language telugu --categories animals,colors --words 10

# OR start server separately, then run robot script without auto-launch
DISABLE_PASS1=true python -m uvicorn main:app --host 127.0.0.1 --port 8765 --workers 1 &
python robot_teacher.py --no-server --language telugu --words 10
```

### Pros
- No dependency on GCP — works as long as WiFi is up (gTTS still needs internet)
- No cold-start delay (Whisper stays warm in RAM)
- No GCP billing for robot sessions
- Can be set up as a systemd service that starts on boot

### Cons
- Full app install on Pi (~600MB Python deps + ffmpeg)
- Whisper recognition is ~1–3s per attempt on Pi CPU (vs. ~200ms on GCP)
- More moving parts to maintain

### Optional: systemd service for production use

Create `/etc/systemd/system/myra.service` so the Myra server starts automatically at boot:

```ini
[Unit]
Description=Myra Language Teacher Server
After=network.target

[Service]
User=pollen
WorkingDirectory=/home/pollen/myra-language-teacher
Environment=DISABLE_PASS1=true
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 127.0.0.1 --port 8765 --workers 1
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable myra
sudo systemctl start myra
# Then run robot_teacher.py with --no-server flag
```

---

## robot_teacher.py — Design (both options)

This is the new file to create. Same code for both options — only `SERVER_URL` changes.

### File structure

```python
# ── Configuration ─────────────────────────────────────────
SERVER_URL = "https://kiddos-telugu-teacher.com"  # Option A
# SERVER_URL = "http://localhost:8765"             # Option B

RECORD_DURATION = 5.0     # seconds to record per attempt
SAMPLE_RATE = 16000        # robot mic/speaker rate (fixed by SDK)
MAX_ATTEMPTS = 3
SIMILARITY_THRESHOLD = 50  # permissive for a toddler
LANGUAGES = ["telugu", "assamese"]
CATEGORIES = ["animals", "colors", "food", "numbers"]
```

### Audio bridge (the key bridging code)

The robot mic outputs numpy arrays; the API expects WAV bytes. The API returns MP3; the robot speaker expects numpy arrays.

```
RECORDING PATH
──────────────
SDK output:   (N, 2) float32 stereo at 16kHz
              ↓ mix to mono
              ↓ clip to [-1, 1]
              ↓ convert to int16
              ↓ scipy.io.wavfile.write → BytesIO
POST /api/recognize with audio_format="audio/wav"

PLAYBACK PATH
─────────────
GET /api/tts → MP3 bytes
              ↓ pydub AudioSegment.from_file()
              ↓ resample to 16kHz mono
              ↓ int16 → float32 → reshape(-1, 1)
mini.media.push_audio_sample(samples)
```

### Robot animation states

Maps lesson events to physical robot expressions:

| Lesson state | Head movement | Antennas | SDK calls |
|-------------|--------------|----------|-----------|
| idle | slow sway ±8° roll | relaxed at 30° | `goto_target` loop in background thread |
| speaking (TTS) | gentle nod | wiggle up/down | short repeated `goto_target` bursts |
| listening (recording) | tilt 15° (curious) | perk up to 60° | `goto_target` hold position |
| correct answer | bob up/down × 3 | rapid alternating ±0.8 | celebrate sequence |
| wrong answer | shake left/right | droop to -20° | express_wrong sequence |

### Lesson loop flow

```
1. GET /api/word?languages=telugu&categories=animals
   → print "CAT | పిల్లి | pilli | 🐱"

2. GET /api/tts?text=పిల్లి&language=telugu&slow=true
   → play through robot speaker + speak animation

3. GET /api/dino-voice?text=Can+you+say+it%3F
   → play English prompt through speaker

4. For each attempt (up to MAX_ATTEMPTS):
   a. robot enters listen pose
   b. mini.media.start_recording()
   c. wait 0.5s (echo gap after TTS)
   d. sleep 5s (recording window)
   e. samples = mini.media.get_audio_sample()
   f. mini.media.stop_recording()
   g. convert to WAV bytes
   h. POST /api/recognize → {is_correct, similarity, transcribed}
   i. if correct → celebrate + praise → next word
      if wrong + attempts left → express_wrong + "Try again!" + replay TTS
      if out of attempts → reveal word → next word
```

### Key issues to handle

| Issue | Solution |
|-------|---------|
| Port conflict (Reachy daemon uses 8000) | Myra server runs on 8765 (Option B only) |
| Audio echo (mic picks up speaker) | 0.5s gap between TTS end and recording start |
| goto_target blocks the thread | Animation loops run in daemon threads with `threading.Event` stop signals |
| GCP cold-start delay (Option A) | Send a dummy `/health` + warm-up word request at startup |
| Whisper cold-start on Pi (Option B) | Pre-warm model on Pi boot via systemd, or send dummy recognize at startup |
| gTTS needs internet | Pre-cache all word TTS as numpy arrays at session start (~30s, then fully cached) |

---

## New files to create

| File | Purpose |
|------|---------|
| `robot_teacher.py` | Main robot script — lesson loop, animations, audio bridge, server management |
| `requirements-robot.txt` | Robot-only deps: `reachy-mini>=1.2.0`, `soundfile>=0.12.1`, `requests>=2.31.0` |

`requirements-robot.txt` supplements `requirements.txt` — install both:
```bash
pip install -r requirements.txt -r requirements-robot.txt
```

`pydub` and `scipy` (already in `requirements.txt`) handle the audio conversion. `soundfile` adds lightweight WAV write support. `requests` is for synchronous HTTP from the robot script (no FastAPI needed on the client side).

---

## Testing plan

### Step 1 — API smoke test (no robot, just curl)

```bash
# Option A
curl https://kiddos-telugu-teacher.com/health
curl "https://kiddos-telugu-teacher.com/api/word?languages=telugu&categories=animals"

# Option B — start server first
DISABLE_PASS1=true python -m uvicorn main:app --host 127.0.0.1 --port 8765 --workers 1 &
sleep 5
curl http://localhost:8765/health
curl "http://localhost:8765/api/word?languages=telugu&categories=animals"
```

### Step 2 — Audio bridge test (no robot hardware needed)

```python
# Run on Pi (or Mac) — simulates mic output, sends to API
import numpy as np, io, scipy.io.wavfile as wav, requests

fake_stereo = np.random.uniform(-0.1, 0.1, (16000*5, 2)).astype(np.float32)
mono = fake_stereo.mean(axis=1)
pcm16 = (np.clip(mono, -1, 1) * 32767).astype(np.int16)
buf = io.BytesIO()
wav.write(buf, 16000, pcm16)

r = requests.post(f"{SERVER_URL}/api/recognize",
    data={"language": "telugu", "expected_word": "పిల్లి",
          "romanized": "pilli", "audio_format": "audio/wav",
          "similarity_threshold": "50"},
    files={"audio": ("audio.wav", buf.getvalue(), "audio/wav")},
    timeout=30)
print(r.json())
```

### Step 3 — TTS decode test (no robot hardware needed)

```python
import requests, io, numpy as np
from pydub import AudioSegment

mp3 = requests.get(f"{SERVER_URL}/api/tts",
    params={"text": "పిల్లి", "language": "telugu", "slow": "true"}).content
seg = AudioSegment.from_file(io.BytesIO(mp3), format="mp3")
seg = seg.set_frame_rate(16000).set_channels(1)
samples = (np.array(seg.get_array_of_samples(), dtype=np.int16)
           .astype(np.float32) / 32767.0).reshape(-1, 1)
print(f"Shape: {samples.shape}, dtype: {samples.dtype}")
# Expected: (N, 1) float32
```

### Step 4 — Robot animations only (no server needed)

```python
from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose
import numpy as np, time

with ReachyMini() as mini:
    # Celebrate
    mini.goto_target(head=create_head_pose(z=15, mm=True),
                     antennas=[0.8, -0.8], duration=0.3, method="minjerk")
    mini.goto_target(head=create_head_pose(z=-5, mm=True),
                     antennas=[-0.8, 0.8], duration=0.3)
    # Sad/wrong
    mini.goto_target(head=create_head_pose(roll=10, degrees=True),
                     antennas=[-0.2, -0.2], duration=0.3)
    mini.goto_target(head=create_head_pose(roll=0, degrees=True),
                     antennas=[0, 0], duration=0.4)
```

### Step 5 — Full dry run (one word, with robot)

```bash
python robot_teacher.py --words 1 --language telugu --categories animals
# Watch: console print, TTS playback, 5s recording window, API response, animation
```

---

## Recommended path

1. Start with **Option A** — get the robot working with the cloud server in a day
2. Once the lesson loop feels right, migrate to **Option B** for classroom use

The switch is one line: change `SERVER_URL` in `robot_teacher.py`.

---

## Progress tracking

- [x] Create `requirements-robot.txt`
- [x] Implement audio bridge functions (`mic_samples_to_wav_bytes`, `mp3_bytes_to_robot_samples`)
- [x] Implement HTTP client wrappers (`api_get_word`, `api_get_tts`, `api_recognize`, `api_get_dino_voice`)
- [x] Implement `RobotController` class (idle, speak, listen, celebrate, express_wrong)
- [x] Implement `run_lesson_word()` — single word cycle
- [x] Implement `run_lesson_session()` + `main()` with argparse
- [x] Implement `start_myra_server()`, `wait_for_server()`, `warm_up_server()`
- [x] Pass Step 1–3 tests on Mac
- [ ] Pass Step 4 test on Pi (animations)
- [ ] Pass Step 5 full dry run on Pi
