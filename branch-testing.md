# Branch Testing Guide — Myra Language Teacher

How to switch between git branches and test the app locally.

---

## 1. See All Branches

```bash
# All local branches
git branch

# All branches (local + remote)
git branch -a
```

**Current branches:**

| Branch | Location | Purpose |
|--------|----------|---------|
| `main` | local + remote | Stable production code |
| `backup` | local | Safety snapshot |
| `claude/add-unit-tests-1MqNb` | local + remote | Comprehensive test suite (159 tests) |
| `feature/llm-speech-to-text-matching` | local + remote | LLM-based speech recognition matching |
| `claude/dino-interactions-feedback-rpsqC` | remote only | Dino interaction & feedback UX |
| `claude/reachy-mini-integration-Q1sbf` | remote only | Reachy robot integration |

---

## 2. Switch to a Local Branch

```bash
git checkout <branch-name>

# Examples:
git checkout main
git checkout feature/llm-speech-to-text-matching
git checkout claude/add-unit-tests-1MqNb
```

---

## 3. Fetch and Check Out a Remote-Only Branch

For branches that only exist on `origin` (e.g. `claude/dino-interactions-feedback-rpsqC`):

```bash
# Step 1: Fetch latest refs from remote
git fetch origin

# Step 2: Check out the remote branch locally
git checkout -b claude/dino-interactions-feedback-rpsqC origin/claude/dino-interactions-feedback-rpsqC

# Or use the shorthand (git ≥ 2.23):
git switch --track origin/claude/dino-interactions-feedback-rpsqC
```

---

## 4. Run the App on a Branch

After switching branches, run the app the same way every time:

```bash
# Activate the shared virtual environment
source venv/bin/activate

# Re-install dependencies (only needed if requirements.txt changed between branches)
pip install -r requirements.txt

# Start the server
python main.py
# → http://localhost:8000
```

> **First run tip:** Whisper downloads `~/.cache/whisper/base.pt` (~140 MB) on the first speech
> recognition call. This cache is shared across all branches — no re-download needed.

---

## 5. Run the Test Suite on a Branch

```bash
source venv/bin/activate
pip install -r requirements-dev.txt   # pytest, httpx, anyio — once per venv

# Run all tests
pytest -v

# Run a specific test file
pytest tests/test_api.py -v
pytest tests/test_speech_service.py -v

# Run a specific test by name
pytest -v -k "test_word_api"
```

The test suite (159 tests) uses mocks — no real audio, network, or Whisper calls are made.

---

## 6. Compare Branches

```bash
# Files changed between main and another branch
git diff main..feature/llm-speech-to-text-matching --name-only

# Full code diff vs main
git diff main..feature/llm-speech-to-text-matching

# Commits in a branch that aren't in main
git log main..feature/llm-speech-to-text-matching --oneline

# Summary of what changed (stats only)
git diff main..feature/llm-speech-to-text-matching --stat
```

---

## 7. Return to Main

```bash
git checkout main
```

---

## 8. Keep Branches Up to Date

```bash
# Pull latest changes for current branch
git pull origin main

# Update a feature branch with latest main (rebase keeps history clean)
git checkout feature/llm-speech-to-text-matching
git rebase main
```

---

## Project-Specific Notes

- **`venv/`** — one shared virtualenv for all branches. Re-run `pip install -r requirements.txt`
  after switching if `requirements.txt` differs between branches.
- **`config.json`** — gitignored; persists unchanged when you switch branches.
- **`~/.cache/whisper/base.pt`** — Whisper model lives outside the repo; shared across branches.
- **`infra/`** — Terraform files differ between branches (GCP migration in progress on `main`).
  Be careful not to run `terraform apply` from a branch that has stale infra files.
- **`.env`** — gitignored; your secrets are safe when switching branches.
