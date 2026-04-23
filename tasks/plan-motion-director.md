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

The Motion Director adds an intelligent layer that:

1. Reads the live conversation (assistant + child transcript and affect),
2. Selects from a curated vocabulary of pre-choreographed moves, and
3. Hands those moves to the existing `KidsTeacherRobotHooks` for execution.

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

- **Pros:** zero extra infra; gestures perfectly intent-aligned with words.
- **Cons:** tool calls add latency and can stall the audio stream;
  Realtime models are uneven at "do this *while* speaking"; failure mode
  is a beat-late wave or a stuttered sentence. Kids notice.

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

**Decision:** Build Option C. Keep a small rules layer underneath as a
fallback so the robot never goes fully silent if the director is
unavailable.

---

## 4. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                       Reachy Mini (Pi)                           │
│                                                                  │
│   ┌────────────────────┐     transcript stream                   │
│   │  Realtime session  │────────────┐                            │
│   │ (KidsTeacher       │            ▼                            │
│   │  RealtimeHandler)  │     ┌─────────────────┐                 │
│   │                    │     │ Motion Director │                 │
│   │  audio in/out      │     │ (small LLM,     │                 │
│   │  status events     │────▶│  ~1s tick)      │                 │
│   └────────┬───────────┘     └────────┬────────┘                 │
│            │                          │  GestureIntent           │
│            │ speak/listen/idle        ▼                          │
│            │                 ┌─────────────────┐                 │
│            └────────────────▶│ GestureScheduler │                │
│                              │  (debounce,      │                │
│                              │   priority,      │                │
│                              │   barge-in)      │                │
│                              └────────┬─────────┘                │
│                                       ▼                          │
│                              ┌─────────────────┐                 │
│                              │ChoreographyLib  │                 │
│                              │ (named moves)   │                 │
│                              └────────┬─────────┘                │
│                                       ▼                          │
│                              ┌─────────────────┐                 │
│                              │ RobotController │                 │
│                              └─────────────────┘                 │
└──────────────────────────────────────────────────────────────────┘
```

Three new components, all owned by the kids_teacher module:

1. **ChoreographyLibrary** — the gesture vocabulary (§5).
2. **MotionDirector** — the model-driven gesture selector (§6).
3. **GestureScheduler** — concurrency, priority, debounce (§7).

The existing `KidsTeacherRobotHooks` becomes the integration seam: it
forwards transcript and status events into the director and exposes a
`play_gesture(name)` method to the scheduler.

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

## 6. Motion Director model

### Inputs (per tick, ~1s cadence)
- Last 5–10 seconds of assistant transcript
- Last 5 seconds of child transcript (if available)
- Current `SessionStatus` (LISTENING / SPEAKING / THINKING / IDLE)
- Last gesture played + how long ago (avoid repeats)
- Topic hint if the prompt template provided one (e.g. "we're on
  animals today")

### Output (strict JSON)
```json
{
  "gesture": "mini_dance_excited",
  "args": {},
  "priority": "normal",
  "skip_reason": null
}
```

`gesture: null` is valid and common — most ticks should produce no
movement. The director must learn to under-act.

### Prompt shape
A compact system prompt that:
1. Describes the robot's expressive vocabulary (the names + 1-line
   semantics from §5),
2. Gives 5–10 few-shot examples mapping transcript snippets to gestures,
3. Hard-rules: no movement during `THINKING`, max one celebration per
   30s, prefer null over noise.

### Model choice
- **V1:** Claude Haiku 4.5 via the Anthropic SDK. Latency ~300ms, cheap,
  follows JSON schemas reliably. Runs as a background async task on the
  Pi; failures are silent and non-blocking.
- **V2 candidate:** on-device classifier (DistilBERT-class, ONNX) for
  the affect dimension; LLM only for novelty/topic-aware moves.

### Cost ceiling
Cap director calls at 1/sec with rolling-window dedup. Worst case ~3,600
small calls/hour ≈ pennies. Hard kill-switch via env flag.

---

## 7. Concurrency, priority, debouncing

`GestureScheduler` rules:

1. **State precedence** — anything from `KidsTeacherRobotHooks` (the
   speak/listen/idle base state machine) wins. The director only fills
   gaps.
2. **Barge-in cancels mid-gesture** — `stop_assistant_playback` flushes
   the gesture queue and forces `listening_attentive`.
3. **Debounce** — same gesture can't repeat within 8s. Total moves
   capped at 1 every 4s.
4. **Priority lanes** — `safety` > `system` > `celebration` > `affect` >
   `idle_filler`. Lower lanes drop when a higher one fires.
5. **Blend, don't snap** — every clip starts and ends at the neutral
   pose so transitions don't look jerky.

---

## 8. Integration touchpoints

Minimal, surgical changes:

- `kids_teacher_robot_bridge.py`
  - Add `play_gesture(name, args)` that delegates to `GestureScheduler`.
  - In `start_assistant_playback`, also publish the assistant transcript
    chunk to the director's input queue.
  - In `_on_speech_started`, flush director queue (barge-in).
- `kids_teacher_realtime.py`
  - Forward `response.audio_transcript.delta` events to the bridge so
    the director can see assistant text before audio finishes.
- `robot_kids_teacher.py` (CLI entry)
  - Construct `MotionDirector` + `GestureScheduler` and inject into the
    bridge. Fully optional via `--motion-director` flag.

No changes to FastAPI routes. The browser status page never touches
gestures.

---

## 9. Safety & failure modes

- Director output validated against the `ChoreographyLibrary` allow-list.
  Unknown gesture names are dropped with a log line, not executed.
- `RobotController` already enforces joint limits; new clips inherit
  this for free.
- Director failure (timeout, network, parse error) → no gesture, log
  event. Base speak/listen/idle keeps running.
- Hard kill: `KIDS_TEACHER_MOTION_DIRECTOR=off` env flag falls back to
  current 3-state behavior. Default off in V1; opt-in per session.
- Telemetry: every gesture decision (chosen + reason for skip) logged
  to the existing `KidsReviewStore` so we can tune offline.

---

## 10. Phased rollout

**Phase 0 — Choreography library only**
- Land `ChoreographyLibrary` with 6–8 named clips and unit tests against
  a `FakeRobotController`. No model, no scheduler. Wire one clip into a
  manual celebration trigger to validate the path.

**Phase 1 — Scheduler + rules**
- Add `GestureScheduler` with priority/debounce.
- Add a simple keyword/sentiment rules layer that fires gestures from
  the library. No LLM yet. Feature-flagged.

**Phase 2 — Motion Director (LLM)**
- Add `MotionDirector` calling Haiku 4.5. Run side-by-side with rules;
  director outputs override rules when present.
- Telemetry comparing rules vs director firing rates and child
  reactions (subjective for now).

**Phase 3 — Pedagogical extensions**
- `count_bob`, `mimicry`, `point_with_gaze` — gestures that need
  *content* awareness, not just affect. These benefit most from the LLM
  director.

Each phase is independently mergeable and reversible.

---

## 11. Open questions

1. **Director model location** — Anthropic API (network) or on-device?
   Network is simpler; on-device is robust to flaky WiFi but constrains
   model choice.
2. **Transcript fidelity** — The Realtime audio_transcript is partial
   and lags audio. Is the lag tolerable for gesture timing, or do we
   need to peek at the model's text channel earlier?
3. **Gesture authoring tool** — should choreography be Python only, or
   do we want a small JSON/YAML keyframe format so non-engineers can
   contribute moves?
4. **Multi-language tone cues** — the director sees transcripts in
   English, Telugu, Assamese. Does Haiku handle affect equally across
   them, or do we need per-language prompts/examples?
5. **Evaluation** — how do we measure "more fun"? Likely
   parent-in-the-loop A/B + simple metrics (session length, return
   rate). Out of scope for V1 but worth flagging early.

---

## 12. Prior art

- `pollen-robotics/reachy_mini_conversation_app` — TBD: pending review;
  this section will be filled in with what we learn from how their
  conversation app sequences movement against speech.
- The current Myra `robot_teacher.py` celebration animations — narrow
  but a good source of pre-choreographed primitives we can lift into
  the library.
