# Plan: Tooling Layer

## Context

`tasks/kids-teacher-requirements.md` §7 calls for "a tool layer" as one of
the 8 layers of the kids-teacher architecture. What actually shipped is
not a layer — it is two divergent per-backend implementations:

- **OpenAI backend** (`src/kids_teacher_backend.py:136-143`) emits a stub
  tool spec with no schema and no dispatch path. The inline comment is
  candid: *"Tool-spec lookup is deliberately NOT part of V1 — this is a
  minimal stub so the backend can wire up allowlisted names."*
- **Gemini backend** (`src/kids_teacher_gemini_backend.py`) implements
  four tools (`set_about`, `add_note`, `remember_face`, `forget_face`)
  with hand-rolled schema builders (`:122-237`) and a 100-line
  hardcoded if/elif dispatcher in `_handle_tool_call_message`
  (`:537-661`). The unknown-name branch falls through to a logged
  warning (`:649`).

The next planned tool — `play_music` (`tasks/plan-music-tool-barge-in.md`)
— was scoped against the OpenAI backend only, with a fresh `_TOOL_SPECS`
registry just for that backend (`plan-music-tool-barge-in.md:71-96`). It
would not work on Gemini without re-implementing the entire schema +
dispatch in `kids_teacher_gemini_backend.py` from scratch. Today the
team has to *pick a backend per tool*. That is the tooling layer
problem.

## The hypothesis (validated)

> "The current tooling-layer shape limits which tools we can integrate
> (e.g. a music player) because tools are wired per-backend instead of
> through a shared layer."

Validated against the current code:

- **No shared registry.** Tool names are private constants in the
  Gemini backend (`_SET_ABOUT_TOOL_NAME` etc.,
  `kids_teacher_gemini_backend.py:79-82`). Schemas are built inline
  inside `_build_memory_tool` / `_build_remember_face_tool` /
  `_build_forget_face_tool` (`:122-237`). The OpenAI side has nothing.
- **Hardcoded dispatch.** `_handle_tool_call_message` is a sequence of
  `if name == _SET_ABOUT_TOOL_NAME: ... elif name == ...` blocks
  (`:560`, `:591`, `:619`, `:636`). Adding a 5th tool means adding a
  5th block in exactly that file.
- **Provider-specific result shape.** Gemini results go through
  `types.FunctionResponse` with a per-tool `behavior=NON_BLOCKING`
  flag (`:283-298`). The OpenAI plan proposes `send_tool_result` with
  `conversation.item.create` + `function_call_output`
  (`plan-music-tool-barge-in.md:163-167`). Same concept, two unrelated
  call sites.
- **Tools tests are per-backend.** `test_kids_teacher_gemini_backend.py`
  has full tool coverage; `test_kids_teacher_backend.py` has none;
  `test_kids_teacher_realtime.py` has zero `tool_call` references.
- **Asymmetric capability today.** `set_about`, `add_note`,
  `remember_face`, `forget_face` work on Gemini sessions and are
  silently absent on OpenAI sessions. The kids-teacher experience
  forks based on `KIDS_TEACHER_REALTIME_PROVIDER`.

Where the hypothesis is overstated:

- **The hooks protocol is fine.** `KidsTeacherRuntimeHooks`
  (`src/kids_teacher_types.py`) is the right injection seam for
  *side effects* (camera frame, motion, music playback). The tooling
  problem is upstream of hooks — it's how the model's function-call
  intent gets routed to a handler at all. Hooks need a small extension
  for music playback (per `plan-music-tool-barge-in.md` §4) but no
  redesign.
- **Per-tool prompt copy still belongs near the tool.** The
  `_MEMORY_TOOL_PROMPT_APPENDIX` block
  (`kids_teacher_gemini_backend.py:84-114`) is tightly coupled to
  the tool's response contract ("after `remember_face` returns
  status `ok`, say…"). Moving copy into a registry is fine; *unifying*
  it across tools is not the goal.

## Goal

Add a single tool definition + dispatch path that both the OpenAI
Realtime backend and the Gemini Live backend consume, so:

1. Defining a new tool (e.g. `play_music`) is one `ToolSpec` object plus
   one async handler — not a fresh schema/dispatch fork per backend.
2. The same tool works on both providers without `if provider == ...`
   branching above the adapter layer.
3. The backend's job shrinks to (a) translating `ToolSpec` into the
   provider-specific declaration, (b) parsing the wire-format
   `tool_call` into a normalized event, and (c) sending the
   provider-specific result for a normalized `ToolResult`.

Non-goals:

- Replacing the `KidsTeacherRuntimeHooks` protocol.
- Generalizing the per-tool prompt-instruction copy.
- Changing the `tools.txt` allowlist contract per profile.
- Adding new tools (this plan unblocks them; landing them is separate).

## Design

### 1. Provider-neutral `ToolSpec`

New module `src/kids_teacher_tools.py`. JSON Schema is the lingua franca
— OpenAI Realtime takes it under `parameters`, Gemini takes it as
`parameters_json_schema`. One schema feeds both.

```python
ToolHandler = Callable[["ToolContext", dict[str, Any]], Awaitable["ToolResult"]]

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]      # JSON Schema (object)
    handler: ToolHandler
    non_blocking: bool = False      # Gemini-only hint; ignored by OpenAI adapter
    prompt_appendix: str = ""       # appended to the system prompt when allowlisted

@dataclass(frozen=True)
class ToolResult:
    output: dict[str, Any]          # serializable; provider adapters wrap it
```

The handler is a coroutine — same shape regardless of provider.
`prompt_appendix` is concatenated when the profile loader assembles
instructions, so the per-tool model copy stays adjacent to the
schema/handler that owns its semantics.

### 2. `ToolContext`

Per-call dependency bag, injected by the dispatcher. Frozen, narrow:

```python
@dataclass(frozen=True)
class ToolContext:
    hooks: KidsTeacherRuntimeHooks
    services: ToolServices            # face_service, memory_file, music_store, ...
    call_id: Optional[str]
    provider: str                     # "openai" | "gemini" — for logging only
```

`ToolServices` is a separate dataclass populated at session start. It
collects the existing stateful collaborators that today's Gemini
backend reaches for through `self.*` (`face_service`, `memory_file`,
`memory_reconciler`, future `music_store`). Tool handlers accept the
context, never the backend.

### 3. `ToolRegistry`

```python
class ToolRegistry:
    def __init__(self, specs: Iterable[ToolSpec]) -> None: ...
    def filter(self, allowed_names: Iterable[str]) -> "ToolRegistry": ...
    def get(self, name: str) -> Optional[ToolSpec]: ...
    def __iter__(self) -> Iterator[ToolSpec]: ...
```

A module-level `BUILTIN_TOOLS: tuple[ToolSpec, ...]` declares every
tool once. The profile loader (`src/kids_teacher_profile.py`) intersects
`BUILTIN_TOOLS` with `tools.txt` and hands the filtered registry to
`KidsTeacherSessionConfig`. Allowlist semantics are unchanged — the
intersection is the only shape the backend ever sees.

### 4. Provider adapters

Each backend keeps a small, focused adapter — that's all the
backend-specific tool code that survives.

**OpenAI** (`src/kids_teacher_backend.py`):

- `build_session_payload` walks the registry and emits real
  `{"type": "function", "name", "description", "parameters"}` entries
  instead of the current stub (`:136-143`).
- `_normalize_event` translates `response.function_call_arguments.done`
  into a normalized `tool_call` event (per the existing music plan,
  `plan-music-tool-barge-in.md:160-167`).
- `send_tool_result(call_id, output_dict)` posts
  `conversation.item.create` with `function_call_output` then
  `response.create`.

**Gemini** (`src/kids_teacher_gemini_backend.py`):

- A new `build_gemini_tools(registry, types_module)` replaces
  `_build_memory_tool` / `_build_remember_face_tool` /
  `_build_forget_face_tool` (`:122-237`). It walks the registry and
  produces one `types.Tool` containing N `FunctionDeclaration`s, each
  carrying its `parameters_json_schema` and (when supported) the
  `behavior=NON_BLOCKING` flag.
- `_handle_tool_call_message` (`:537-661`) shrinks to: parse
  `function_calls`, emit a normalized `tool_call` event per call,
  await the dispatcher, format `types.FunctionResponse` for each
  `ToolResult`, send via `session.send_tool_response`. The if/elif
  ladder dies.
- `send_tool_result(call_id, name, output)` is the only backend-specific
  result-shaping code that remains.

Both adapters live entirely inside their existing backend files. No
new "adapter abstraction layer" — just two ~30-line translators.

### 5. Dispatcher

A new `ToolDispatcher` is constructed once per session and lives on the
realtime handler. It owns the registry and the `ToolContext`. On a
`tool_call` event it:

1. Looks up `spec = registry.get(name)`. Unknown name → emit a
   `ToolResult({"status": "ignored"})` and warn.
2. Validates `arguments` against `spec.parameters` (jsonschema or a
   minimal hand-rolled check — the existing argument-extraction code
   in `_extract_args` / `_extract_remember_face_args` shows the shape
   we already do informally).
3. `await spec.handler(context, arguments)`.
4. Calls `backend.send_tool_result(call_id, name, result.output)`.

Crucially, the dispatcher is provider-agnostic. The realtime handler
gets one new dispatch branch:

```python
elif event_type == "tool_call":
    await self._dispatcher.dispatch(event)
```

That's the entire change in `kids_teacher_realtime.py` for tool
*dispatch*. Tools that interact with barge-in (e.g. `play_music`)
still touch the handler — but only to register hook callbacks, not
to know tool names.

### 6. Migrating existing tools

`set_about`, `add_note`, `remember_face`, `forget_face` move from the
Gemini backend into `BUILTIN_TOOLS` entries in
`src/kids_teacher_tools.py`. Each entry pulls its current schema and
handler logic verbatim — only the wrapping changes:

- `set_about` / `add_note` handlers call into a `MemoryWriteScheduler`
  service (extracted from the current `_schedule_memory_write` /
  `_run_memory_writer`, `kids_teacher_gemini_backend.py:781-845`) so
  the fire-and-forget queue isn't backend-coupled.
- `remember_face` / `forget_face` handlers call the existing
  `face_service` paths through `ToolServices`. Relationship-note
  scheduling moves alongside the memory writer.
- The `_MEMORY_TOOL_PROMPT_APPENDIX` text becomes the
  `prompt_appendix` field on the relevant specs and is concatenated
  into `instructions` by the profile loader, so it works on *both*
  providers (today it ships only on Gemini sessions).

Side effect: the four tools that today are silently absent on OpenAI
sessions start working there as soon as the OpenAI adapter lands.

### 7. The `play_music` test case

`play_music` is the load-bearing example for whether this layer is
worth it. Under this design:

- One `ToolSpec("play_music", schema, handler)` in
  `kids_teacher_tools.py`.
- `start_music_playback` / `stop_music_playback` added to
  `KidsTeacherRuntimeHooks` (already in the music plan, §4).
- The handler resolves the song via `music_store` (still per
  `plan-music-tool-barge-in.md` §2) and calls
  `context.hooks.start_music_playback(...)`.
- Barge-in extension in `_on_speech_started` to flush music
  (`plan-music-tool-barge-in.md` §5) is unchanged — that part of the
  music plan was always tooling-layer-independent.

Files touched for `play_music` after this layer ships: 1 new spec, 1
hook addition, 1 new `kids_teacher_music.py`, plus the existing
realtime barge-in change. No backend edits. Compare to the current
plan's 8 files, of which 4 are backend-fork.

## Open questions (resolve before coding)

- **JSON Schema fidelity.** Confirm OpenAI Realtime accepts the same
  `additionalProperties: False` + `enum` fragments that Gemini's
  `parameters_json_schema` already takes. If the OpenAI Realtime API
  rejects a feature we use today (e.g. enum on `set_about.key`),
  the adapter has to translate, not pass through.
- **Non-blocking semantics on OpenAI.** Gemini's `NON_BLOCKING` lets
  the model keep talking while a tool runs. OpenAI Realtime does not
  expose that flag; tool calls there are "fire and continue" by
  default once `response.create` is sent. Decide whether
  `non_blocking=True` is silently ignored on OpenAI or whether the
  dispatcher schedules the handler on a background task.
- **Argument validation.** `jsonschema` is not currently a dependency.
  Either add it (small) or write a 30-line subset validator that
  covers `type`/`enum`/`required`/`additionalProperties`. The four
  existing tools all fit the subset.
- **Scope of v1.** Land the layer + migrate the four Gemini tools
  *without* enabling them on OpenAI in the same PR. That keeps the
  diff focused and lets the OpenAI tool path be exercised separately
  (it's a behavior change for any OpenAI session today).

## Tests

Provider-neutral tests are the headline win. New file
`tests/test_kids_teacher_tools.py`:

- Registry filter: `tools.txt` ∩ `BUILTIN_TOOLS` is exact, missing
  names log + drop.
- Dispatcher dispatches by name; unknown name yields
  `{"status": "ignored"}` and never raises.
- Each builtin tool's handler runs against fake services and returns
  the expected `ToolResult` shape (one parametrized test per tool).
- Argument validation rejects malformed args before reaching the
  handler.

Adapter tests stay in their existing files, narrowed:

- `tests/test_kids_teacher_backend.py` — `build_session_payload` emits
  full schema for every allowlisted tool (replaces the stub
  assertion); `_normalize_event` converts
  `response.function_call_arguments.done` into a `tool_call` event.
- `tests/test_kids_teacher_gemini_backend.py` — the existing
  per-tool dispatch tests (`:983-1632`) reduce to two adapter
  tests: "registry of N specs becomes one `types.Tool` with N
  declarations" and "a `ToolResult` becomes a
  `types.FunctionResponse` with the right `behavior`". Per-tool
  semantic tests move to `test_kids_teacher_tools.py`.

Per CLAUDE.md: existing tests are not modified; the migration adds
new tests in `test_kids_teacher_tools.py` and only deletes Gemini
tests once their semantic coverage is reproduced there. Confirm with
the user before deleting any Gemini tool tests.

## Files

New:

- `src/kids_teacher_tools.py` — `ToolSpec`, `ToolResult`,
  `ToolContext`, `ToolServices`, `ToolRegistry`, `ToolDispatcher`,
  `BUILTIN_TOOLS`.
- `tests/test_kids_teacher_tools.py`.

Modified:

- `src/kids_teacher_profile.py` — registry filter on load; concat
  `prompt_appendix` text into instructions.
- `src/kids_teacher_types.py` — `KidsTeacherSessionConfig` carries a
  `ToolRegistry`. (Hooks protocol gets `start_music_playback` /
  `stop_music_playback` only when `play_music` lands.)
- `src/kids_teacher_backend.py` — real schema in
  `build_session_payload`, `tool_call` normalization,
  `send_tool_result`.
- `src/kids_teacher_gemini_backend.py` — replace
  `_build_memory_tool` / `_build_remember_face_tool` /
  `_build_forget_face_tool` and the `_handle_tool_call_message` ladder
  with a `build_gemini_tools(registry)` adapter and a thin
  dispatcher-driven loop.
- `src/kids_teacher_realtime.py` — one new `tool_call` dispatch
  branch; construct `ToolDispatcher` at session start.
- `src/kids_teacher_fakes.py` — `FakeRealtimeBackend` records
  `send_tool_result` calls (already proposed in the music plan).

Profiles + allowlist (`profiles/kids_teacher/tools.txt`) are
unchanged in this PR — flipping tools on for OpenAI sessions is a
follow-up gated on the open-question resolutions above.

## Verification

- `pytest` clean.
- Run a Gemini session: `set_about`, `add_note`, `remember_face`,
  `forget_face` behave identically to today (same prompts, same
  response copy, same memory writes).
- Run an OpenAI session with the four tools allowlisted (local-only,
  not committed): confirm the same flows fire end-to-end. Land in a
  follow-up PR once on-device verification is done.
- Diff the on-device log lines for one Gemini tool call before/after
  — they should be byte-identical except for the dispatcher's own
  added log line.
