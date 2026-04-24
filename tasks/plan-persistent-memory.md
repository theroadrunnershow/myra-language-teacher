# Plan: Persistent Memory ("Robot That Remembers Myra")

## Context

The kids-teacher flow on Reachy Mini should remember things about Myra
across sessions — her brother's name, that she loves tigers, that her
favorite colour is blue, the inside joke from yesterday. Today everything
is in-process; nothing survives a restart.

## The smallest thing that works

A single markdown file, parent- and child-curated, read into the model's
system prompt at session start.

```
~/.myra/memory.md            # override via MYRA_MEMORY_FILE
```

Example contents:

```markdown
# Things to remember about Myra

- Her little brother is Ahaan _(2026-03-12)_
- She loves tigers and elephants _(2026-03-14)_
- Favourite colour is blue _(2026-04-01)_
- Inside joke: when she says "ba-ba-banana" we say "yes ma'am!" _(2026-04-12)_
```

That's it. No SQLite, no schema, no migrations, no episode log, no
spaced-repetition machinery, no LLM-written recaps, no admin routes.

## Scope

- **Reachy-only, kids-teacher-only.** No web client, no other modes.
- **Single child per device.** No keying.
- **Memory ≠ mastery tracking.** Mastery (per-word success rate, spaced
  repetition) is a separate feature, deferred. Mixing them was what
  bloated the previous design.

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
*asks* the robot to remember (e.g. "remember I love tigers"). A raw
utterance isn't a clean memory entry — it needs deduping, filler-word
stripping, third-person rewriting, and date-stamping. That conversion
step uses a lightweight LLM. Three phases:

**v1 — read-only / parent-curated.**
The parent edits `~/.myra/memory.md` directly. The robot reads it on
every session start. Ships immediately, no LLM conversion needed. This
is genuinely useful on its own: a parent can seed the file with "her
brother is Ahaan, she loves tigers" and the robot will reference those
naturally for weeks.

**v2 — end-of-session conversion via Ollama Cloud.**
Once v1 is in use, add automatic enrichment. Mechanism:

1. **Collect** the session transcript. The realtime layer already emits
   `publish_transcript` events with speaker + final-vs-partial flags
   (`kids_teacher_realtime.py:319`); a small in-memory collector
   subscribes for the duration of the session.
2. **Real-time ack.** Add one sentence to `instructions.txt`:
   > If the child or parent asks you to remember something, simply say
   > "Got it, I'll remember!" — you don't need to do anything else.
   The Live model handles conversational confirmation; the actual file
   write is async.
3. **At session end**, call the project's text-only LLM abstraction
   (see "Text-LLM abstraction" below) with:
   - The current `~/.myra/memory.md` contents
   - The full session transcript (final lines only, joined as plain
     `Speaker: text` lines)
   - A prompt like:
     > Extract moments where the child or parent asked the robot to
     > remember something. Return new facts as markdown bullets in the
     > existing file's style (one fact per bullet, third person about
     > the child, append `_(YYYY-MM-DD)_`). Skip facts already covered
     > by existing memory. If nothing was asked to be remembered,
     > return exactly `NONE`.
4. **Append** the returned bullets to `memory.md` via the same atomic
   write path used by manual edits.

Why end-of-session, not real-time per turn:
- One LLM call per session is cheap and predictable.
- The trigger detection ("did anyone ask to remember?") and the
  conversion collapse into one LLM judgement — fewer moving parts
  than a regex trigger plus a separate conversion call.
- The child already got real-time acknowledgement from the Live model.
- Avoids tying memory writes to Gemini Live's tool-calling path, which
  doesn't exist on this codebase.

### Text-LLM abstraction (project-wide)

Project policy: any text-only (non-vision, non-audio, non-Live-API)
LLM call routes through a single configurable abstraction. Three
supported providers: **Ollama** (cloud), **OpenAI**, and **Gemini /
Google**. Cloud-only for v1 — no local-on-Pi fallback yet.

New module `src/text_llm.py`. Minimal public surface:

```python
def complete(*, system: str, user: str, temperature: float = 0.0) -> str:
    """Single-shot text completion. Provider + model from env."""
```

No streaming, no tools, no images, no message history — that's all
out of scope. If a future caller needs more, extend then.

Configuration via env (project-wide, not kids-teacher-scoped):

| Env var                  | Values / Notes |
|--------------------------|---|
| `MYRA_TEXT_LLM_PROVIDER` | `ollama` \| `openai` \| `gemini` |
| `MYRA_TEXT_LLM_MODEL`    | provider-specific model id |
| `OLLAMA_API_KEY`         | required when provider=ollama |
| `OLLAMA_HOST`            | optional, defaults to `https://ollama.com` |
| `OPENAI_API_KEY`         | required when provider=openai (already used by realtime path) |
| `GEMINI_API_KEY`         | required when provider=gemini (already used by realtime path) |

Implementation notes:
- Each provider gets a thin adapter function inside `text_llm.py`
  (`_complete_ollama`, `_complete_openai`, `_complete_gemini`); the
  public `complete()` dispatches on `MYRA_TEXT_LLM_PROVIDER`.
- Lazy-import the SDKs inside their adapter to keep import cost down
  and so missing-SDK errors only fire when that provider is selected.
- `ollama` is the only new dependency — add to `requirements.txt`.
  `openai` and `google-genai` are already present.
- Document defaults and the three provider options in `.env.example`.
  Don't hard-code a default `MYRA_TEXT_LLM_MODEL`; require explicit
  selection so the choice is visible in config.
- Tests: one fake-client test per provider plus a dispatcher test for
  unknown / missing provider strings.

Memory summarizer wiring:

- `src/memory_summarizer.py` — single public function
  `summarize_session_to_memory(transcript: str, existing_memory: str)
  -> str` that returns either `"NONE"` or one or more markdown bullet
  lines. Internally calls `text_llm.complete(...)` — provider-agnostic.
- v2 is **disabled when `MYRA_TEXT_LLM_PROVIDER` is unset** — v1
  (parent-edit) keeps working; the system never blocks a session on
  memory writes.

Privacy note (be explicit):
Whichever provider is selected becomes a *second* vendor beyond the
Live API's audio path. Session transcripts (text only, no audio) are
sent there. Worth confirming the parent is OK with the chosen
provider before enabling.

Failure modes:
- `MYRA_TEXT_LLM_PROVIDER` unset → skip v2 entirely, log info once at
  startup.
- Provider-specific API key missing → log error once, skip v2.
- Network error / timeout → log warning, no file change. Acceptable;
  parent can edit manually.
- LLM hallucinates a fact → tight prompt + low temperature; parent can
  delete the bullet from the file.
- Empty / unremarkable transcript → returns `NONE`, nothing happens.

**v3 — real-time tool-call enrichment.**
*Only if v2 surfaces a real friction point* (e.g. parent wants to see
the file update mid-conversation, or session-end conversion frequently
misses things). Add `remember` / `forget` tools to the Gemini Live
config; route `tool_call` events back to `memory_file`. This requires
adding Live tool-use plumbing to the backend — a bounded but real
change. Not on the v1/v2 path.

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
  tools on the Live config; route `tool_call` events back to
  `memory_file`. Existing event names already include `tool_call` /
  `tool_call_cancellation` (see line 542) so the realtime layer will
  surface them; we just have to handle them.
- One nudge sentence in `instructions.txt`.
- Tests: tool-call round-trip with a fake backend, file mutation
  asserted.

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
   a `## Things to remember about Myra` heading, or just appended raw?
   I'd default to appending raw since the file already has its own
   `# Things to remember about Myra` heading at the top.
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
3. v2 only if v1 surfaces a real friction point.
