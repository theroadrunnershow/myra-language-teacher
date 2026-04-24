# Plan: Persistent Memory

## Context

The kids-teacher flow on Reachy Mini should remember things about the
child it's talking to across sessions — their name, a sibling's name,
that they love tigers, that their favourite colour is blue, the inside
joke from yesterday. Today everything is in-process; nothing survives a
restart.

The robot doesn't ship knowing whom it's talking to. On a fresh device
the memory file is empty; the kids-teacher flow asks the child for
their name in the first turn and persists it through the same memory
mechanism as everything else (see "Capturing the child's name" below).

## The smallest thing that works

A single markdown file, parent- and child-curated, read into the model's
system prompt at session start.

```
~/.myra/memory.md            # override via MYRA_MEMORY_FILE
```

Example contents:

```markdown
# Things to remember about the child

- Her name is Aanya _(2026-03-10)_
- Her little brother is Rohan _(2026-03-12)_
- She loves tigers and elephants _(2026-03-14)_
- Favourite colour is blue _(2026-04-01)_
- Inside joke: when she says "ba-ba-banana" we say "yes ma'am!" _(2026-04-12)_
```

That's it. No SQLite, no schema, no migrations, no episode log, no
spaced-repetition machinery, no LLM-written recaps, no admin routes.

## Scope

- **Reachy-only, kids-teacher-only.** No web client, no other modes.
- **Single child per device.** No keying. The name is learned, not
  hardcoded — see "Capturing the child's name" below.
- **Memory ≠ mastery tracking.** Mastery (per-word success rate, spaced
  repetition) is a separate feature, deferred. Mixing them was what
  bloated the previous design.

## Capturing the child's name

The child's name is not configured anywhere — it's learned from the
child themself and persisted in `memory.md` like any other fact.

- On a fresh device, `memory.md` is missing or empty. The robot has no
  name to greet with.
- One sentence in `instructions.txt` covers the prompt side:
  > If you don't yet know the child's name, gently ask in the first
  > turn ("What should I call you?"). Once they tell you, use it.
- The name is captured by the same path as any other memory:
  - **v1 (parent-curated):** the parent can pre-seed `memory.md` with
    `- Her name is Aanya` so the robot is ready on first run. No prompt
    nudge needed if the line is already there.
  - **v2 (on-demand tool call):** when the child first introduces
    themself, the Live model invokes the `remember` tool with
    `Her/His/Their name is …`, the bullet is appended to `memory.md`
    mid-session, and the name is used from the next turn onward.
    Subsequent sessions read it from `memory.md` via the v1 path.
- The name lives in the same file as everything else; no separate
  `child_name` field, no extra schema. Keeps the "one markdown file"
  invariant intact.

## Why markdown over a DB

- LLM-native: the file *is* the preamble. No `build_memory_preamble`
  function, no formatting layer.
- Parent-readable: `cat ~/.myra/memory.md` shows everything. Edit with
  vim, delete a line, done. Best privacy UX possible.
- Explicit consent: nothing is remembered without a human asking. This
  is stronger than "log carefully and add a delete button".
- ~40 lines of Python instead of ~400.

## Integration

Existing wiring (already in the codebase):

- `src/kids_teacher_profile.py` reads `instructions.txt` into the
  session payload's `instructions` field.
- `src/kids_teacher_gemini_backend.py:140` passes that string into
  `system_instruction` on the Gemini Live config.

The memory feature is one concatenation:

```python
# in kids_teacher_profile.load_profile (or wherever instructions are assembled)
memory_text = memory_file.read()  # "" if missing
if memory_text:
    instructions = f"{instructions}\n\n{memory_text}"
```

That's the entire v1 hot path.

## Writes

The user's framing: memory is enriched only when the child or parent
*asks* the robot to remember (e.g. "remember I love tigers"), and the
write happens **on demand, in-session** — not at the end of the
session. This mirrors Claude Code's memory flow: saying "remember X"
triggers an immediate append to the memory file via a tool call, and
the new fact is usable on the very next turn. The voice surface is
the only thing that changes; the mechanism is the same.

Two phases:

**v1 — read-only / parent-curated.**
The parent edits `~/.myra/memory.md` directly. The robot reads it on
every session start. Ships immediately, no LLM machinery beyond the
existing Live model. Genuinely useful on its own: a parent can seed
the file with "her name is Aanya, her brother is Rohan, she loves
tigers" and the robot will reference those naturally for weeks.

**v2 — on-demand tool calls from the Live model.**
The Gemini Live session declares two tools — `remember(fact: str)`
and `forget(substring: str)` — and the model invokes them
mid-conversation when the child or parent asks it to. The Live model
itself produces the cleaned, third-person fact text inside the tool
call, so there is no separate summarizer LLM and no second vendor on
the transcript path.

Mechanism:

1. **Declare tools on the Live config.** Extend `_build_live_config`
   in `kids_teacher_gemini_backend.py` (around line 138) to set
   `tools=[…]` on the returned `LiveConnectConfig` with two
   `FunctionDeclaration`s:
   - `remember(fact: str)` — "Persist a fact the child or parent
     asked you to remember. `fact` should be a single, third-person
     sentence about the child (e.g. 'Her favourite colour is blue').
     Skip facts already covered by existing memory."
   - `forget(substring: str)` — "Remove a remembered fact when the
     child or parent says to forget it. `substring` matches a line
     in the memory file."
2. **Instruct the model** in `instructions.txt` to use the tools
   eagerly:
   > If the child or parent asks you to remember something, call the
   > `remember` tool with a clean, third-person sentence about the
   > child, then say "Got it, I'll remember!" If they ask you to
   > forget something, call `forget` with a phrase that identifies
   > the line. If you don't yet know the child's name, gently ask in
   > the first turn ("What should I call you?"); once they tell you,
   > call `remember` with "Her/His/Their name is …" and use the name
   > from then on.
3. **Handle `tool_call` events asynchronously** in the realtime
   layer. Gemini Live already surfaces `tool_call` and
   `tool_call_cancellation` at the top level of the message envelope
   (see `_TOP_LEVEL_LIVE_MESSAGE_FIELDS` at
   `kids_teacher_gemini_backend.py:542`); the reader task currently
   ignores them. The handler must **not block the conversation** on
   disk I/O — the child should never hear a pause while the file is
   written. Concretely:
   - On receiving a `tool_call`, immediately send a synthetic
     `tool_response` of `{"status": "scheduled"}` back to the Live
     session so the model is unblocked and the next conversational
     turn proceeds without delay.
   - Spawn a background `asyncio.Task` that performs the actual
     `memory_file.append(fact)` / `memory_file.remove(substring)` —
     including the `fcntl.flock` acquisition and atomic
     tempfile-rename — entirely off the realtime read path.
   - When the background task completes, emit a normalized
     `memory.updated` event on the existing event queue (with
     `status=ok|already_known|error` and the fact text) for
     observability / UI / tests. The Live model has already moved
     on; this event is for everyone else.
   - The verbal "Got it, I'll remember!" is a soft commitment from
     the model, decoupled from the actual write. If the background
     write fails (disk full, permission), the failure is logged and
     emitted but **not** re-spoken to the child — the parent will
     notice via logs or by inspecting `memory.md`. This is an
     intentional simplification: the conversation stays smooth, and
     write failures on a Pi with a healthy SD card are rare enough
     that surfacing them mid-conversation isn't worth the
     complexity.
4. **No re-load mid-session.** Gemini Live's `system_instruction` is
   fixed for the connection, but the newly remembered fact is already
   in the model's working context (it just produced it), so
   in-session continuity is automatic — and conveniently independent
   of whether the background write has finished yet. Subsequent
   sessions pick up the appended line via the v1 read path.

Why on-demand, not end-of-session:
- The child or parent gets immediate, verifiable persistence — they
  can `cat ~/.myra/memory.md` mid-session and see the line appear
  within a second of the request.
- A follow-up turn in the same session can already reference the new
  fact ("you said you love tigers — what's a tiger's roar sound
  like?"), which a session-end pass cannot do.
- One LLM (the Live model) handles both detection and formatting. No
  separate text-LLM provider, no extra API key, no second vendor to
  ship transcripts to.
- The previous design's end-of-session summarizer + project-wide
  text-LLM abstraction (Ollama / OpenAI / Gemini dispatcher,
  `src/text_llm.py`, `src/memory_summarizer.py`) all drop out. Net
  reduction in moving parts.

Why async (non-blocking) tool dispatch:
- The child must never experience dead air while the file is written.
  Replying "scheduled" to the Live session immediately keeps
  turn-taking smooth.
- File I/O on Reachy's SD card is fast in the common case but not
  guaranteed (flock contention, transient errors). Decoupling the
  conversation from disk means a slow write degrades observability,
  not UX.
- The model's working context already holds the fact; the file write
  is for *future* sessions. There is no in-session correctness reason
  to wait for the write to land before continuing.

Failure modes (all handled off the conversation path):
- Tool call malformed (missing `fact`, empty string) → background
  task logs a warning and emits `memory.updated{status: "error"}`;
  no file change. Model already said "Got it" — accept the small lie
  in exchange for not derailing a 4-year-old's conversation. Parent
  notices via logs.
- Tool call duplicates an existing line → `memory_file.append` does
  a case-insensitive substring check, skips silently, and emits
  `memory.updated{status: "already_known"}`.
- File write error (disk full, permission) → logged + emitted as
  `error`; no re-speak to the child. Parent investigates.
- Live session disconnects between tool call dispatch and the
  background write completing → the background task is anchored to
  the realtime layer, not the Live socket; it finishes the append
  regardless. Worst case the process is killed mid-write, which is
  why the writer uses tempfile + `os.replace` (atomic).
- Model hallucinates a fact the child didn't actually ask for → tight
  tool description + the explicit instruction to only call on
  request; parent can delete the bullet from the file.
- Parent disagrees with a remembered fact → edit `memory.md`
  directly, same as v1.

## Files to add / touch

v1:
- `src/memory_file.py` — `read() -> str`, `append(fact: str) -> None`,
  `remove(substring: str) -> bool`. Atomic write via tempfile+rename.
  ~50 lines.
- `src/kids_teacher_profile.py` — concat memory text into
  `instructions`. ~3 lines.
- `tests/test_memory_file.py` — read/append/remove, missing file,
  concurrent write via lock.
- `tests/test_kids_teacher_profile.py` (extend) — assert memory text
  appears in the assembled instructions.

v2 (only when v1 has shipped and is in use):
- `src/kids_teacher_gemini_backend.py` — declare `remember` / `forget`
  tools on the Live config (`_build_live_config`); on incoming
  `tool_call`, immediately reply with `{"status": "scheduled"}` and
  spawn a background `asyncio.Task` that calls `memory_file.append` /
  `memory_file.remove` and emits `memory.updated`. Existing event
  names already include `tool_call` / `tool_call_cancellation` (see
  line 542) so the realtime layer will surface them; we just have to
  handle them.
- `src/kids_teacher_realtime.py` — propagate `memory.updated` to any
  consumers that want it (logging, future UI).
- One paragraph in `instructions.txt` (the wording above).
- Tests:
  - Tool declarations appear on the assembled `LiveConnectConfig`.
  - Tool-call round-trip with a fake backend: synthetic `tool_call`
    event in → synthetic `tool_response` (`scheduled`) sent back
    *before* the background task is awaited; once awaited,
    `memory_file` is mutated and `memory.updated{status: "ok"}` is
    emitted. The "before" assertion is the non-blocking guarantee.
  - Duplicate-fact handling: second `remember` of the same fact
    emits `already_known` and does not double-write.
  - Slow-write simulation: a `memory_file.append` that sleeps for
    500ms does not delay the `tool_response` send.

## Bounds and edge cases

- **Size cap.** Soft cap at 4 KB. Beyond that, log a warning and ask
  the parent to edit. (A 4-year-old's memory file isn't going to hit
  this for a long time.)
- **Concurrency.** Only one robot process runs at a time on Reachy, but
  use `fcntl.flock` on append for safety.
- **Atomicity on remove.** Read into memory, filter, write to
  `memory.md.tmp`, `os.replace` over the original.
- **Missing file.** Treat as empty. Don't auto-create until the first
  successful write.
- **Reinstall survival.** Lives at `~/.myra/memory.md` — outside any
  package dir, so `pip install` / `git pull` / `apt reinstall` leave
  it alone. SD-card reflash wipes it; mitigate later only if needed.

## Open questions

1. **System-prompt heading.** Should the file's contents be wrapped in
   a `## Things to remember about the child` heading, or just appended
   raw? I'd default to appending raw since the file already has its
   own `# Things to remember about the child` heading at the top.
2. **Date stamps in the file.** Useful for the LLM ("we said this
   recently"); also helps the parent prune. Default: yes, append
   `_(YYYY-MM-DD)_` on each new line.
3. **Mastery tracking.** Out of scope here. If we want SR later, add
   a tiny `mastery` SQLite table (or even another markdown file)
   purely for that — don't conflate it with memory.
4. **`forget` in v2 or defer?** `remember` covers the 90% case. The
   parent can also delete lines by editing `memory.md` directly.
   Shipping `forget` from day one is cheap (one extra
   `FunctionDeclaration`, one extra dispatch arm) and lets the child
   say "forget about the tiger thing" naturally — recommended. Open
   to dropping it from v2 if the tool description bloat affects
   `remember` accuracy.
5. **Surfacing async write failures.** Current plan: log + emit
   `memory.updated{error}`, no verbal correction. Alternative: queue
   a one-shot system message into the next session ("by the way, I
   couldn't save 'X' last time"). Skipped for v2 as overkill; revisit
   if write failures turn out to be common.

## Rollout

1. v1: read-only, parent-curated. ~half a day with tests.
2. Use it for a couple of weeks. See whether the parent-edits-the-file
   loop is actually annoying enough to justify v2.
3. v2 (on-demand tool calls, async dispatch) once v1 is in use and
   the parent-edit loop feels like friction.
