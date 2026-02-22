# Myra Language Teacher

A toddler-friendly web app that teaches Telugu and Assamese to Myra (age 4) through a pink animated dino mascot.

## Stack
- **Backend**: Python / FastAPI (`main.py`), port 8000
- **STT**: OpenAI Whisper (offline, lazy-loaded, `base` model ~140MB)
- **TTS**: gTTS (Google TTS, requires internet; Telugu=`te`, Assamese=`as`)
- **Audio conversion**: pydub + ffmpeg (WebM/MP4/OGG → WAV for Whisper)
- **Fuzzy matching**: rapidfuzz `token_sort_ratio`, threshold configurable in settings
- **Frontend**: Vanilla HTML/CSS/JS + Jinja2 templates

## AWS Infrastructure (infra/)
- **Region**: us-west-2
- **Prefix**: dino-app (all resources named dino-app-*)
- ECS Fargate (1 vCPU, 3 GB RAM) behind ALB behind CloudFront + WAF
- Nightly scale-to-zero: 8 PM PST off, 7:30 AM PST on
- Budget kill-switch: $40 warning email, $50 Lambda scales ECS to 0
- State backend: S3 bucket `dino-app-tfstate` with native locking

## Running Locally
```bash
source venv/bin/activate
pip install -r requirements.txt   # first time only
python main.py                    # http://localhost:8000
```

## Config Defaults
```json
{
  "languages": ["telugu", "assamese"],
  "categories": ["animals", "colors", "body_parts", "numbers", "food", "common_objects"],
  "child_name": "Myra",
  "show_romanized": true,
  "similarity_threshold": 50,
  "max_attempts": 3
}
```
Config is client-side (sessionStorage) — no server-side persistence needed.

## Notes
- Whisper downloads `~/.cache/whisper/base.pt` (~140MB) on first STT call
- ffmpeg must be installed: `brew install ffmpeg`
- Speech matching runs against both native script AND romanized pronunciation — higher score wins
- `static/sounds/` exists but is currently unused

---

## Workflow Orchestration

### Plan Node Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately – don't keep pushing
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
- Skip this for simple, obvious fixes – don't over-engineer
- Challenge your own work before presenting it

### Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests – then resolve them
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
