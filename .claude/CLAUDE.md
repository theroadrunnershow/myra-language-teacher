# Myra Language Teacher

A toddler-friendly web app that teaches Telugu and Assamese to Myra (age 4) through a pink animated dino mascot.

## Stack
- **Backend**: Python / FastAPI (`main.py`), port 8000
- **STT**: OpenAI Whisper (offline, lazy-loaded, `base` model ~140 MB)
- **TTS**: gTTS (Google TTS, requires internet; Telugu=`te`, Assamese=`as`)
- **Audio conversion**: pydub + ffmpeg (WebM/MP4/OGG → WAV for Whisper)
- **Fuzzy matching**: rapidfuzz `token_sort_ratio`, threshold configurable in settings
- **Frontend**: Vanilla HTML/CSS/JS + Jinja2 templates

## GCP Infrastructure (infra/)
- **Cloud**: GCP (migrated from AWS — see `infra/GCP_MIGRATION.md`)
- **Service**: Cloud Run (`dino-app`), min=0 / max=2 instances
- **Resources**: 1 vCPU, 3 GB RAM per instance
- **Registry**: Artifact Registry (`{region}-docker.pkg.dev/{project_id}/myra`)
- **WAF**: Cloud Armor
- **State backend**: GCS bucket `myra-tfstate/myra/terraform.tfstate`
- **Budget kill-switch**: Cloud Run scales to 0 when monthly spend exceeds limit (`budgets.tf`)
- **Scale-to-zero**: Native Cloud Run feature (`min_instance_count=0`)

## Codebase Structure
```
myra-language-teacher/
├── main.py               # FastAPI app — all routes, request handling
├── speech_service.py     # Whisper STT + audio conversion pipeline
├── tts_service.py        # gTTS wrapper (async)
├── words_db.py           # In-memory word database (60+ words, 6 categories)
├── config.json           # Default server config (Assamese-focused)
├── requirements.txt      # Runtime dependencies
├── requirements-dev.txt  # Test dependencies (pytest, httpx, anyio)
├── Dockerfile            # GCP Cloud Run image (Python 3.11-slim + ffmpeg)
├── pytest.ini            # Pytest config (asyncio_mode=auto)
│
├── templates/
│   ├── index.html        # Main learning page (Jinja2)
│   └── config.html       # Settings page (Jinja2)
│
├── static/
│   ├── css/style.css     # Toddler-friendly bubbly pink theme
│   └── js/app.js         # Vanilla JS state machine + animations
│
├── tests/
│   ├── conftest.py       # Stubs whisper/noisereduce; Whisper cache fixture
│   ├── test_api.py       # FastAPI route tests (40+ tests)
│   ├── test_words_db.py  # Database integrity tests (25+ tests)
│   ├── test_speech_service.py  # STT pipeline tests (60+ tests)
│   └── test_tts_service.py    # TTS service tests
│
├── infra/
│   ├── providers.tf      # GCS backend, Google provider ~5.0
│   ├── cloud_run.tf      # Cloud Run service (scaling, probes, timeout)
│   ├── artifact_registry.tf  # Docker image registry
│   ├── cloud_armor.tf    # WAF rules
│   ├── variables.tf      # project_id, region, budget_limit, etc.
│   ├── budgets.tf        # Monthly budget + kill-switch trigger
│   ├── secret_manager.tf # GCP Secret Manager integration
│   ├── load_balancer.tf  # Cloud Load Balancing
│   ├── outputs.tf        # Service URL outputs
│   ├── GCP_MIGRATION.md  # AWS → GCP migration notes
│   └── lambda/
│       ├── kill_ecs.py   # Legacy: AWS ECS scale-to-zero
│       └── kill_run.py   # GCP: Cloud Run scale-to-zero
│
├── deploy/
│   ├── bootstrap.sh      # Initial GCP project setup
│   └── build-push.sh     # Docker build + push to Artifact Registry
│
├── .claude/
│   ├── CLAUDE.md         # This file
│   └── settings.json     # Claude Code preferences
│
└── tasks/
    ├── todo.md           # Active task tracking
    └── lessons.md        # Self-improvement notes after corrections
```

## Running Locally
```bash
source venv/bin/activate
pip install -r requirements.txt       # first time only
pip install -r requirements-dev.txt   # for tests
python main.py                        # http://localhost:8000
```

## Running Tests
```bash
source venv/bin/activate
pytest                    # all tests
pytest tests/test_api.py  # specific file
pytest -v                 # verbose output
```

**Mocking strategy:**
- `main.generate_tts` and `main.recognize_speech` are `AsyncMock`ed — no network/Whisper in API tests
- `words_db` is **not** mocked — tests use real in-memory data
- `conftest.py` stubs `whisper` and `noisereduce` at import time for `test_speech_service.py`

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/` | Main learning page |
| GET | `/settings` | Settings configuration page |
| GET | `/api/config` | Returns merged default config (JSON) |
| POST | `/api/config` | Validates and merges config; no server-side persistence |
| GET | `/api/word` | Random word; supports `?languages=&categories=` query params |
| GET | `/api/tts` | Text-to-speech stream (audio/mpeg); params: `text`, `language`, `slow` |
| POST | `/api/recognize` | Speech recognition; multipart form with audio file + metadata |
| GET | `/api/words/all` | All words for given languages/categories |

### `/api/recognize` Request Shape
```
Form fields: language, expected_word, romanized, audio_format, similarity_threshold
File field:  audio (WebM/MP4/OGG/WAV bytes)
```

### `/api/recognize` Response Shape
```json
{
  "transcribed": "pilli",
  "expected": "పిల్లి",
  "similarity": 92.0,
  "script_similarity": 85.0,
  "roman_similarity": 92.0,
  "is_correct": true,
  "language": "telugu",
  "error": null
}
```

## Config Defaults (`config.json`)
```json
{
  "languages": ["assamese"],
  "categories": ["animals", "colors", "body_parts", "numbers", "food", "common_objects"],
  "child_name": "Myra",
  "show_romanized": true,
  "similarity_threshold": 85,
  "max_attempts": 3
}
```

Config is **client-side (sessionStorage)** — no server-side persistence needed. The server's `config.json` provides defaults that are merged with sessionStorage on page load.

## Word Database (`words_db.py`)

**Structure:** Python dict, in-memory, immutable (no DB)
**Coverage:** 60+ words across 6 categories, 2 languages

Each word entry:
```json
{
  "english": "cat",
  "telugu": "పిల్లి",
  "assamese": "মেকুৰী",
  "emoji": "🐱",
  "tel_roman": "pilli",
  "asm_roman": "mekuri"
}
```

**Key functions:**
- `get_random_word(category, language)` → single word dict
- `get_all_words_for_language(language, categories)` → filtered list
- `ALL_CATEGORIES` → `["animals", "colors", "body_parts", "numbers", "food", "common_objects"]`

Romanized fields (`tel_roman`, `asm_roman`) are ASCII phonetic guides used as fallback targets in fuzzy matching.

## Speech Recognition Architecture (`speech_service.py`)

### Dual-Pass Recognition
Whisper is run **twice per recording** to maximize match accuracy:
1. **Pass 1** — force target language (e.g., `te`) → native script output (Telugu glyphs)
2. **Pass 2** — force English → romanized/phonetic output

The **higher** `token_sort_ratio` score between the two passes is used. This handles Whisper's tendency to output romanization even in native-language mode.

### Audio Pipeline
```
Browser WebM/OGG/MP4 audio
  → MIME detection → extension mapping
  → pydub AudioSegment (ffmpeg codec)
  → resample to 16 kHz mono WAV
  → [optional noise reduction: spectral subtraction + high-pass filter]
  → Whisper inference (dual-pass)
  → temp file cleanup
```

### Feature Flags (in `speech_service.py`)
| Flag | Default | Purpose |
|------|---------|---------|
| `NOISE_REDUCTION_ENABLED` | `False` | Spectral subtraction + high-pass filter |
| `INITIAL_PROMPT_ENABLED` | `True` | Bias Whisper with expected word |

### Similarity Scoring
- **Algorithm:** `rapidfuzz.token_sort_ratio` (0–100)
- **Normalization:** Unicode NFC, lowercase, strip punctuation
- **Threshold:** Configurable per session (default 85)

## Frontend Architecture (`static/js/app.js`)

### State Object
```javascript
{
  currentWord: null,
  config: {},
  score: 0,
  wordsAttempted: 0,
  attempts: 0,
  maxAttempts: 3,
  isRecording: false,
  mediaRecorder: null,
  audioChunks: [],
  pendingTimeoutIds: []   // tracks cancellable timers (Stop button)
}
```

### Learning Loop Flow
1. `loadNextWord()` → `GET /api/word` → display word, auto-play TTS
2. User presses **Say It!** → `playPromptThenRecord()` → TTS prompt → `startRecording()`
3. 5-second recording auto-stops → `processAudio()` → `POST /api/recognize`
4. `handleResult()`:
   - Correct → confetti + dino celebrates → `nextWord()` after 2.2s
   - Wrong (attempts left) → dino shakes → retry after 1.5s
   - Out of attempts → reveal answer → next word after 3s
5. **Stop button** clears all `pendingTimeoutIds` to cancel in-flight transitions

### Dino Animations
| State | CSS Class | Description |
|-------|-----------|-------------|
| idle | `dino-idle` | Subtle bounce (3s loop) |
| celebrate | `dino-celebrate` | Jump + scale (2×) |
| shake | `dino-shake` | Left-right wobble |
| talk | `dino-talk` | Vertical squash (loop) |
| ask | `dino-ask` | Mirrored + bounce (loop) |

Mouth is animated via SVG path manipulation in `animateMouth(open)` with 130ms per frame.

### Config Management
- `fetchConfig()` merges `GET /api/config` (server defaults) with `myra_config` sessionStorage key
- Settings page writes directly to sessionStorage on form submit

## Docker & Deployment

### Dockerfile Key Notes
- Base: `python:3.11-slim`
- ffmpeg installed at build time
- Whisper `base` model **pre-downloaded** during image build (avoids cold-start delay)
- Single `uvicorn` worker (keeps Whisper model in RAM)
- Port 8000

### Build & Push
```bash
./deploy/build-push.sh   # builds and pushes to GCP Artifact Registry
```

### GCP Terraform Deploy
```bash
cd infra
terraform init
terraform plan -var="project_id=<YOUR_PROJECT>"
terraform apply -var="project_id=<YOUR_PROJECT>"
```

### Cloud Run Notes
- `min_instance_count=0` → scales to zero when idle (cost saving)
- Startup probe timeout: 120s (allows Whisper model to load from image cache)
- Liveness probe: 30s check, 3 failures → restart
- Request timeout: 300s (5 min max per request)
- Public access: `allUsers` (no auth)

## Notes
- ffmpeg required: `apt-get install ffmpeg` (Linux) or `brew install ffmpeg` (macOS)
- First STT call loads Whisper model (~30s); all subsequent calls are fast
- `static/sounds/` exists but is currently unused
- `config.json` is in `.gitignore` — do not commit local config overrides

---

## Workflow Orchestration

### Plan Node Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately — don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### Self-Improvement Loop
- After ANY correction from the user: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes — don't over-engineer
- Challenge your own work before presenting it

### Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests — then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management
1. **Plan First**: Write plan to `tasks/todo.md` with checkable items
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review section to `tasks/todo.md`
6. **Capture Lessons**: Update `tasks/lessons.md` after corrections

## Core Principles
- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.
