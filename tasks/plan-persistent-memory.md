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
*asks* the robot to remember. Two phases:

**v1 — read-only / parent-curated.**
The parent edits `~/.myra/memory.md` directly. The robot reads it on
every session start. Ships immediately, no tool-use plumbing needed.
This is genuinely useful on its own: a parent can seed the file with
"her brother is Ahaan, she loves tigers" and the robot will reference
those naturally for weeks.

**v2 — robot-writable via a `remember` tool.**
Gemini Live supports function calling. We declare:

```python
remember = FunctionDeclaration(
    name="remember",
    description=(
        "Append a fact about the child to long-term memory. Call this "
        "when the child or parent says 'remember that...' or shares "
        "something the robot should know next time."
    ),
    parameters={"type": "object", "properties": {
        "fact": {"type": "string", "description": "One-sentence fact."}
    }, "required": ["fact"]},
)

forget = FunctionDeclaration(
    name="forget",
    description="Remove a remembered fact by substring match.",
    parameters={"type": "object", "properties": {
        "fact_substring": {"type": "string"}
    }, "required": ["fact_substring"]},
)
```

Tool body: `memory_file.append(fact)` / `memory_file.remove(substring)`.
File operation is `open(... "a")` plus a `flock`; on remove, read-modify-
rewrite with atomic rename.

Adds a small system-prompt nudge:

> If the child or parent says "remember that…", call the `remember` tool.
> At the end of an interesting session, you may call it once to record a
> notable fact (e.g. a new favourite, a milestone).

This requires adding tool-use plumbing to the Gemini Live backend, which
doesn't exist yet — a bounded but real change. v1 ships without it.

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
