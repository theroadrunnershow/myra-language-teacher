# Camera-Based Object Recognition Design: Kids Teacher Mode

## Context

The Reachy Mini has a built-in head camera that kids-teacher mode does not yet use. Myra (age 4) naturally holds things up during conversations ("look!", "what is this?"). Today the robot only hears her, so those moments fall flat.

This feature rides on capabilities already in-repo: `src/kids_teacher_gemini_backend.py` (V1.1, committed last week) gives us a Gemini Flash Live session that natively accepts video on the same realtime channel as audio. We extend that channel with a ~1 fps 480p camera stream; the model proactively notices new objects and offers fun facts. No separate CV pipeline, no local ML, no new API integrations.

**Key hardware facts:**
- Platform: Raspberry Pi 5 (8 GB RAM) — no cold-start, always-on
- Camera: Reachy Mini built-in head camera, accessed via the SAME `mini.media` subsystem we already use for audio (`mini.media.get_audio_sample()`)
- Bandwidth headroom: Pi 5 WiFi easily handles ~100 KB/s at default settings

**Reference implementation (grounds every API fact below):**
[pollen-robotics/reachy_mini_conversation_app](https://github.com/pollen-robotics/reachy_mini_conversation_app) ships a working Gemini Live + camera pipeline for Reachy Mini. We adopt their patterns verbatim for the parts they already proved:

| Concern | Fact grounded in Pollen reference |
|---|---|
| Camera capture API | `mini.media.get_frame()` returns a BGR numpy array. Same subsystem as audio; no new SDK. |
| JPEG encoding | PyAV (`av` package) with `mjpeg` codec, `qscale=3`, `pix_fmt=yuvj444p`. PyAV is already a transitive dependency of the `reachy_mini` SDK — no new install. |
| Gemini video send | `session.send_realtime_input(video=types.Blob(data=jpeg_bytes, mime_type="image/jpeg"))`. Kwarg is `video=`, not `media=` or `image=`. |
| Concurrency pattern | Daemon `threading.Thread` producer polls the camera at ~25 fps and stores the latest frame behind a lock. A separate async consumer loop reads that frame and sends at 1 fps. Decouples camera poll rate from send rate and prevents the mic pump from ever blocking on camera I/O. |

---

## Decisions locked with the user

| # | Decision | Value |
|---|---|---|
| 1 | Vision pipeline | **Gemini Live native video-in** (frames on the same live session as audio) |
| 2 | Trigger | **Continuous** — ~1 fps while session is active |
| 3 | Object vocabulary | **Open** (model describes whatever it sees in kid-friendly language; safety layer still screens output) |
| 4 | Frame retention | **Never** — frames sent to Gemini and discarded; not persisted in the review store |
| 5 | Proactivity | **Proactive on new object** — "I see you have a red ball! Want to hear fun facts?" (ask permission before launching into facts) |
| 6 | OpenAI fallback | **Gemini-only feature** — skipped entirely when `KIDS_TEACHER_REALTIME_PROVIDER=openai` |
| 7 | Feature gate | **On by default when provider=gemini** (no opt-in flag; auto-enables when Gemini is selected and camera hardware responds) |
| 8 | Frame rate / res | **1 fps at 480p**, configurable via `KIDS_TEACHER_CAMERA_FPS` / `KIDS_TEACHER_CAMERA_RESOLUTION` |

---

## 1. Functional requirements

- **FR-V1** Stream camera frames at a configurable fps (default 1) while a Gemini-backed kids-teacher session is active.
- **FR-V2** Frames are encoded as JPEG at 480p (default) using PyAV's mjpeg codec, and sent as `image/jpeg` blobs on the same `google.genai` live session as audio: `session.send_realtime_input(video=Blob(data=jpeg, mime_type="image/jpeg"))`.
- **FR-V3** The model receives a system-prompt addition instructing it to: (a) notice when the child holds up a new object for several seconds; (b) name the object simply; (c) ask if the child wants fun facts before launching into them; (d) stay silent about background clutter / adults in frame.
- **FR-V4** When `KIDS_TEACHER_REALTIME_PROVIDER=openai`, no camera code paths run. Status log once per session: `camera disabled: provider=openai`.
- **FR-V5** If the camera fails at any point (hardware error, missing SDK attribute, PyAV encode error), the session continues audio-only. One warning log. Never fatal.
- **FR-V6** Frames are **not** saved locally, **not** synced to GCS, **not** included in `KidsReviewStore` artifacts. The existing review-store paths stay untouched.
- **FR-V7** Camera worker thread and the async send loop both stop cleanly on session end / KeyboardInterrupt, sharing the existing mic-pump `stop_event`.

## 2. Safety requirements

- **SR-V1** Prompt-level: `profiles/kids_teacher/instructions.txt` gets a new section ("# When you can see the child") that reinforces: no commenting on the child's appearance, clothing, body, or people in the background; objects only.
- **SR-V2** Output classifier: existing `classify_topic()` in `kids_safety.py` continues to run on assistant transcripts. If the model describes something visual that hits a disallowed category (e.g. the child holds up a medication bottle, a weapon toy), existing policy still redirects. No new classifier code required — the text path is the bottleneck.
- **SR-V3** No face analysis: the system prompt forbids identifying people; we do not call face APIs or send frames to any face-analysis service. Even if a person is in frame, the robot talks about objects only. Future face-recognition work in `tasks/face-recognition-design.md` stays separate and local-only.
- **SR-V4** Visual redirect blocklist: a small `VISUAL_REDIRECT_KEYWORDS` set added to `kids_safety_keywords.py` (medication, alcohol, weapon, gun, knife, lighter, matches, pills). Triggered when the assistant's transcript mentions these — routes to the existing REDIRECT category.

## 3. Non-functional requirements

- **NFR-V1** Bandwidth budget ≤ 100 KB/s at default settings (1 fps × ~50–80 KB per 480p JPEG at qscale=3). Well within Pi 5 WiFi headroom.
- **NFR-V2** No measurable impact on audio latency. Camera frames go through a dedicated producer thread + async send loop; the mic pump is never blocked.
- **NFR-V3** CPU on Pi 5 ≤ +5% at default settings (JPEG encode only; no ML inference).
- **NFR-V4** Graceful degradation: missing PyAV / broken camera → audio-only session, logged once.

---

## 4. Architecture

```
┌─ Reachy Mini ──────────────────────────────────────────────────────────┐
│  mini.media.get_frame()  → numpy BGR (H×W×3 uint8)                     │
│         │                                                              │
│         ▼  (new: src/kids_teacher_camera.py)                           │
│  ┌─ CameraWorker (daemon threading.Thread) ────────────────┐           │
│  │   loop @ ~25 fps:                                        │           │
│  │     frame = mini.media.get_frame()                       │           │
│  │     with self.frame_lock: self.latest_frame = frame      │           │
│  │     sleep(0.04)                                          │           │
│  └──────────────────────────────────────────────────────────┘           │
│         │ (producer thread — decouples camera poll from send rate)      │
│         │                                                              │
│         ▼                                                              │
│  async def video_sender_loop(handler, worker, stop_event, fps=1.0):    │
│     while not stop_event.is_set():                                     │
│         frame = worker.get_latest_frame()                              │
│         if frame is not None:                                          │
│             jpeg = encode_bgr_frame_as_jpeg(frame)                     │
│             await handler.push_video(jpeg)                             │
│         await asyncio.sleep(1.0 / fps)                                 │
│         │                                                              │
│         ▼  (extends: KidsTeacherRealtimeHandler)                       │
│  handler.push_video(jpeg_bytes)                ← NEW method            │
│         │                                                              │
│         ▼  (extends: GeminiRealtimeBackend)                            │
│  backend.send_video(chunk)                     ← NEW method            │
│         │                                                              │
│         ▼                                                              │
│  session.send_realtime_input(                                          │
│      video=types.Blob(data=chunk, mime_type="image/jpeg")              │
│  )                                                                     │
│                                                                        │
│  OpenAI backend: send_video() = no-op (provider gate above never       │
│  starts the camera worker on OpenAI anyway; defense-in-depth).         │
└────────────────────────────────────────────────────────────────────────┘
```

**Why producer thread + async consumer (instead of a single asyncio task):**
- `mini.media.get_frame()` is a sync call. Running it inside the asyncio loop would either block other tasks or require per-tick `run_in_executor` indirection.
- Pollen's working code uses this exact split. The thread polls at ~25 fps so there's always a fresh frame available; the async consumer picks whichever frame is current at each 1-Hz send tick. Old frames are simply overwritten — we never queue.
- Shared `stop_event` keeps teardown symmetric with the existing mic pump.

---

## 5. File plan

### New files

| File | Purpose |
|---|---|
| `src/kids_teacher_camera.py` | `CameraWorker` (daemon thread), `encode_bgr_frame_as_jpeg()` (adapted verbatim from Pollen), `video_sender_loop()` async consumer. Lazy-imports `av` and `numpy`. |
| `tests/test_kids_teacher_camera.py` | Unit tests: worker start/stop, latest-frame under lock, encode sizing, send loop honors fps, send loop exits on stop_event, encode error skipped. |

### Modified files

| File | Change |
|---|---|
| `src/kids_teacher_backend.py` | Add `send_video(chunk)` to the `RealtimeBackend` Protocol (default impl = no-op on OpenAI; documented as gemini-only). |
| `src/kids_teacher_gemini_backend.py` | Implement `send_video()` that calls `self._session.send_realtime_input(video=types.Blob(data=chunk, mime_type="image/jpeg"))`. Reuses the lazy-imported `types` module already held on the backend. |
| `src/kids_teacher_realtime.py` | Add `push_video(jpeg_bytes)` that forwards to `backend.send_video(chunk)` iff session active. No queue — we drop frames rather than buffer on backpressure (unlike audio). |
| `src/robot_kids_teacher.py` | After the mic-pump task is launched: if `provider == "gemini"` and the camera probe succeeds, start `CameraWorker` thread and schedule `video_sender_loop` as a second asyncio task. Shares the same `stop_event` as the mic pump. |
| `profiles/kids_teacher/instructions.txt` | Append a "# When you can see the child" section (FR-V3 + SR-V1 + SR-V3 wording). Keep under 15 lines. Stays locked/immutable. |
| `src/kids_safety_keywords.py` | Add `VISUAL_REDIRECT_KEYWORDS` small set (medication, alcohol, weapon, gun, knife, lighter, matches, pills). |
| `src/kids_safety.py` | One wiring line so `VISUAL_REDIRECT_KEYWORDS` maps to the existing REDIRECT category. |
| README | Document `KIDS_TEACHER_CAMERA_FPS`, `KIDS_TEACHER_CAMERA_RESOLUTION`, `KIDS_TEACHER_CAMERA_JPEG_QUALITY`. No new `KIDS_TEACHER_CAMERA_ENABLED` flag — auto-on with Gemini per decision 7. |

### Untouched (confirming scope)

- `kids_review_store.py` — frames are never retained.
- `kids_teacher_robot_bridge.py` — audio bridge stays independent.
- Any lesson-mode (`robot_teacher.py`) or Whisper/gTTS code.
- `tasks/face-recognition-design.md` — stays a separate future work item.

### Dependencies

- **No new dependency installs.** PyAV (`av`) is already a transitive dependency of the `reachy_mini` SDK. Confirm at implementation time with `pip show av`; if it ever drops from the transitive set, add `av` explicitly to `requirements-robot.txt`.

---

## 6. Config keys (new)

```
KIDS_TEACHER_CAMERA_FPS           1           float; 0.2–5.0 supported, clamped
KIDS_TEACHER_CAMERA_RESOLUTION    480p        360p | 480p | 720p (unknown → 480p + warning)
KIDS_TEACHER_CAMERA_JPEG_QUALITY  3           PyAV qscale (1–31, lower = higher quality)
```

Parsed via the same `_env_bool` / `_env_int` / `_env_float` helpers already used in `main.py` / `robot_kids_teacher.py`. Validation: unknown resolution strings fall back to 480p with one warning log; fps clamped to [0.2, 5.0]; qscale clamped to [1, 31].

---

## 7. Prompt addendum (goes into `profiles/kids_teacher/instructions.txt`)

Draft (final wording written during implementation):

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
  a real or toy gun, alcohol, anything sharp), do not describe it.
  Say calmly: "That one is for grown-ups. Can you show me a toy or a
  book instead?"
- If you cannot see clearly, just keep listening. Do not pretend to
  see.
```

---

## 8. Testing plan (for the follow-up implementation)

| Test | Verifies |
|---|---|
| `test_camera_worker_starts_and_stops` | Worker thread starts, polls, exits cleanly on `stop_event` |
| `test_camera_worker_stores_latest_frame_under_lock` | `get_latest_frame()` returns most-recent frame; concurrent read safe |
| `test_camera_worker_handles_get_frame_exception` | SDK raises → worker logs and keeps polling; returns `None` until next good frame |
| `test_encode_bgr_frame_as_jpeg_produces_valid_jpeg` | Output starts with JPEG magic `\xff\xd8`, decodes via PIL |
| `test_encode_respects_resolution` | 480p input → H×W within tolerance |
| `test_video_sender_loop_honors_fps` | At fps=2.0 over 1s, ~2 push_video calls observed |
| `test_video_sender_loop_stops_on_event` | `stop_event.set()` → loop exits within one tick |
| `test_video_sender_loop_drops_frame_on_encode_error` | One bad frame → loop logs warning, continues |
| `test_gemini_backend_send_video_uses_video_blob` | `send_realtime_input` called with `video=Blob(mime_type="image/jpeg", ...)` (kwarg name verified) |
| `test_openai_backend_send_video_noop` | `OpenAIRealtimeBackend.send_video()` returns without network call |
| `test_handler_push_video_routes_to_backend` | `handler.push_video(b"jpeg")` → `backend.send_video(b"jpeg")` |
| `test_handler_push_video_ignored_when_not_started` | Pre-session frames dropped silently |
| `test_robot_kids_teacher_skips_camera_on_openai` | Provider=openai → no `CameraWorker` spawned; log emitted |
| `test_safety_visual_redirect_keywords` | Assistant transcript mentioning "medicine" → REDIRECT category triggered |

All mocked; no real camera / real Gemini / real robot needed. Same pattern as `test_kids_teacher_robot_bridge.py`.

---

## 9. Verification (how to prove it works end-to-end)

1. `pytest` — all new tests pass, existing suite stays green.
2. On Pi 5 with Gemini API key: `python src/robot_kids_teacher.py`
   - Hold up an apple → robot says "I see a red apple! Want to hear fun facts about apples?"
   - Child says "yes" → robot shares 2–3 short, age-appropriate facts.
   - Child says "no" → robot acknowledges ("Okay!") and stays silent about visuals.
   - Hold up a medicine bottle → robot redirects to a safe topic.
   - Hold up nothing → robot continues normal audio-only conversation.
3. Confirm no frames landed in `data/kids_review.runtime.v1/` even when `KIDS_REVIEW_TRANSCRIPTS_ENABLED=true`.
4. Swap `KIDS_TEACHER_REALTIME_PROVIDER=openai` → logs "camera disabled: provider=openai", session still runs audio-only.
5. Cover the camera lens → robot continues chatting, does not comment on darkness.
6. Remove the `av` package → one warning log at session start, session continues audio-only.

---

## 10. Open questions (not blockers)

- **Gemini free-tier quota** — at 1 fps for a 15-min session that's ~900 frames. Need to confirm this fits Google's free-tier quota during implementation; if not, dropping the default to 0.5 fps is the first lever.
- **Resolution selection at capture time** — Pollen's `mini.media.get_frame()` returns whatever the camera is configured to deliver. We may need to downscale in `encode_bgr_frame_as_jpeg()` before JPEG encode if the native resolution differs from the requested setting. Confirm native resolution during implementation; add a single `numpy` slice or PyAV `VideoFrame.reformat()` step if needed.
- **Future: face recognition** — intentionally out of scope here. If implemented later per `tasks/face-recognition-design.md`, the camera infrastructure from this feature can be reused (`CameraWorker` is designed to share).
- **Future: motion director integration** — when the robot notices a new object, a "look-at-the-child" head gesture would be natural. Defer to `tasks/plan-motion-director.md` rollout.

---

## What happens next

This document is the requirements + design. It does **not** contain code changes, env-var changes, or dependency changes. Implementation (new files, test suite, prompt update) is a **separate follow-up task** gated by the user.
