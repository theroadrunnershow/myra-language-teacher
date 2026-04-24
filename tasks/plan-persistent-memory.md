# Plan: Persistent Memory ("Robot That Remembers Myra")

## Context

The robot should accumulate memory of the child across sessions, devices, and
reinstalls so it can:

- Greet her with continuity ("last time we worked on Telugu animals")
- Run spaced repetition on words she's actually struggling with
- Reference family members, favorites, and shared moments to feel personal
- Resume narratives and inside jokes across days/weeks

Today every state lives in-process or in `sessionStorage`; nothing survives a
restart, let alone a reinstall.

## Design constraints

- **Long-lived & device-independent.** Memory must outlive `apt purge`, SD-card
  reflashes, browser-cache clears, and moves between robot and web client.
- **Child-keyed, not device-keyed.** The primary key is the child, not the
  hardware. Multiple devices for the same child must converge.
- **Tight LLM context budget.** Gemini system instructions can't hold a year of
  history; memory has to be compressible to a ≤500-token preamble.
- **Privacy first.** This is a 4-year-old's data. Admin-only access, explicit
  delete path, no PII beyond first name + stated family relations.
- **Offline-tolerant.** A robot with no internet should still run a session;
  memory writes can buffer and flush later.

## Identity

Primary key: `child_id` — a stable string set via the admin config flow
(parent picks/types it once; "myra" is fine). Re-entered post-reinstall in
<30s, or auto-recovered from the planned face-recognition encodings.

Secondary key: `device_id` — robot serial # on Reachy, localStorage UUID on
web. Used for telemetry and last-seen, never as the lookup key.

## Memory taxonomy

Four categories, separated because their access patterns differ:

| Kind        | Examples                                  | Read freq | Write freq | Store     |
|-------------|-------------------------------------------|-----------|------------|-----------|
| Semantic    | name, age, family, favorite animal        | each session start | rare | Firestore doc |
| Procedural  | per-word mastery (attempts, ease, last_seen) | every turn | every turn | Firestore subcoll |
| Episodic    | session timestamp, words covered, mood    | rare (recap) | end of session | GCS JSONL |
| Affective   | excites/frustrates/jokes                  | each session start | weekly summarizer | Firestore doc |

## Storage layout

```
Firestore:
  memory/{child_id}                          # semantic + affective doc
    schema_version: 1
    child:        { name, age, languages, family: [...] }
    affect:       { excites: [...], frustrates: [...], jokes: [...] }
    last_session: { device_id, ended_at, summary_text }

  memory/{child_id}/mastery/{english_word}   # procedural — one doc per word
    attempts: int
    successes: int
    ease: float
    last_seen: timestamp
    languages_seen: [...]

GCS:
  gs://{bucket}/memory/{child_id}/episodes/{YYYY-MM}.jsonl
                                              # append one line per session
```

Local cache: SQLite at `~/.myra/memory.db` on robot, IndexedDB on web. Read
on boot, write-through on update, periodic reconcile against the cloud.

## Retrieval (hot path)

Session start:

1. Read `memory/{child_id}` (1 doc, <2 KB).
2. Read top-N due-for-review mastery docs (`last_seen + ease_interval < now`).
3. Compose a preamble string (~300 tokens) and inject into Gemini system
   instructions. Example:
   > You're talking with Myra (4). She loves tigers; her little brother is
   > Ahaan. Last session she nailed Telugu colors but stumbled on "water"
   > (పానీయం). Today try animals + revisit "water" once.
4. Don't pull episodes into context; the semantic+affective doc is the
   compressed view of them.

## Writes (cold path)

End of session:

1. Aggregate the in-memory turn log into:
   - Episode JSONL line → append to month blob in GCS
   - Mastery patches → batched Firestore writes (one per word touched)
   - Affective deltas → optional, only when something notable surfaces
2. Re-summarize `last_session.summary_text` (~2 sentences) so next session's
   preamble has fresh context.
3. Weekly cron / app-startup task: re-summarize affective signals from the
   last 30 days of episodes; trim episodes older than 1 year.

Sync policy mirrors `dynamic_words_store`: `never` / `session_end` /
`shutdown`.

## LLM integration

- New helper `build_memory_preamble(child_id) -> str` consumed by both
  `kids_teacher_backend` and `kids_teacher_gemini_backend`.
- Bounded length (hard cap ~500 tokens; truncate oldest first).
- Falls back to an empty string when memory is missing or fetch fails — never
  blocks session start.

## Privacy & admin

- Admin-only routes: `GET /admin/memory/{child_id}` (view), `DELETE` (cascade
  delete Firestore + GCS), `POST /admin/memory/{child_id}/redact` (drop
  affective + episodic, keep mastery).
- Memory writes gated by the existing kids-safety review filter; anything
  flagged is dropped before reaching cloud.
- No raw audio in memory — that's `kids_review_store`'s job, kept separate.
- Schema versioned so future migrations don't strand old data.

## Open questions to resolve before coding

1. Firestore vs. just-GCS for the hot path. Firestore is new dependency; one
   alternative is a single JSON blob in GCS keyed by child_id, read-modify-
   write at session boundaries. Simpler, fine until mastery grows past ~1 K
   words.
2. Web client identity — does the parent type `child_id` once and we trust
   the browser, or do we require Google sign-in? Family-only deployment can
   probably get away with the typed-once model.
3. Local cache: do we ship offline-first from day one, or cloud-only v1 and
   add caching when a real offline scenario shows up?
4. Multi-child households — out of scope for v1 (Myra-only) but the schema
   above supports it without changes.

## Rollout sketch

1. v1: Single GCS blob per child, semantic + last-3-sessions summary only.
   No mastery yet. Memory preamble works. ~2 days.
2. v2: Add mastery (Firestore subcollection or expand the blob), drive
   spaced-repetition word selection. ~3 days.
3. v3: Episodic JSONL + weekly summarizer. ~2 days.
4. v4: Local cache + offline tolerance. Only if a real need emerges.

Each step shipping with tests before moving on.
