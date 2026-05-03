# Plan: Tools framework for Myra (location + Google Search grounding)

**Status**: design complete (open questions resolved 2026-05-03), not started.
**Owner**: TBD.
**Backend scope**: Gemini Live only.
**Trajectory**: V1 ships two location tools (register/get) plus the
built-in `google_search` grounding tool. Future tools (story picker,
joke, calendar/timer, …) plug in via the same registry. Inline-per-tool
wiring will not scale; standing up a proper pack + registry now is
justified.

---

## 1. What exists today

The codebase already has *one* working tool surface — the motion-director
gestures. Reading it carefully reveals the pattern to copy and the gaps to
fix:

| Layer | File | Role |
| --- | --- | --- |
| Spec + router | `src/motion/tool_specs.py` | OpenAI-Realtime-shaped tool dicts; sync router returns a JSON ack. |
| Pack | `src/motion/stack.py` (`MotionStack.additional_tool_specs/handle_tool_call/gesture_vocabulary_prompt_block`) | Bundles router + lifecycle; exposes the three hook methods. |
| Hooks adapter | `src/kids_teacher_robot_bridge.py:347-406` | Forwards hook calls through to the motion stack. |
| Hook protocol | `src/kids_teacher_types.py:176` (`KidsTeacherRuntimeHooks`) | Declares `handle_tool_call`; `additional_tool_specs` / `additional_instructions` are probed via `getattr` (`kids_teacher_realtime.py:468-499`). |
| Aggregator | `src/kids_teacher_backend.py:122` (`build_session_payload`) | Merges profile-allowlisted tools + `additional_tools` into `session_payload["tools"]`. |
| Gemini Live path | `src/kids_teacher_gemini_backend.py:408` | **Bug-shaped gap:** `LiveConnectConfig(tools=[...])` is hardcoded to memory/face tools. `session_payload["tools"]` is dropped on the floor; the built-in `google_search` tool is also not wired. |

### Other observations

- Tool dispatch is **sync** (`handle_tool_call(...) -> Optional[str]`).
  Motion tools just enqueue into a scheduler — no I/O, no blocking. The
  location tools need GCS I/O on writes, so the framework must support
  an async dispatch path.
- Tool hooks today live only on the **robot bridge**, and that turns
  out to be the *only* place the kids-teacher Gemini Live session runs:
  `GeminiRealtimeBackend` is constructed exclusively in
  `robot_kids_teacher.py:651-657`; the kids-teacher web routes
  (`kids_teacher_routes.py`) only serve a page + status endpoint, no
  SSE/WebSocket. So the framework only ever needs to mount once, at
  the robot bridge layer.
- Tool *output* is plain JSON (e.g. `{"ok": true, "detail": "..."}`).
  The model reads it and produces speech in the lesson language. Tools
  do not need to localise their payloads.

---

## 2. Goals & non-goals

**Goals**

1. One module per tool — adding a new tool means writing one file and
   registering it, no edits in the realtime/backend layers.
2. Works for the Gemini Live backend (the active path). Keep tool
   specs in OpenAI-Realtime shape internally so a later OpenAI Realtime
   adapter is possible, but parity is *not* a V1 goal.
3. Mounts in one place — the robot bridge — because that is the only
   surface that today instantiates the kids-teacher Gemini Live session
   (no separate web/SSE realtime path exists).
4. Async-friendly — a tool can `await` an HTTP/GCS call without
   blocking the event loop or stalling the assistant turn.
5. Failure-safe — a tool that errors or times out returns a structured
   `ok=False` payload; the assistant turn keeps going.
6. Always-on for the kids profile — the registry does not filter by
   allowlist. Existing `profile.allowed_tools` continues to gate legacy
   memory/face stubs untouched.

**Non-goals (V1)**

- A UI for managing tools.
- Per-tool quota / rate limiting (revisit after we have 3+ live tools).
- Multi-turn tool conversations (the model gets one response per call).
- Tools that mutate device state outside the existing motion surface.
- OpenAI Realtime parity.

---

## 3. Proposed shape

```
src/tools/
  __init__.py
  base.py             # Tool, ToolResult, ToolRegistry
  registry.py         # default_registry(): wires built-in tools
  gemini_adapter.py   # OpenAI-shaped spec → google.genai types.Tool
  hooks.py            # ToolsHooksMixin: provides the three hook methods
  location.py         # RegisterCurrentLocationTool, GetCurrentLocationTool
  location_store.py   # GCS-backed location cache (loaded once at startup)

tests/tools/
  test_base.py
  test_gemini_adapter.py
  test_location.py
  test_location_store.py
  test_hooks_integration.py
```

### 3.1 The `Tool` protocol

```python
# src/tools/base.py
class Tool(Protocol):
    name: str                                  # unique tool name

    def spec(self) -> dict:
        """OpenAI-Realtime-shaped function spec (the canonical form)."""

    def prompt_block(self) -> str:
        """Optional short paragraph appended to the system prompt."""

    async def call(self, arguments: Mapping[str, Any]) -> ToolResult:
        """Run the tool. Must not raise — return ToolResult(ok=False, ...)."""
```

`ToolResult` mirrors `motion.tool_specs.ToolCallResult` (an `ok` bool +
`detail` string), with one addition — an optional `data: dict` for
structured payloads (e.g. the registered location). `to_payload()`
emits the JSON the model receives.

### 3.2 The registry

```python
class ToolRegistry:
    def __init__(self, tools: Iterable[Tool]) -> None: ...

    def specs(self) -> list[dict]:
        return [t.spec() for t in self._tools]

    def prompt_block(self) -> str:
        # concatenate non-empty prompt_block() outputs

    async def dispatch(self, name: str, arguments: Any) -> ToolResult:
        # parse arguments (reuse motion's _coerce_arguments), look up by
        # name, await tool.call(args) under a 3s wall-clock cap, wrap
        # exceptions / timeouts into ok=False.
```

The registry is the single place that knows about argument coercion,
exception trapping, the 3s timeout (Q5), and unknown-name handling —
individual tools stay small.

### 3.3 The hooks mixin

```python
# src/tools/hooks.py
class ToolsHooksMixin:
    def __init__(self, *args, tool_registry: Optional[ToolRegistry], **kw):
        super().__init__(*args, **kw)
        self._tool_registry = tool_registry

    def additional_tool_specs(self) -> list[dict]:
        return self._tool_registry.specs() if self._tool_registry else []

    def additional_instructions(self) -> str:
        return self._tool_registry.prompt_block() if self._tool_registry else ""

    async def handle_tool_call(self, call_id, name, arguments) -> Optional[str]:
        if not self._tool_registry:
            return None
        result = await self._tool_registry.dispatch(name, arguments)
        return result.to_payload()
```

Both the robot bridge (`KidsTeacherRobotHooks`) and the web-side hooks
class compose this mixin. The robot bridge already has its own
`additional_tool_specs` / `handle_tool_call` (motion). Two options:

- **(a) chain manually** — keep the existing motion methods and merge
  with the registry's outputs inside the robot bridge; or
- **(b) treat motion as just another `Tool` registered in the registry**
  — the registry becomes the only source of tools and the bridge stops
  doing tool plumbing entirely.

**Recommendation: (a).** Motion has a *much* tighter integration with the
bridge (lifecycle, scheduler, decision logger) than the location tools
ever will. Forcing it through the registry's async dispatch buys
nothing. The mixin's outputs are merged with motion's at the bridge
layer — a few lines of `list(motion_specs) + list(registry_specs)` —
preserving each subsystem's existing tests.

### 3.4 The async dispatch change

Today: `KidsTeacherRuntimeHooks.handle_tool_call` is sync and the realtime
handler does `output = handler(call_id, name, arguments)`
(`kids_teacher_realtime.py:334`).

Change: allow the return to be `str | Awaitable[str] | None`. The handler
becomes:

```python
output = handler(call_id, name, arguments)
if asyncio.iscoroutine(output):
    output = await output
```

Backwards-compatible — motion's sync return still works.

### 3.5 The Gemini adapter (closing the bug + enabling google_search)

`gemini_adapter.py` translates the canonical OpenAI-shaped spec list into
`types.Tool(function_declarations=[...])` and is invoked from
`_build_live_connect_config`:

```python
extra_tools = build_gemini_tools(types_module, session_payload.get("tools") or [])
return types_module.LiveConnectConfig(
    ...
    tools=[
        _build_memory_tool(...),
        _build_remember_face_tool(...),
        _build_forget_face_tool(...),
        {"google_search": {}},     # built-in grounding tool — no schema needed
        *extra_tools,
    ],
)
```

`google_search` is a Gemini-native built-in tool: declared inline as a
single-key dict, no `FunctionDeclaration`, no registry round-trip. It
runs inside the model's turn so the registry's 3s cap does not apply
(documented trade — see §6).

This adapter is the only place backend-shape divergence lives. Tests
cover: enum properties, `additionalProperties: False`, empty-args
schemas, and dropping any spec the adapter can't translate (with a
warning) so a malformed entry doesn't take down the session.

### 3.6 Allowlist gating

**Decision (Q3): always-on for the kids profile.** The registry takes no
allowlist arg. The existing `profile.allowed_tools` stub loop in
`build_session_payload` (`kids_teacher_backend.py:148`) is left alone —
its concerns (gating legacy memory/face stubs) are orthogonal to the
new framework.

### 3.7 Location persistence

```python
# src/tools/location_store.py
class LocationStore:
    """In-memory cache backed by a single GCS object.

    Loaded once at server startup; reads always hit the cache.
    Writes update the cache and persist to GCS in the same call.
    """
    DEFAULT = {"location": "Seattle, WA 98177"}

    async def load(self) -> None: ...    # called from FastAPI startup
    def get(self) -> dict: ...           # sync, cache-only
    async def set(self, location: str) -> None: ...   # cache + GCS write
```

- Same pattern as `dynamic_words_store.py`.
- GCS object key: `kids_teacher/location.json` (single object, not
  per-profile — Myra is the only user).
- Pre-populates with `"Seattle, WA 98177"` if the GCS object is missing.
- The location is also injected into the system prompt at session
  start (so the model knows it without a tool call), *and* exposed via
  `get_current_location` for mid-session refresh after a register call.

---

## 4. Step-by-step implementation

Each step ends with a verification criterion. Stop and re-plan if any
step's verification fails.

### Step 1 — Framework skeleton (no real tools)

- Add `src/tools/base.py` with `Tool`, `ToolResult`, `ToolRegistry`
  (3s `asyncio.wait_for` cap baked into `dispatch`).
- Add `src/tools/hooks.py` with `ToolsHooksMixin`.
- Tests: registry dispatch (happy path, unknown tool, exception in
  `call`, malformed JSON args, timeout → ok=False), prompt-block
  concatenation.

✅ `pytest tests/tools/test_base.py` green.

### Step 2 — Async dispatch in the realtime handler

- Update `KidsTeacherRuntimeHooks.handle_tool_call` signature comment to
  allow `Awaitable[str]`.
- In `kids_teacher_realtime.py:_on_tool_call`, await the result if it's
  a coroutine.
- Tests: a fake hook returning a coroutine completes without warnings;
  motion's sync return still works.

✅ Existing motion tests pass; new test for awaitable return passes.

### Step 3 — Gemini adapter + google_search

- Add `src/tools/gemini_adapter.py` translating OpenAI-shaped specs to
  `types.Tool(function_declarations=[FunctionDeclaration(...)])`.
- Wire it into `_build_live_connect_config`, and add the inline
  `{"google_search": {}}` entry alongside the existing memory/face
  tools.
- Tests: schema translation for required-only, optional, enum, empty;
  malformed spec → warning + skip, not crash; `google_search` always
  present in the resulting `tools` list regardless of `extra_tools`.

✅ A fake `additional_tools=[{"type":"function","name":"X","parameters":{...}}]`
makes it into `LiveConnectConfig.tools`; `google_search` present;
existing memory/face tools unchanged.

### Step 4 — Mount on robot bridge (no new tool yet)

- `KidsTeacherRobotHooks.__init__` accepts an optional `ToolRegistry`.
- `additional_tool_specs` returns `motion_specs + registry_specs`.
- `handle_tool_call` tries motion's tool names first, falls back to the
  registry. (Alternative: a single `dispatch_chain` helper — pick during
  implementation, both are 5 lines.)
- Tests: a mocked registry's tool gets called when the model invokes it;
  motion tools still route to motion.

✅ Robot bridge integration test passes both motion and registry tool
calls.

### Step 5 — Location store + register/get tools

- `src/tools/location_store.py` — `LocationStore` per §3.7. Load once
  on FastAPI startup; cache-only reads; cache+GCS writes.
- `src/tools/location.py`:
  - `RegisterCurrentLocationTool` — args `{"location": "string"}`.
    Calls `store.set(location)`. Returns
    `{"ok": true, "location": "..."}`.
  - `GetCurrentLocationTool` — no args. Returns
    `{"ok": true, "location": "..."}` from cache.
- Wire both into the default registry.
- Inject the current location into the system prompt at
  `build_session_payload` time (read from `LocationStore.get()`).
- Pre-populate Seattle ZIP 98177 if the GCS object is absent on first
  load.
- Tests: register updates cache + writes once to a stubbed GCS client;
  get returns the cached value; missing-GCS-object path falls back to
  default; cache survives across `get()` calls without re-reading GCS.
- Manual smoke: ask Myra "where do I live?" → "Seattle"; "I moved to
  San Francisco" → register fires, GCS object updated; restart server
  → location persists.

✅ Manual smoke succeeds end-to-end on both web flow and (if available)
robot.

### Step 6 — Prompt hint for google_search usage

- Update the kids profile system prompt: "If the kid asks about
  weather, current events, or anything that needs up-to-date facts,
  use Google Search. If asked about location and you don't have one,
  call register_current_location after the kid tells you their city."
- No code in `src/tools/` for this — it's a prompt change only;
  google_search is already wired via Step 3.
- Manual smoke: ask "what's the weather?" — verify in the Gemini
  trace that `google_search` was invoked and that the answer
  references current Seattle weather. Verify the assistant
  paraphrases in Telugu/English at the lesson level.

✅ Manual smoke succeeds; no false-positive search calls when the
question is answerable from the lesson DB.

### Step 7 — Cleanup

- Update `.claude/CLAUDE.md` "Layout" section: add `src/tools/`.
- Update `.claude/CLAUDE.md` "Key architectural notes": one sentence
  describing the tools framework + `google_search` grounding.

✅ `pytest` full suite green.

---

## 5. Decisions (closed open questions)

| # | Question | Decision (2026-05-03) |
| --- | --- | --- |
| Q1 | Weather data source | **N/A** — no weather tool. The model uses built-in `google_search` to answer weather questions via Gemini Live grounding. |
| Q2 | Kids-events data source | **N/A** — no events tool. `google_search` covers it. **No hard-coded JSON fixtures.** |
| Q3 | Tool gating | **Always-on** for the kids profile. Registry does not filter by allowlist. |
| Q4 | API key storage | **Env vars** for now (matches existing pattern); GCS Secret Manager when we deploy. The location tools need no third-party keys; only existing GCS credentials. |
| Q5 | Per-call timeout | **3s registry-level cap**, in addition to per-tool timeouts. Tighter than the original 5s default to maintain a high latency bar. The built-in `google_search` tool bypasses this cap because it runs inside the Gemini Live model's turn. |

Additional decisions made while closing Q1–Q5:

- **OpenAI Realtime parity dropped.** Gemini Live is the only target
  backend. Internal spec shape stays OpenAI-flavoured so a future
  adapter is still possible, but no V1 testing or wiring.
- **Pre-populated location**: `Seattle, WA 98177` — written into the
  `LocationStore` at first boot if the GCS object is missing.
- **Persistence shape**: GCS-backed key/value, loaded once into an
  in-memory cache at server startup. Reads always hit the cache; writes
  go through both the cache and a single GCS write.
- **`get_current_location` tool ships in V1** alongside `register`,
  even though the location is also injected into the system prompt —
  so the model can re-read after a mid-session register call without
  waiting for the next session.

---

## 6. Risks

- **`google_search` latency on the audio path.** Search runs inside the
  model's turn and the 3s registry cap does not apply. Variable
  latency is the trade for skipping a custom weather/events tool.
  Mitigation: the system prompt should restrict `google_search` to
  questions that genuinely need fresh facts (weather, "what's
  happening this weekend") and forbid it for content already in the
  lesson DB.
- **Spec drift between Gemini SDK versions.** The adapter is the only
  place this lives, but Gemini's schema field names have changed across
  SDK releases. Pin against a specific `google.genai` version range and
  test it.
- **`google_search` returning content unsafe for a 4-year-old.** Free
  text from search results is harder to constrain than a structured
  weather payload. Mitigations: system prompt instructs the assistant
  to *paraphrase in the lesson language* (so verbatim odd English
  doesn't leak through) and to skip results that reference adult
  topics. Watch the first week's transcripts for slips.
- **Tool failures during a turn.** A `ToolResult(ok=False, detail=...)`
  reaches the model as JSON — the system prompt should explicitly tell
  the assistant: *"if a tool reports `ok=false`, gently say you couldn't
  find out and move on."* Add this prompt hint as part of the
  registry's `prompt_block()`.
- **GCS read at startup is a cold-start cost.** Cloud Run min=0 means
  cold starts. The first request after a cold start pays the GCS read
  once. Acceptable; same pattern as `dynamic_words_store.py`.
- **`google_search` billing meter.** Gemini 2.5 Flash gets 500 grounded
  requests/day free, then $14/1K. With one kid, this is functionally
  unlimited — but the Cloud Run kill-switch budget should still flag
  any spike (existing infra/ alarm covers this).

---

## 7. Verification gates

- Steps 1–4 are infrastructure; verified by unit tests.
- Step 5 is the first runtime signal — manual smoke per the step's
  bullets (register → restart → persist).
- Step 6 — manual smoke: weather question goes through `google_search`,
  comes back paraphrased in the lesson language; non-weather question
  doesn't trigger a search call.
- Adding a *future* tool should be additive only. If it requires
  touching anything outside `src/tools/` and the registry's tool list,
  the framework's abstraction is wrong and we re-plan.
