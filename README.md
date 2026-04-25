# 🦕 Myra Language Teacher

A toddler-friendly platform with **two modes**:

- **Language Lesson** — scripted pronunciation practice for **Telugu**, **Assamese**, **Tamil**, and **Malayalam** with an animated mascot.
- **Kids Teacher** — open-ended preschool conversation for 4–5 year olds over a realtime voice backend (OpenAI Realtime *or* Google Gemini Flash Live, selectable at runtime), with a locked safety profile and no scripted answers.

Both modes share the same FastAPI app and, on the Reachy Mini robot, the same audio hardware. The current language-lesson behavior is untouched; kids-teacher is an additive sibling flow.

## ✨ Features

### Language Lesson (the original mode)

- 🦕 Animated **pink dino** mascot with expressions (celebrate, shake, talk, idle bounce)
- 🔊 **Hear It!** – plays just the target-language word via TTS
- 🎤 **Say It!** – dino says *"Myra, repeat after me! word"* then listens via microphone
- 🔁 After a wrong answer the prompt replays automatically before the next attempt
- 📚 **600+ words** across 8 categories (Animals, Colors, Body Parts, Numbers, Food, Common Objects, Verbs, Phrases)
- 🌟 Score tracking with confetti celebrations on correct answers
- 🎨 6 colour themes and 6 mascots (dino, cat, dog, panda, fox, rabbit)
- 🐛 **Debug panel** – shows exactly what Whisper heard and the match score (useful while tuning)
- ⚙️ **Settings page** – configure child's name, languages, categories, and difficulty

### Kids Teacher (new)

- 🧸 Open-ended voice conversation for 4–5 year olds via **OpenAI Realtime** or **Google Gemini Flash Live** (chosen by `KIDS_TEACHER_REALTIME_PROVIDER`)
- 🔒 **Locked preschool profile** with disallowed-topic refusals, restricted-topic family-safe answers, and clarification behavior
- 🌐 Multilingual — English (primary) and Telugu with confidence-based language selection and English fallback (Assamese/Tamil/Malayalam stay on the language-lesson flow)
- ✋ Natural **barge-in** — the child can interrupt the assistant at any time (server-VAD triggered)
- 👪 **Admin policy precedence** — admins can add restrictions; the system safety floor can never be weakened
- 📼 Optional **review storage** — transcript and raw-audio retention are separate opt-ins, default OFF, local-first with optional GCS sync

## 🛠 Tech Stack


| Layer               | Technology                                                  | Used by |
| ------------------- | ----------------------------------------------------------- | ------- |
| Backend             | Python 3.11 / FastAPI                                       | both |
| Speech-to-Text      | faster-whisper (`tiny` model, CTranslate2, offline, no API key) | language lesson |
| Text-to-Speech      | gTTS (Google TTS, requires internet)                        | language lesson |
| Audio conversion    | pydub + ffmpeg (WebM / MP4 / OGG → WAV)                     | language lesson |
| Fuzzy matching      | rapidfuzz (`token_sort_ratio`, 0–100 scale)                 | language lesson |
| Translation         | Google Cloud Translate (on-demand, cached to GCS)           | language lesson |
| Realtime voice      | OpenAI Realtime (`gpt-realtime`, `gpt-realtime-mini`) **or** Gemini Flash Live (`gemini-live-2.5-flash-native-audio`) — server-side VAD on both | kids teacher |
| Input transcription | `gpt-4o-mini-transcribe` (OpenAI path) / built-in on Gemini  | kids teacher |
| Frontend            | Vanilla HTML / CSS / JS + Jinja2 templates                  | both |


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
| `requirements-common.txt` | Shared runtime/audio deps used by both the FastAPI app and the robot scripts (`numpy`, `scipy`, `pydub`, `python-dotenv`). |
| `requirements-robot.txt` | Reachy Mini SDK + `requests` only. It pulls in `requirements-common.txt`, so `pip install -r requirements-robot.txt` still works for cloud-mode Pi installs. |
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

## 🧸 Kids Teacher Mode

An open-ended preschool conversation mode for 4–5 year olds. Unlike the language-lesson flow (which scores pronunciation of a known target word), kids teacher is a streaming voice conversation with a locked safety profile. It runs on either OpenAI Realtime or Google Gemini Flash Live (chosen at runtime) and reuses the robot's audio hardware.

### Provider selection — OpenAI vs. Gemini

Kids-teacher supports two realtime backends behind the same `RealtimeBackend` protocol. Pick one per deployment via `KIDS_TEACHER_REALTIME_PROVIDER` (`openai` | `gemini`, default `openai`). Both backends implement the same 9-event contract (`input.speech_started`, `input_transcript.delta/final`, `assistant_transcript.delta/final`, `audio.chunk`, `response.done`, `error`, etc.) so the handler, safety layer, review store, and robot bridge are unchanged.

| Dimension | OpenAI Realtime | Gemini Flash Live |
| --------- | --------------- | ----------------- |
| Model    | `gpt-realtime` / `gpt-realtime-mini` | `gemini-2.5-flash-native-audio-preview-12-2025` (AI Studio default), `gemini-3.1-flash-live-preview` (AI Studio), or `gemini-live-2.5-flash-native-audio` (Vertex GA only) |
| Transport | WebSocket / WebRTC | WebSocket only |
| Mic input rate | PCM16 LE 24 kHz mono | PCM16 LE 16 kHz mono |
| Speaker output | PCM16 LE 24 kHz mono | PCM16 LE 24 kHz mono (same — robot playback unchanged) |
| Free tier | No | Yes (3 concurrent sessions; 15-min audio session cap) |
| Approx. cost for a full 15-min session | ~$1.44 on `gpt-realtime` | ~$0.35 (paid tier); free for single-user AI Studio |
| Data handling | Not used for training | **Free tier may be used for training** — enable billing (or use Vertex AI) to disable |
| Voice options | `alloy`, `echo`, `shimmer`, … (mapped automatically on the Gemini path) | 30 prebuilt voices (`Kore`, `Puck`, `Aoede`, …); `Kore` is the default for child-facing use |
| Telugu output | Supported | Listed as supported in Live docs; empirical quality not yet verified — see `tasks/todo.md` |

The `profiles/kids_teacher/voice.txt` value is an OpenAI voice name; when provider is Gemini the backend maps it to the closest Gemini voice (`alloy → Kore`, `echo → Puck`, etc.), so no profile-file change is needed when switching providers.

### Architecture at a glance

```
Child speech ─▶ mic pump ─▶ RealtimeBackend (OpenAI | Gemini) ─▶ Handler ─▶ Safety wrapper ─▶ Review store ─▶ Hooks ─▶ Robot speaker
                                    ▲                                       │
                                    └──────── interrupt() ──────────────────┘   (on unsafe input)
```

- `src/kids_teacher_realtime.py` — session handler (partial/final transcripts, recent-turn memory, barge-in, fallback)
- `src/kids_teacher_backend.py` — OpenAI Realtime adapter (the only place `import openai` lives) + `resolve_realtime_provider()`
- `src/kids_teacher_gemini_backend.py` — Gemini Flash Live adapter (the only place `import google.genai` lives); translates the OpenAI-shaped session payload into `LiveConnectConfig` and always enables `input_audio_transcription` so the safety layer keeps working
- `src/kids_safety.py` — topic classifier, refusal/redirect/family-safe helpers, admin-policy precedence
- `src/kids_review_store.py` — local-first transcript + raw-audio retention, optional GCS sync
- `src/kids_teacher_flow.py` — orchestrator; wires safety + review + hooks around the handler
- `src/kids_teacher_robot_bridge.py` — robot audio bridge (playback thread + mic pump)
- `profiles/kids_teacher/{instructions,tools,voice}.txt` — the locked persona content

### Prerequisites

In addition to the base setup, provide credentials for whichever provider you're using:

**OpenAI path (default):**

```bash
export OPENAI_API_KEY=sk-...
# openai>=1.59.0 is already in requirements.txt
pip install -r requirements.txt
```

**Gemini path:**

```bash
export GEMINI_API_KEY=...                              # from https://aistudio.google.com/apikey
export KIDS_TEACHER_REALTIME_PROVIDER=gemini
# google-genai>=1.0.0 is already in requirements.txt
pip install -r requirements.txt
```

Or drop them into `.env` at the repo root (loaded automatically by `env_loader.load_project_dotenv()`, shell env takes precedence):

```
GEMINI_API_KEY=your-key-here
KIDS_TEACHER_REALTIME_PROVIDER=gemini
# AI Studio (api_key auth) — this is what a free-tier key hits:
KIDS_TEACHER_GEMINI_MODEL=gemini-2.5-flash-native-audio-preview-12-2025
# Vertex AI only — do NOT use with an AI Studio key (404s with "not found for v1beta"):
# KIDS_TEACHER_GEMINI_MODEL=gemini-live-2.5-flash-native-audio
```

`.env` is already gitignored, so the API key stays local. Both SDKs (`openai` and `google-genai`) are installed from `requirements.txt` regardless of which provider you pick, so switching providers is just an env-var flip — no reinstall.

> **Model-id gotcha.** Google exposes Gemini Live on two separate endpoints with **different** model ids for the same capability. If you use an AI Studio API key (the `api_key` path), you must use an AI Studio model id. The Vertex GA id (`gemini-live-2.5-flash-native-audio`) will fail with `1008 models/... not found for API version v1beta` on the AI Studio endpoint. The defaults in this repo target AI Studio; Vertex users should override `KIDS_TEACHER_GEMINI_MODEL` explicitly.

### Persistent memory

Kids-teacher keeps a small sectioned markdown file that is loaded into the system prompt at session start:

```bash
~/.myra/memory.md      # default; override via MYRA_MEMORY_FILE
```

The file has three sections:

```markdown
# Things to remember about the child

## Current
- name: Aanya _(2026-04-25)_
- mom_name: Diya _(2026-04-25)_
- favourite_colour: blue _(2026-04-25)_

## Notes
- She loves dinosaurs _(2026-04-24)_
- She is afraid of vacuum cleaners _(2026-04-24)_

## History
- name: Abi _(2026-04-24 → 2026-04-25)_
```

Only **`## Current`** and **`## Notes`** are injected into the system instruction; **`## History`** is a parent-facing audit log and never enters the model context (re-injecting superseded values would put contradictions back in front of the model).

If the file is missing, startup still works — the session just starts with no persistent memory. On any provider, the robot asks "What should I call you?" if it does not yet know the child's name.

#### Memory tools (Gemini path)

When the child or parent explicitly asks the robot to remember something, Gemini calls one of two tools:

- `set_about(key, value)` — single-valued slots. Allowed keys:
  `name`, `age`, `pronouns`, `mom_name`, `dad_name`, `favourite_colour`,
  `favourite_animal`, `favourite_food`, `favourite_book`. Setting a key supersedes the previous value into `## History`.
- `add_note(text)` — free-form observations. Routed through a relevance-filtered reconciler before persisting.

On the OpenAI path memory is currently read-only — the robot uses pre-seeded memory but does not write new facts.

#### Note reconciler (LLM-assisted dedup)

When `add_note` runs and there are already ≥ 3 existing notes, the reconciler:

1. Picks the top-K most-similar existing notes via rapidfuzz `token_set_ratio`.
2. Sends only those K + the new note to a small text LLM (Ollama by default).
3. The LLM returns one of: `skip` (already covered) | `append` (new info) | `merge` (combine with existing) | `replace` (supersede existing).
4. Replaced/merged notes flow into `## History`.

LLM failures (network, malformed JSON, missing SDK) fall back to a plain append. The realtime session never blocks — reconciler writes run on a background task on the Gemini path.

#### Reconciler prerequisites

Default provider is **Ollama** (local on the robot, or remote via `OLLAMA_HOST`). The `ollama` Python SDK is in `requirements.txt` (`pip install -r requirements.txt`). For the default you also need an Ollama server reachable from the host running kids-teacher:

```bash
# On whichever host runs Ollama
ollama pull llama3.2:3b
ollama serve &                            # if not already running

# Or point at a remote Ollama:
export OLLAMA_HOST=http://10.0.0.5:11434
```

Switch providers via env (no reinstall — `openai` and `google-genai` are already in `requirements.txt`):

| Variable                  | Default       | Notes |
|---------------------------|---------------|-------|
| `MYRA_TEXT_LLM_PROVIDER`  | `ollama`      | `ollama` \| `gemini` \| `openai` |
| `MYRA_TEXT_LLM_MODEL`     | per-provider  | Default: `llama3.2:3b` (ollama), `gemini-2.5-flash` (gemini), `gpt-4o-mini` (openai) |

`gemini` reuses `GEMINI_API_KEY`; `openai` reuses `OPENAI_API_KEY`.

#### Pre-seeding the memory file

You can write `## Current` and `## Notes` by hand before a session — the bullet format is the same one the robot writes:

```markdown
# Things to remember about the child

## Current
- name: Aanya _(2026-04-24)_

## Notes
- They love tigers _(2026-04-24)_
```

### Run a kids-teacher session

There are two startup paths: a FastAPI dashboard that runs on your dev laptop, and a headless CLI that runs the live realtime conversation on the Reachy Mini / Pi. The dashboard does **not** start a conversation — it only surfaces config and review state. They are independent processes; start whichever one(s) you need.

#### A. Web dashboard on your dev laptop (status only)

```bash
# From the repo root on your laptop
source venv/bin/activate
export OPENAI_API_KEY=sk-...                          # required — the page reads model/profile from config
# Optional — enable review listing on the dashboard:
# export KIDS_REVIEW_TRANSCRIPTS_ENABLED=true

PYTHONPATH=src python src/main.py                     # serves on http://localhost:8000
```

Then open **[http://localhost:8000/kids-teacher](http://localhost:8000/kids-teacher)**. The page shows the current model, enabled languages, default language, voice, and (when review is enabled) recent sessions.

#### B. Live session on the Reachy Mini / Pi (headless)

**B1. One-time install on the Pi.** SSH in (`ssh pollen@reachy-mini.local`), clone the repo, then install the full dependency set — `requirements.txt` provides the OpenAI + server-side packages, while `requirements-robot.txt` adds the Reachy-specific layer on top of the shared runtime/audio packages in `requirements-common.txt`:

```bash
# On the Pi, inside the repo directory
sudo apt install -y ffmpeg                            # once per Pi image
python3 -m venv /home/pollen/myra-venv
source /home/pollen/myra-venv/bin/activate
pip install -r requirements.txt -r requirements-robot.txt
```

**B2. Set credentials.** Either export them in the shell, or add them to `/home/pollen/myra-language-teacher/.env` so the entry script picks them up via `env_loader`. For OpenAI:

```bash
export OPENAI_API_KEY=sk-...
```

For Gemini (Phase 1 default on dev laptops — set to `gemini` in `.env` after you have a key):

```bash
export GEMINI_API_KEY=...
export KIDS_TEACHER_REALTIME_PROVIDER=gemini
```

**B3. Start the session on the Pi.** No FastAPI server is required — the CLI connects to the selected realtime provider directly and drives the robot's mic + speaker via `kids_teacher_robot_bridge.py`:

```bash
source /home/pollen/myra-venv/bin/activate
cd /home/pollen/myra-language-teacher

PYTHONPATH=src python src/robot_kids_teacher.py \
  --session-id session-2026-04-23-a \                 # optional; defaults to a fresh UUID
  --max-seconds 900                                   # optional; no cap when omitted
```

If the selected provider's SDK (`openai` or `google.genai`) is not importable, the script exits cleanly with code `2` rather than crashing. The same command also works on any non-robot host that has a mic + speaker, the relevant API key, and the matching SDK installed — useful for smoke-testing off the robot.

### Configuration (env vars)

Provider + backend model:

| Variable                           | Default                                   | Purpose |
| ---------------------------------- | ----------------------------------------- | ------- |
| `KIDS_TEACHER_REALTIME_PROVIDER`   | `openai`                                  | Realtime backend: `openai` or `gemini`. Picks which SDK is imported and which model allowlist is applied |
| `OPENAI_API_KEY`                   | _(required when provider=openai)_         | OpenAI credentials for the realtime session |
| `KIDS_TEACHER_REALTIME_MODEL`      | `gpt-realtime`                            | OpenAI model name. Only `gpt-realtime` and `gpt-realtime-mini` are accepted |
| `GEMINI_API_KEY`                   | _(required when provider=gemini)_         | Google AI Studio API key — https://aistudio.google.com/apikey |
| `KIDS_TEACHER_GEMINI_MODEL`        | `gemini-2.5-flash-native-audio-preview-12-2025` | Gemini Live model. Accepted: `gemini-2.5-flash-native-audio-preview-12-2025` + `gemini-3.1-flash-live-preview` on AI Studio, `gemini-live-2.5-flash-native-audio` on Vertex. The Vertex id will 404 on the AI Studio endpoint |
| `MYRA_MEMORY_FILE`                 | `~/.myra/memory.md`                       | Optional override for the persistent kids-teacher memory markdown file |
| `MYRA_TEXT_LLM_PROVIDER`           | `ollama`                                  | Provider for the note reconciler: `ollama` \| `gemini` \| `openai` |
| `MYRA_TEXT_LLM_MODEL`              | `llama3.2:3b`                             | Reconciler model id (per-provider default applied when blank) |
| `OLLAMA_HOST`                      | _(local)_                                 | Optional remote Ollama endpoint (e.g. `http://10.0.0.5:11434`) |

Review storage (both default **OFF**; enable explicitly per deployment):

| Variable                       | Default                               | Purpose |
| ------------------------------ | ------------------------------------- | ------- |
| `KIDS_REVIEW_TRANSCRIPTS_ENABLED` | `false`                            | Persist transcript text to `session.json` after each session |
| `KIDS_REVIEW_AUDIO_ENABLED`       | `false`                            | Persist raw child audio chunks under `<session_id>/audio/` |
| `KIDS_REVIEW_RETENTION_DAYS`      | `30`                               | Days to keep persisted sessions before `prune_expired` removes them (`0` disables pruning) |
| `KIDS_REVIEW_LOCAL_DIR`           | `data/kids_review.runtime.v1`      | Local directory for session JSON + audio files |
| `KIDS_REVIEW_OBJECT_BUCKET`       | _(empty)_                          | GCS bucket name — leave blank for local-only |
| `KIDS_REVIEW_OBJECT_PREFIX`       | `kids_review/v1`                   | Object-key prefix inside the bucket |
| `KIDS_REVIEW_SYNC_TO_GCS`         | `never`                            | `never`, `session_end`, or `shutdown` |

When both review toggles are false, **no transcript text or raw audio is retained after the live session** — live transcript events are still published to hooks for real-time UI, but nothing is written to disk.

### The kids-teacher profile (`profiles/kids_teacher/`)

Three plain-text files drive the locked persona. Edit them without touching code:

| File              | Purpose |
| ----------------- | ------- |
| `instructions.txt`| The full system prompt. Preschool tone, safe/disallowed/restricted topic lists, multilingual guidance, clarification behavior, output constraints. Missing or empty = startup error |
| `tools.txt`       | One tool name per line; `#` comments and blank lines are skipped. Profile-owned tools still ship **empty** by default. Gemini's internal `remember` memory tool is backend-owned and is **not** listed here |
| `voice.txt`       | Single line: OpenAI Realtime voice name. V1 default is `alloy`. Options include `ash`, `ballad`, `coral`, `echo`, `sage`, `shimmer`, `verse`, `marin`, `cedar` |

The profile is treated as **locked by default** (`KidsTeacherProfile.locked=True`). Change requires a code edit and review, not a runtime flag.

### Safety controls

Policy is layered. Admin can add restrictions; admin **cannot** weaken the system floor:

1. System hard safety rules (baked in via `kids_safety.py` keyword tables and the locked profile)
2. Admin-added restrictions (`KidsTeacherAdminPolicy.avoid_topics`, `redirect_to`, `extra_rules`)
3. Admin profile defaults (teaching style, preferred topics)
4. Session overrides (per-session preferences)

Topic outcomes per `classify_topic()`:

| Decision                | When                                             | Behavior |
| ----------------------- | ------------------------------------------------ | -------- |
| `ALLOW`                 | Safe preschool topic                             | Assistant answers normally |
| `FAMILY_SAFE_ANSWER`    | Approved restricted category (reproduction, sickness, death, simple body questions) | Short safe answer + suggest asking a grown-up |
| `REDIRECT`              | Scary events, conflict, admin-avoid match        | Short refusal + redirect to a safe adjacent topic |
| `REFUSE`                | Disallowed (weapons, drugs, sexual content, gore, self-harm, criminal how-to, etc.) | Short refusal with no engagement on the unsafe content |

When safety trips on child input, two things happen:
1. The assistant's in-flight response is **cancelled** via `handler.interrupt()` → `backend.cancel_response()` so the unsafe audio never plays.
2. A synthetic safe assistant transcript is emitted so the UI/review log shows what was said instead.

The original unsafe child transcript is still preserved in the review log — so admins can audit what was asked.

### Review data model

When review persistence is enabled, each session produces a directory:

```
<KIDS_REVIEW_LOCAL_DIR>/<session_id>/
  session.json        # metadata + transcripts[] (or [] when transcripts disabled)
  audio/              # present only when audio retention is enabled
    <timestamp_ms>-child.webm
    <timestamp_ms>-child.webm
```

Admin-only routes (localhost only, `403` from non-local clients):

| Route                                       | Behavior |
| ------------------------------------------- | -------- |
| `GET /api/kids-teacher/status`              | Non-sensitive snapshot (model, languages, profile name, review toggle states) |
| `GET /api/kids-teacher/review/sessions`     | List persisted sessions — `404` when review is disabled |
| `GET /api/kids-teacher/review/sessions/{id}`| Session detail — transcripts stripped when `KIDS_REVIEW_TRANSCRIPTS_ENABLED=false` |

GCS sync follows `KIDS_REVIEW_SYNC_TO_GCS` automatically — V1 does not expose a manual force-sync endpoint for kids-teacher review. Set the policy to `session_end` or `shutdown` to upload at those boundaries.

### Testing

All kids-teacher code is unit-testable without a robot, an OpenAI key, or network. Run:

```bash
pytest tests/test_kids_teacher_*.py tests/test_kids_safety.py \
       tests/test_kids_review_store.py tests/test_api_kids_teacher.py -v
```

The fake realtime backend (`src/kids_teacher_fakes.py::FakeRealtimeBackend`) lets you script scenarios deterministically — see `tests/test_kids_teacher_flow.py` for end-to-end examples (safe topic, disallowed topic, overlong output, barge-in).

### What requires hardware + API key

Code below this line is structurally complete but has not been smoke-tested on live hardware:

- Live wire-format of OpenAI Realtime events (names match the documented API; a first-run on real endpoints may need minor tweaks)
- Robot microphone capture feeding `pump_microphone_to_backend` (uses the same audio helpers as `robot_teacher.py`)
- End-to-end latency tuning (target: assistant audio starts <1.5s after child finishes; <3s acceptable)

---

## 📝 Dynamic Word Expansion

The built-in database ships **600+ words**. Whenever an English word is looked up that isn't in that list, the app translates it on demand via Google Cloud Translate and **permanently adds it to the word pool** — so the vocabulary grows automatically as Myra uses the app.

### Lookup order

Every call to `GET /api/translate` walks this chain and stops at the first hit:

1. **In-memory cache** — instant; lives for the lifetime of the server process
2. **Dynamic word store** — words accumulated during previous sessions (loaded from disk or GCS at startup)
3. **Built-in database** (`words_db.py`) — the 600+ shipped words
4. **Google Cloud Translate API** — translates to the native script, then romanizes; result is saved to the dynamic store so the API is never called twice for the same word

### Word format

Custom words are stored in the same shape as built-in words:

```json
{
  "english":     "butterfly",
  "translation": "సీతాకోకచిలుక",
  "romanized":   "sitakokachiluka",
  "emoji":       "✏️",
  "language":    "telugu",
  "category":    "custom"
}
```

Romanization uses Google's `romanize_text` API, with an `indic_transliteration` fallback for complex Telugu, Tamil, and Malayalam consonant clusters that the API returns empty for.

### Local persistence

Translated words are saved to `data/custom_words.runtime.v1.json`. The file is written:

- **Periodically** — after 50 new words accumulate, or every 6 hours (whichever comes first)
- **On shutdown** — always flushed when the server exits cleanly

On the next startup the server reloads this file, so no translations are lost between restarts.

### GCS sync (multi-device sharing)

When running a Raspberry Pi alongside a Cloud Run deployment, both caches can stay in sync via a shared GCS bucket:

| `WORDS_SYNC_TO_GCS` | Behaviour |
| ------------------- | --------- |
| `never` (default)   | Words stay local only |
| `session_end`       | Uploaded to GCS when the robot lesson session ends |
| `shutdown`          | Uploaded to GCS when the server process shuts down |

On startup the server downloads the GCS snapshot and merges it with the local file, so a fresh Cloud Run instance automatically inherits words translated on the Pi, and vice-versa.

**Force an immediate GCS upload** (localhost-only endpoint):

```bash
curl -X POST http://localhost:8000/api/internal/words/sync
```

### Environment variables

| Variable                     | Default                             | Purpose |
| ---------------------------- | ----------------------------------- | ------- |
| `WORDS_STORE_ENABLED`        | `true`                              | Set `false` to disable dynamic words entirely |
| `WORDS_LOCAL_PATH`           | `data/custom_words.runtime.v1.json` | Local snapshot file path |
| `WORDS_OBJECT_BUCKET`        | _(empty)_                           | GCS bucket name for cross-device sync |
| `WORDS_OBJECT_KEY`           | `words/custom_words.v1.json`        | Object path inside the bucket |
| `WORDS_SYNC_TO_GCS`          | `never`                             | Sync policy: `never`, `session_end`, or `shutdown` |
| `WORDS_FLUSH_INTERVAL_SEC`   | `21600` (6 h)                       | Max time between local disk flushes |
| `WORDS_FLUSH_MAX_NEW_WORDS`  | `50`                                | Flush early when this many words accumulate |
| `WORDS_REFRESH_INTERVAL_SEC` | `3600` (1 h)                        | How often to re-read the GCS object |

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
│   ├── main.py                      # FastAPI app & all API routes
│   ├── words_db.py                  # Word database (600+ words across 4 languages)
│   ├── speech_service.py            # faster-whisper STT, dual-pass, MIME detection
│   ├── tts_service.py               # gTTS text-to-speech (async wrapper)
│   ├── translate_service.py         # Google Cloud Translate (on-demand, cached)
│   ├── dynamic_words_store.py       # GCS-backed dynamic word cache
│   ├── robot_teacher.py             # Reachy Mini language-lesson driver (runs on Pi)
│   ├── kids_teacher_types.py        # Shared contract (events, session config, hooks)
│   ├── kids_teacher_profile.py      # File-based profile loader
│   ├── kids_teacher_backend.py      # OpenAI Realtime adapter + provider resolver (only place that imports openai)
│   ├── kids_teacher_gemini_backend.py # Gemini Flash Live adapter (only place that imports google.genai)
│   ├── kids_teacher_realtime.py     # Session handler: transcripts, memory, barge-in, fallback
│   ├── kids_teacher_fakes.py        # FakeRealtimeBackend for tests
│   ├── kids_safety.py               # Topic classifier + response helpers + policy merge
│   ├── kids_safety_keywords.py      # Keyword tables and response copy
│   ├── kids_review_store.py         # Local-first review store (transcript/audio toggles)
│   ├── kids_teacher_flow.py         # Orchestrator; wires safety + review around the handler
│   ├── kids_teacher_routes.py       # FastAPI router for kids-teacher status + review endpoints
│   ├── kids_teacher_robot_bridge.py # Robot audio bridge (playback thread + mic pump)
│   ├── robot_kids_teacher.py        # Kids-teacher CLI entry (headless runtime)
│   ├── memory_file.py               # Sectioned markdown memory store (Current / Notes / History)
│   ├── memory_reconciler.py         # rapidfuzz relevance filter + LLM-assisted note dedup
│   └── text_llm.py                  # Provider-agnostic text completion (ollama default; gemini/openai)
├── profiles/
│   └── kids_teacher/
│       ├── instructions.txt         # Locked preschool persona (required)
│       ├── tools.txt                # Tool allowlist (empty in V1)
│       └── voice.txt                # OpenAI Realtime voice name
├── requirements-common.txt          # Shared runtime/audio deps for app + robot scripts
├── requirements.txt                 # App/server deps, including openai>=1.59.0 for kids-teacher
├── requirements-dev.txt             # Test dependencies (pytest, httpx, anyio)
├── requirements-robot.txt           # Robot-only overlay (Reachy Mini SDK + requests)
├── Dockerfile                       # GCP Cloud Run image (Python 3.11-slim + ffmpeg + pre-cached Whisper model)
├── pytest.ini
├── templates/
│   ├── index.html                   # Language-lesson page (pink dino SVG + lesson UI)
│   ├── config.html                  # Settings page
│   └── kids_teacher.html            # Kids-teacher status dashboard
├── static/
│   ├── css/style.css                # All styles + animations
│   ├── js/app.js                    # Language-lesson JS (recording, TTS, confetti)
│   └── js/kids_teacher.js           # Kids-teacher status page JS
├── tests/
│   ├── conftest.py                  # Stubs faster-whisper / noisereduce at import time
│   ├── test_api.py                  # FastAPI route tests
│   ├── test_words_db.py             # Database integrity tests
│   ├── test_speech_service.py       # STT pipeline tests
│   ├── test_tts_service.py          # TTS service tests
│   ├── test_robot_teacher.py        # Reachy Mini integration tests
│   ├── test_translate_service.py
│   ├── test_dynamic_words_store.py
│   ├── test_security.py             # Security / rate-limit tests
│   ├── test_bridge.py               # Audio bridge integration (requires live server on :8765)
│   ├── test_kids_teacher_profile.py
│   ├── test_kids_teacher_backend.py
│   ├── test_kids_teacher_gemini_backend.py
│   ├── test_kids_teacher_provider_selection.py
│   ├── test_kids_teacher_realtime.py
│   ├── test_kids_teacher_robot_bridge.py
│   ├── test_kids_safety.py
│   ├── test_kids_review_store.py
│   ├── test_kids_teacher_flow.py
│   ├── test_api_kids_teacher.py
│   ├── test_robot_kids_teacher.py
│   ├── test_memory_file.py
│   ├── test_memory_reconciler.py
│   └── test_text_llm.py
├── infra/                       # Terraform — GCP Cloud Run infrastructure
│   ├── providers.tf
│   ├── cloud_run.tf
│   ├── artifact_registry.tf
│   ├── budgets.tf
│   ├── secret_manager.tf
│   ├── variables.tf
│   └── GCP_MIGRATION.md         # AWS → GCP migration notes
└── .github/
    └── workflows/
        └── deploy.yml           # Build + deploy pipeline for Cloud Run
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
| Monthly limit (default $15) | Billing budget alert publishes to Pub/Sub; Cloud Function scales Cloud Run to 0 |

Restart manually when ready.

**Rate limiting** (FastAPI middleware — applied before requests reach the app):

| Endpoint           | Limit per IP |
| ------------------ | ------------ |
| `/api/recognize`   | 10 req/min   |
| `/api/tts`         | 30 req/min   |
| All other `/api/*` | 100 req/min  |

**Audio size limit:** 10 MB hard cap on uploaded audio in FastAPI. A 5-second recording is ~160 KB — 10 MB is 60× headroom for legitimate use.

### Build & Deploy

**Recommended deploy path:** push to `main` and let GitHub Actions build and deploy to Cloud Run:

```bash
git push origin main
```

Workflow: [.github/workflows/deploy.yml](/Users/abhisheksunku/Downloads/claude_projects/myra-language-teacher/.github/workflows/deploy.yml)

**Deploy / update GCP infrastructure with Terraform:**

```bash
cd infra
terraform init
terraform plan  -var="project_id=<YOUR_PROJECT>"
terraform apply -var="project_id=<YOUR_PROJECT>"
```

**One-time GCP bootstrap and manual Artifact Registry setup:**

See [infra/GCP_MIGRATION.md](/Users/abhisheksunku/Downloads/claude_projects/myra-language-teacher/infra/GCP_MIGRATION.md) for the full setup flow, including:
- enabling GCP APIs
- creating the Terraform service account
- creating the GCS Terraform state bucket
- configuring Docker for Artifact Registry

### Docker Notes

- Base image: `python:3.11-slim`
- ffmpeg installed at build time
- faster-whisper `tiny` model **pre-downloaded** during image build — avoids cold-start delay on Cloud Run
- Single Uvicorn worker keeps the Whisper model resident in RAM between requests
- Startup probe allows up to 120 s for the model to load; liveness probe checks every 30 s
