# Motion Director — Dynamic Robot Movement for Kids Teacher Mode

**Status:** Design proposal
**Owner:** TBD
**Mode targeted:** `kids_teacher` (Reachy Mini, on-device realtime session)

---

## 1. Goal

Make the robot feel like a *fun teacher* (think Blippi) instead of a polite
news anchor. Today the robot has a three-state body language loop —
`speak` / `listen` / `idle` — driven entirely by audio playback edges and VAD.
This is correct but lifeless: the body never reacts to *what* is being said,
*how* it is being said, or *who* is saying it.

This plan replaces that with a **three-layer motion stack**, modeled on
`pollen-robotics/reachy_mini_conversation_app` (see §12), where most
"aliveness" comes from cheap procedural layers and the LLM only fires
discrete, intentional gestures:

1. **Idle breathing** — the robot is never frozen.
2. **Audio-reactive wobble** — the head subtly moves with speech.
3. **LLM-chosen gestures** — dances, emotes, points: chosen by the
   Realtime model itself via tool calls and dispatched in the
   background so audio never stalls.

The "Motion Director" name now refers to the whole stack. A model-only
director (the originally-proposed Option C) is preserved as an optional
Phase 3 enhancement.

### Non-goals (V1)

- Generating raw joint trajectories from a model. Choreography stays
  human-authored and safety-bounded.
- Visual perception (face tracking, gaze following). Audio + transcript only.
- Replacing the existing `speak` / `listen` / `idle` state machine. The
  Director sits on top of it as a richer overlay.
- Multi-robot or multi-child coordination.

---

## 2. Background — what we have today

`src/kids_teacher_robot_bridge.py:49-277` defines `KidsTeacherRobotHooks`,
the single class that touches the actuators. It exposes three actions, each
backed by a `RobotController` method:

| Trigger | Method | Effect |
|---|---|---|
| First assistant audio chunk | `start_assistant_playback` → `robot.speak()` | Head-nod talking gesture |
| VAD `input.speech_started` / barge-in | `stop_assistant_playback` → `robot.listen()` | Listening posture |
| `SessionStatus` IDLE / ENDED / ERROR | `publish_status` → `robot.idle()` | Resting pose |

Audio playback is debounced: `play_audio(..., suppress_speak_anim=True)`
keeps the speaker chunked without re-triggering animation per chunk.

**Constraint we want to preserve:** all motor calls go through
`RobotController` so safety limits and pose blending stay centralized.

---

## 3. Design options considered

### Option A — Inline tool calls from the Realtime model

Expose a `move_robot(name)` tool to the OpenAI Realtime session. The voice
model decides when to gesture as it speaks.

- **Pros:** zero extra infra; gestures perfectly intent-aligned with
  words. **Proven in production by `pollen-robotics/reachy_mini_conversation_app`** —
  see §12 for details.
- **Cons:** naive implementations stall audio when a tool runs. Pollen
  fixes this with a `BackgroundToolManager` so tool calls execute on a
  separate task while the audio stream continues. With that mitigation
  the latency objection largely disappears.
- **Caveat that remains:** the LLM only fires discrete gestures at
  decision points; the body still goes still between them. Pollen
  compensates with a non-LLM audio-reactive layer + idle breathing
  (see "Layered architecture" below).

### Option B — Inline annotation tags in the assistant text

Have the Realtime prompt emit pseudo-SSML like `<wiggle>` inside its spoken
text; a parser strips them before TTS and queues moves.

- **Pros:** tight word/move alignment without true tool calls.
- **Cons:** Realtime audio path doesn't expose a clean
  "text-before-speech" hook the way classic TTS does. Tag emission is
  unreliable. The Realtime model may also speak the tags out loud.

### Option C — Parallel "director" model (recommended)

A small, fast secondary model runs alongside the Realtime session. It
consumes the rolling assistant transcript (and optionally the child's),
plus session state, and emits *named gestures* on a tick. The voice path
is untouched; gestures are best-effort and can drop without breaking
speech.

- **Pros:** safe, debounceable, hot-swappable, model-agnostic, cheap to
  iterate. The voice model never thinks about the body.
- **Cons:** gesture timing trails speech by ~0.5–1.5s. We mitigate with
  short transcript windows and a low-latency model (Haiku-class).

### Option D — Pure rules

Hand-coded rules: keyword spotting + sentiment classifier → gestures.

- **Pros:** deterministic, free, low-latency.
- **Cons:** brittle, joyless, doesn't generalize to topics we didn't
  anticipate. Defeats the point.

### Revised recommendation — layered, not single-source

After reviewing Pollen's working implementation, the right answer is
**not "pick one option"** — it's a **three-layer composition** where
each layer handles what it's best at:

| Layer | Driver | Cadence | Purpose |
|---|---|---|---|
| **L1 — Idle breathing** | Procedural | 60 Hz | Robot never looks frozen between turns |
| **L2 — Audio-reactive wobble** | Audio amplitude / VAD | 50 ms hops | Continuous, speech-locked secondary motion |
| **L3 — Discrete gestures** | LLM-chosen (Option A) and/or director (Option C) | Per gesture event | Intentional reactions: dance, emote, point |

Pollen uses Option A for L3. We start there too — it's proven, the
infra cost is low, and it composes cleanly with the existing realtime
session. We keep the **parallel director (Option C)** as a Phase-3
enhancement *only if* tool-call gesture selection misses content-aware
nuance we can't unlock by improving the prompt.

This means our V1 is closer to Pollen's pattern than to the original
parallel-director sketch — a good thing: less novel infra, more proven
behavior.

---

## 4. Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                         Reachy Mini (Pi)                           │
│                                                                    │
│   ┌────────────────────┐                                           │
│   │  Realtime session  │                                           │
│   │ (KidsTeacher       │                                           │
│   │  RealtimeHandler)  │                                           │
│   │                    │                                           │
│   │  ──audio out──┬────┼──► AudioWobbler ──┐ (L2: secondary,       │
│   │              │     │   (amp/VAD →      │  additive offsets)    │
│   │              │     │    sinusoids)     │                       │
│   │              │     │                   │                       │
│   │  ──tool────────────┼──► BackgroundTool │                       │
│   │    calls           │    Manager        │                       │
│   │                    │      │            │                       │
│   │  ──status events───┼──┐   ▼            │                       │
│   │                    │  │  GestureIntent │                       │
│   └────────────────────┘  │   (L3: discrete│                       │
│                           │    LLM-chosen) │                       │
│                           ▼   ▼            │                       │
│                       ┌──────────────────┐ │                       │
│                       │GestureScheduler  │◄┘                       │
│                       │ (priority,       │                         │
│                       │  debounce,       │                         │
│                       │  barge-in flush) │                         │
│                       └────────┬─────────┘                         │
│                                ▼                                   │
│                       ┌──────────────────┐                         │
│                       │ ChoreographyLib  │   ┌──────────────────┐  │
│                       │ (named clips +   │◄──│ BreathingLoop    │  │
│                       │  procedural)     │   │ (L1: 60Hz idle)  │  │
│                       └────────┬─────────┘   └────────┬─────────┘  │
│                                │ primary pose         │ baseline   │
│                                ▼                      ▼            │
│                       ┌────────────────────────────────────────┐   │
│                       │ MovementComposer                       │   │
│                       │  pose = baseline + primary + L2 offset │   │
│                       └────────┬───────────────────────────────┘   │
│                                ▼                                   │
│                       ┌──────────────────┐                         │
│                       │ RobotController  │                         │
│                       └──────────────────┘                         │
└────────────────────────────────────────────────────────────────────┘
```

Five new components, all owned by the kids_teacher module:

1. **ChoreographyLibrary** — the gesture vocabulary (§5).
2. **GestureScheduler** — concurrency, priority, debounce (§7).
3. **AudioWobbler** — L2 audio-reactive secondary motion (§6.1). No LLM.
4. **BreathingLoop** — L1 idle baseline. No LLM, no inputs.
5. **MovementComposer** — combines L1 + L2 + L3 each control tick into
   a single pose handed to `RobotController`.

The LLM-driven L3 is wired into the existing realtime session as
**function tools** (Option A). Tool dispatch goes through a
`BackgroundToolManager` so audio is never blocked by gesture decisions.

The existing `KidsTeacherRobotHooks` becomes the integration seam: it
owns the lifecycle of L1/L2/L3 components, forwards realtime events into
them, and exposes the gesture tools to the model.

A future `MotionDirector` (Option C) can plug into the scheduler as a
*second* L3 source if Phase 3 needs content-aware moves the LLM tool
call doesn't surface.

---

## 5. Gesture vocabulary

The library is a flat namespace of pre-choreographed clips. Each clip is
a Python function on top of `RobotController` so safety bounds (joint
limits, max velocity, blending) live in one place.

### Affect gestures
- `nod_encourage` — slow double nod, antenna soft pulse
- `head_tilt_curious` — 15° tilt, hold
- `lean_in` — forward body shift, slight tilt
- `gentle_sway` — soft left-right body sway (storytelling)
- `confused_scratch` — head wobble + antenna flick
- `sad_droop` — antennae lower, head down 10°
- `surprise_pop` — quick head-up + antenna flick
- `wow_wide` — head back, antennas spread

### Celebration / reward
- `mini_dance_excited` — 2s body bounce + antenna wiggle (correct answer)
- `victory_wiggle` — full antenna wiggle + double nod (milestone)
- `clap_substitute` — alternating shoulder shimmy (we have no hands)

### Pedagogical
- `count_bob(n)` — n head bobs spaced to speech rhythm (counting)
- `mimicry(animal)` — signature pose per animal (elephant=slow big nod,
  bird=fast tiny nods, cat=slow tilt + blink)
- `point_with_gaze(direction)` — head turn + brief hold
  ("look over here!")

### Conversational
- `listening_attentive` — current `listen()` pose, but with subtle
  breathing
- `thinking_up` — gaze up + slight tilt during model latency
- `farewell_droop` — antenna droop + slow nod

### System / safety
- `idle_breathing` — micro-motion so the robot never looks frozen
- `cancel` — return to neutral mid-move (used by scheduler on barge-in)

V1 ships ~10 of these (the most generally useful). The rest land in
sprints with telemetry on which fire most.

---

## 6. Layer details

### 6.1 L1 — Idle breathing

A 60 Hz procedural loop that applies a small z-axis sway (~5 mm) and a
slow antenna oscillation (~15°) whenever the gesture queue is empty.
Pure math, no inputs. Mirrors Pollen's `BreathingMove` in
`moves.py:96-181`.

### 6.2 L2 — Audio-reactive wobble

A speech-locked secondary layer that adds small additive offsets to
head pitch/yaw/roll and body x/y/z based on the assistant's outgoing
audio amplitude.

- **Input:** PCM chunks from the realtime audio-out stream (already
  pumped through `KidsTeacherRobotHooks.start_assistant_playback`).
- **Pipeline:** dB-VAD on the chunk → loudness envelope → ~6 sinusoidal
  oscillators at slightly detuned frequencies → additive offset matrix
  applied each control tick.
- **Cadence:** ~50 ms hops, but the *result* is sampled at the 60 Hz
  composer tick.
- **No LLM, no gesture allow-list.** This layer cannot pick a "wrong"
  move because it can't pick at all — it only modulates.

This is what makes the robot look alive *during* speech without paying
LLM round-trips. Pollen's `audio/speech_tapper.py:SwayRollRT` is the
working reference.

### 6.3 L3a — LLM tool-call gestures (V1 primary)

Discrete, LLM-chosen gestures exposed as Realtime tools:

- `play_gesture(name)` — fire a named clip from the library
- `dance(name)` — long-form celebration clip
- `move_head(direction)` — coarse pointing (left/right/up/down/front)
- `stop_motion()` — flush primary queue (model can self-cancel)

Tool specs are auto-generated from Python signatures + docstrings.
Tool dispatch runs in a `BackgroundToolManager`-equivalent so the audio
stream is never blocked. The system prompt describes what each gesture
*means* (semantics) but does **not** prescribe when to use them — that
discretion stays with the LLM, exactly like Pollen's
`prompts/default_prompt.txt` pattern.

### 6.4 L3b — Optional Motion Director (Phase 3)

If telemetry shows the LLM under-gesturing or missing topical cues, add
a parallel director that emits gestures from the same allow-list on a
~1s tick. Spec preserved below for reference.

#### Inputs (per tick)
- Last 5–10 seconds of assistant transcript
- Last 5 seconds of child transcript (if available)
- Current `SessionStatus`
- Last gesture played + how long ago
- Topic hint if the prompt template provided one

#### Output (strict JSON)
```json
{
  "gesture": "mini_dance_excited",
  "args": {},
  "priority": "normal",
  "skip_reason": null
}
```

`gesture: null` is valid and common.

#### Model choice
- **Phase 3 candidate:** Claude Haiku 4.5. Latency ~300ms, cheap, JSON-
  reliable. Background async task; failures silent and non-blocking.
- **Phase 4 candidate:** on-device classifier (DistilBERT-class, ONNX)
  for affect; LLM only for novelty/topic-aware moves.

---

## 7. Concurrency, priority, debouncing

`GestureScheduler` and `MovementComposer` rules:

1. **Layer composition** — every 60 Hz tick:
   `pose = baseline (L1) + primary clip (L3) + additive offsets (L2)`.
   L1 and L2 always run; L3 is sparse.
2. **State precedence (L3)** — anything from `KidsTeacherRobotHooks`
   base state (speak/listen/idle) wins over LLM tool gestures. The LLM
   never overrides barge-in posture.
3. **Barge-in cancels mid-gesture** — `stop_assistant_playback` flushes
   the L3 queue, resets the wobbler envelope, and snaps to
   `listening_attentive`. Mirrors Pollen's wobbler reset on
   `input.speech_started`.
4. **Debounce** — same gesture can't repeat within 8s. Total L3 moves
   capped at 1 every 4s. Long clips (`dance`) get their own lane and
   pre-empt shorter affect clips.
5. **Priority lanes** — `safety` > `system` > `celebration` > `affect` >
   `idle_filler`. Lower lanes drop when a higher one fires.
6. **Blend, don't snap** — every clip starts and ends at the neutral
   pose so L3 transitions are smooth even though L1+L2 keep moving
   underneath.

---

## 8. Integration touchpoints

Minimal, surgical changes:

- `kids_teacher_robot_bridge.py`
  - Add `play_gesture(name, args)` that enqueues to `GestureScheduler`.
  - In `start_assistant_playback`, fork audio chunks to `AudioWobbler`
    in addition to the existing speaker pipeline.
  - In `_on_speech_started`, flush L3 queue and reset the wobbler.
  - Start/stop `BreathingLoop` with the session lifecycle.
- `kids_teacher_realtime.py`
  - Register the new gesture tools (`play_gesture`, `dance`, `move_head`,
    `stop_motion`) on session creation, alongside existing tools.
  - Dispatch `response.function_call_arguments.done` events to the
    background tool manager so audio is never blocked.
  - Forward `response.audio_transcript.delta` to the bridge (only used
    if the optional Phase 3 director is enabled).
- `kids_teacher_profile.py`
  - Extend the system prompt with the gesture vocabulary semantics block
    (names + 1-line meaning, no usage rules).
- `robot_kids_teacher.py` (CLI entry)
  - Construct and inject the new components. Two flags:
    `--motion-layers={none,baseline,wobble,full}` for opt-in rollout,
    `--motion-director` for the Phase 3 parallel director.

No changes to FastAPI routes. The browser status page never touches
gestures.

---

## 9. Safety & failure modes

- Tool-call gesture names validated against the `ChoreographyLibrary`
  allow-list. Unknown names dropped with a log line, not executed.
- `RobotController` already enforces joint limits; new clips inherit
  this for free. L2 wobble is bounded by hard offset caps (e.g. ±5°
  pitch, ±3 mm body) so bad audio can't violently shake the head.
- L3 failure (tool dispatch error, library miss) → no gesture, log
  event. L1 + L2 keep the robot alive; base speak/listen/idle keeps
  running.
- Hard kill: `KIDS_TEACHER_MOTION_LAYERS=none` env flag falls back to
  current 3-state behavior. Per-layer flags also supported
  (`...=baseline,wobble` to disable LLM gestures only).
- Telemetry: every gesture decision (tool call args + accepted/dropped +
  reason) logged to the existing `KidsReviewStore` so we can tune
  offline.

---

## 10. Phased rollout

**Phase 0 — Choreography library + composer**
- Land `ChoreographyLibrary` with 6–8 named clips and unit tests against
  a `FakeRobotController`.
- Land `MovementComposer` (60 Hz tick, additive blending) and
  `BreathingLoop` (L1). At end of Phase 0 the robot is alive in idle
  even with the LLM disabled.

**Phase 1 — Audio wobble (L2)**
- Add `AudioWobbler` fed from `start_assistant_playback`. Tune offset
  caps with parent on the couch.
- After Phase 1 the robot looks alive *during* speech with no LLM
  changes at all.

**Phase 2 — LLM tool gestures (L3a)**
- Add gesture tools to the realtime session + background dispatcher.
- Extend system prompt with vocabulary semantics. Feature-flagged.
- Telemetry on which gestures the model picks unprompted.

**Phase 3 — Pedagogical + parallel director (L3b, optional)**
- Add content-aware gestures (`count_bob`, `mimicry`, `point_with_gaze`).
- If telemetry shows the LLM under-gesturing, layer in the parallel
  Motion Director (Option C, §6.4) as a second L3 source.

Each phase is independently mergeable and reversible. Phases 0–1 are
pure-procedural and ship value with no model risk.

---

## 11. Open questions

1. **Reuse vs. reimplement Pollen primitives** — `BreathingMove`,
   `SwayRollRT`, `MovementManager`, and `BackgroundToolManager` are all
   directly applicable. Do we vendor (license-permitting), depend on
   the package, or rewrite to fit our `RobotController` shape? Vendoring
   small files is probably right.
2. **Clip storage** — Pollen loads dance/emotion clips from HuggingFace
   datasets at runtime. Do we follow that pattern (good for non-eng
   contribution, costs first-run download) or keep clips in-repo as
   Python (simple, easy to test)?
3. **Tool-call quality** — does `gpt-realtime-mini` actually fire
   gestures at the right moments without prompt prescription, or does
   it under-call? If the latter, do we need few-shot examples in the
   prompt or jump to the parallel director sooner?
4. **Multi-language tone cues** — the LLM and any director see
   transcripts in English, Telugu, Assamese. Affect detection quality
   across all three needs a small eval before we trust gestures.
5. **Evaluation** — how do we measure "more fun"? Parent-in-the-loop
   A/B + simple metrics (session length, return rate). Out of scope for
   V1 but worth flagging early.

---

## 12. Prior art

### `pollen-robotics/reachy_mini_conversation_app`

The reference implementation. Pluggable cloud realtime backend
(`gpt-realtime` default, Gemini Live alt). Three-layer composition that
this plan deliberately mirrors:

- **L1 — `BreathingMove`** (`moves.py:96-181`) auto-runs when the move
  queue is empty. ~5 mm z-sway + ~15° antenna oscillation. Pure
  procedural.
- **L2 — `SwayRollRT`** (`audio/speech_tapper.py`, called via
  `audio/head_wobbler.py`) consumes outgoing PCM at ~50 ms hops, runs
  dB-VAD + loudness tracking, drives **6 sinusoidal oscillators** to
  produce additive pitch/yaw/roll + x/y/z offsets. No LLM. Reset on
  child speech-start.
- **L3 — LLM tool calls.** Tools registered with `tool_choice="auto"`
  (`openai_realtime.py:659-662`). Vocabulary: `dance`, `play_emotion`,
  `move_head`, `stop_dance`, `stop_emotion`, `head_tracking`,
  `do_nothing`. Tool args arrive as
  `response.function_call_arguments.done` events
  (`openai_realtime.py:~810`) and dispatch through a
  **`BackgroundToolManager`** (`tools/background_tool_manager.py`,
  registered at line 681) so the audio stream is never blocked.
- **Composition** in `MovementManager` (`moves.py:285-824`) at 60 Hz
  (`CONTROL_LOOP_FREQUENCY_HZ = 60.0`). `_compose_full_body_pose` blends
  primary queue + speech_offsets + face_tracking_offsets every tick.
  Cross-thread `_command_queue` + per-channel locks
  (`_speech_offsets_lock`, `_face_offsets_lock`).

Gesture **clips themselves** are not in code — they're loaded at
runtime from HuggingFace datasets:
`pollen-robotics/reachy-mini-dances-library` and
`reachy-mini-emotions-library`. `RecordedMoves.list_moves()` enumerates
them dynamically.

The system prompt (`prompts/default_prompt.txt`) is **terse**: it
describes head directions and head-tracking semantics but does **not**
prescribe when to dance/emote. The LLM decides timing on its own.

DoFs touched: head 6-DoF (x/y/z + roll/pitch/yaw), 2 antennae, 1 body
yaw — same as our Reachy Mini.

**What we take wholesale:** the L1+L2+L3 split, the
`BackgroundToolManager` pattern, the additive-offset composition at
60 Hz, the terse-prompt-with-tool-discretion approach, the
wobbler-reset-on-barge-in pattern.

**What we add:** kid-specific gesture vocabulary (counting, mimicry,
encouragement after misses), tighter integration with our existing
`KidsTeacherRobotHooks` state machine, and the optional Phase 3
parallel director for content-aware moves the LLM might miss.

### Existing Myra `robot_teacher.py`

The legacy language-teacher CLI has a narrow set of celebration and
listening animations directly on `RobotController`. Worth lifting the
specific motion primitives (e.g. antenna wiggle for correct answer)
into the new `ChoreographyLibrary` rather than reimplementing.
