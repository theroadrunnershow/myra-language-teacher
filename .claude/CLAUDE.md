# Myra Language Teacher

A FastAPI app that drives the kids-teacher experience for Myra (age 4): a real-time,
voice-first session backed by Gemini Live, running on a Reachy Mini robot with motion
and face-tracking integrations. The legacy browser-based "say the word, get scored"
flow has been removed; the entry path is now `/kids-teacher`.

## Stack at a glance
- **Backend**: Python / FastAPI, single `uvicorn` worker
- **Realtime LLM**: Gemini Live (audio in / audio out) via the kids-teacher backend
- **TTS** (recovery cues only): gTTS for short English bridge lines
- **Audio**: pydub + ffmpeg + scipy for sample-rate conversion / MP3 decode
- **Robot**: Reachy Mini SDK; `RobotController` in `src/robot_audio.py` owns motion + playback
- **Frontend**: Jinja2 templates + vanilla JS for `/kids-teacher` and the faces admin page
- **Deploy**: Dockerized, GCP Cloud Run (scale-to-zero) via Terraform in `infra/`

## Layout
```
src/            # FastAPI app, kids-teacher flow, robot audio + motion controller
src/motion/     # motion-director (composer + scheduler + L2 gesture tools)
src/tools/      # kids-teacher tools framework (registry + location tools + Gemini adapter)
templates/      # kids_teacher + faces admin pages
static/         # CSS/JS for the kids-teacher UI
tests/          # pytest suite — see pytest.ini (pythonpath=src, asyncio_mode=auto)
infra/          # Terraform for Cloud Run, Artifact Registry, budget kill-switch
deploy/         # bootstrap + build-push scripts
tasks/          # planning docs, security review, UX notes
profiles/       # locked kids-teacher profile (instructions.txt, voice, tools allowlist)
```

## Running locally
```bash
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
PYTHONPATH=src python src/main.py     # http://localhost:8000 → redirects to /kids-teacher
pytest                                 # full test suite
```

ffmpeg is required on the host (used by pydub for MP3 decode of recovery-cue TTS).

## Key architectural notes
- **`/` redirects to `/kids-teacher`.** The only top-level routes mounted on the
  app are `/health`, `/`, the kids-teacher router, and `/static/*`.
- **Tools framework** (`src/tools/`): a `ToolRegistry` mounted on the robot bridge
  alongside the motion stack — exposes `register_current_location` /
  `get_current_location` plus Gemini Live's built-in `google_search` grounding
  tool. Memory and face tools stay inline in the Gemini config (intentionally —
  they have tighter integration than the registry's async dispatch warrants).
  Specs are kept in OpenAI-Realtime shape internally; the Gemini adapter
  (`tools/gemini_adapter.py`) is the only place that boundary lives. Plan:
  `tasks/plan-tools-framework.md`.
- **Recovery cues**: when Gemini drops the session at the 10-min ceiling or the
  safety layer intercepts a refusal, the bridge plays a short English bridge
  line via `tts_service._generate_tts_sync` → `robot_audio._play` (in-process,
  no HTTP).
- **Robot audio**: `src/robot_audio.py` owns `RobotController`, sample-rate
  conversion helpers (`_resample_audio`, `_to_float32_audio`,
  `_extract_first_channel`), and `mp3_bytes_to_robot_samples` /  `_play`.
  Imported lazily by the kids-teacher bridge so the module stays importable on
  hosts without the robot SDK.
- **Cloud Run** runs min=0 / max=2; startup probe budget is short (~30s) since
  there's no model warm-up.

## Testing rules
- Every change ships with tests covering the new behavior.
- **Never modify existing tests without asking first.**
- Run the full suite before declaring work done.
- `conftest.py` stubs `face_recognition` (dlib) at import time so face_service
  imports cleanly on a dev laptop without the robot dep installed.

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
