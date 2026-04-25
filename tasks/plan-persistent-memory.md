# Plan: Persistent Memory

## Context

The kids-teacher flow on Reachy Mini should remember things about the
child it's talking to across sessions — their name, a sibling's name,
that they love tigers, that their favourite colour is blue, the inside
joke from yesterday. Today everything is in-process; nothing survives a
restart.

The robot doesn't ship knowing whom it's talking to. On a fresh device
the memory file is empty; the kids-teacher flow asks the child for
their name in the first turn. In **v1** that name is only used for the
current live session unless the parent pre-seeds `memory.md`. Automatic
persistence of the spoken answer lands in **v2** for Gemini-backed
sessions (see "Capturing the child's name" below).

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
child themself and, when the runtime supports writes, persisted in
`memory.md` like any other fact.

- On a fresh device, `memory.md` is missing or empty. The robot has no
  name to greet with.
- One sentence in `instructions.txt` covers the prompt side:
  > If you don't yet know the child's name, gently ask in the first
  > turn ("What should I call you?"). Once they tell you, use it.
- The name is captured by the same path as any other memory:
  - **v1 (parent-curated):** the parent can pre-seed `memory.md` with
    `- Her name is Aanya` so the robot is ready on first run. No prompt
    nudge needed if the line is already there.
  - **v2 (Gemini-backed sessions only):** when the child first
    introduces themself, the Gemini Live model invokes the internal
    `remember` tool with `Their name is …`, the bullet is appended to
    `memory.md` mid-session, and later sessions read it via the v1 path.
  - **OpenAI-backed sessions:** still ask and use the name for the rest
    of the current session, but remain read-only until an OpenAI tool
    loop exists.
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

**v2 — on-demand tool calls from the Gemini Live model.**
Gemini Live declares one internal tool — `remember(fact: str)` — and
the model invokes it mid-conversation when the child or parent asks it
to. The Live model itself produces the cleaned, third-person fact text
inside the tool call, so there is no separate summarizer LLM and no
second vendor on the transcript path.

The tool is **backend-owned**, not profile-owned:
- It is **not** listed in `profiles/kids_teacher/tools.txt`.
- It is **not** surfaced through the shared `RealtimeBackend` protocol.
- The Gemini backend registers it and handles its responses internally,
  because `session.send_tool_response(...)` is SDK-specific and does not
  belong in the provider-agnostic realtime handler.
- `instructions.txt` carries only the provider-agnostic behavior
  ("ask the child's name if unknown"). The Gemini backend appends the
  tool-specific prompt lines only when it actually registers `remember`.

Mechanism:

1. **Declare the tool on the Gemini Live config.** Extend
   `_build_live_config` in `kids_teacher_gemini_backend.py` (around
   line 138) to set `tools=[…]` on the returned `LiveConnectConfig`
   with one `FunctionDeclaration`:
   - `remember(fact: str)` — "Persist a fact the child or parent
     asked you to remember. `fact` should be a single, third-person
     sentence about the child (e.g. 'Her favourite colour is blue').
     Skip facts already covered by existing memory."

   On Gemini 2.5 Live / native-audio models, declare the function with
   `behavior="NON_BLOCKING"` so the model can keep talking while the
   persistence work finishes. On Gemini 3.1 Flash Live Preview, omit
   that flag because the docs say asynchronous function calling is not
   supported there.

   Forgetting/removing facts is intentionally out of scope: the
   parent edits `~/.myra/memory.md` directly. Voice-driven
   `forget` would add tool-description bloat that risks hurting
   `remember` accuracy, with little payoff for a 4-year-old's flow.
2. **Append Gemini-only prompt guidance** when the tool is registered:
   > If the child or parent explicitly asks you to remember something
   > about the child, call the `remember` tool with a clean,
   > third-person sentence about the child. If the child answers your
   > earlier name question, call `remember` with "Their name is …".
   > After calling `remember`, briefly say "Got it, I'll remember!"
3. **Handle `tool_call` events inside the Gemini backend**. Gemini Live
   surfaces `tool_call` and `tool_call_cancellation` at the top level
   of the message envelope (see `_TOP_LEVEL_LIVE_MESSAGE_FIELDS` at
   `kids_teacher_gemini_backend.py:542`); the reader task currently
   ignores them. The write path must **not block the event loop**.
   Concretely:
   - On receiving a `tool_call`, immediately respond through the real
     SDK method `session.send_tool_response(...)` with one
     `FunctionResponse` per call.
   - For Gemini 2.5 non-blocking calls, send that `FunctionResponse`
     with `scheduling="SILENT"` so the model does not verbalize the
     tool result.
   - Kick the actual markdown append onto a worker thread with
     `asyncio.to_thread(memory_file.append, fact, path)` so `flock`,
     temp-file write, and `os.replace` never block the websocket read
     loop.
   - If the background append raises, just `log.warning(...,
     exc_info=True)`. No event emission, no retry, no verbal
     correction to the child. The verbal "Got it, I'll remember!" is a
     soft commitment from the model, decoupled from the actual write;
     on a Pi with a healthy SD card, write failures are rare enough
     that quietly logging is the right tradeoff. Parent notices by
     inspecting `memory.md` or the log.
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
  Sending the tool response immediately keeps turn-taking smooth.
- `asyncio.create_task(memory_file.append(...))` is **not sufficient**,
  because the synchronous file work would still block the event loop.
  `asyncio.to_thread(...)` is the actual non-blocking primitive here.
- File I/O on Reachy's SD card is fast in the common case but not
  guaranteed (flock contention, transient errors). Decoupling the
  conversation from disk means a slow write degrades observability,
  not UX.
- The model's working context already holds the fact; the file write
  is for *future* sessions. There is no in-session correctness reason
  to wait for the write to land before continuing.

Failure modes (all handled off the conversation path — the rule is
just log and move on):
- Tool call malformed (missing `fact`, empty string) → log a
  warning, no file change. Model already said "Got it"; accept the
  small lie rather than derail a 4-year-old's conversation.
- Tool call duplicates an existing line → `memory_file.append` compares
  normalized bullet bodies exactly (ignoring case, extra spaces, and the
  date suffix) and skips silently. Logged at debug.
- File write error (disk full, permission) → logged as a warning
  with `exc_info`. Parent investigates via the log or by inspecting
  `memory.md`.
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
- `src/kids_teacher_gemini_backend.py` — declare the `remember` tool
  on the Live config (`_build_live_config`); on incoming `tool_call`,
  immediately send `session.send_tool_response(...)`, then schedule
  `memory_file.append` on a worker thread (logging any exception).
  Existing event names already include `tool_call` /
  `tool_call_cancellation` (see line 542); the Gemini backend just has
  to stop ignoring them.
- One provider-agnostic line in `instructions.txt` for asking the
  child's name. Gemini-specific tool guidance is appended at runtime
  only when the tool is registered.
- Tests:
  - The `remember` declaration appears on the assembled
    `LiveConnectConfig`.
  - The default Gemini 2.5 config marks `remember` as
    `behavior="NON_BLOCKING"`; Gemini 3.1 config omits that flag.
  - Tool-call round-trip with a fake Gemini session: synthetic
    `tool_call` event in → real `send_tool_response(...)` recorded
    before the background write finishes; once awaited, `memory_file`
    contains the new line. The "before" assertion is the non-blocking
    guarantee.
  - Slow-write simulation: a `memory_file.append` that blocks in a
    worker thread does not delay the `send_tool_response(...)` call.
  - Failing-write simulation: `memory_file.append` raising
    `OSError` results in a logged warning and no crash.

## Bounds and edge cases

- **Size cap.** Soft cap at 4 KB. Beyond that, log a warning and ask
  the parent to edit. (A 4-year-old's memory file isn't going to hit
  this for a long time.)
- **Concurrency.** Only one robot process runs at a time on Reachy, but
  use `fcntl.flock` on append for safety.
- **Atomicity on remove.** Read into memory, filter, write to
  `memory.md.tmp`, `os.replace` over the original.
- **Matching semantics.** Duplicate detection and removal are **exact**
  on normalized bullet bodies (case-insensitive, whitespace-normalized,
  date suffix ignored). No substring matching. If we later need a
  richer "forget by name" flow, that feature gets its own parser.
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

## Rollout

1. v1: read-only, parent-curated. ~half a day with tests.
2. Use it for a couple of weeks. See whether the parent-edits-the-file
   loop is actually annoying enough to justify v2.
3. v2 (on-demand tool calls, async dispatch) once v1 is in use and
   the parent-edit loop feels like friction.
