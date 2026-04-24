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
    **gated on `_assistant_active`** (set by first `assistant_transcript.delta`
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

- [x] `High` **Emit a barge-in event from `server_content.interrupted=True`**
  in `kids_teacher_gemini_backend.py::_normalize_message`. Done — emits
  `input.speech_started` before `response.done` so the cancel fires while
  `_assistant_active` is still True.
- [x] `High` **Flip `_assistant_active` on first `audio.chunk`**, not just on
  first transcript delta, in `kids_teacher_realtime.py::_on_audio_chunk`. Done.
- [x] `High` **Add a Gemini-backend test** that feeds a fake `LiveServerMessage`
  with `server_content.interrupted=True` and asserts the normalized event
  stream contains a barge-in trigger. Done — plus order-sensitive test that
  `speech_started` precedes `response.done`, plus realtime-handler test that
  audio-first opens the barge-in gate.
- [ ] `Medium` **Verify `flush_output_audio` on the live Reachy SDK.** Once the
  upstream chain is reaching it, confirm `clear_player` (or one of the
  siblings) actually drops the GStreamer appsrc queue. If none of the three
  probed names exist, log at `warning` instead of `debug` so the next on-device
  run surfaces it loudly.
- [ ] `Medium` **Add an on-device acceptance check** (manual for now):
  speak during an assistant response, confirm audio stops within ~300 ms and
  that the log shows `cancelling active assistant response`.

Deprioritized — revisit only if the High fixes don't land the audio stop:
- Speaker flush primitive verification (`clear_player` probe in
  `robot_teacher.py::flush_output_audio`): the live session log proves the
  upstream chain never reached this code, so there is no evidence it's broken.
  Revisit only if, after fixes #1 + #2, the handler logs `cancelling active
  assistant response` but the audio still plays to completion.
- Client-side VAD on the robot mic as a local interrupt shortcut: overkill
  while Gemini is already sending `server_content.interrupted=True` that we're
  just ignoring. Only worth considering if Gemini's own barge-in signal turns
  out to be too slow in practice.

### Kids Teacher Spec Gaps
Source: [tasks/kids-teacher-requirements.md](kids-teacher-requirements.md)

- [ ] `High` Add a real admin-only kids-teacher configuration flow for preferences, restrictions, language settings, session defaults, and precedence-safe policy updates
- [ ] `High` Wire raw child-audio retention end-to-end so `KIDS_REVIEW_AUDIO_ENABLED=true` actually persists review audio artifacts
- [ ] `High` Implement unclear-speech and no-speech fallback behavior so empty/unclear turns trigger clarification or gentle reprompts instead of falling through
- [ ] `Medium` Add a live web kids-teacher path that shares the realtime core instead of only showing status and past sessions
- [ ] `Medium` Wire confidence-based multilingual reply selection into the live runtime, including fallback to the configured default language and support for preference ordering
- [ ] `Medium` Add code-level personal-data screening/redaction for persisted kids-teacher review data instead of relying only on profile instructions

### Face Recognition for Reachy Mini
Design doc: [tasks/face-recognition-design.md](face-recognition-design.md)

- [ ] Use the camera for image recognition and auto-recognize Myra
- [ ] Create `src/face_service.py` — camera capture + identify_person()
- [ ] Create `scripts/enroll_faces.py` — enrollment CLI (enroll / list / remove / verify)
- [ ] Create `tests/test_face_service.py` — unit tests (mocked camera + face_recognition)
- [ ] Modify `src/robot_teacher.py` — add `_identify_and_greet()` + wire into `run_lesson_session()`
- [ ] Update `requirements-robot.txt` — add face_recognition, opencv-python-headless
- [ ] Update `.gitignore` — exclude `faces/encodings.pkl` and `faces/*/`
- [ ] Run full test suite — confirm all tests pass
- [ ] On-Pi verification — enroll, verify, run full session

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
  + delete; no admin routes needed.
- **No DB, no schema, no episode log, no summarizer, no cron.**
- **Integration is one concat** into the existing `instructions` string
  built by `kids_teacher_profile.py` and consumed at
  `kids_teacher_gemini_backend.py:140`.

Checklist:

- [ ] `High` v1: `src/memory_file.py` — `read()`, `append(fact)`,
  `remove(substring)`. Atomic write (tempfile + `os.replace`), `flock` on
  append, missing-file → empty. ~50 lines + tests.
- [ ] `High` v1: Concat memory text into `instructions` in
  `kids_teacher_profile.py`. Extend `tests/test_kids_teacher_profile.py`
  to assert it shows up in the assembled session payload.
- [ ] `High` v1: Soft 4 KB cap with a warning log when exceeded (parent
  prunes manually).
- [ ] `Medium` v2: One-sentence nudge in `instructions.txt` so the Live
  model verbally acknowledges "remember…" requests in real time even
  though the file write is async.
- [ ] `Medium` v2: Session-transcript collector that subscribes to
  `publish_transcript` events (`kids_teacher_realtime.py:319`), keeping
  final lines in memory for the duration of the session.
- [ ] `Medium` v2: `src/text_llm.py` — project-wide configurable
  abstraction for any non-vision / non-audio / non-Live-API LLM call.
  Single `complete(system, user, temperature)` function dispatching on
  `MYRA_TEXT_LLM_PROVIDER` ∈ {`ollama`, `openai`, `gemini`} +
  `MYRA_TEXT_LLM_MODEL`. Cloud-only for v1. Lazy-imports per-provider
  SDK; only new dep is `ollama` (`openai` and `google-genai` already
  present). One fake-client test per provider + dispatcher tests.
  `.env.example` documents all three options.
- [ ] `Medium` v2: `src/memory_summarizer.py` —
  `summarize_session_to_memory(transcript, existing_memory) -> str`
  returning new markdown bullets or `NONE`. Calls `text_llm.complete()`,
  provider-agnostic. Disabled when `MYRA_TEXT_LLM_PROVIDER` is unset.
- [ ] `Medium` v2: Wire end-of-session call into `kids_teacher_flow` —
  invoke summarizer, append result to `memory.md` via `memory_file`.
  Failures log a warning, never block session shutdown.
- [ ] `Low` v3: Real-time `remember` / `forget` tool-call enrichment
  via Gemini Live tool-use plumbing. **Only if v2 surfaces a real
  friction point.**

### Language Lesson Polish

- [ ] Add celebratory jingles
- [ ] Use "let's try again with another word <child name>" when the child gets it wrong
- [ ] For every correct word, ensure there is an encouraging line like "great work <child name>" or similar

### Gemini Flash Live Migration Follow-ups
Amendment: [tasks/kids-teacher-requirements.md § "2026-04-23 Amendment"](kids-teacher-requirements.md)

- [ ] `High` Listen to Gemini's Telugu output: ask the model "respond in Telugu" and judge whether it (a) actually switches languages and (b) sounds acceptable for a 4-year-old learning pronunciation. Evidence conflicts — the Live-API docs list Telugu as supported; a Jan-2026 knowledge-cutoff hedge says it may not be in the native-audio "24 languages" list. Only a real session will settle it.
- [ ] `High` If the Telugu listening check fails: add a `KIDS_TEACHER_GEMINI_LANGUAGE` env var wired into `speech_config.language_code` (per-session) in `build_gemini_live_config`, OR narrow kids-teacher to English-only and drop Telugu from `KIDS_SUPPORTED_LANGUAGES`
- [ ] `Medium` Add `.env.example` documenting `GEMINI_API_KEY`, `KIDS_TEACHER_REALTIME_PROVIDER`, `KIDS_TEACHER_GEMINI_MODEL` (currently undocumented outside the amendment)
- [ ] `Medium` Terraform wiring for `GEMINI_API_KEY` in `infra/secret_manager.tf` + Cloud Run service env so the Gemini path works in deployed environments, not just locally
- [ ] `Low` Revisit the free-tier privacy trade-off (Google may train on child audio on free tier). Either enable billing with a low budget cap, or move to Vertex AI for a ZDR-eligible path, once the app is used beyond the family

---

## Completed

_(nothing yet)_
