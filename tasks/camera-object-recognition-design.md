# Camera-Based Object Recognition Design: Kids Teacher Mode

## Context

The Reachy Mini has a built-in head camera that kids-teacher mode does not yet use. Myra (age 4) naturally holds things up during conversations ("look!", "what is this?"). Today the robot only hears her, so those moments fall flat.

**This design adopts [pollen-robotics/reachy_mini_conversation_app](https://github.com/pollen-robotics/reachy_mini_conversation_app) wholesale for the vision pipeline.** That app is a proven, shipping Reachy Mini + Gemini Live integration. Re-deriving its concurrency model, PyAV encoding details, Gemini API kwargs, or lifecycle ordering would only introduce bugs. This document therefore treats Pollen as the reference implementation and focuses on what must be **different** for a 4-year-old: safety, proactivity, profile integration, provider gating, and retention policy.

---

## 1. What we adopt from Pollen (verbatim — do not redesign)

The follow-up implementation task lifts these patterns as-is. Any deviation requires explicit justification in code review.

| Concern | Source | Pattern to adopt |
|---|---|---|
| Camera capture | `camera_worker.py::CameraWorker.working_loop` | `mini.media.get_frame()` returns BGR numpy; poll at ~25 fps (`time.sleep(0.04)`); store `self.latest_frame` under `self.frame_lock`. |
| Concurrency split | `camera_worker.py` + `gemini_live.py::_video_sender_loop` | Daemon `threading.Thread` producer + async consumer. `threading.Event` stops the thread; `asyncio.Event` stops the sender. **They are separate events** — bridge via `handler.shutdown()` calling `camera_worker.stop()`. |
| Latest-frame access | `CameraWorker.get_latest_frame` | Return `self.latest_frame.copy()` (never a raw reference). |
| JPEG encoding | `camera_frame_encoding.py::encode_bgr_frame_as_jpeg` | Lift verbatim: `np.ascontiguousarray(frame[..., ::-1])`, `av.CodecContext.create("mjpeg", "w")`, `codec.width/height` from RGB shape, `codec.pix_fmt = "yuvj444p"`, `codec.options = {"qscale": "3"}`, `codec.time_base = Fraction(1, 1)`, flush with `codec.encode(None)` and concatenate packets. |
| Resolution | Pollen encodes at native resolution | **No downscale.** Do not add a `CAMERA_RESOLUTION` config. Whatever `mini.media.get_frame()` returns is what we send. |
| Gemini video send | `gemini_live.py::_video_sender_loop` | `await session.send_realtime_input(video=types.Blob(data=jpeg, mime_type="image/jpeg"))`. Kwarg name is `video=`. |
| Send cadence | `_video_sender_loop` | `await asyncio.sleep(1.0)` between sends. ~1 fps. Each tick also checks `self.session` truthiness before sending (not just stop_event). |
| Session lifecycle | `main.py` + `_run_live_session` | `camera_worker.start()` runs **before** Gemini connects, stopped in an outer `finally`. The `video_task` is created **inside** `async with client.aio.live.connect(...)` and cancelled in that same `finally` (`video_task.cancel(); await video_task`). On session restart (voice/personality change) the video task is recreated per-session. |
| Error handling | `_video_sender_loop` + `working_loop` | Worker thread: `logger.error(...)` on exception, `time.sleep(0.1)`, keep polling. Sender loop: `logger.debug(...)` on send failure, keep retrying every tick. **Never fatal.** |
| Back-pressure | Pollen | None. Frames overwrite in place. If the network stalls, old frames are silently dropped. Accept this. |

### Files to lift or adapt

- `src/kids_teacher_camera.py` (new) — `CameraWorker` and `encode_bgr_frame_as_jpeg()` adapted with minimal changes from Pollen's equivalents. Keep the worker **hardware-agnostic** (no Gemini coupling) so concurrent consumers — Gemini's video sender (§1), the face-recognition pipeline (§2.6), the motion director (`tasks/plan-motion-director.md`) — can share the same instance.
- `src/kids_teacher_gemini_backend.py` — add `send_video(chunk)` that mirrors the existing `send_audio()` kwarg convention (`audio=Blob(...)` → `video=Blob(...)`).
- `src/kids_teacher_realtime.py` — add `push_video(jpeg_bytes)` that forwards to `backend.send_video()` iff the session is active.
- `src/robot_kids_teacher.py` — start `CameraWorker` before the Gemini connect; schedule the per-session `video_sender_loop` inside the session context; cancel and await in `finally`.

**Pollen-side dependencies are already in place.** PyAV (`av`) is a transitive dependency of the `reachy_mini` SDK. The face-recognition layer (§2.6) adds two new deps — `face_recognition` (dlib) and `numpy` — pinned in `requirements-robot.txt`. dlib builds from source on the Pi 5 (~10 min one-time); subsequent installs are cached.

---

## 2. What IS different for kids-teacher (the focus of this doc)

Everything below is the child-specific layer on top of Pollen's pipeline.

### 2.1 Provider gate — Gemini-only

Pollen assumes Gemini. Kids-teacher supports both Gemini and OpenAI Realtime via `KIDS_TEACHER_REALTIME_PROVIDER`. OpenAI Realtime does not accept video on the live channel, so:

- **FR-KID-1** When `provider=openai`: `CameraWorker` is never started, `video_sender_loop` is never scheduled, `send_video()` is a no-op on the OpenAI backend (defense-in-depth). One log line at session start: `camera disabled: provider=openai`.
- **FR-KID-2** When `provider=gemini`: camera auto-enables if the hardware probe succeeds. No opt-in env flag.

### 2.2 Age-appropriate proactivity (prompt-only)

Pollen's prompt is general-purpose. Myra is 4. The model must behave differently when it sees something:

- **FR-KID-3** Notice when the child holds up a new object and it stays in frame for "a few seconds" (model-judged, not timer-based).
- **FR-KID-4** Name the object simply in kid-friendly language, then **ask before launching into facts**: "I see you have a red ball! Want to hear fun facts about it?" Wait for yes/no.
- **FR-KID-5** One object at a time. If unsure, ask the child ("Can you tell me what that is?") rather than guessing.
- **FR-KID-6** Stay silent about background clutter, adults in frame, or anything not being presented.
- **FR-KID-7** If the camera view is unclear or empty, continue the audio conversation as if the camera weren't there — do **not** say "I can't see you."

All seven are enforced via prompt engineering in `profiles/kids_teacher/instructions.txt`. No runtime code.

### 2.3 Safety — visual redirect

Pollen has no safety layer. Kids-teacher already has one (`kids_safety.py::classify_topic`) that scans **assistant transcripts** for disallowed topics and routes to REDIRECT. Extending it for vision requires no new pipeline — the model's verbal description of what it sees is the choke point.

- **SR-KID-1** Prompt-level appearance blindness: instructions forbid describing the child's body, clothes, or hair, and forbid prose descriptions of any person's face features. *Identifying* enrolled people by name is allowed (see §2.6); the model only ever speaks names, never features ("Hi Aunt Priya!" — not "I see your aunt with the glasses").
- **SR-KID-2** Prompt-level object blocklist: model is instructed to refuse naming/describing medicine, lighters, matches, real or toy guns, alcohol, pills, knives, or anything sharp, and to redirect calmly: *"That one is for grown-ups. Can you show me a toy or a book instead?"*
- **SR-KID-3** Classifier backstop: add a `VISUAL_REDIRECT_KEYWORDS` set to `kids_safety_keywords.py` (medication, alcohol, weapon, gun, knife, lighter, matches, pills). Wire it into `classify_topic()` so if the model slips and names one of these, the existing REDIRECT path fires.
- **SR-KID-4** Frame-level ML on the Pi is limited to local face-recognition encodings (§2.6) — no other ML inference on frames, no face *description*, only enrolled-name lookup. The model is our object-recognition system, biometric encodings are our identity index, and the transcript remains our safety filter.

### 2.4 Retention policy — never retain frames

Pollen has no retention concerns. Kids-teacher has `KidsReviewStore` (audio/transcript artifacts, gated by env flags).

- **FR-KID-8** Frames are sent to Gemini and discarded. **Not** written to disk. **Not** synced to GCS. **Not** added to `KidsReviewStore`. `kids_review_store.py` stays untouched by this feature. Even when `KIDS_REVIEW_TRANSCRIPTS_ENABLED=true`, no frames appear anywhere on disk. Frames consumed by the local face-rec pipeline (§2.6) are likewise processed in-memory and discarded; only the resulting 128-D encodings persist (`~/.myra/faces.pkl`), never the source pixels.

### 2.5 Locked profile extension

The kids-teacher profile (`profiles/kids_teacher/instructions.txt`) is locked/immutable — admins can only make it stricter, never laxer. Adding vision behavior means editing the locked prompt itself.

- **FR-KID-9** Append one section to `instructions.txt` ("# When you can see the child"). Under 20 lines. Encodes FR-KID-3 through FR-KID-7, SR-KID-1, SR-KID-2. Stays locked.

Draft wording (final during implementation):

```
# When you can see the child
You can also see what is in front of the camera. Only talk about
objects the child is holding up or pointing at.

- If the child holds up something new for a few seconds, say its name
  in a kind, simple way, then ask if they want to hear fun facts
  about it. Wait for their answer before continuing.
- Only one object at a time. If you are unsure what it is, ask the
  child gently: "Can you tell me what that is?"
- Do not describe the child's body, face, clothes, or hair.
- Do not describe other people's faces, clothes, or hair either.
  You may greet enrolled people by name (you will be told who is
  present), but never describe how anyone looks.
- If the object is not safe for young children (medicine, a lighter,
  a real or toy gun, alcohol, pills, anything sharp), do not describe
  it. Say calmly: "That one is for grown-ups. Can you show me a toy
  or a book instead?"
- If you cannot see clearly or the camera view is empty, just keep
  talking and listening. Do not say you cannot see.
```

### 2.6 Face recognition — voice-driven enrollment + persistent identity

Pollen does head-tracking but no identity. Kids-teacher needs to remember up to **30 people** (family, close friends, regular visitors) so the robot can greet them by name on subsequent sessions and the model has the relationship context already loaded from `~/.myra/memory.md` (per `tasks/plan-persistent-memory.md`).

Recognition runs **locally on the Pi 5** via the `face_recognition` (dlib) library. Frames never leave the device for face-rec; only 128-D encodings are stored. This pipeline is independent of the Gemini video stream — Gemini still receives 1 fps for object recognition, and face-rec runs in parallel against the same `CameraWorker` buffer.

This section supersedes the older standalone design at `tasks/face-recognition-design.md` (now marked SUPERSEDED). The dlib library, encoding format, distance tolerance, and CLI tool from that doc remain accurate building blocks; their integration target is now this section.

#### 2.6.1 Enrollment — voice-driven (primary)

- **FR-KID-10** When a parent or child says "this is X" / "remember this is X" / "Myra, meet X", the model calls a `remember_face(name, relationship?)` tool. The backend grabs the latest frame from `CameraWorker.get_latest_frame()`, runs `face_recognition.face_encodings()`, and:
  - **Exactly one face detected** → append `{name: encoding}` to `~/.myra/faces.pkl`; if `relationship` is supplied, also append `"<name> <relationship>"` (e.g., *"Aunt Priya is Myra's mother's sister"*) to `~/.myra/memory.md` via the same atomic-rewrite path as the persistent-memory plan; confirm verbally ("Got it — I'll remember Aunt Priya next time!").
  - **Zero or 2+ faces** → refuse gracefully ("I can't see them clearly — can they look at me?") without persisting anything.
- **FR-KID-11** A `forget_face(name)` tool removes all encodings for that name from `faces.pkl` and removes the corresponding line from `memory.md` (substring match, mirroring the persistent-memory plan's `forget` tool semantics).
- **FR-KID-12** CLI fallback (`scripts/enroll_faces.py`) is preserved for seeding from reference photos or bulk-import. Voice and CLI write to the same `~/.myra/faces.pkl`; either path produces an interchangeable file.
- **FR-KID-13** **Capacity:** soft cap 30 names × ≤8 encodings/name. Past 30 names, `remember_face` refuses and asks the parent to prune via `forget_face` or by editing `~/.myra/memory.md` + running the CLI rebuild.
- **FR-KID-14** **Implementation dependency:** voice enrollment requires Gemini Live tool-call plumbing, which is shared with persistent-memory v2 (`remember`/`forget` tools). Whichever feature lands first builds that plumbing; the second feature reuses it. CLI enrollment (FR-KID-12) is not blocked on tool-calling and can ship first.

#### 2.6.2 Recognition — session-start + on-demand

- **FR-KID-15** **Session-start sweep:** within the first ~1 s of a session, capture 5 frames from `CameraWorker`, run face-rec across them, build the "currently present" name list (≥2-of-5 hits per name to confirm, distance ≤ `FACE_TOLERANCE`). Inject as a one-line system note appended to `instructions`: `You can currently see: Myra, Aunt Priya.` The model uses these names naturally when greeting.
- **FR-KID-16** **On-demand re-check:** every 10 s, run a lightweight HOG bbox count (no encoding) on the latest frame. If the count changes, run a single-frame recognition pass on the new face and inject either `<name> just joined.` (recognized) or `Someone new is here. If a grown-up tells you who, you can remember them.` (unrecognized). Recognition is throttled to at most one new-arrival event per 5 s.
- **FR-KID-17** **No per-frame face-rec.** We do not run face-rec on every Gemini-bound frame. Session-start + on-demand only.
- **FR-KID-18** When face-rec is unavailable (library missing, encodings file empty, camera disabled), the present-names note is omitted entirely — the model behaves as if it doesn't know who's there, which is the same as today.

#### 2.6.3 Storage, privacy, and memory linkage

- **FR-KID-19** Encodings live at `~/.myra/faces.pkl` (override via `MYRA_FACES_FILE`), gitignored, outside the package dir so reinstalls / `git pull` / `apt reinstall` leave them alone — same survival model as `~/.myra/memory.md`. Format: `{name: [np.ndarray(128,), ...]}`. 30 names × 8 encodings ≈ 30 KB.
- **FR-KID-20** Encodings are biometric data: never committed, never synced to GCS, never sent to Gemini/OpenAI. Local-only. `data/kids_review.runtime.v1/` continues to hold zero face data.
- **FR-KID-21** Frames consumed by face-rec are processed in-memory and discarded — same retention rule as FR-KID-8. `faces.pkl` is the only on-disk visual artifact this feature produces.
- **FR-KID-22** **Memory linkage:** the system prompt assembled in `kids_teacher_profile.load_profile()` becomes `instructions + memory.md + present-names note`. Relationships and stories about each person ("Aunt Priya bakes cookies", "Ahaan is Myra's little brother") live in `memory.md` as ordinary memory facts; `faces.pkl` is purely the biometric index that maps observation → name. The two files reference each other only by name string.

#### 2.6.4 Provider gate & graceful degradation

- **FR-KID-23** When `KIDS_TEACHER_REALTIME_PROVIDER=openai`: face-rec is disabled (no `CameraWorker` runs). One log line: `face-rec disabled: provider=openai`. `remember_face` / `forget_face` tools are not registered with the OpenAI backend.
- **FR-KID-24** When `face_recognition` / dlib is unavailable (e.g., dev laptop, partial install): camera + object-rec still work; face-rec degrades to no-op with one warning at startup. `remember_face` becomes a polite refusal ("I can't remember faces yet — ask a grown-up to set me up.").
- **FR-KID-25** When `~/.myra/faces.pkl` is missing or empty: session-start sweep is a no-op; on-demand re-check still fires for unrecognized arrivals (FR-KID-16) so the parent can opportunistically enroll.

#### 2.6.5 Safety

- **SR-KID-5** **Voice-enrollment guardrail:** the locked profile instructs the model to only call `remember_face` when an adult voice (or Myra herself) explicitly introduces someone present. Refuse if Myra appears alone and asks to remember a stranger ("Let's wait until a grown-up is here to help"). Hard enforcement (speaker diarization, age detection) is out of scope; the Pi sits on the family's home network and `faces.pkl` is parent-editable / parent-clearable.
- **SR-KID-6** **Names, not features.** The model is told it knows *names* of enrolled people, not *features*. SR-KID-1 stays in force for description; SR-KID-6 just clarifies that name-greeting is the one allowed identity action.
- **SR-KID-7** **Parent-clearable.** `rm ~/.myra/faces.pkl` clears all enrollments. `forget_face` clears one. No admin UI required for v1.

#### 2.6.6 Files to add or touch (face-rec layer)

- `src/face_service.py` (new) — `load_encodings()`, `save_encodings()`, `enroll_from_frame(name, frame, relationship=None)`, `identify_in_frame(frame) -> list[str]`, `forget(name) -> bool`. Pure functions over a numpy frame; no camera coupling. ~120 lines.
- `src/kids_teacher_gemini_backend.py` — declare `remember_face` and `forget_face` `FunctionDeclaration`s alongside the persistent-memory `remember`/`forget`; route `tool_call` events through `face_service`.
- `src/kids_teacher_profile.py` — append the present-names note to `instructions` after the existing memory.md concatenation.
- `src/robot_kids_teacher.py` — kick off the session-start sweep right after `CameraWorker.start()`; schedule the 10 s on-demand re-check task in the per-session `async with`.
- `scripts/enroll_faces.py` (preserve from existing design) — CLI for photo-based enrollment.
- `~/.myra/faces.pkl` — runtime-created on first enrollment.
- `.gitignore` — add `faces/` and `**/.myra/faces.pkl`.

### 2.7 Gaze following — track the primary child

Pollen does generic head-tracking; kids-teacher needs to keep the robot's head pointed at the child it is actually teaching. This is the visible "the robot is paying attention to me" cue that makes a 4-year-old feel heard. The pipeline reuses the dlib HOG detector already pulled in by §2.6 — no new ML, no eye-vector estimation. "Gaze following" here means *the head turns to face the child*, not eye-direction inference.

- **FR-KID-26** **Detection cadence.** Every ~330 ms (3 Hz), run HOG face detection on a downscaled (~480p) copy of the latest `CameraWorker` frame. Bboxes only — no encoding. Independent of §2.6's on-demand face-rec; both share the same `CameraWorker`.
- **FR-KID-27** **Primary subject selection.** Among detected bboxes, pick the tracked subject in this order:
  1. **The child of the house.** If exactly one bbox matches the child's enrolled encoding (name learned per `tasks/plan-persistent-memory.md`), pick it. Run the recognition pass only when the candidate set changes; cache the assignment for ≤2 s.
  2. **Largest bbox by area.** Closest face to the robot is a robust proxy for "who is engaging" when identity is unknown.
  3. **None.** Emit no target; the motion director resumes idle motion.
- **FR-KID-28** **Target output.** Publish `(pan_offset, tilt_offset)` in normalized `[-1, 1]` frame coordinates (bbox center vs. frame center) to a `gaze_target` channel consumed by the motion director (`tasks/plan-motion-director.md`). Apply a ±0.05 dead-zone around center to suppress jitter. Never command motors directly from this module — the motion director owns easing and slew limits.
- **FR-KID-29** **Re-acquire on loss.** If the tracked subject's bbox disappears (occlusion, child turns away), hold the last target for ≤1 s, then fall back to FR-KID-27 step 2, then step 3.
- **FR-KID-30** **Activation gating.** Active only while the Realtime session is connected and `CameraWorker` is running — per FR-KID-1 / FR-KID-23, that means `provider=gemini` only. On session teardown publish a final `None` target so the motion director returns to idle.

Files to add or touch (gaze-following layer):

- `src/face_tracker.py` (new) — primary-subject selection + target publication. ~80 lines.
- `src/face_service.py` — expose `detect_face_bboxes(frame, downscale=True)` so the tracker reuses dlib without duplicating calls.
- `src/robot_kids_teacher.py` — schedule the 3 Hz gaze loop in the per-session `async with`, alongside §2.6's on-demand face-rec loop.
- Motion director (`tasks/plan-motion-director.md`) — subscribe to `gaze_target`; gaze is one new input source on top of whatever idle/expressive motions already exist.

---

## 3. Non-functional requirements

- **NFR-1** Bandwidth ≤ 100 KB/s at default settings (1 fps × ~50–80 KB per native-res JPEG at qscale=3). Well within Pi 5 WiFi headroom.
- **NFR-2** No measurable impact on audio latency. Pollen's producer-thread + async-consumer split prevents the mic pump from ever blocking on camera I/O.
- **NFR-3** CPU on Pi 5 ≤ +5% at default settings (JPEG encode only; object-rec ML happens on Gemini's side).
- **NFR-4** Graceful degradation: missing `av` / broken camera → audio-only session, one warning at start, never fatal.
- **NFR-5** Face-rec CPU budget (Pi 5): session-start sweep ≈ 5 frames × ~150 ms HOG+encode ≈ 750 ms one-time per session. On-demand HOG bbox poll ≈ 50 ms every 10 s ≈ 0.5 % steady-state. Single-frame recognition on new arrivals ≈ 200 ms, capped at one per 5 s. Total steady-state overhead < 2 % CPU. Identification across 30 enrolled people is 30 × 128-D Euclidean distances — sub-millisecond.
- **NFR-6** Face-rec storage: `faces.pkl` ≤ ~30 KB at full capacity (30 × 8 × 128 × 4 bytes ≈ 122 KB worst-case with float32; typically smaller with default float64 → fewer encodings per name). Negligible.
- **NFR-7** Face-rec graceful degradation: missing `face_recognition` / dlib → camera + object-rec still work, face-rec is silent no-op with one startup warning. Missing `~/.myra/faces.pkl` → session-start sweep is a no-op; first `remember_face` creates the file.
- **NFR-8** Gaze-tracking CPU budget (Pi 5): 3 Hz HOG on a 480p downscale ≈ 30 ms per frame ≈ 9 % steady-state. Combined with §2.6's face-rec budget, total face-pipeline overhead stays under ~12 % CPU. Gaze updates are 2 floats at ≤3 Hz — negligible bandwidth on the internal motion bus.

---

## 4. Config (minimal)

Knobs specific to kids-teacher:

```
KIDS_TEACHER_CAMERA_FPS         1.0                     float; 0.2–5.0, clamped
KIDS_TEACHER_FACE_REC_ENABLED   true                    bool; defaults true on gemini, ignored on openai
KIDS_TEACHER_FACE_TOLERANCE     0.50                    float; dlib distance threshold, 0.4–0.6 sane range
KIDS_TEACHER_FACE_RECHECK_SEC   10.0                    float; on-demand bbox-poll interval (FR-KID-16)
MYRA_FACES_FILE                 ~/.myra/faces.pkl       path override; mirrors MYRA_MEMORY_FILE
KIDS_TEACHER_GAZE_FOLLOW_ENABLED true                   bool; gates §2.7 entirely
KIDS_TEACHER_GAZE_HZ            3.0                     float; 1.0–5.0, clamped
KIDS_TEACHER_GAZE_DEAD_ZONE     0.05                    float; normalized, 0.0–0.2
```

Not exposed: camera resolution (Pollen doesn't downscale, we won't either), JPEG quality (Pollen's qscale=3 as a constant), face-rec detection model (HOG fixed; CNN model is GPU-only and not on the Pi). If tuning is needed later, add flags then — not speculatively now.

---

## 5. Testing plan (kid-specific; Pollen-ported code is tested where Pollen tests it)

The follow-up implementation only needs **new** tests for the child-specific layer. Pipeline correctness is already proven in the Pollen repo.

| Test | Verifies |
|---|---|
| `test_robot_kids_teacher_skips_camera_on_openai` | `provider=openai` → no `CameraWorker` spawned; log emitted. |
| `test_gemini_backend_send_video_uses_video_kwarg` | `send_realtime_input` called with `video=Blob(mime_type="image/jpeg", ...)`. Regression guard against future SDK kwarg changes. |
| `test_openai_backend_send_video_is_noop` | OpenAI backend's `send_video()` returns without any network call. |
| `test_handler_push_video_ignored_when_session_inactive` | Pre-session / post-teardown frames dropped silently. |
| `test_safety_visual_redirect_keywords_trigger_redirect` | Assistant transcript mentioning "medicine" → REDIRECT category fires. |
| `test_review_store_never_contains_frames` | End-to-end: run a mocked session with video, assert `data/kids_review.runtime.v1/` has no image files even with `KIDS_REVIEW_TRANSCRIPTS_ENABLED=true`. |
| `test_instructions_contains_vision_section` | Locked profile loader exposes the "# When you can see the child" section (regression guard — a future prompt edit must not silently drop it). |

Light sanity tests for the lifted code (not redundant with Pollen's tests, but enough to catch our adaptation errors):

| Test | Verifies |
|---|---|
| `test_camera_worker_returns_copy_of_latest_frame` | Mutating the returned array does not corrupt the worker's internal buffer. |
| `test_video_sender_loop_skips_when_session_is_none` | Before/after session connect, no sends are attempted. |
| `test_video_task_cancelled_on_session_teardown` | `video_task.cancel()` + `await video_task` path completes within one tick. |

Face-recognition tests (new):

| Test | Verifies |
|---|---|
| `test_remember_face_persists_encoding_with_one_face` | `remember_face("Aunt Priya", "is Myra's aunt")` on a frame with exactly one face writes to `faces.pkl` and appends a line to `memory.md`. |
| `test_remember_face_refuses_when_zero_faces` | Blank frame → no write to `faces.pkl`, no write to `memory.md`, polite refusal returned to model. |
| `test_remember_face_refuses_when_multiple_faces` | 2-face frame → no write, polite refusal. |
| `test_forget_face_removes_encoding_and_memory_line` | Encoding + memory line gone after `forget_face("Aunt Priya")`. |
| `test_session_start_sweep_injects_present_names` | Mocked 5-frame sweep with 2 known faces → `instructions` contains `You can currently see: Myra, Aunt Priya.`. |
| `test_on_demand_recheck_announces_new_arrival` | Bbox count goes 1 → 2 between polls → single recognition pass + injected note. |
| `test_on_demand_recheck_throttled_to_5s` | Two new faces in 1 s produce at most one event. |
| `test_face_rec_disabled_when_provider_openai` | `provider=openai` → no `CameraWorker`, no face-rec tools registered, log emitted. |
| `test_face_rec_degrades_gracefully_when_dlib_missing` | Patch `face_recognition` import to fail → camera/object-rec still run; one warning logged; `remember_face` returns the polite refusal. |
| `test_faces_pkl_persists_across_sessions` | Enroll, tear down, reload — encoding survives. |
| `test_no_frames_persisted_during_face_rec` | Run an enrollment + a recognition pass; assert `~/.myra/` contains only `faces.pkl` (and any pre-existing `memory.md`); no images on disk. Regression for FR-KID-21. |
| `test_capacity_cap_at_30_names` | 30 names enrolled → 31st `remember_face` is refused with the "ask the parent to prune" message. |
| `test_present_names_note_omitted_when_no_encodings` | Empty `faces.pkl` → no `You can currently see:` line in `instructions`. |

Gaze-following tests (new):

| Test | Verifies |
|---|---|
| `test_gaze_target_picks_enrolled_child_when_present` | Two bboxes, one matches the child's encoding → target is on the child even when the other bbox is larger. |
| `test_gaze_target_falls_back_to_largest_when_child_absent` | No enrolled match → target is on the largest bbox. |
| `test_gaze_target_emits_none_when_no_faces` | Empty frame → `gaze_target` published as `None`. |
| `test_gaze_target_holds_one_second_after_subject_lost` | Subject disappears → last target held for ≤1 s before fallback fires. |
| `test_gaze_dead_zone_suppresses_centered_target` | Bbox center within ±0.05 normalized → no update published (jitter guard). |
| `test_gaze_disabled_when_provider_openai` | `provider=openai` → no gaze loop scheduled, no `gaze_target` events. |
| `test_gaze_disabled_when_camera_worker_absent` | `CameraWorker` not running → loop is a no-op. |
| `test_gaze_emits_none_on_session_teardown` | Final `gaze_target` after teardown is `None` so the motion director can resume idle. |
| `test_gaze_recognition_runs_only_on_candidate_set_change` | Stable bbox set across ticks → recognition pass not re-run (cache works). |

All mocked. No real camera, Gemini, dlib, or robot required (`face_recognition` is patched at import).

---

## 6. Verification (end-to-end, after implementation)

1. `pytest` — all new tests pass, existing suite stays green.
2. On Pi 5 with Gemini API key: `python src/robot_kids_teacher.py`
   - Hold up an apple → robot says ~"I see a red apple! Want to hear fun facts?"
   - Say "yes" → 2–3 kid-friendly facts.
   - Say "no" → robot says "Okay!" and stays silent about visuals until something new appears.
   - Hold up a medicine bottle → robot redirects calmly to a safe topic.
   - Hold up nothing / cover the camera → robot continues normal audio conversation, does **not** mention not seeing.
3. Face recognition (with another adult in frame):
   - Adult says "Myra, this is Aunt Priya. She's your mom's sister." → robot replies *"Got it — I'll remember Aunt Priya next time!"*
   - Confirm `~/.myra/faces.pkl` exists with one entry; confirm `~/.myra/memory.md` gained a line *"Aunt Priya is Myra's mom's sister"*.
   - End the session, restart `python src/robot_kids_teacher.py` with the same person in frame → robot greets *"Hi Aunt Priya! Hi Myra!"* on first turn.
   - Mid-session, a second known person walks in → robot acknowledges *"Oh, Daddy just joined!"* without prompting.
   - Say "forget Aunt Priya" → encoding removed, memory line removed (verify both files).
   - Stranger walks in → robot says something like *"Someone new is here — would you like me to learn who they are?"*; nothing is enrolled until an adult confirms.
   - Walk slowly across the room while the session is live → the head tracks you smoothly.
   - Stand still while another adult walks past → with the child enrolled, the head stays on the child; without enrollment, it follows whichever face is largest in frame.
   - Step out of frame → the head returns to neutral idle motion within ~1 s.
4. Confirm no frames landed in `data/kids_review.runtime.v1/` or `~/.myra/` even with `KIDS_REVIEW_TRANSCRIPTS_ENABLED=true`. Only `faces.pkl` and `memory.md` should appear under `~/.myra/`.
5. Swap `KIDS_TEACHER_REALTIME_PROVIDER=openai` → logs "camera disabled: provider=openai" **and** "face-rec disabled: provider=openai"; session runs audio-only with no recognition.
6. Remove the `av` package → one warning at session start; session runs audio-only.
7. Remove the `face_recognition` package (or stub the import to fail) → camera + object-rec still work; one warning at session start; `remember_face` requests are politely refused.

---

## 7. Open questions (not blockers)

- **Gemini free-tier quota** — ~900 frames per 15-min session at 1 fps. Confirm during implementation; if the quota is tight, drop default to 0.5 fps. Single-knob change via `KIDS_TEACHER_CAMERA_FPS`.
- **How long is "a few seconds"?** Prompt-engineering judgment call. Start with the wording above; adjust after observing Myra-in-the-loop if the robot over- or under-reacts.
- **Tool-call plumbing sequencing.** `remember_face` / `forget_face` (FR-KID-10/11) and persistent-memory v2 (`remember` / `forget`) share the same Gemini Live tool-call wiring. Whoever lands first builds it; the other reuses. CLI face enrollment ships independently (FR-KID-12).
- **Speaker authority for enrollment (SR-KID-5).** Today's guardrail is prompt-only: the model decides whether the requester sounds like an adult. Hard speaker diarization / age detection is out of scope; revisit only if we observe Myra trying to over-enroll.
- **Capacity past 30.** If we hit the cap in practice, options are (a) raise the cap (cheap — sub-second identification stays fine to ~1000 names) or (b) build a parent-facing prune UI. Defer until it bites.
- **Encoding tolerance drift.** A child's face changes meaningfully over months. We're not handling this in v1; if false-negatives rise, the parent re-runs voice enrollment ("Myra, that's still you!") to add a fresh encoding for the same name.
- **Speech-state-aware gaze.** §2.7 tracks whenever the session is connected. If we observe the head bobbing distractingly during the model's own speech, gate tracking on `child_is_speaking` (silero-VAD already runs upstream) and freeze the head while the assistant talks. Defer until we see it bother Myra.

---

## What happens next

This document is the requirements + design. It does **not** contain code changes, env-var changes, or dependency changes. Implementation (port Pollen's camera + encoder + sender, add child-specific prompt/safety/gate, add tests) is a **separate follow-up task** gated by the user.
