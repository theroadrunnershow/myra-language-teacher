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

- `src/kids_teacher_camera.py` (new) — `CameraWorker` and `encode_bgr_frame_as_jpeg()` adapted with minimal changes from Pollen's equivalents. Keep the worker **hardware-agnostic** (no Gemini coupling) so future features (face recognition per `tasks/face-recognition-design.md`, motion director per `tasks/plan-motion-director.md`) can share the same instance.
- `src/kids_teacher_gemini_backend.py` — add `send_video(chunk)` that mirrors the existing `send_audio()` kwarg convention (`audio=Blob(...)` → `video=Blob(...)`).
- `src/kids_teacher_realtime.py` — add `push_video(jpeg_bytes)` that forwards to `backend.send_video()` iff the session is active.
- `src/robot_kids_teacher.py` — start `CameraWorker` before the Gemini connect; schedule the per-session `video_sender_loop` inside the session context; cancel and await in `finally`.

**No new dependency installs.** PyAV (`av`) is already a transitive dependency of the `reachy_mini` SDK.

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

- **SR-KID-1** Prompt-level face blindness: instructions forbid describing the child's body, face, clothes, hair, or identifying anyone in the room. (No face-recognition API is called. Future local face-rec stays separate per `tasks/face-recognition-design.md`.)
- **SR-KID-2** Prompt-level object blocklist: model is instructed to refuse naming/describing medicine, lighters, matches, real or toy guns, alcohol, pills, knives, or anything sharp, and to redirect calmly: *"That one is for grown-ups. Can you show me a toy or a book instead?"*
- **SR-KID-3** Classifier backstop: add a `VISUAL_REDIRECT_KEYWORDS` set to `kids_safety_keywords.py` (medication, alcohol, weapon, gun, knife, lighter, matches, pills). Wire it into `classify_topic()` so if the model slips and names one of these, the existing REDIRECT path fires.
- **SR-KID-4** No face analysis. No frame-level ML. The model is our entire computer vision system; the transcript is our entire safety filter.

### 2.4 Retention policy — never retain frames

Pollen has no retention concerns. Kids-teacher has `KidsReviewStore` (audio/transcript artifacts, gated by env flags).

- **FR-KID-8** Frames are sent to Gemini and discarded. **Not** written to disk. **Not** synced to GCS. **Not** added to `KidsReviewStore`. `kids_review_store.py` stays untouched by this feature. Even when `KIDS_REVIEW_TRANSCRIPTS_ENABLED=true`, no frames appear anywhere on disk.

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
- Do not describe anyone else in the room. Do not guess who they are.
- If the object is not safe for young children (medicine, a lighter,
  a real or toy gun, alcohol, pills, anything sharp), do not describe
  it. Say calmly: "That one is for grown-ups. Can you show me a toy
  or a book instead?"
- If you cannot see clearly or the camera view is empty, just keep
  talking and listening. Do not say you cannot see.
```

---

## 3. Non-functional requirements

- **NFR-1** Bandwidth ≤ 100 KB/s at default settings (1 fps × ~50–80 KB per native-res JPEG at qscale=3). Well within Pi 5 WiFi headroom.
- **NFR-2** No measurable impact on audio latency. Pollen's producer-thread + async-consumer split prevents the mic pump from ever blocking on camera I/O.
- **NFR-3** CPU on Pi 5 ≤ +5% at default settings (JPEG encode only; no ML inference).
- **NFR-4** Graceful degradation: missing `av` / broken camera → audio-only session, one warning at start, never fatal.

---

## 4. Config (minimal)

Only one knob specific to kids-teacher:

```
KIDS_TEACHER_CAMERA_FPS   1.0   float; 0.2–5.0 supported, clamped
```

Not exposed: resolution (Pollen doesn't downscale, we won't either) and JPEG quality (adopt Pollen's qscale=3 as a constant). If tuning is needed later, add flags then — not speculatively now.

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

All mocked. No real camera, Gemini, or robot required.

---

## 6. Verification (end-to-end, after implementation)

1. `pytest` — all new tests pass, existing suite stays green.
2. On Pi 5 with Gemini API key: `python src/robot_kids_teacher.py`
   - Hold up an apple → robot says ~"I see a red apple! Want to hear fun facts?"
   - Say "yes" → 2–3 kid-friendly facts.
   - Say "no" → robot says "Okay!" and stays silent about visuals until something new appears.
   - Hold up a medicine bottle → robot redirects calmly to a safe topic.
   - Hold up nothing / cover the camera → robot continues normal audio conversation, does **not** mention not seeing.
3. Confirm no frames landed in `data/kids_review.runtime.v1/` even with `KIDS_REVIEW_TRANSCRIPTS_ENABLED=true`.
4. Swap `KIDS_TEACHER_REALTIME_PROVIDER=openai` → logs "camera disabled: provider=openai"; session runs audio-only.
5. Remove the `av` package → one warning at session start; session runs audio-only.

---

## 7. Open questions (not blockers)

- **Gemini free-tier quota** — ~900 frames per 15-min session at 1 fps. Confirm during implementation; if the quota is tight, drop default to 0.5 fps. Single-knob change via `KIDS_TEACHER_CAMERA_FPS`.
- **How long is "a few seconds"?** Prompt-engineering judgment call. Start with the wording above; adjust after observing Myra-in-the-loop if the robot over- or under-reacts.

---

## What happens next

This document is the requirements + design. It does **not** contain code changes, env-var changes, or dependency changes. Implementation (port Pollen's camera + encoder + sender, add child-specific prompt/safety/gate, add tests) is a **separate follow-up task** gated by the user.
