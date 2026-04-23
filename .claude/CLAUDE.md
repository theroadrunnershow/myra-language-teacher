# Myra Language Teacher

A toddler-friendly web app (FastAPI + vanilla JS) that teaches Telugu and Assamese
to Myra (age 4) through an animated mascot. Kids hear a word, attempt to say it, and
get scored via on-device speech recognition with fuzzy matching.

## Stack at a glance
- **Backend**: Python / FastAPI, single `uvicorn` worker
- **STT**: `faster-whisper` (tiny, int8 CPU) — dual-pass for native + romanized scoring
- **TTS**: gTTS (Google) for Telugu, Assamese, and English voice lines
- **Translation**: cache → in-memory `words_db` → Google Cloud Translate fallback
- **Audio**: pydub + ffmpeg (browser WebM/OGG/MP4 → 16kHz mono WAV)
- **Matching**: rapidfuzz `token_sort_ratio` against native script + romanized targets
- **Frontend**: Jinja2 templates + vanilla JS state machine; config in sessionStorage
- **Deploy**: Dockerized, GCP Cloud Run (scale-to-zero) via Terraform in `infra/`

## Layout
```
src/            # FastAPI app + STT/TTS/translate services, words DB, robot controller
templates/      # index (learning page) + config (settings)
static/         # CSS/JS, mascot SVGs
tests/          # pytest suite — see pytest.ini (pythonpath=src, asyncio_mode=auto)
infra/          # Terraform for Cloud Run, Artifact Registry, budget kill-switch
deploy/         # bootstrap + build-push scripts
tasks/          # planning docs, security review, UX notes
```

## Running locally
```bash
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
PYTHONPATH=src python src/main.py     # http://localhost:8000
pytest                                 # full test suite
```

ffmpeg is required on the host. First STT call loads Whisper (~30s); subsequent calls are fast.

## Key architectural notes
- **Config is client-side.** `GET /api/config` returns `DEFAULT_CONFIG` from `main.py`;
  the browser merges it with `sessionStorage`. Nothing is persisted server-side.
- **Word DB is in-memory and immutable** (`words_db.py`, ~600 words / 8 categories /
  2 languages). Tests use it directly — do not mock it.
- **Dual-pass Whisper**: one pass in the target language (native script), one in
  English (romanization). The higher similarity score wins.
- **Dynamic words** (outside the static DB) are cached in GCS via `dynamic_words_store.py`.
- **Cloud Run** runs min=0 / max=2; startup probe allows 120s for Whisper to warm.
- For deeper details, read the source — this file is intentionally a map, not a mirror.

## Testing rules
- Every change ships with tests covering the new behavior.
- **Never modify existing tests without asking first.**
- Run the full suite before declaring work done.
- `main.generate_tts` and `main.recognize_speech` are `AsyncMock`ed in API tests;
  `conftest.py` stubs `faster_whisper` / `noisereduce` at import time.

---

# Behavioral guidelines

Adapted from Karpathy-style coding guidelines. These bias toward caution over speed;
use judgment for trivial tasks.

## 1. Think before coding
**Don't assume. Don't hide confusion. Surface tradeoffs.**
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity first
**Minimum code that solves the problem. Nothing speculative.**
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you wrote 200 lines and it could be 50, rewrite it.

Ask: *would a senior engineer call this overcomplicated?* If yes, simplify.

## 3. Surgical changes
**Touch only what you must. Clean up only your own mess.**
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- Remove imports/variables/functions that *your* changes orphaned — leave pre-existing
  dead code alone (mention it if noticed).

The test: every changed line should trace directly to the user's request.

## 4. Goal-driven execution
**Define success criteria. Loop until verified.**
- "Add validation" → "Write tests for invalid inputs, then make them pass."
- "Fix the bug" → "Write a failing test that reproduces it, then make it pass."
- "Refactor X" → "Ensure tests pass before and after."

For multi-step tasks, state a brief plan with a verification step for each item.
Strong success criteria let you work independently; weak criteria ("make it work")
force constant clarification.

## 5. Plan node default
- Enter plan mode for any non-trivial task (3+ steps or architectural decisions).
- If something goes sideways, stop and re-plan — don't keep pushing.
- Every planning session worth revisiting gets a project-level `tasks/plan-<topic>.md`
  so context persists across sessions.

## 6. Subagents
- Offload research, exploration, and parallel analysis to subagents liberally.
- One focused task per subagent. Use them to protect the main context.

## 7. Verify before "done"
- Never mark work complete without proving it works (tests, logs, manual check).
- Diff behavior vs. `main` when the change is risky.
- Ask yourself: *would a staff engineer approve this?*

## 8. Self-improvement loop
- After any correction from the user, update `tasks/lessons.md` with the pattern and
  a rule that prevents repeating it.
- Review `tasks/lessons.md` at the start of a session.

---

**These guidelines are working if:** diffs contain fewer unrelated changes, rewrites
for overcomplication drop, and clarifying questions arrive *before* implementation
rather than after mistakes.
