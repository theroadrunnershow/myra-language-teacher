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

1. **L1 — Audio-reactive wobble** — the head subtly moves with speech.
2. **L2 — LLM-chosen gestures** — dances, emotes, points: chosen by the
   Realtime model itself via tool calls and dispatched in the
   background so audio never stalls.
3. **L3 — Face-tracking offsets** — head pans/tilts to keep the
   primary subject (child preferred, else largest face) in frame.

The "Motion Director" name refers to the whole stack. A model-only
director (the originally-proposed Option C) is preserved as an optional
Phase 4 enhancement.

> **Why no idle-breathing layer?** The existing
> `RobotController.idle()` (`src/robot_teacher.py:617-643`) already runs
> a slow alternating roll sway with antennas perked when the queue is
> empty — it covers the "frozen statue" problem on its own. Once L3
> face tracking is on, the head is rarely truly idle anyway. We can add
> a dedicated breathing loop later if observation shows the existing
> idle pose feels dead, but it isn't worth a Phase-0 line.

### Non-goals (V1)

- Generating raw joint trajectories from a model. Choreography stays
  human-authored and safety-bounded.
- Speaker-aware tracking (lip-motion / mic direction-of-arrival). L3
  uses identity + size heuristics only — see §6.3 for why this is
  enough.
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
| **L1 — Audio-reactive wobble** | Audio amplitude / VAD | 50 ms hops | Continuous, speech-locked secondary motion |
| **L2 — Discrete gestures** | LLM-chosen (Option A) and/or director (Option C) | Per gesture event | Intentional reactions: dance, emote, point |
| **L3 — Face-tracking offsets** | `FaceTracker` `(pan, tilt)` publish | 3 Hz publish, 60 Hz consume | Robot looks at the right person |

Pollen uses Option A for L2. We start there too — it's proven, the
infra cost is low, and it composes cleanly with the existing realtime
session. We keep the **parallel director (Option C)** as a Phase-4
enhancement *only if* tool-call gesture selection misses content-aware
nuance we can't unlock by improving the prompt.

This means our V1 is closer to Pollen's pattern than to the original
parallel-director sketch — a good thing: less novel infra, more proven
behavior.

---

## 4. Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                          Reachy Mini (Pi)                            │
│                                                                      │
│   ┌────────────────────┐         ┌──────────────────┐                │
│   │  Realtime session  │         │  CameraWorker    │                │
│   │ (KidsTeacher       │         │  (existing,      │                │
│   │  RealtimeHandler)  │         │   gemini only)   │                │
│   │                    │         └────────┬─────────┘                │
│   │  ──audio out──┬────┼──► AudioWobbler  │                          │
│   │              │     │   (amp/VAD →     ▼                          │
│   │              │     │    sinusoids)  ┌──────────────────┐         │
│   │              │     │       │        │ FaceTracker      │         │
│   │  ──tool────────────┼──► BackgroundTool│ (pan, tilt) pub │         │
│   │    calls           │    Manager     │  3 Hz, src/      │         │
│   │                    │      │         │  face_tracker.py │         │
│   │  ──status / VAD────┼──┐   ▼         └────────┬─────────┘         │
│   │                    │  │  GestureIntent       │                   │
│   └────────────────────┘  │   (L2: discrete      │                   │
│                           │    LLM-chosen)       ▼                   │
│                           ▼   ▼            ┌──────────────────┐      │
│                       ┌──────────────────┐ │ FaceOffsetMixer  │      │
│                       │GestureScheduler  │ │  (L3: gain by    │      │
│                       │ (priority,       │ │   gaze policy,   │      │
│                       │  debounce,       │ │   §6.3)          │      │
│                       │  barge-in flush) │ └────────┬─────────┘      │
│                       └────────┬─────────┘          │                │
│                                ▼                    │                │
│                       ┌──────────────────┐          │                │
│                       │ ChoreographyLib  │          │                │
│                       │ (named clips +   │          │                │
│                       │  procedural)     │          │                │
│                       └────────┬─────────┘          │                │
│                                │ primary pose       │ L3 offset      │
│                                ▼                    ▼                │
│                       ┌──────────────────────────────────────┐       │
│                       │ MovementComposer                     │       │
│                       │  pose = state pose + primary clip    │       │
│                       │       + L1 wobble + L3 face offset   │       │
│                       └────────┬─────────────────────────────┘       │
│                                ▼                                     │
│                       ┌──────────────────┐                           │
│                       │ RobotController  │                           │
│                       └──────────────────┘                           │
└──────────────────────────────────────────────────────────────────────┘
```

Five new components, all owned by the kids_teacher module:

1. **ChoreographyLibrary** — the gesture vocabulary (§5).
2. **GestureScheduler** — concurrency, priority, debounce (§7).
3. **AudioWobbler** — L1 audio-reactive secondary motion (§6.1). No LLM.
4. **FaceOffsetMixer** — L3 gain modulator that subscribes to the
   existing `FaceTracker` and applies the gaze policy (§6.3). No new
   perception.
5. **MovementComposer** — combines current state pose (`speak`/`listen`/
   `idle`) + primary clip (L2) + L1 wobble + L3 face offset each
   control tick, into a single pose handed to `RobotController`.

The LLM-driven L2 is wired into the existing realtime session as
**function tools** (Option A). Tool dispatch goes through a
`BackgroundToolManager` so audio is never blocked by gesture decisions.

L3 reuses `src/face_tracker.py` as-is — its `subscribe` channel is what
the `FaceOffsetMixer` consumes. No changes to the tracker itself.

The existing `KidsTeacherRobotHooks` becomes the integration seam: it
owns the lifecycle of L1/L2/L3 components, forwards realtime events
(VAD start/stop, playback start/stop) into them, and exposes the
gesture tools to the model.

A future `MotionDirector` (Option C) can plug into the scheduler as a
*second* L2 source if Phase 4 needs content-aware moves the LLM tool
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
- `listening_attentive` — current `listen()` pose, with a subtle
  internal sway so it doesn't feel locked
- `thinking_up` — gaze up + slight tilt during model latency
- `farewell_droop` — antenna droop + slow nod

### System / safety
- `cancel` — return to neutral mid-move (used by scheduler on barge-in)

V1 ships ~10 of these (the most generally useful). The rest land in
sprints with telemetry on which fire most.

---

## 6. Layer details

### 6.1 L1 — Audio-reactive wobble

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

### 6.2 L2 — LLM tool-call gestures (V1 primary)

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

### 6.3 L3 — Face-tracking offsets

The robot's head should follow the right person around the room. We
already have the producer side: `src/face_tracker.py` runs HOG face
detection at 3 Hz over the shared `CameraWorker` buffer and publishes
`(pan, tilt) ∈ [-1, 1]²` (or `None`) to subscribers. What's missing is
a consumer that turns those tuples into additive head-pose offsets and
hands them to the composer.

**New component:** `FaceOffsetMixer`. Subscribes to `FaceTracker`,
maintains the latest target with a small linear smoother (3 Hz publish
→ 60 Hz consume; ease toward target instead of snapping), applies a
gain modulator driven by the gaze policy below, and exposes the
current `(pan_offset_rad, tilt_offset_rad)` to `MovementComposer` each
tick. Hard caps: ±20° pan, ±15° tilt, max angular velocity ~60°/s.

#### 6.3.1 Gaze policy

Three states, all derived from signals we already have. No new
perception (no lip motion, no mic direction-of-arrival).

| Situation | Trigger signal | Gaze target | Gain |
|---|---|---|---|
| Child is speaking | `input.speech_started` from realtime session, until `input.speech_stopped` | Child face if enrolled & visible; else largest face | **1.0** (full follow) |
| Robot is speaking | `start_assistant_playback` until `stop_assistant_playback` | Hold *last* target (don't re-pick mid-utterance); fall back to largest face if last target lost | **0.4** (reduced — L1 wobble already drives the head) |
| Otherwise (between turns) | default | Child if enrolled & visible; else largest face; else `None` | **0.7** |

Falling back to "largest face" matters in two real situations: (a) the
child is enrolled but momentarily off-axis (looking sideways defeats
HOG identity matching), (b) the child isn't enrolled at all. Either
way, "closest visible person" is a sensible fallback because in this
app the closest face to the robot is almost always the child.

The picker itself (`FaceTracker._pick_subject`) already implements
"child > largest > none." We are not changing it; the mixer only
selects the *gain* and *smoothing*.

#### 6.3.2 Why this is not "look at the talker"

A literal "look at whoever is speaking" rule would need either visual
lip-motion detection or mic-array DoA. Both are new perception
pipelines and neither is justified by Myra's actual UX:

- The two voices that matter are the **robot's own TTS** and **the
  child**. Other adults in the room are observers, not the audience —
  pivoting to a parent commenting from the couch would be the *wrong*
  behavior.
- During child speech, VAD + child identity already lets us lock onto
  the child without knowing which face is moving its lips.
- During robot speech, the right behavior is "keep looking at the
  child you're talking to," not "look at yourself" — i.e. *don't*
  re-pick.

The Pollen reference also doesn't do speaker-aware tracking; it tracks
the closest/largest face. We get the same result with one extra rule
(prefer the enrolled child) and one extra signal (VAD already in the
session).

#### 6.3.3 Provider gating preserved

L3 inherits the existing gate from `_maybe_make_gaze_loop_factory`
(`src/robot_kids_teacher.py:305`): no tracker, no mixer, and no L3
contribution to the composer when the provider is `openai`, when the
camera worker is absent, when `KIDS_TEACHER_GAZE_FOLLOW_ENABLED` is
falsey, or when `face_service.HAS_FACE_REC` is false. In any of those
cases the composer simply runs without an L3 term.

### 6.4 Optional Motion Director (Phase 4)

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
- **Phase 4 candidate:** Claude Haiku 4.5. Latency ~300ms, cheap, JSON-
  reliable. Background async task; failures silent and non-blocking.
- **Phase 5 candidate:** on-device classifier (DistilBERT-class, ONNX)
  for affect; LLM only for novelty/topic-aware moves.

---

## 7. Concurrency, priority, debouncing

`GestureScheduler` and `MovementComposer` rules:

1. **Layer composition** — every 60 Hz tick:
   `pose = state pose (speak/listen/idle) + primary clip (L2) + L1 wobble + L3 face offset`.
   L1 and L3 always run when their inputs are present; L2 is sparse.
2. **State precedence (L2)** — anything from `KidsTeacherRobotHooks`
   base state wins over LLM tool gestures. The LLM never overrides
   barge-in posture.
3. **Face offset is additive, never overriding** — L3 shifts the head
   pose around the current state pose; it does not replace it. A clip
   that ends at neutral still ends at neutral, plus whatever pan/tilt
   the mixer is currently applying. The hard caps in §6.3 keep this
   bounded.
4. **Barge-in cancels mid-gesture** — `stop_assistant_playback` flushes
   the L2 queue, resets the wobbler envelope, and snaps to
   `listening_attentive`. The L3 mixer simultaneously raises gain to
   1.0 (child speaking) and locks onto the child face. Mirrors Pollen's
   wobbler reset on `input.speech_started`.
5. **Debounce** — same gesture can't repeat within 8s. Total L2 moves
   capped at 1 every 4s. Long clips (`dance`) get their own lane and
   pre-empt shorter affect clips.
6. **Priority lanes (L2 only)** — `safety` > `system` > `celebration` >
   `affect` > `idle_filler`. Lower lanes drop when a higher one fires.
7. **Blend, don't snap** — every clip starts and ends at the neutral
   pose so L2 transitions are smooth even though L1+L3 keep moving
   underneath. The L3 mixer eases (linear, ~150 ms) toward each new
   target rather than stepping at the 3 Hz publish edge.

---

## 8. Integration touchpoints

Minimal, surgical changes:

- `kids_teacher_robot_bridge.py`
  - Add `play_gesture(name, args)` that enqueues to `GestureScheduler`.
  - In `start_assistant_playback`, fork audio chunks to `AudioWobbler`
    in addition to the existing speaker pipeline; signal the
    `FaceOffsetMixer` to switch to "robot speaking" gain (0.4, hold).
  - In `_on_speech_started`, flush L2 queue, reset the wobbler, and
    signal the `FaceOffsetMixer` to switch to "child speaking" gain
    (1.0, follow).
  - In `_on_speech_stopped` / `stop_assistant_playback`, signal the
    mixer back to idle gain (0.7, follow).
  - Own the lifecycle of `FaceOffsetMixer.subscribe(...)` against the
    existing `FaceTracker` (replacing today's debug-log subscriber in
    `robot_kids_teacher.py:368`).
- `kids_teacher_realtime.py`
  - Register the new gesture tools (`play_gesture`, `dance`, `move_head`,
    `stop_motion`) on session creation, alongside existing tools.
  - Dispatch `response.function_call_arguments.done` events to the
    background tool manager so audio is never blocked.
  - Forward `response.audio_transcript.delta` to the bridge (only used
    if the optional Phase 4 director is enabled).
- `kids_teacher_profile.py`
  - Extend the system prompt with the gesture vocabulary semantics block
    (names + 1-line meaning, no usage rules).
- `robot_kids_teacher.py` (CLI entry)
  - Construct and inject the new components. Two flags:
    `--motion-layers={none,wobble,gestures,tracking,full}` for opt-in
    rollout, `--motion-director` for the Phase 4 parallel director.
  - L3 inherits the existing provider/camera/`HAS_FACE_REC` gate from
    `_maybe_make_gaze_loop_factory`; no new gating logic needed.
- `face_tracker.py`
  - **No changes.** The mixer subscribes via the existing `subscribe()`
    API. The producer-only contract documented at `face_tracker.py:25`
    stays intact.

No changes to FastAPI routes. The browser status page never touches
gestures.

---

## 9. Safety & failure modes

- Tool-call gesture names validated against the `ChoreographyLibrary`
  allow-list. Unknown names dropped with a log line, not executed.
- `RobotController` already enforces joint limits; new clips inherit
  this for free. L1 wobble is bounded by hard offset caps (e.g. ±5°
  pitch, ±3 mm body) so bad audio can't violently shake the head.
  L3 face offsets are similarly bounded (±20° pan, ±15° tilt, max 60°/s
  angular velocity) so a flicker in the bbox can't whip the head.
- L2 failure (tool dispatch error, library miss) → no gesture, log
  event. L1 + L3 keep the robot alive; base speak/listen/idle keeps
  running.
- L3 failure (tracker stops publishing, camera crashes mid-session) →
  mixer holds its last offset for `hold_seconds`, then eases back to
  zero. Composer drops the L3 term cleanly. The existing
  `FaceTracker.run` already publishes `None` on shutdown (FR-KID-30),
  so the mixer just observes that as "no target."
- Hard kill: `KIDS_TEACHER_MOTION_LAYERS=none` env flag falls back to
  current 3-state behavior. Per-layer flags also supported
  (e.g. `...=wobble,tracking` to disable LLM gestures only). The
  pre-existing `KIDS_TEACHER_GAZE_FOLLOW_ENABLED=false` continues to
  hard-kill L3 specifically without touching L1/L2.
- Telemetry: every gesture decision (tool call args + accepted/dropped +
  reason) logged to the existing `KidsReviewStore` so we can tune
  offline. L3 logs target identity (child / largest / none) and gain
  state on each transition, not per tick.

---

## 10. Phased rollout

**Phase 0 — Choreography library + composer**
- Land `ChoreographyLibrary` with 6–8 named clips and unit tests against
  a `FakeRobotController`.
- Land `MovementComposer` (60 Hz tick, additive blending). At the end
  of Phase 0 the composer runs but only emits the existing
  speak/listen/idle state pose — no new aliveness yet.

**Phase 1 — Audio wobble (L1)**
- Add `AudioWobbler` fed from `start_assistant_playback`. Tune offset
  caps with parent on the couch.
- After Phase 1 the robot looks alive *during* speech with no LLM
  changes at all.

**Phase 2 — LLM tool gestures (L2)**
- Add gesture tools to the realtime session + background dispatcher.
- Extend system prompt with vocabulary semantics. Feature-flagged.
- Telemetry on which gestures the model picks unprompted.

**Phase 3 — Face-tracking offsets (L3)**
- Add `FaceOffsetMixer` subscribed to the existing `FaceTracker`.
- Wire VAD/playback signals from `KidsTeacherRobotHooks` into the gain
  modulator (§6.3.1).
- Replace the debug-log subscriber at `robot_kids_teacher.py:368` with
  the mixer.
- After Phase 3 the robot follows the right person around the room
  while still doing L1 wobble during speech.

**Phase 4 — Pedagogical + parallel director (optional)**
- Add content-aware gestures (`count_bob`, `mimicry`, `point_with_gaze`).
- If telemetry shows the LLM under-gesturing, layer in the parallel
  Motion Director (Option C, §6.4) as a second L2 source.

Each phase is independently mergeable and reversible. Phase 1 is
pure-procedural and ships value with no model risk; Phase 3 is
pure-perceptual (no LLM) and uses code already in the repo.

---

## 11. Open questions

1. **Reuse vs. reimplement Pollen primitives** — `SwayRollRT`,
   `MovementManager`, and `BackgroundToolManager` are all
   directly applicable. Do we vendor (license-permitting), depend on
   the package, or rewrite to fit our `RobotController` shape? Vendoring
   small files is probably right. (We are *not* taking `BreathingMove`
   — see §1 callout.)
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
6. **Face-track gain during long robot turns** — at gain 0.4 the head
   still drifts visibly toward the child during a 20-second utterance.
   Is that desirable (engagement) or distracting (head moves while
   talking)? Tune in Phase 3 with parent observation; consider further
   reducing gain if the L1 wobble already conveys "alive."

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

**What we take wholesale:** the multi-layer additive composition at
60 Hz (Pollen's L1 breathing + L2 wobble + L3 LLM gestures + face
tracking offsets — we use the wobble, gestures, and face-tracking
offsets but skip standalone breathing per §1), the
`BackgroundToolManager` pattern, the terse-prompt-with-tool-discretion
approach, the wobbler-reset-on-barge-in pattern, and the
`face_tracking_offsets` channel — implemented here as the
`FaceOffsetMixer` reading from our existing `FaceTracker`.

**What we add:** kid-specific gesture vocabulary (counting, mimicry,
encouragement after misses), tighter integration with our existing
`KidsTeacherRobotHooks` state machine, the gaze policy in §6.3.1
(child-preferred + VAD-gated gain — Pollen tracks closest face
unconditionally), and the optional Phase 4 parallel director for
content-aware moves the LLM might miss.

### Existing Myra `robot_teacher.py`

The legacy language-teacher CLI has a narrow set of celebration and
listening animations directly on `RobotController`. Worth lifting the
specific motion primitives (e.g. antenna wiggle for correct answer)
into the new `ChoreographyLibrary` rather than reimplementing.
