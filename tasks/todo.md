# Myra Language Teacher — Task Tracker

## In Progress

### Barge-in doesn't stop the robot audibly when the child says "stop"

Reported: 2026-04-24. User perception: after saying "stop", the robot continues
speaking until the current output audio stream finishes naturally.

**Root cause — confirmed from on-device log (provider=gemini, 2026-04-24):**

The log shows a turn where the assistant replied for ~6 seconds of text
(`18:07:23`–`18:07:29`), followed by a long audio-playback tail during which
the child said "stop" several times:

- `18:07:34` Gemini sent `server_content.interrupted=True` — the earliest
authoritative barge-in signal from the Live API.
- The Gemini backend (`kids_teacher_gemini_backend.py:432`) **only logs** this
signal; it does not emit any normalized event. The realtime handler never
learns the user spoke.
- `18:07:40` Gemini finally delivered `input_transcription: 'Stop. Stop. Stop.'`
— 11 seconds after the response started, **6 seconds after `interrupted=True`**,
and long after `turn_complete`. This is the *only* path that today synthesizes
`input.speech_started` for Gemini (kids_teacher_gemini_backend.py:328).
- No `cancelling active assistant response` log line appears anywhere in the
session. Barge-in literally never fires. `stop_assistant_playback()` /
`flush_output_audio()` are never reached — the audio plays out naturally.

Conclusion: the flush/speaker-primitive questions are a red herring. The chain
breaks at the very first link on the Gemini provider.

Pipeline reference (for context):

- Trigger sources in `src/kids_teacher_realtime.py`:
  - `_on_speech_started()` (:186) — fires on normalized `input.speech_started`,
  **gated on `_assistant_active*`* (set by first `assistant_transcript.delta`
  at :213, NOT by first `audio.chunk` at :228).
  - `_on_input_delta()` (:191) — same gate.
  - `handler.interrupt()` (:107) — same cancel path.
- Backend: `kids_teacher_gemini_backend.py::cancel_response()` (:483) sends
`audio_stream_end=True` (Gemini has no true cancel primitive). Gemini
synthesizes `input.speech_started` from the first transcription delta only.
- Local flush: `kids_teacher_robot_bridge.py::stop_assistant_playback()` (:199)
clears the deque + calls `robot.flush_output_audio()`.
- Speaker flush: `robot_teacher.py::flush_output_audio()` (:819) probes
`clear_player` / `audio.clear_player` / `clear_output_buffer`. Not exercised
in this session because the upstream chain never reached it.

Test coverage today (fakes only — none of these would catch the above):

- `tests/test_kids_teacher_realtime.py` — three barge-in tests, all driven by
`FakeRealtimeBackend`.
- `tests/test_kids_teacher_robot_bridge.py:433` — fake-robot flush assertion.
- `tests/test_robot_teacher.py:168` — fake-media flush assertion.
- **Nothing asserts the Gemini backend translates `server_content.interrupted`
or `input.speech_started` correctly.**

Fix checklist (in priority order):

- `High` **Emit a barge-in event from `server_content.interrupted=True`**
in `kids_teacher_gemini_backend.py::_normalize_message`. Done — emits
`input.speech_started` before `response.done` so the cancel fires while
`_assistant_active` is still True.
- `High` **Flip `_assistant_active` on first `audio.chunk`**, not just on
first transcript delta, in `kids_teacher_realtime.py::_on_audio_chunk`. Done.
- `High` **Add a Gemini-backend test** that feeds a fake `LiveServerMessage`
with `server_content.interrupted=True` and asserts the normalized event
stream contains a barge-in trigger. Done — plus order-sensitive test that
`speech_started` precedes `response.done`, plus realtime-handler test that
audio-first opens the barge-in gate.
- `Medium` **Verify `flush_output_audio` on the live Reachy SDK.** Once the
upstream chain is reaching it, confirm `clear_player` (or one of the
siblings) actually drops the GStreamer appsrc queue. If none of the three
probed names exist, log at `warning` instead of `debug` so the next on-device
run surfaces it loudly.
- `Medium` **Add an on-device acceptance check** (manual for now):
speak during an assistant response, confirm audio stops within ~300 ms and
that the log shows `cancelling active assistant response`.

Deprioritized — revisit only if the High fixes don't land the audio stop:

- Speaker flush primitive verification (`clear_player` probe in
`robot_teacher.py::flush_output_audio`): the live session log proves the
upstream chain never reached this code, so there is no evidence it's broken.
Revisit only if, after fixes #1 + #2, the handler logs `cancelling active assistant response` but the audio still plays to completion.
- Client-side VAD on the robot mic as a local interrupt shortcut: overkill
while Gemini is already sending `server_content.interrupted=True` that we're
just ignoring. Only worth considering if Gemini's own barge-in signal turns
out to be too slow in practice.

### Process aborts with `free(): corrupted unsorted chunks` after `remember_face`

Reported: 2026-04-26 (also reproduced 2026-04-25). Provider=gemini, on-device
Reachy Pi.

**Symptom:** saying anything that triggers the `remember_face` tool ("Remember
my face", "Remember that") consistently crashes the python process within ~1 s
of the partial-transcript log. glibc prints `free(): corrupted unsorted chunks`
to stderr (i.e. malloc detected heap corruption and called `abort()` →
SIGABRT). No Python traceback. `pgrep -af 'robot_kids_teacher'` confirms the
process is gone. `dmesg -T | tail -40` shows no OOM event — the kernel did not
kill it; native code did.

**Logs (verbatim, 2026-04-26):**

```
15:47:38  INFO     [kids_teacher_robot_bridge] child partial transcript: ' This is my round. Remember that.'
15:47:39  INFO     [kids_teacher_gemini_backend] session_resumption_update: new_handle='0a1f0c2e-bcc0-4711-97b2-ebdb1fc5d2bb' resumable=True last_consumed_client_message_index=None
free(): corrupted unsorted chunks
```

(2026-04-25 repro had the same shape — process died right after
`child partial transcript: 'My name is Albi. Remember my face.'`.)

**Analysis:**

`free(): corrupted unsorted chunks` is glibc's malloc guard tripping `abort()`
— this is native-code memory corruption (double-free, use-after-free, or OOB
write in C/C++), not a Python error. The trace ends *before* any
`[kids_teacher_gemini_backend] remember_face …` log line, so either:

1. Gemini was still mid-turn when the process died — the tool call hadn't
   fired yet.
2. The process died inside `asyncio.to_thread(face_service.enroll_from_frame,
   …)` — a thread-side native crash takes the whole process with it, with no
   Python exception.

Hot suspects on this code path:

- **dlib CNN encoder** — `face_recognition.face_encodings(rgb, locations)`
  (`src/face_service.py:132`) lazy-loads `dlib_face_recognition_resnet_model_v1`
  on first call. `face_recognition_models` was newly wired up in `ed83e73` /
  `bb7dcd4`.
- **dlib HOG at full resolution** — `face_recognition.face_locations(rgb,
  model="hog")` (`src/face_service.py:122`). `detect_face_bboxes`
  (sweep-loop tick) deliberately downscales to 480p first
  (`src/face_service.py:218-227`); `enroll_from_frame` does not.
- **Concurrent native code in different threads** — CameraWorker daemon thread
  mutating BGR frames + Gemini video-send calling PyAV MJPEG + face-rec sweep
  calling HOG + dlib CNN load — all sharing the glibc heap.

**Diagnostic next steps (need C stack before patching):**

- `High` Re-run with `PYTHONFAULTHANDLER=1 PYTHONUNBUFFERED=1 python -X
  faulthandler src/robot_kids_teacher.py …` and capture the Python frame
  on abort. Cheap, no rebuild required.
- `High` Run under `gdb --args python src/robot_kids_teacher.py …`; inside
  gdb: `handle SIGABRT stop print pass`, `run`, repro the crash, then
  capture `bt` and `thread apply all bt`. The C-level frame at the abort
  tells us which library has the bug.
- Alternative: `ulimit -c unlimited` to enable core dumps, then
  `coredumpctl list` / `coredumpctl gdb` on the python crash.

**Fix candidates (gated on the C stack):**

- Frames in `libdlib*` / `face_recognition_models` ⇒ upgrade dlib
  (`pip install -U dlib`); pre-warm the CNN model at startup (load once
  outside the concurrent-thread context); downscale the frame in
  `enroll_from_frame` to mirror `detect_face_bboxes`.
- Frames in `libavcodec*` / `libav*` ⇒ serialize JPEG encoding in
  `kids_teacher_camera.encode_bgr_frame_as_jpeg` off the camera-worker
  frame (single dedicated thread, reuse codec context).
- Frames in numpy / Pillow ⇒ frame layout / dtype issue at the BGR→RGB
  swap site.
- Frames in libcamera / Reachy SDK ⇒ file upstream.

May share a root cause with the next entry (concurrent native-code
corruption); diagnosis decides whether it's one fix or two.

### Process aborts mid-session even without `remember_face`

Reported: 2026-04-26. Provider=gemini, on-device Reachy Pi.

**Symptom:** same exit shape as the `remember_face` crash above (process
gone, no Python traceback) but with no face-rec invocation on the path.
Triggered here by a normal barge-in mid-assistant-response, with the child
starting a new utterance (`'अरे'`, Devanagari).

**Logs (verbatim, 2026-04-26):**

```
15:49:54  INFO     [kids_teacher_robot_bridge] assistant partial transcript: ' for me?'
15:49:54  INFO     [kids_teacher_gemini_backend] generation_complete received
15:49:54  INFO     [kids_teacher_gemini_backend] session_resumption_update: new_handle='68c03140-7a7a-4dfb-a753-43c8b69c2bab' resumable=True last_consumed_client_message_index=None
15:49:55  INFO     [kids_teacher_gemini_backend] session_resumption_update: new_handle='4d5864a4-ae72-4672-b2f3-5012ddea1b15' resumable=True last_consumed_client_message_index=None
15:49:55  INFO     [kids_teacher_gemini_backend] server reported interrupted=True
15:49:55  INFO     [kids_teacher_gemini_backend] turn_complete received
15:49:55  INFO     [kids_teacher_gemini_backend] turn 2 ended; awaiting next turn on same session
15:49:55  INFO     [kids_teacher_realtime] cancelling active assistant response (reason=input.speech_started)
15:49:55  INFO     [kids_teacher_gemini_backend] cancel_response invoked
15:49:55  INFO     [kids_teacher_gemini_backend] sent audio_stream_end=True to Gemini Live
15:49:56  INFO     Cleared player queue
15:49:58  INFO     [kids_teacher_realtime] response.done — turn complete
15:49:59  INFO     [kids_teacher_robot_bridge] mic pump heartbeat: sent=11 none=0 (last 2.0s)
15:49:59  INFO     [kids_teacher_gemini_backend] session_resumption_update: new_handle='f9531cb5-48b6-49ca-8a46-d567c8a3aecf' resumable=True last_consumed_client_message_index=None
15:49:59  INFO     [kids_teacher_gemini_backend] input_transcription: first delta of turn text='अरे'
```

**Analysis:**

No `remember_face` on this path → rules out dlib's CNN encoder as the *sole*
cause of the heap corruption seen in the previous entry. Native-code
suspects that run regardless:

- **HOG detector** in `face_service.detect_face_bboxes` (sweep tick ~every
  10 s, **runs synchronously on the asyncio loop**, `src/face_service.py:218`).
- **PyAV / FFmpeg** in `kids_teacher_camera.encode_bgr_frame_as_jpeg` —
  every outgoing video frame to Gemini, ~25 fps, with a fresh
  `av.CodecContext.create("mjpeg", "w")` per call (`src/kids_teacher_camera.py:35-56`).
- **Reachy SDK audio**: `play_audio_streaming` plus the barge-in
  `flush_output_audio` / `clear_player` path. The log shows
  `Cleared player queue` at 15:49:56 (1 s before the trace ends) — that's
  the GStreamer appsrc queue flush.
- **libcamera capture** (`mini.media.get_frame()`) in the CameraWorker
  daemon thread (`src/kids_teacher_camera.py:92-101`).

Notable timing: the trace ends just after a barge-in flush + new turn
opening. That window — `clear_player` + possibly-in-flight audio chunks +
face-rec sweep + video send + new mic delta — is the densest concurrent
native-code period in the session. That fits a thread-safety / use-after-
free bug somewhere in that stack.

Open questions:

- Was the python process confirmed gone (shell prompt back, `pgrep` empty,
  another `free(): corrupted unsorted chunks` in stderr)? Or is the log
  just truncated? If the process was still alive, this is a different bug
  (event loop wedged) and not the same heap-corruption pattern.
- Frequency: does *every* barge-in eventually crash the process, or only
  some? A repro rate would tell us how synchronous the trigger is.

**Diagnostic next steps:** identical to the previous entry —
`PYTHONFAULTHANDLER=1` + `python -X faulthandler`, plus `gdb` `bt` /
core-dump, to capture the C frame at the abort. The two issues likely
share a single root cause (concurrent native-code heap corruption) but
the stack trace decides whether it's one fix or two.

### Process exits with `Segmentation fault` mid-session (turn 17, post-barge-in)

Reported: 2026-04-26. Provider=gemini, on-device Reachy Pi.

**Symptom:** same family as the two crashes above (process gone, no Python
traceback) but a **different signal**: glibc/Linux prints `Segmentation
fault` (SIGSEGV) instead of `free(): corrupted unsorted chunks` (SIGABRT).
Trigger pattern matches the previous entry — a barge-in turn-change with
the child opening the next turn, no `remember_face` involved. The session
had completed 17 turns before the crash, so this is **accumulated** native-
code stress, not a cold-start failure.

**Logs (verbatim, 2026-04-26):**

```
15:55:48  INFO     [kids_teacher_gemini_backend] session_resumption_update: new_handle='f6723c98-…'
15:55:49  INFO     [kids_teacher_gemini_backend] session_resumption_update: new_handle='9f559980-…'
15:55:49  INFO     [kids_teacher_robot_bridge] mic pump heartbeat: sent=43 none=9 (last 2.0s)
15:55:49  INFO     [kids_teacher_robot_bridge] child partial transcript: ' roupa'
15:55:49  INFO     [kids_teacher_realtime] assistant response started
15:55:49  INFO     [kids_teacher_robot_bridge] assistant partial transcript: 'Oh! "Rupa"'
15:55:49  INFO     [kids_teacher_robot_bridge] assistant partial transcript: ' is the'
15:55:49  INFO     [kids_teacher_robot_bridge] assistant partial transcript: ' Telugu'
15:55:49  INFO     [kids_teacher_robot_bridge] assistant partial transcript: ' word'
15:55:49  INFO     [kids_teacher_robot_bridge] assistant partial transcript: ' for "face"!'
15:55:49  INFO     [kids_teacher_robot_bridge] assistant partial transcript: " That's"
15:55:49  INFO     [kids_teacher_gemini_backend] session_resumption_update: new_handle='a38da919-…'
15:55:49  INFO     Robot motion recovered.
15:55:50  INFO     [kids_teacher_gemini_backend] server reported interrupted=True
15:55:50  INFO     [kids_teacher_gemini_backend] turn_complete received
15:55:50  INFO     [kids_teacher_gemini_backend] turn 17 ended; awaiting next turn on same session
15:55:50  INFO     [kids_teacher_robot_bridge] assistant partial transcript: ' right.'
15:55:50  INFO     [kids_teacher_robot_bridge] assistant partial transcript: ' Or'
15:55:50  INFO     [kids_teacher_realtime] cancelling active assistant response (reason=input.speech_started)
15:55:50  INFO     [kids_teacher_gemini_backend] cancel_response invoked
15:55:50  INFO     [kids_teacher_gemini_backend] sent audio_stream_end=True to Gemini Live
15:55:50  INFO     Cleared player queue
15:55:52  INFO     [kids_teacher_realtime] response.done — turn complete
15:55:53  INFO     [kids_teacher_robot_bridge] mic pump heartbeat: sent=18 none=0 (last 2.0s)
15:55:53  INFO     [kids_teacher_gemini_backend] session_resumption_update: new_handle='6bbf0cf7-…'
15:55:53  INFO     [kids_teacher_gemini_backend] session_resumption_update: new_handle='907c38e6-…'
15:55:53  INFO     [kids_teacher_gemini_backend] input_transcription: first delta of turn text='into the room.'
15:55:53  INFO     [kids_teacher_robot_bridge] child partial transcript: 'into the room.'
Segmentation fault
```

**Second repro (2026-04-26, turn 1 — disproves "accumulated stress only"):**

```
16:09:09  INFO     [robot_kids_teacher] face-rec announce unknown arrival
16:09:09  INFO     [kids_teacher_gemini_backend] session_resumption_update: new_handle='d39fc7c2-…'
16:09:09  INFO     [kids_teacher_gemini_backend] server reported interrupted=True
16:09:09  INFO     [kids_teacher_gemini_backend] turn_complete received
16:09:09  INFO     [kids_teacher_gemini_backend] turn 1 ended; awaiting next turn on same session
16:09:09  INFO     Robot motion timed out during speak nod up; retrying once with 0.60s duration.
16:09:10  INFO     [kids_teacher_realtime] cancelling active assistant response (reason=input.speech_started)
16:09:10  INFO     [kids_teacher_gemini_backend] cancel_response invoked
16:09:10  INFO     [kids_teacher_gemini_backend] sent audio_stream_end=True to Gemini Live
16:09:10  INFO     Cleared player queue
16:09:11  INFO     [kids_teacher_realtime] response.done — turn complete
16:09:12  INFO     [kids_teacher_robot_bridge] mic pump heartbeat: sent=4 none=0 (last 2.0s)
16:09:12  INFO     [kids_teacher_gemini_backend] session_resumption_update: new_handle='e2e2a61d-…'
Segmentation fault
```

Same exit signal (SIGSEGV), same trigger pattern (barge-in flush + new turn
opening), but **turn 1** instead of turn 17 — i.e. the bug can fire on the
very first barge-in. So whatever is corrupting the heap is **not** simply
N-turns of accumulated drift; it's a per-event hazard that just happens to
miss most of the time.

**Analysis:**

SIGSEGV vs SIGABRT — different signals, same family. They are commonly the
two faces of one heap-corruption story:

- SIGABRT (`free(): corrupted unsorted chunks`) fires when `free()` walks
  the freelist and finds inconsistency. The corruption usually happened
  earlier; `free` just noticed.
- SIGSEGV fires when a pointer is dereferenced and points at garbage —
  often the *consequence* of earlier corruption, which was hidden until a
  later allocation/use stumbled on it.

So Issue 2 (no-face-rec abort) and this one are very likely the **same root
cause** — accumulating heap corruption from concurrent native-code paths
running in the same process. Issue 1 (`remember_face`) may be the same bug
amplified by the heavy dlib CNN load happening to be the first allocation
that walks into a poisoned chunk.

Three signals from these sessions that point more concretely:

1. **Reachy motion timing out** in both repros. Turn-17 trace shows
   `Robot motion recovered.` (15:55:49) — `robot_teacher.RobotController._goto_target`
   (`src/robot_teacher.py:607`) emits this only after a previous
   `mini.goto_target(...)` call timed out and was retried successfully.
   Turn-1 trace shows the failing half: `Robot motion timed out during
   speak nod up; retrying once with 0.60s duration.` (16:09:09, from
   `src/robot_teacher.py:597`). Either way, the Reachy motion subsystem
   (native ZMQ → firmware) is missing its deadlines around the moment of
   the crash. Reachy SDK native code is a strong suspect alongside
   dlib / PyAV.
2. **`face-rec announce unknown arrival`** at 16:09:09 in the turn-1
   repro — the face-rec sweep tick from
   `_make_face_rec_loop_factory._announce` (`src/robot_kids_teacher.py:509-513`).
   That tick called `face_service.detect_face_bboxes` (HOG, downscaled,
   on the asyncio loop) and `face_service.identify_in_frame` (HOG +
   `face_recognition.face_encodings` if `faces.pkl` was non-empty —
   the CNN encoder load) within ~1 s of the segfault. Strongest direct
   evidence yet that the face-rec native path is part of the bad window.
3. **Turn 1 segfault disproves cold-start immunity.** The turn-17 +
   turn-1 pair together means the per-event hazard is dense enough to
   hit on a single barge-in if the right concurrent native calls overlap.
   Steady-state suspects active at *every* turn boundary:
   - PyAV `av.CodecContext.create("mjpeg", "w")` + `codec.encode(...)` /
     `codec.encode(None)` cycle once per outgoing frame at ~25 fps
     (`src/kids_teacher_camera.py:35-56`) — fresh codec context per frame.
   - HOG sweep tick on the asyncio loop every ~10 s
     (`src/face_service.py:218`).
   - Reachy SDK audio: `play_audio_streaming` + `flush_output_audio` /
     `clear_player` on every barge-in (both traces show `Cleared player
     queue` 1–2 s before the segfault).
   - Reachy SDK motion: `goto_target` and the head-pose stream
     (timing out in both traces).

Open questions:

- Repro rate: out of N barge-ins, how often does this happen? Turn-1 +
  turn-17 in the same day says it's not rare, but "every barge-in
  eventually" vs "1 in 5" changes the urgency.
- Does the crash still happen with **video disabled** (no `CameraWorker`)?
  Setting `KIDS_TEACHER_REALTIME_PROVIDER=openai` skips the camera worker
  entirely (`src/robot_kids_teacher.py:707-709`); if Issues 2/3 don't
  reproduce on the OpenAI backend, the camera + PyAV + face-rec stack is
  the prime suspect even without a stack trace.
- Does the crash still happen with **face-rec disabled** (e.g. setting
  the sweep interval to a very large number, or short-circuiting
  `_make_face_rec_loop_factory` to a no-op)? Given the turn-1 repro
  fired right after the face-rec announce, this is the cheapest
  bisection — if the segfault disappears when the sweep doesn't run,
  `face_service.detect_face_bboxes` / `identify_in_frame` is the
  smoking gun.

**Diagnostic next steps:** identical to the two previous entries — capture
the C-level `bt` from `gdb` (or `coredumpctl gdb` on a saved core dump,
which is more practical for an intermittent turn-N crash than running
under gdb live). For SIGSEGV specifically, `info registers` and `disas
$pc-32,$pc+32` at the crash frame help identify the exact instruction —
worth running alongside `bt`/`thread apply all bt`.

If diagnosis confirms a shared heap-corruption root cause across Issues
1, 2, and 3, fold them into a single fix; otherwise treat them
independently per the C stack each one produces.

### Gemini Live `GoAway` → 1008 close not handled — no reconnect, fallback line on loop

Reported: 2026-04-26. Provider=gemini, on-device Reachy Pi.

**Different bug class from issues 1–3:** the **process is alive** (mic-pump
heartbeats continue firing at 16:06:43, :50, :53). What died is the
**Gemini Live session itself** — the server sent a `GoAway` signal because
it had reached the max session duration, then dropped the WebSocket with
close code **1008 (policy violation)** when the client kept the connection
open. The backend has no reconnect logic, so it hammers `send_audio` at
the closed socket and the child hears `_FALLBACK_ASSISTANT_LINE`
(`"Let me try that again in a moment."`) on loop.

**Logs (verbatim, 2026-04-26):**

```
16:06:42  INFO     [kids_teacher_robot_bridge] assistant partial transcript: ' up can'
16:06:42  INFO     [kids_teacher_robot_bridge] assistant partial transcript: ' tell'
…
16:06:43  INFO     [kids_teacher_robot_bridge] mic pump heartbeat: sent=25 none=0 (last 2.0s)
16:06:43  INFO     [kids_teacher_robot_bridge] assistant partial transcript: ' a picture'
16:06:43  INFO     [kids_teacher_robot_bridge] assistant partial transcript: ' of a baby'
16:06:43  WARNING  [kids_teacher_gemini_backend] reader loop error (session likely dead): 1008 None. Connection aborted because the client failed to close the connection after receiving a GoAway signal once the session durat
16:06:43  WARNING  [kids_teacher_robot_bridge] session error: 1008 None. <same>
16:06:44  INFO     [kids_teacher_robot_bridge] assistant final transcript: 'Let me try that again in a moment.'
16:06:47  WARNING  [kids_teacher_gemini_backend] Gemini Live session dropped (first send failure — likely keepalive timeout or server disconnect): received 1008 (policy violation) … ; then sent 1008 (policy violation) …
16:06:47  INFO     [kids_teacher_robot_bridge] assistant final transcript: 'Let me try that again in a moment.'
16:06:50  WARNING  [kids_teacher_gemini_backend] send_audio failed (#2, session still dead): … 1008 …
16:06:50  INFO     [kids_teacher_robot_bridge] assistant final transcript: 'Let me try that again in a moment.'
16:06:53  WARNING  Robot motion failed during idle sway left: TimeoutError …
16:06:53  WARNING  [kids_teacher_gemini_backend] send_audio failed (#3, session still dead): … 1008 …
16:06:55  WARNING  [kids_teacher_gemini_backend] send_audio failed (#4, session still dead): … 1008 …
16:06:55  INFO     [kids_teacher_robot_bridge] assistant final transcript: 'Let me try that again in a moment.'
```

(The 1008 message is truncated mid-word in the SDK error string — the full
text is "…once the session duration was reached".)

**Analysis:**

The 1008 error string ("Connection aborted because the client failed to
close the connection after receiving a GoAway signal once the session
duration was reached") is Gemini Live's max-session-duration enforcement.
Gemini emits a `BidiGenerateContentServerMessage.go_away` a few seconds
before the deadline so clients can close cleanly and (optionally) reopen
with a `session_resumption.handle` to keep the conversation context. If
the client doesn't close, the server force-drops with 1008.

The backend already **observes** both signals but takes **no action**:

- `_normalize_message` logs `go_away` events
  (`src/kids_teacher_gemini_backend.py:888-893`) and discards them.
- It logs `session_resumption_update` handles
  (`src/kids_teacher_gemini_backend.py:873-886`) but never **caches** the
  latest `new_handle`.
- `send_audio` (`src/kids_teacher_gemini_backend.py:1020-1042`) on
  failure increments a counter, emits an `error` event, and returns —
  the session field is never marked dead, so subsequent calls keep
  hammering the closed WebSocket. The log shows failures #1 → #4 in
  ~12 seconds.
- The reader loop logs `reader loop error (session likely dead)` and
  exits (`src/kids_teacher_gemini_backend.py:526-531`); there is no
  reconnect attempt.
- Each error event drives `kids_teacher_realtime._on_error`
  (`src/kids_teacher_realtime.py:293-306`), which emits
  `_FALLBACK_ASSISTANT_LINE` and returns to LISTENING — so the child
  hears the fallback transcript four times in eleven seconds.

`Robot motion failed during idle sway left: TimeoutError` at 16:06:53 is
unrelated to the 1008 — it's the Reachy motion subsystem timing out on a
goto_target call (same path as the "Robot motion recovered" line in
issue 3). Worth noting because it suggests the device is also under SDK
stress in this window.

**Fix checklist:**

- `High` Cache the most recent `session_resumption.new_handle` on every
  `session_resumption_update` event in the Gemini backend.
- `High` On `go_away`, proactively close the current session via the
  connection context manager and immediately re-enter with
  `session_resumption.handle=<cached>` on the new `LiveConnectConfig`
  (the `google-genai` SDK supports this through
  `types.SessionResumptionConfig`). Do this BEFORE the deadline so the
  child never sees a 1008.
- `High` When `_session.send_realtime_input` raises, mark the session
  dead (`self._session = None` or a `_session_alive` flag) and short-
  circuit subsequent `send_audio` / `send_video` calls instead of
  retrying against the closed socket — silences the "send_audio failed
  (#N)" spam.
- `High` After session-dead, kick off a reconnect with the cached handle
  on the same backend instance; only surface a single error event to
  the realtime handler if the reconnect itself fails.
- `Medium` Rate-limit / dedupe fallback `_FALLBACK_ASSISTANT_LINE`
  emissions in `kids_teacher_realtime._on_error` — emit once per
  contiguous error stretch, not per error event. Otherwise multiple
  reconnect-time errors stack up audibly.
- `Medium` Differentiate "session ended, reconnecting" vs generic
  backend error so the fallback line either matches the situation or
  the robot stays silent during the brief reconnect window.
- `Low` Backend-side test using a fake SDK: emit a `go_away` then close
  the connection, assert the backend (a) caches the latest
  `session_resumption_update` handle, (b) reconnects with that handle,
  (c) does NOT emit a stream of send_audio failures.
- `Low` Document Gemini Live's max session duration in
  `tasks/kids-teacher-requirements.md` and link the SDK doc for
  `session_resumption`.

**Open questions:**

- What's the actual session-duration cap? Need the session-start time vs.
  16:06:43 to compute it. The Gemini Live docs list 10 min for native-
  audio half-cascade — confirm against this trace.
- Does reconnect-with-handle preserve **conversation context** (system
  prompt, prior turns) or does the model start cold? Kids-teacher
  experience needs context preservation, otherwise the assistant forgets
  mid-conversation and the fix only solves part of the problem.
- Should we proactively recycle the session every N minutes regardless of
  `go_away`, to avoid being right at the edge?

Independent of the heap-corruption issues 1–3 — separate root cause,
separate fix.

### Kids Teacher Spec Gaps

Source: [tasks/kids-teacher-requirements.md](kids-teacher-requirements.md)

- `High` Add a real admin-only kids-teacher configuration flow for preferences, restrictions, language settings, session defaults, and precedence-safe policy updates
- `High` Wire raw child-audio retention end-to-end so `KIDS_REVIEW_AUDIO_ENABLED=true` actually persists review audio artifacts
- `High` Implement unclear-speech and no-speech fallback behavior so empty/unclear turns trigger clarification or gentle reprompts instead of falling through
- `Medium` Add a live web kids-teacher path that shares the realtime core instead of only showing status and past sessions
- `Medium` Wire confidence-based multilingual reply selection into the live runtime, including fallback to the configured default language and support for preference ordering
- `Medium` Add code-level personal-data screening/redaction for persisted kids-teacher review data instead of relying only on profile instructions

### Face Recognition for Reachy Mini

Design doc: [tasks/face-recognition-design.md](face-recognition-design.md)

- Use the camera for image recognition and auto-recognize Myra
- Create `src/face_service.py` — camera capture + identify_person()
- Create `scripts/enroll_faces.py` — enrollment CLI (enroll / list / remove / verify)
- Create `tests/test_face_service.py` — unit tests (mocked camera + face_recognition)
- Modify `src/robot_teacher.py` — add `_identify_and_greet()` + wire into `run_lesson_session()`
- Update `requirements-robot.txt` — add face_recognition, opencv-python-headless
- Update `.gitignore` — exclude `faces/encodings.pkl` and `faces/*/`
- Run full test suite — confirm all tests pass
- On-Pi verification — enroll, verify, run full session

### Visual Commands Beyond Primary-Child Tracking

Extends: [tasks/camera-object-recognition-design.md](camera-object-recognition-design.md)

- `High` Add adult-directed visual command support so the robot can act on room-level instructions instead of only following the primary child. Example: if an adult says "Find the books close by and read that to the child," the robot should look for a nearby book, choose the likely target, and use Gemini to inspect and understand the visible book/story content before speaking.
- `Medium` Add a child-friendly read-aloud / discussion flow for visually grounded books and printed materials: after understanding the book, the robot should summarize or read age-appropriate content and start a topic about it with the child.
- `Medium` Keep this as a general visual-task capability, not a book-only special case: when an adult references nearby objects in the room, the robot should use camera grounding plus a short clarification question when the target is ambiguous.

### Persistent Memory ("Robot That Remembers Myra")

Design doc: [tasks/plan-persistent-memory.md](plan-persistent-memory.md)

Goal: kids-teacher flow on Reachy Mini remembers things about Myra across
sessions — her brother's name, that she loves tigers, the inside joke from
yesterday.

Scope: **Reachy-only, kids-teacher only, single child per device.** Memory
is *only* enriched when a human asks (parent edits the file, or — in v2 —
the child/parent says "remember that…" and the LLM calls a tool). Memory
is **not** the same as mastery tracking; spaced repetition is a separate
deferred feature.

Key design choices (see plan doc):

- **One markdown file** at `~/.myra/memory.md` (override via
`MYRA_MEMORY_FILE`). The file *is* the system-prompt preamble.
- **Parent-readable, parent-editable.** `cat`/`vim` covers privacy + audit
  - delete; no admin routes needed.
- **No DB, no schema, no episode log, no summarizer, no cron.**
- **Integration is one concat** into the existing `instructions` string
built by `kids_teacher_profile.py` and consumed at
`kids_teacher_gemini_backend.py:140`.

Checklist:

- `High` v1: `src/memory_file.py` — `read()`, `append(fact)`,
`remove(substring)`. Atomic write (tempfile + `os.replace`), `flock` on
append, missing-file → empty. ~50 lines + tests.
- `High` v1: Concat memory text into `instructions` in
`kids_teacher_profile.py`. Extend `tests/test_kids_teacher_profile.py`
to assert it shows up in the assembled session payload.
- `High` v1: Soft 4 KB cap with a warning log when exceeded (parent
prunes manually).
- `Medium` v2: One-sentence nudge in `instructions.txt` so the Live
model verbally acknowledges "remember…" requests in real time even
though the file write is async.
- `Medium` v2: Session-transcript collector that subscribes to
`publish_transcript` events (`kids_teacher_realtime.py:319`), keeping
final lines in memory for the duration of the session.
- `Medium` v2: `src/text_llm.py` — project-wide configurable
abstraction for any non-vision / non-audio / non-Live-API LLM call.
Single `complete(system, user, temperature)` function dispatching on
`MYRA_TEXT_LLM_PROVIDER` ∈ {`ollama`, `openai`, `gemini`} +
`MYRA_TEXT_LLM_MODEL`. Cloud-only for v1. Lazy-imports per-provider
SDK; only new dep is `ollama` (`openai` and `google-genai` already
present). One fake-client test per provider + dispatcher tests.
`.env.example` documents all three options.
- `Medium` v2: `src/memory_summarizer.py` —
`summarize_session_to_memory(transcript, existing_memory) -> str`
returning new markdown bullets or `NONE`. Calls `text_llm.complete()`,
provider-agnostic. Disabled when `MYRA_TEXT_LLM_PROVIDER` is unset.
- `Medium` v2: Wire end-of-session call into `kids_teacher_flow` —
invoke summarizer, append result to `memory.md` via `memory_file`.
Failures log a warning, never block session shutdown.
- `Low` v3: Real-time `remember` / `forget` tool-call enrichment
via Gemini Live tool-use plumbing. **Only if v2 surfaces a real
friction point.**

### memory.md ⇄ faces.pkl Linkage Hardening

Context: `faces.pkl` keys on the person's `name` (string). `memory.md`
relationship notes are written as `f"{name} {relationship}"` so the body
*starts with* that name, and `forget_face` cleans the line via
`memory_file.remove_notes_starting_with(name)` (case-insensitive prefix
match on the body). The cross-store linkage is purely lexical — both
sides must agree on the name string.

Issues surfaced after commit `3690d30` (relationship notes now go through
the reconciler):

- `High` Add a prompt rule to `memory_reconciler._SYSTEM_PROMPT`: when
the original note(s) started with a person's name, the merged/replaced
text must continue to start with that exact name string. Otherwise a
`merge`/`replace` could rewrite `"Aunt Priya is Myra's aunt"` into
`"Myra's aunt Priya likes mangoes"` and `forget_face("Aunt Priya")`
would silently leave the line behind.
- `Medium` Pre-existing asymmetry in `_handle_forget_face_call`:
`face_service.forget(name)` does exact-match `del encodings[name]`
while `remove_notes_starting_with(name)` casefolds. Mixed-case input
(`"aunt priya"`) cleans memory.md but leaves the encoding orphaned
(the resulting `KeyError` is caught and treated as `removed_face=False`).
Either lowercase the name on enroll/forget or apply the same
normalization both sides.
- `Low` Stretch goal: make the linkage structural rather than lexical
— tag relationship notes in memory.md (e.g. `[person:Aunt Priya] is
Myra's aunt`) and have `forget_face` look up by tag. More invasive but
removes the prefix-preservation requirement entirely.

### Language Lesson Polish

- Add celebratory jingles
- Use "let's try again with another word " when the child gets it wrong
- For every correct word, ensure there is an encouraging line like "great work " or similar

### Gemini Flash Live Migration Follow-ups

Amendment: [tasks/kids-teacher-requirements.md § "2026-04-23 Amendment"](kids-teacher-requirements.md)

- `High` Listen to Gemini's Telugu output: ask the model "respond in Telugu" and judge whether it (a) actually switches languages and (b) sounds acceptable for a 4-year-old learning pronunciation. Evidence conflicts — the Live-API docs list Telugu as supported; a Jan-2026 knowledge-cutoff hedge says it may not be in the native-audio "24 languages" list. Only a real session will settle it.
- `High` If the Telugu listening check fails: add a `KIDS_TEACHER_GEMINI_LANGUAGE` env var wired into `speech_config.language_code` (per-session) in `build_gemini_live_config`, OR narrow kids-teacher to English-only and drop Telugu from `KIDS_SUPPORTED_LANGUAGES`
- `Medium` Add `.env.example` documenting `GEMINI_API_KEY`, `KIDS_TEACHER_REALTIME_PROVIDER`, `KIDS_TEACHER_GEMINI_MODEL` (currently undocumented outside the amendment)
- `Medium` Terraform wiring for `GEMINI_API_KEY` in `infra/secret_manager.tf` + Cloud Run service env so the Gemini path works in deployed environments, not just locally
- `Low` Revisit the free-tier privacy trade-off (Google may train on child audio on free tier). Either enable billing with a low budget cap, or move to Vertex AI for a ZDR-eligible path, once the app is used beyond the family

---

## Completed

*(nothing yet)*
