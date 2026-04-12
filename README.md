# 🦕 Myra Language Teacher

A fun, toddler-friendly web app that teaches **Telugu**, **Assamese**, **Tamil**, and **Malayalam** to your 4-year-old through a cute animated pink dino mascot!

## ✨ Features

- 🦕 Animated **pink dino** mascot with expressions (celebrate, shake, talk, idle bounce)
- 🔊 **Hear It!** – plays just the target-language word via TTS
- 🎤 **Say It!** – dino says *"Myra, repeat after me! word"* then listens via microphone
- 🔁 After a wrong answer the prompt replays automatically before the next attempt
- 📚 **600+ words** across 8 categories (Animals, Colors, Body Parts, Numbers, Food, Common Objects, Verbs, Phrases)
- 🌟 Score tracking with confetti celebrations on correct answers
- 🎨 6 colour themes and 6 mascots (dino, cat, dog, panda, fox, rabbit)
- 🐛 **Debug panel** – shows exactly what Whisper heard and the match score (useful while tuning)
- ⚙️ **Settings page** – configure child's name, languages, categories, and difficulty

## 🛠 Tech Stack


| Layer            | Technology                                                  |
| ---------------- | ----------------------------------------------------------- |
| Backend          | Python 3.11 / FastAPI                                       |
| Speech-to-Text   | faster-whisper (`tiny` model, CTranslate2, offline, no API key needed) |
| Text-to-Speech   | gTTS (Google TTS, requires internet)                        |
| Audio conversion | pydub + ffmpeg (WebM / MP4 / OGG → WAV)                     |
| Fuzzy matching   | rapidfuzz (`token_sort_ratio`, 0–100 scale)                 |
| Translation      | Google Cloud Translate (on-demand, cached to GCS)           |
| Frontend         | Vanilla HTML / CSS / JS + Jinja2 templates                  |


---

## 🚀 Local Setup

### 1. Prerequisites

```bash
# Python 3.11+
python3 --version

# ffmpeg (required by pydub for audio conversion)
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Ubuntu/Debian
# Windows: https://ffmpeg.org/download.html
```

### 2. Create a virtual environment

```bash
cd myra-language-teacher
python3 -m venv venv
source venv/bin/activate     # macOS/Linux
# venv\Scripts\activate      # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> The faster-whisper `tiny` model (~39 MB) downloads automatically on the **first** speech recognition call if not already cached locally.

### 4. Run the server

```bash
PYTHONPATH=src python src/main.py
```

Open **[http://localhost:8000](http://localhost:8000)** in your browser.

---

## 🤖 Raspberry Pi / Reachy Mini (Robot Mode)

For running Myra on a **Raspberry Pi** with a **Reachy Mini** robot (toddler-led lessons with physical dino), use the robot-specific dependencies:

### Remote install on Raspberry Pi

```bash
# On the Raspberry Pi (e.g. Pi 5), clone the repo and install both dependency sets:
pip install -r requirements.txt -r requirements-robot.txt
```


| File                     | Purpose                                                                                                                                         |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `requirements-robot.txt` | Reachy Mini SDK (wireless), `soundfile` (WAV I/O), `requests` (HTTP to Myra API). Install **in addition to** `requirements.txt` for robot mode. |
| `src/robot_teacher.py`   | Drives the lesson loop on the Pi: starts the Myra server on port 8765, uses robot mics/speaker, and calls `/api/recognize` and `/api/tts`.      |
| `tests/test_bridge.py`   | Verifies audio bridge (mic → WAV → API) and TTS without the robot attached. Run with the server already running on port 8765.                   |


Port **8765** is used to avoid conflict with the Reachy Mini daemon (port 8000). `robot_teacher.py` supports three modes:

| Mode | How to invoke | Whisper runs on |
|------|--------------|-----------------|
| Cloud | `--runtime-mode cloud` | GCP Cloud Run |
| Pi local | `--runtime-mode reachy_local` | Pi CPU (~200ms, single-pass) |
| Mac mini / MacBook | `--server-url http://<ip>:8765` | Mac CPU / Neural Engine (full dual-pass) |

Other flags:
- `--no-server` — only with `reachy_local`, when a local Myra server is already running
- `--words-sync-to-gcs never|session_end|shutdown` — control when locally-cached custom words are uploaded to GCS

### SSH into the robot

If the Reachy Mini is already on your Wi-Fi, SSH into it with:

```bash
ssh pollen@reachy-mini.local
```

After the first login, change the default password:

```bash
passwd
```

If `reachy-mini.local` is not reachable yet, do the one-time network setup first:

1. Power on the robot and wait about 30 seconds.
2. Connect your laptop to the robot hotspot `reachy-mini-ap` with password `reachy-mini`.
3. Open `http://reachy-mini.local:8000/settings` and connect the robot to your home Wi-Fi.
4. Reconnect your laptop to the same Wi-Fi network, then run `ssh pollen@reachy-mini.local` again.

Optional health check after SSH:

```bash
reachyminios_check
```

### Start the robot teacher

From the Raspberry Pi / Reachy Mini, activate your virtualenv and run from the repo root:

```bash
source /home/pollen/myra-venv/bin/activate
cd /home/pollen/myra-language-teacher
```

Use the hosted Myra server (`cloud` mode):

```bash
python src/robot_teacher.py --runtime-mode cloud --language telugu --categories animals,colors,body_parts,numbers,food,common_objects,verbs --words 10 --child-name Myra
```

Run Myra locally on the Pi (`reachy_local` mode, starts the FastAPI server for you):

```bash
WORDS_SYNC_TO_GCS=never python src/robot_teacher.py \
  --runtime-mode reachy_local \
  --language both \
  --categories animals,colors,food,numbers \
  --words 10 \
  --child-name Myra
```

If you already started the local server yourself on port `8765`, add `--no-server`:

```bash
python src/robot_teacher.py \
  --runtime-mode reachy_local \
  --no-server \
  --words-sync-to-gcs session_end \
  --language both \
  --categories animals,colors,food,numbers \
  --words 10 \
  --child-name Myra
```

Run Myra on a **Mac mini or MacBook** over LAN or Tailscale (best Whisper quality — full dual-pass, no `DISABLE_PASS1`):

```bash
# On Mac mini / MacBook — start the server (do this once per session)
source venv/bin/activate
PYTHONPATH=src uvicorn main:app --host 0.0.0.0 --port 8765

# Find your Mac's IP:
ipconfig getifaddr en0          # LAN IP (e.g. 192.168.1.x)
# or use your Tailscale IP:     # tailscale ip -4  (e.g. 100.x.x.x)
```

```bash
# On the Pi — point the robot at the Mac (LAN or Tailscale IP both work)
python src/robot_teacher.py \
  --server-url http://192.168.1.x:8765 \   # or http://100.x.x.x:8765 for Tailscale
  --language assamese \
  --categories animals,colors,food,numbers \
  --words 10 \
  --child-name Myra
```

> **Why Mac mini?** Running Whisper on the Mac enables the full dual-pass recognition strategy (native script + romanised), which significantly improves Assamese accuracy. The Pi's single-pass mode is a CPU-saving shortcut that trades accuracy for speed.

If `--child-name` is omitted, the script prompts for it interactively. See `REACHY_PI_SETUP.md` for the full Pi setup, env vars, and systemd service examples.

---

## 📖 How It Works

### Learning loop

1. App picks a random word (e.g. "cat") and shows it in English + the target language (e.g. **పిల్లి** in Telugu).
2. Press **🔊 Hear It!** to play just the word pronunciation (no prompt).
3. Press **🎤 Say It!** — the dino says *"Myra, repeat after me! పిల్లి"* then the microphone opens automatically.
4. Whisper transcribes what the child says and fuzzy-matches it against the expected word.
  - ✅ **Correct** → confetti + dino dances → auto-advances to next word
  - ❌ **Wrong** → dino shakes → replays *"repeat after me!"* prompt → child tries again
  - ❌ **Out of attempts** → shows the correct answer → moves on after 3 seconds

### Speech recognition details

- Audio MIME type is detected from the browser (`audio/webm`, `audio/mp4`, etc.) and the correct extension is used when converting with ffmpeg.
- Whisper is run **twice per recording** (dual-pass): once forced to the target language (e.g. `te` for Telugu) to get native script output, and once forced to English for romanized/phonetic output. The higher score of the two passes is used — this handles Whisper's tendency to output Latin transliteration instead of the native script.
- Matching runs against both the **native script** and the **romanized pronunciation** (e.g. "pilli"). The higher of the two scores wins.

---

## ⚙️ Settings

Visit **[http://localhost:8000/settings](http://localhost:8000/settings)** to configure:


| Setting           | Description                                                                          |
| ----------------- | ------------------------------------------------------------------------------------ |
| Child's name      | Used in the spoken prompt ("Myra, repeat after me!") and header                      |
| Languages         | Telugu, Assamese, Tamil, Malayalam, or a mixed lesson rotation                       |
| Categories        | Animals, Colors, Body Parts, Numbers, Food, Common Objects, Verbs, Phrases           |
| Show romanized    | Show phonetic pronunciation guide below the translation                              |
| Accuracy required | Minimum fuzzy-match score to count as correct (30–90%)                               |
| Max attempts      | How many tries before auto-advancing (2–5)                                           |
| Theme             | Colour theme: pink, blue, green, purple, orange, or yellow                           |
| Mascot            | Character: dino, cat, dog, panda, fox, or rabbit                                     |


Settings are stored in **browser sessionStorage** and take effect immediately. They are not persisted server-side.

---

## 🐛 Debug Panel

After each recording attempt a dark panel appears showing:

```
🎙️ Whisper heard: "పిల్లి"
📊 Match score: 87%
```

Use this to quickly diagnose problems:


| What you see                            | Likely cause                                              | Fix                                            |
| --------------------------------------- | --------------------------------------------------------- | ---------------------------------------------- |
| `⚠️ Error: … ffmpeg …`                  | ffmpeg not installed                                      | `brew install ffmpeg`                          |
| `Whisper heard: ""`                     | Audio conversion failed or mic too quiet                  | Check ffmpeg; speak closer to mic              |
| `Whisper heard: "pilli"` (Latin)        | Whisper output romanized text                             | Already handled via romanized fallback         |
| `Match score: 0%` with non-empty heard  | Child said the English word, not the target language word | Encourage them to say the Telugu/Assamese/Tamil/Malayalam word |
| Low score despite correct pronunciation | Threshold too high                                        | Settings → lower "Accuracy required"           |


---

## 🗂 Project Structure

```
myra-language-teacher/
├── src/
│   ├── main.py                  # FastAPI app & all API routes
│   ├── words_db.py              # Word database (600+ words across 4 languages)
│   ├── speech_service.py        # faster-whisper STT, dual-pass, MIME detection
│   ├── tts_service.py           # gTTS text-to-speech (async wrapper)
│   ├── translate_service.py     # Google Cloud Translate (on-demand, cached)
│   ├── dynamic_words_store.py   # GCS-backed dynamic word cache
│   └── robot_teacher.py         # Reachy Mini lesson driver (runs on Pi)
├── requirements.txt
├── requirements-dev.txt         # Test dependencies (pytest, httpx, anyio)
├── requirements-robot.txt       # Robot mode deps (Reachy Mini, soundfile, requests)
├── Dockerfile                   # GCP Cloud Run image (Python 3.11-slim + ffmpeg + pre-cached Whisper model)
├── pytest.ini
├── templates/
│   ├── index.html               # Main learning page (pink dino SVG + lesson UI)
│   └── config.html              # Settings page
├── static/
│   ├── css/style.css            # All styles + animations
│   └── js/app.js                # Recording, TTS playback, confetti, dino expressions
├── tests/
│   ├── conftest.py              # Stubs faster-whisper / noisereduce at import time
│   ├── test_api.py              # FastAPI route tests
│   ├── test_words_db.py         # Database integrity tests
│   ├── test_speech_service.py   # STT pipeline tests
│   ├── test_tts_service.py      # TTS service tests
│   ├── test_robot_teacher.py    # Reachy Mini integration tests
│   ├── test_translate_service.py
│   ├── test_dynamic_words_store.py
│   ├── test_security.py         # Security / rate-limit tests
│   └── test_bridge.py           # Audio bridge integration (requires live server on :8765)
├── infra/                       # Terraform — GCP Cloud Run infrastructure
│   ├── providers.tf
│   ├── cloud_run.tf
│   ├── artifact_registry.tf
│   ├── budgets.tf
│   ├── secret_manager.tf
│   ├── variables.tf
│   └── GCP_MIGRATION.md         # AWS → GCP migration notes
└── deploy/
    ├── bootstrap.sh             # Initial GCP project setup
    └── build-push.sh            # Docker build + push to Artifact Registry
```

---

## ☁️ GCP Deployment

### Architecture

```
Browser ──HTTPS──▶ Cloud Run (FastAPI + faster-whisper)
                        │
                        ├──▶ Google TTS (gTTS, internet)
                        ├──▶ Google Cloud Translate (on-demand translations)
                        └──▶ GCS (dynamic word cache + Terraform state)
```


| Component          | GCP service                  | Notes                                                                        |
| ------------------ | ---------------------------- | ---------------------------------------------------------------------------- |
| Backend            | Cloud Run (`dino-app`)       | 1 vCPU, 3 GB RAM; scales 0–2 instances; single Uvicorn worker keeps Whisper warm |
| Container registry | Artifact Registry            | Private; image tagged `latest`                                               |
| Translation cache  | GCS bucket                   | On-demand translations cached to avoid repeat API calls                      |
| Dynamic words      | GCS bucket                   | Custom word lists synced from the Pi                                         |
| Secrets            | Secret Manager               | API keys and env config                                                      |
| State backend      | GCS bucket (`myra-tfstate`)  | Terraform remote state                                                       |


### Scale-to-Zero & Cost Guardrails

**Cloud Run** scales to zero when idle — no running instances means $0 compute cost:

- `min_instance_count = 0` — scales to zero when idle
- `max_instance_count = 2` — hard cap on concurrent instances

**Budget kill-switch** (`infra/budgets.tf`):

| Threshold            | Action                                                               |
| -------------------- | -------------------------------------------------------------------- |
| Monthly limit (default $15) | Cloud Scheduler trigger scales Cloud Run to 0; app goes offline |

Restart manually when ready.

**Rate limiting** (FastAPI middleware — applied before requests reach the app):

| Endpoint           | Limit per IP |
| ------------------ | ------------ |
| `/api/recognize`   | 10 req/min   |
| `/api/tts`         | 30 req/min   |
| All other `/api/*` | 100 req/min  |

**Audio size limit:** 10 MB hard cap on uploaded audio in FastAPI. A 5-second recording is ~160 KB — 10 MB is 60× headroom for legitimate use.

### Build & Deploy

**Build and push the Docker image to Artifact Registry:**

```bash
./deploy/build-push.sh
```

**Deploy / update GCP infrastructure with Terraform:**

```bash
cd infra
terraform init
terraform plan  -var="project_id=<YOUR_PROJECT>"
terraform apply -var="project_id=<YOUR_PROJECT>"
```

**First-time GCP project setup:**

```bash
./deploy/bootstrap.sh
```

> See `infra/GCP_MIGRATION.md` for full migration notes from the previous AWS setup.

### Docker Notes

- Base image: `python:3.11-slim`
- ffmpeg installed at build time
- faster-whisper `tiny` model **pre-downloaded** during image build — avoids cold-start delay on Cloud Run
- Single Uvicorn worker keeps the Whisper model resident in RAM between requests
- Startup probe allows up to 120 s for the model to load; liveness probe checks every 30 s
