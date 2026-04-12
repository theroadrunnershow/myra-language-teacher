# 🦕 Myra Language Teacher

A fun, toddler-friendly web app that teaches **Telugu**, **Assamese**, **Tamil**, and **Malayalam** to your 4-year-old through a cute animated pink dino mascot!

## ✨ Features

- 🦕 Animated **pink dino** mascot with expressions (celebrate, shake, talk, idle bounce)
- 🔊 **Hear It!** – plays just the target-language word via TTS
- 🎤 **Say It!** – dino says *"Myra, repeat after me! word"* then listens via microphone
- 🔁 After a wrong answer the prompt replays automatically before the next attempt
- 📚 **60+ words** across 6 categories (Animals, Colors, Body Parts, Numbers, Food, Objects)
- 🌟 Score tracking with confetti celebrations on correct answers
- 🐛 **Debug panel** – shows exactly what Whisper heard and the match score (useful while tuning)
- ⚙️ **Settings page** – configure child's name, languages, categories, and difficulty

## 🛠 Tech Stack


| Layer            | Technology                                             |
| ---------------- | ------------------------------------------------------ |
| Backend          | Python 3.10+ / FastAPI                                 |
| Speech-to-Text   | OpenAI Whisper `20250625` (offline, no API key needed) |
| Text-to-Speech   | gTTS (Google TTS, requires internet)                   |
| Audio conversion | pydub + ffmpeg (WebM / MP4 / OGG → WAV)                |
| Fuzzy matching   | rapidfuzz (`token_sort_ratio`, 0–100 scale)            |
| Frontend         | Vanilla HTML / CSS / JS + Jinja2 templates             |


---

## 🚀 Local Setup

### 1. Prerequisites

```bash
# Python 3.10+
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

> The Whisper `base` model (~140 MB) downloads automatically on the **first** speech recognition call.

### 4. Run the server

```bash
python main.py
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


Port **8765** is used to avoid conflict with the Reachy Mini daemon (port 8000). `robot_teacher.py` now supports:

- `--runtime-mode cloud` to keep using the hosted Myra server
- `--runtime-mode reachy_local` to run the FastAPI server and Whisper locally on the Reachy Pi
- `--no-server` only with `reachy_local`, when a local Myra server is already running
- `--words-sync-to-gcs never|session_end|shutdown` to control when locally-cached custom words are uploaded back to GCS

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
- Whisper is run with the target language forced (`te` for Telugu, `as` for Assamese, `ta` for Tamil, `ml` for Malayalam). If the output is too short, it falls back to auto-detect.
- Matching runs twice: once against the **native script** and once against the **romanized pronunciation** (e.g. "pilli"). The higher of the two scores is used — this handles cases where Whisper outputs Latin transliteration instead of the native script.

---

## ⚙️ Settings

Visit **[http://localhost:8000/settings](http://localhost:8000/settings)** to configure:


| Setting           | Description                                                     |
| ----------------- | --------------------------------------------------------------- |
| Child's name      | Used in the spoken prompt ("Myra, repeat after me!") and header |
| Languages         | Telugu, Assamese, Tamil, Malayalam, or a mixed lesson rotation  |
| Categories        | Animals, Colors, Body Parts, Numbers, Food, Common Objects      |
| Show romanized    | Show phonetic pronunciation guide below the translation         |
| Accuracy required | Minimum fuzzy-match score to count as correct (30–90%)          |
| Max attempts      | How many tries before auto-advancing (2–5)                      |


Settings are saved to `config.json` and take effect immediately.

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
├── main.py              # FastAPI app & all API routes
├── words_db.py          # Word database (Telugu + Assamese + Tamil + Malayalam + romanized)
├── speech_service.py    # Whisper STT, MIME detection, romanized fallback
├── tts_service.py       # gTTS text-to-speech (async wrapper)
├── requirements.txt
├── requirements-robot.txt # Robot mode deps (Reachy Mini, soundfile, requests) — install on Raspberry Pi
├── robot_teacher.py       # Reachy Mini lesson driver (runs on Pi)
├── test_bridge.py         # Tests audio/TTS bridge (no robot required)
├── config.json            # Auto-created on first run; stores your settings
├── templates/
│   ├── index.html       # Main learning page (pink dino SVG + lesson UI)
│   └── config.html      # Settings page
└── static/
    ├── css/style.css    # All styles + animations
    └── js/app.js        # Recording, TTS playback, confetti, dino expressions
```

---

## 🔮 AWS Deployment Plan

### Architecture

```
Browser ──HTTPS──▶ CloudFront ──▶ WAF ──▶ ALB ──▶ ECS Fargate (FastAPI + Whisper)
                        │                              │
                        ▼                              ▼
                       S3                         NAT Gateway ──▶ Google (gTTS)
                 (static frontend)
```


| Component          | AWS service                  | Notes                                                                            |
| ------------------ | ---------------------------- | -------------------------------------------------------------------------------- |
| Frontend           | S3 + CloudFront              | Static HTML/CSS/JS; CDN edge caching                                             |
| Backend            | ECS Fargate                  | Whisper stays warm in memory; Lambda too slow (cold-start re-loads 140 MB model) |
| Load balancer      | ALB                          | Health-checks ECS; only public entry point to private subnet                     |
| Networking         | VPC (public/private subnets) | ECS has no public IP; only ALB exposed                                           |
| Container registry | ECR                          | Private; image scanning enabled                                                  |
| Config storage     | SSM Parameter Store          | Replaces `config.json`; encrypted at rest                                        |
| TTS (future)       | Amazon Polly                 | Eliminates Google dependency; Telugu supported                                   |


### DDoS / Cost Guardrails (no login required)

**WAF rate limits** (at CloudFront edge — bots never reach ECS):


| Endpoint           | Limit per IP |
| ------------------ | ------------ |
| `/api/recognize`   | 10 req/min   |
| `/api/tts`         | 30 req/min   |
| All other `/api/`* | 100 req/min  |


**ECS hard cap:** max 2 Fargate tasks. Auto-scaling can never spin up more, bounding compute cost regardless of traffic.

**Audio size limit:** 5 MB hard cap on uploaded audio in FastAPI. A 5-second recording is ~160 KB — 5 MB is 30× headroom for legitimate use.

**Single budget — $50/month hard kill:**


| Threshold  | Action                                                                                     |
| ---------- | ------------------------------------------------------------------------------------------ |
| $40 (80%)  | Email alert → "investigate"                                                                |
| $50 (100%) | **Automated kill** — Budget Action sets ECS desired tasks to 0; SNS push notification sent |


App goes offline when the kill fires. Restart manually via console or CLI when ready.

**Nightly scale-to-zero** (EventBridge Scheduler):

- `8:00 PM` → ECS desired = 0
- `7:30 AM` → ECS desired = 1
- Saves ~$15–18/month vs always-on

**Cost Anomaly Detection:** free AWS ML service; emails on unusual spending spikes.

### Estimated Monthly Cost


| Scenario                                                     | Cost                    |
| ------------------------------------------------------------ | ----------------------- |
| Normal home use (evenings + weekends, nightly scale-to-zero) | ~$22–28/mo              |
| Heavy all-day use                                            | ~$42/mo                 |
| DDoS hits (WAF throttles, ECS capped at 2 tasks)             | ~$48/mo                 |
| Hard stop triggers                                           | ≤ $50, app goes offline |


### Files to Add for Deployment

```
myra-language-teacher/
├── Dockerfile           # Python + ffmpeg + app
├── .dockerignore
└── infra/               # Terraform
    ├── ecr.tf           # Container registry
    ├── ecs.tf           # Fargate task + service + auto-scaling
    ├── alb.tf           # Load balancer
    ├── cloudfront.tf    # CDN + WAF + rate limit rules
    ├── vpc.tf           # Network (public/private subnets, NAT)
    ├── iam.tf           # ECS task role (least-privilege)
    ├── ssm.tf           # Parameter Store entries (replaces config.json)
    ├── budgets.tf       # $50 budget + automated kill action
    └── scheduler.tf     # Nightly scale-to-zero
```
