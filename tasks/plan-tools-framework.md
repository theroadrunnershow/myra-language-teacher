# Plan: Tools framework for Myra (weather, kids-events, …)

**Status**: design draft, not started.
**Owner**: TBD.
**Trajectory**: 5+ tools (weather, local kids events, story picker, joke,
calendar/timer, …). Inline-per-tool wiring will not scale; standing up a
proper pack + registry now is justified.

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
| OpenAI Realtime path | (downstream of `build_session_payload`) | Sends `session_payload["tools"]` directly. |
| Gemini Live path | `src/kids_teacher_gemini_backend.py:408` | **Bug-shaped gap:** `LiveConnectConfig(tools=[...])` is hardcoded to memory/face tools. `session_payload["tools"]` is dropped on the floor. |

### Other observations

- Tool dispatch is **sync** (`handle_tool_call(...) -> Optional[str]`).
  Motion tools just enqueue into a scheduler — no I/O, no blocking. Weather
  and kids-events both need network I/O, so the framework must support an
  async dispatch path.
- Tool hooks today are only on the **robot bridge**. The browser/SSE
  flow does not implement them. If we want the web-only deployment to
  call weather/events too, the pack must mount somewhere both the web and
  robot paths share — most naturally as a small mixin/composer added to
  whichever hooks class the path uses.
- Tool *output* is plain JSON (e.g. `{"ok": true, "detail": "..."}`).
  The model reads it and produces speech in the lesson language. Tools
  do not need to localise their payloads.

---

## 2. Goals & non-goals

**Goals**

1. One module per tool — adding a 6th tool means writing one file and
   registering it, no edits in the realtime/backend layers.
2. Works in both backends (OpenAI Realtime *and* Gemini Live) without
   per-tool branching.
3. Works in both surfaces (web/SSE flow *and* robot bridge).
4. Async-friendly — a tool can `await` an HTTP call without blocking the
   event loop or stalling the assistant turn.
5. Failure-safe — a tool that errors or times out returns a structured
   `ok=False` payload; the assistant turn keeps going.
6. Configurable per-profile — `profile.allowed_tools` already exists; the
   framework should opt tools in/out via that allowlist.

**Non-goals (V1)**

- A UI for managing tools.
- Per-tool quota / rate limiting (revisit after we have 3+ live tools).
- Multi-turn tool conversations (the model gets one response per call).
- Tools that mutate device state outside the existing motion surface.

---

## 3. Proposed shape

```
src/tools/
  __init__.py
  base.py             # Tool, ToolResult, ToolRegistry
  registry.py         # default_registry(): wires built-in tools
  gemini_adapter.py   # OpenAI-shaped spec → google.genai types.Tool
  hooks.py            # ToolsHooksMixin: provides the three hook methods
  weather.py          # WeatherTool
  kids_events.py      # KidsEventsTool

tests/tools/
  test_base.py
  test_gemini_adapter.py
  test_weather.py
  test_kids_events.py
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
structured payloads (weather: temp/condition/forecast). `to_payload()`
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
        # name, await tool.call(args), wrap exceptions into ok=False.
```

The registry is the single place that knows about argument coercion,
exception trapping, and unknown-name handling — individual tools stay
small.

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
bridge (lifecycle, scheduler, decision logger) than weather/events ever
will. Forcing it through the registry's async dispatch buys nothing. The
mixin's outputs are merged with motion's at the bridge layer — a few
lines of `list(motion_specs) + list(registry_specs)` — preserving each
subsystem's existing tests.

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

### 3.5 The Gemini adapter (closing the bug)

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
        *extra_tools,
    ],
)
```

This single adapter is the only place backend-shape divergence lives.
Tests cover: enum properties, `additionalProperties: False`, empty-args
schemas, and dropping any spec the adapter can't translate (with a
warning) so a malformed entry doesn't take down the session.

### 3.6 Per-profile allowlist

`profile.allowed_tools` is already used by `build_session_payload` to add
empty stub specs for allowlisted names (`kids_teacher_backend.py:148`).
The registry takes the allowlist as a constructor arg and filters tools
whose `name` is not in it. The `{"type": "function", "name": name}` stub
loop in `build_session_payload` becomes dead code once the registry
provides full specs and is removed (Step 7).

---

## 4. Step-by-step implementation

Each step ends with a verification criterion. Stop and re-plan if any
step's verification fails.

### Step 1 — Framework skeleton (no real tools)

- Add `src/tools/base.py` with `Tool`, `ToolResult`, `ToolRegistry`.
- Add `src/tools/hooks.py` with `ToolsHooksMixin`.
- Tests: registry dispatch (happy path, unknown tool, exception in
  `call`, malformed JSON args), prompt-block concatenation, allowlist
  filtering.

✅ `pytest tests/tools/test_base.py` green.

### Step 2 — Async dispatch in the realtime handler

- Update `KidsTeacherRuntimeHooks.handle_tool_call` signature comment to
  allow `Awaitable[str]`.
- In `kids_teacher_realtime.py:_on_tool_call`, await the result if it's
  a coroutine.
- Tests: a fake hook returning a coroutine completes without warnings;
  motion's sync return still works.

✅ Existing motion tests pass; new test for awaitable return passes.

### Step 3 — Gemini adapter

- Add `src/tools/gemini_adapter.py` translating OpenAI-shaped specs to
  `types.Tool(function_declarations=[FunctionDeclaration(...)])`.
- Wire it into `_build_live_connect_config`.
- Tests: schema translation for required-only, optional, enum, empty;
  malformed spec → warning + skip, not crash.

✅ A fake `additional_tools=[{"type":"function","name":"X","parameters":{...}}]`
makes it into `LiveConnectConfig.tools`; existing memory/face tools
unchanged.

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

### Step 5 — First real tool: weather

- `src/tools/weather.py` — `WeatherTool` calling a chosen API
  (Open-Meteo is keyless and fits "minimal infra"; revisit if accuracy is
  poor).
- Args: `{ "location": "string" }` (start with city name; geocode via
  Open-Meteo's geocoding endpoint).
- Returns a small payload: `{ ok, condition, temp_c, summary }`.
- Cache-by-location for ~10 min in-process.
- 2s timeout; on timeout/network error return `ok=False` with a kid-safe
  detail.
- Tests: happy path with a stubbed HTTP client, cache hit, timeout
  returns `ok=False`, malformed location returns `ok=False`.
- Manual smoke: ask Myra "what's the weather?" → mascot answers
  appropriately in Telugu/English.

✅ Manual smoke succeeds end-to-end on both web flow and (if available)
robot.

### Step 6 — Second real tool: kids events (proof of pluggability)

- `src/tools/kids_events.py` — pick a source (Eventbrite kids-category
  API? city-specific? local JSON file?). **Open question — pick during
  implementation.** If no good free API exists, ship a static JSON
  fixture so the framework can be exercised end-to-end and revisit data
  source separately.
- Should require **zero** changes outside `src/tools/` and the
  registry's default tool list.

✅ Adding the tool is a pure-additive diff, no edits to backend / realtime
/ bridge.

### Step 7 — Cleanup

- Remove the `{"type": "function", "name": name}` stub loop in
  `build_session_payload` once the registry produces full specs (verify
  no remaining caller relies on it).
- Update `.claude/CLAUDE.md` "Layout" section: add `src/tools/`.
- Update `.claude/CLAUDE.md` "Key architectural notes": one sentence
  describing the tools framework.

✅ `pytest` full suite green.

---

## 5. Open questions

| # | Question | Default if not answered |
| --- | --- | --- |
| Q1 | Weather data source — Open-Meteo (keyless, free) vs OpenWeatherMap (key, richer)? | Open-Meteo for V1; revisit if Myra notices wrong forecasts. |
| Q2 | Kids-events data source. | Static JSON fixture for first ship; real source as a follow-up. |
| Q3 | Should tools be opt-in per `profile.allowed_tools`, or always-on for the kids profile? | Per-profile allowlist (matches existing pattern). |
| Q4 | Where do tool API keys live — env vars, settings UI, GCP Secret Manager? | Env vars first (matches current pattern); Secret Manager when we deploy. |
| Q5 | Does the realtime handler need a per-tool-call timeout in addition to the tool's own timeout? | Yes — defensive 5s upper bound at the registry layer. |

---

## 6. Risks

- **Latency on the audio path.** A tool call inserts an HTTP round-trip
  between the child's question and the assistant's reply. Mitigation:
  hard 2s tool-side timeout, plus aggressive caching where possible
  (weather is the obvious win). If latency becomes painful, consider
  pre-fetching the most common tool (today's weather for the
  configured location) at session start.
- **Spec drift between OpenAI Realtime and Gemini Live.** The adapter
  is the only place this lives, but Gemini's schema field names have
  changed across SDK releases. Pin the adapter against a specific
  `google.genai` version range and test it.
- **Inappropriate tool output for a 4-year-old.** Weather and events
  payloads are factual, but free-text fields (event names, weather
  descriptions) could surface things outside Myra's vocabulary. Mitigate
  by having tools only return short, structured fields; the model
  paraphrases them in the lesson language.
- **Tool failures during a turn.** A `ToolResult(ok=False, detail=...)`
  reaches the model as JSON — the system prompt should explicitly tell
  the assistant: *"if a tool reports `ok=false`, gently say you couldn't
  find out and move on."* Add this prompt hint as part of the
  registry's `prompt_block()`.

---

## 7. Verification gates

- Step 1–4 are infrastructure; verified by unit tests.
- Step 5 is the first real-world signal — manual smoke test on both web
  and robot before declaring the framework working.
- Step 6 is the *real* validation: it should be additive only. If Step 6
  requires touching anything outside `src/tools/` and the registry's tool
  list, the framework's abstraction is wrong and we re-plan.
