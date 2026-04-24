# Plan: Persistent Memory ("Robot That Remembers Myra")

## Context

The kids-teacher flow on Reachy Mini should accumulate memory across sessions
so the robot can:

- Greet with continuity ("last time we worked on Telugu animals")
- Run spaced repetition on words she's actually struggling with
- Reference family members, favorites, and shared moments to feel personal
- Resume narratives and inside jokes across days/weeks

Today every state lives in-process; nothing survives a restart.

## Scope

- **Reachy-only.** Web client is out of scope. The kids-teacher flow only runs
  on the Pi-hosted robot.
- **Single child per device.** One Reachy = one child. No multi-tenancy, no
  `child_id` keying. If we ever need it, the schema below trivially extends.
- **Kids-teacher flow only.** No reuse from the legacy lesson page or the
  word-DB editor.

## Storage: local SQLite on the Pi

Path: `~/.myra/memory.db` (override via `MYRA_MEMORY_DB_PATH`).

Why SQLite over a JSON file:
- Atomic writes — matters for crashes mid-session
- Queries for spaced repetition (`WHERE next_due <= now ORDER BY next_due`)
- Cheap schema migrations (`schema_version` table + idempotent `ALTER`s)
- Single file, no daemon, `sqlite3` is in the stdlib

What "persistent" actually buys us:
- Reboots: ✓ (filesystem)
- App reinstall / `pip install` / `git pull`: ✓ (data lives outside the
  package)
- `apt reinstall` of the Myra package: ✓ (DB is in `~/.myra/`, not in any
  packaged dir)
- SD-card reflash: ✗ — accept this, mitigate with an optional backup script
  (`scripts/backup_memory.py` → copy to USB or email). Not required for v1.

No cloud. No Firestore. No GCS. Memory never leaves the device, which is
strictly better for a 4-year-old's data.

## Schema

```sql
CREATE TABLE schema_version (version INTEGER NOT NULL);

-- singleton row
CREATE TABLE child (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL,
    age INTEGER,
    languages TEXT,                 -- JSON array
    family TEXT,                    -- JSON: [{relation, name}, ...]
    updated_at TEXT NOT NULL
);

-- singleton row, refreshed by the weekly summarizer
CREATE TABLE affect (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    excites TEXT,                   -- JSON array
    frustrates TEXT,                -- JSON array
    jokes TEXT,                     -- JSON array
    updated_at TEXT NOT NULL
);

CREATE TABLE mastery (
    english TEXT NOT NULL,
    language TEXT NOT NULL,         -- 'telugu' | 'assamese' | ...
    attempts INTEGER NOT NULL DEFAULT 0,
    successes INTEGER NOT NULL DEFAULT 0,
    ease REAL NOT NULL DEFAULT 2.5, -- SM-2 style
    last_seen TEXT,
    next_due TEXT,
    PRIMARY KEY (english, language)
);
CREATE INDEX idx_mastery_due ON mastery(next_due);

CREATE TABLE episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    words_covered TEXT,             -- JSON array of english words
    mood TEXT,                      -- 'engaged'|'frustrated'|'tired'|...
    summary TEXT                    -- one-line LLM-written recap
);
CREATE INDEX idx_episodes_ended_at ON episodes(ended_at);
```

`schema_version` starts at 1. Migrations: `memory_store.migrate()` runs on
open and is idempotent.

## Retrieval (hot path)

Session start, in `kids_teacher_flow` (or backend init):

1. `child = memory_store.get_child()` — 1 row.
2. `affect = memory_store.get_affect()` — 1 row.
3. `due = memory_store.due_for_review(limit=5)` — words past `next_due`.
4. `recap = memory_store.last_session_summary()` — newest `episodes.summary`.
5. `build_memory_preamble(child, affect, due, recap) -> str` — hard cap
   ~500 tokens, truncate `due` first if needed. Inject into the system
   instructions for both `kids_teacher_backend` and
   `kids_teacher_gemini_backend`.

Failure mode: any DB error → return empty preamble, log a warning, never
block session start.

## Writes (cold path)

End of session, in `kids_teacher_flow`:

1. For each word touched: `memory_store.record_attempt(word, language,
   correct: bool)` — updates `attempts/successes`, recomputes `ease` and
   `next_due` (SM-2-ish, kept simple: 1d / 3d / 7d / 14d ladder gated on
   success).
2. `memory_store.append_episode(started_at, ended_at, words_covered, mood,
   summary)` — `summary` is a 1–2 sentence string the LLM produces at
   session-end (cheap; we already have a turn log).
3. Affect updates are *not* per-session. A weekly task (or app-startup
   check) re-summarizes the last N episodes into the affect row. Out of
   scope for v1 — start with manual updates via admin CLI.

All writes are inside a single `with conn:` transaction so a crash leaves
the DB consistent.

## Files to add / touch

- `src/memory_store.py` — new. Public API:
  - `open(path: str | None = None) -> Connection`
  - `migrate(conn)`
  - `get_child(conn) -> dict | None`
  - `set_child(conn, **fields)`
  - `get_affect(conn) -> dict`
  - `due_for_review(conn, limit=5) -> list[dict]`
  - `record_attempt(conn, english, language, correct)`
  - `append_episode(conn, **fields)`
  - `last_session_summary(conn) -> str | None`
- `src/memory_preamble.py` — pure function `build_memory_preamble(...)`
  → `str`. Keeps prompt-shaping out of the store.
- `src/kids_teacher_backend.py` + `kids_teacher_gemini_backend.py` —
  inject preamble into system instructions. One call site each.
- `src/kids_teacher_flow.py` — record attempts + append episode on
  session end. Hooked into the existing end-of-session path.
- `tests/test_memory_store.py` — DB lifecycle, migrations, SR scheduling,
  due-for-review correctness, transaction rollback on error.
- `tests/test_memory_preamble.py` — preamble shape, token cap, graceful
  empties.
- `tests/test_kids_teacher_flow.py` (extend) — assert attempts + episodes
  written on session end.
- `scripts/backup_memory.py` — *optional, deferred.* Copy DB to a path
  (USB / scp). Only if SD-reflash recovery becomes a real need.

## Open questions

1. **DB path.** `~/.myra/memory.db` follows XDG-ish convention; alternative
   is `/var/lib/myra/memory.db` if we want it survivable across user
   account changes. Recommend `~/.myra/` for now (simpler perms).
2. **Mood detection.** `episodes.mood` is easy if the LLM tags it at
   session-end; punt to a follow-up if that's not free.
3. **Spaced-repetition aggressiveness.** Start with a fixed 1d / 3d / 7d /
   14d ladder; tune later if Myra's retention curve says otherwise.
4. **Backup story.** Defer until reflash actually loses something the
   parent cares about.

## Rollout

1. v1: `memory_store.py` + `build_memory_preamble` + write hook on session
   end. No mastery-driven word selection yet — just remember and recall.
   ~1.5 days incl. tests.
2. v2: Spaced repetition drives word selection in `kids_teacher_flow`.
   ~1 day.
3. v3: Weekly affect summarizer (LLM-driven, cron or app-startup). ~0.5 day.
4. v4 (optional): backup script.

Each step ships with tests before the next starts.
