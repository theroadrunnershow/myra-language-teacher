# Myra Language Teacher — Task Tracker

## In Progress

### `memory_reconciler` dedup silently disabled — pre-flight + short-circuit still missing

Reported: 2026-04-26. Provider=gemini, on-device Reachy Pi.

**Status:** `text_llm` provider dispatch is in (`src/text_llm.py` supports
ollama / openai / gemini), but the Pi default is still `ollama`, the daemon
isn't running, and the reconciler logs a WARNING and falls back to plain
`append` on every memory write. Dedup is silently disabled.

Remaining work:

- `High` Set `MYRA_TEXT_LLM_PROVIDER=gemini` on the Pi and document it in
  `.env.example` so the reconciler actually has a working backend.
- `Medium` One-shot pre-flight `text_llm.complete(...)` in
  `robot_kids_teacher.main`. On failure log a single WARNING and set a
  module-level `_DEDUP_DISABLED` flag.
- `Medium` In `memory_reconciler.add_note`, short-circuit to
  `memory_file.append_note` directly when `_DEDUP_DISABLED` is set; demote
  the per-write `[memory_reconciler] LLM call failed` log to DEBUG.
- `Medium` Test (fakes): connection-refused completer → assert one WARNING
  total after pre-flight, not one per write.
- `Low` `scripts/dedup_memory.py` to replay accumulated `memory.md` lines
  through the reconciler once a working provider is wired up.

Sanity-check first: `which ollama && systemctl --user status ollama`. If
ollama was meant to be running and just failed to start, restarting it may
be the right answer instead of switching providers.

### Kids Teacher Spec Gaps

Source: [tasks/kids-teacher-requirements.md](kids-teacher-requirements.md)

- `High` Add a real admin-only kids-teacher configuration flow for preferences, restrictions, language settings, session defaults, and precedence-safe policy updates
- `High` Wire raw child-audio retention end-to-end so `KIDS_REVIEW_AUDIO_ENABLED=true` actually persists review audio artifacts
- `High` Implement unclear-speech and no-speech fallback behavior so empty/unclear turns trigger clarification or gentle reprompts instead of falling through
- `Medium` Add a live web kids-teacher path that shares the realtime core instead of only showing status and past sessions
- `Medium` Wire confidence-based multilingual reply selection into the live runtime, including fallback to the configured default language and support for preference ordering
- `Medium` Add code-level personal-data screening/redaction for persisted kids-teacher review data instead of relying only on profile instructions

### Kids Teacher Tool Execution Layer

Source: [tasks/plan-kids-tutor-skill.md § "Why pure system-prompt is the only viable v1"](plan-kids-tutor-skill.md#why-pure-system-prompt-is-the-only-viable-v1)

`kids_teacher_backend.py:136-141` currently sends placeholder tool specs as
`{"type": "function", "name": name}` with no parameter schemas and no executor.
The code comment explicitly defers real tool-spec lookup to the future
integration phase. That made a pure system-prompt language lesson the only
viable v1, because a `get_lesson_word(language, category)` tool would require
building the full tool-use layer first.

- `High` Replace placeholder tool specs with provider-ready function declarations, including names, descriptions, and JSON parameter schemas for each enabled kids-teacher tool.
- `High` Add a real tool-call executor in the kids-teacher realtime path that validates arguments, dispatches to registered Python handlers, returns structured tool results to OpenAI Realtime / Gemini Live, and logs failures without crashing the session.
- `High` Design a small provider-neutral tool registry so OpenAI and Gemini share the same canonical tool definitions instead of duplicating schemas in backend-specific code.
- `Medium` Add tests for tool schema generation, argument validation, successful dispatch, handler exceptions, and provider-specific response/result serialization.
- `Medium` Once the layer exists, revisit language-lesson tools such as `get_lesson_word(language, category)` and pronunciation helpers instead of relying entirely on prompt-side word selection and counting.

### Visual Commands Beyond Primary-Child Tracking

Extends: [tasks/camera-object-recognition-design.md](camera-object-recognition-design.md)

- `High` Add adult-directed visual command support so the robot can act on room-level instructions instead of only following the primary child. Example: if an adult says "Find the books close by and read that to the child," the robot should look for a nearby book, choose the likely target, and use Gemini to inspect and understand the visible book/story content before speaking.
- `Medium` Add a child-friendly read-aloud / discussion flow for visually grounded books and printed materials: after understanding the book, the robot should summarize or read age-appropriate content and start a topic about it with the child.
- `Medium` Keep this as a general visual-task capability, not a book-only special case: when an adult references nearby objects in the room, the robot should use camera grounding plus a short clarification question when the target is ambiguous.

### Persistent Memory v2 — session-end summarizer

Design doc: [tasks/plan-persistent-memory.md](plan-persistent-memory.md)

v1 is in (memory file + profile concat + 8 KB cap). Remaining v2 work:

- `Medium` v2: One-sentence nudge in `instructions.txt` so the Live
  model verbally acknowledges "remember…" requests in real time even
  though the file write is async.
- `Medium` v2: Session-transcript collector that subscribes to
  `publish_transcript` events (`kids_teacher_realtime.py:319`), keeping
  final lines in memory for the duration of the session.
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

Remaining issues (the High prompt-rule item landed in
`memory_reconciler._SYSTEM_PROMPT`):

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

- Add celebratory jingles — **done** (`src/robot_teacher.py:846`)
- Use "let's try again with another word " when the child gets it wrong
- For every correct word, ensure there is an encouraging line like "great work " or similar

### Gemini Flash Live Migration — language_code wiring + env docs

Amendment: [tasks/kids-teacher-requirements.md § "2026-04-23 Amendment"](kids-teacher-requirements.md)

Most of the migration landed; remaining follow-ups:

- `High` Listen to Gemini's Telugu output: ask the model "respond in Telugu" and judge whether it (a) actually switches languages and (b) sounds acceptable for a 4-year-old learning pronunciation. Evidence conflicts — the Live-API docs list Telugu as supported; a Jan-2026 knowledge-cutoff hedge says it may not be in the native-audio "24 languages" list. Only a real session will settle it.
- `High` If the Telugu listening check fails: add a `KIDS_TEACHER_GEMINI_LANGUAGE` env var wired into `speech_config.language_code` (per-session) in `build_gemini_live_config`, OR narrow kids-teacher to English-only and drop Telugu from `KIDS_SUPPORTED_LANGUAGES`. Today `build_gemini_live_config` does not set `language_code` at all.
- `Medium` Add `.env.example` documenting `GEMINI_API_KEY`, `KIDS_TEACHER_REALTIME_PROVIDER`, `KIDS_TEACHER_GEMINI_MODEL` (currently undocumented outside the amendment)
- `Medium` Terraform wiring for `GEMINI_API_KEY` in `infra/secret_manager.tf` + Cloud Run service env so the Gemini path works in deployed environments, not just locally
- `Low` Revisit the free-tier privacy trade-off (Google may train on child audio on free tier). Either enable billing with a low budget cap, or move to Vertex AI for a ZDR-eligible path, once the app is used beyond the family

### Voice strength filter

### Animations fixing

### Face tracking

### Gemini Live reconnect polish

Auto-reconnect with session resumption is in (commit `73cdabb`,
`src/kids_teacher_gemini_backend.py:455-617`). Remaining tightening:

- `Medium` Track session liveness explicitly (`_session_alive` flag flipped
  False on send-side exception, True on successful reconnect) so
  `send_audio` / `send_video` short-circuit to a single DEBUG log instead
  of repeatedly hitting the dead socket and emitting `send_audio failed
  (#N)` WARNINGs.
- `Medium` In `kids_teacher_realtime._on_error` dedupe the
  `_FALLBACK_ASSISTANT_LINE` so multiple errors during a reconnect window
  produce one playback, not four.

---

## Completed

### Barge-in doesn't stop the robot audibly when the child says "stop"

Reported: 2026-04-24. The Gemini backend was logging
`server_content.interrupted=True` and discarding it; the realtime
handler's `_assistant_active` gate also missed the audio-only window
(only flipped on the first transcript delta). Fix landed:

- `src/kids_teacher_gemini_backend.py::_normalize_message` now emits
  `input.speech_started` on `interrupted=True`, ordered before
  `response.done` so the cancel fires while the gate is still open.
- `src/kids_teacher_realtime.py::_on_audio_chunk` flips
  `_assistant_active=True` on the first audio chunk, not just on the
  first transcript delta.
- New tests in `tests/test_kids_teacher_gemini_backend.py` and
  `tests/test_kids_teacher_realtime.py` cover the interrupted→barge-in
  event, ordering vs `response.done`, and the audio-first gate.

Deferred (manual on-device): verify `flush_output_audio` clears the
GStreamer appsrc queue on the live Reachy SDK, and a manual acceptance
run that confirms audio stops within ~300 ms of "stop".

### Native heap corruption / segfaults from concurrent dlib HOG

Reported: 2026-04-26. Three traces (`free(): corrupted unsorted chunks`
after `remember_face`, an abort with no face-rec on the path, and a
SIGSEGV at turn 17 / turn 1) all turned out to be the same root cause:
dlib HOG is not thread-safe and was being entered concurrently by the
asyncio loop (gaze tracker at 3 Hz, face-rec sweep) and worker threads
(`asyncio.to_thread(face_service.enroll_from_frame, …)`). Confirmed via
`PYTHONFAULTHANDLER=1 python -X faulthandler` showing two threads inside
`_raw_face_locations` at the abort. Fix landed:

- `src/face_service.py`: module-level `threading.Lock` (`_DLIB_LOCK`)
  around every dlib entry point (HOG + CNN encoder), threaded through a
  shared `_locate_in_rgb` helper used by `enroll_from_frame`,
  `identify_in_frame`, and `detect_face_bboxes`.
- `enroll_from_frame` now downscales to ≤480p for HOG before acquiring
  the lock, then runs the encoder on the full-res frame with rescaled
  bboxes — drops worst-case lock-hold from ~500 ms to ~100 ms.
- `face_service.prewarm()` called from `robot_kids_teacher.main` (gemini
  path) lazy-loads the CNN encoder at startup before the gaze loop /
  face-rec sweep / any tool-call worker can race for the lock.
- `tests/test_face_service.py` adds regressions for concurrent
  serialization (`detect`+`detect`, `enroll`+`detect`), prewarm, and the
  enroll downscale path.

The PyAV "fresh codec context per frame" hardening (reuse codec context,
`to_thread` the encode) is no longer load-bearing for crash prevention
and remains a fine follow-up for allocator hygiene only.

### Gemini Live `GoAway` → 1008 close — auto-reconnect with session resumption

Reported: 2026-04-26. Gemini Live emits `BidiGenerateContentServerMessage.go_away`
a few seconds before the max-session-duration deadline; the backend was
logging both `go_away` and `session_resumption_update` events but storing
no state and never reconnecting, so the WebSocket got force-dropped at
1008 and the child heard `_FALLBACK_ASSISTANT_LINE` on loop. Fix landed
in commit `73cdabb`:

- `src/kids_teacher_gemini_backend.py` caches the latest
  `session_resumption.new_handle`, schedules an `_attempt_reconnect()`
  on `go_away`, and re-enters `client.aio.live.connect(...)` with
  `SessionResumptionConfig(handle=…)` to preserve conversation context.
- New tests in `tests/test_kids_teacher_gemini_backend.py`
  (`test_reader_loop_disconnect_triggers_reconnect_with_handle`,
  `test_reconnect_falls_back_to_fresh_when_handle_rejected`,
  `test_reconnect_gives_up_after_max_attempts_and_emits_single_error`).

Remaining polish (explicit `_session_alive` flag + fallback-line dedup)
tracked under "Gemini Live reconnect polish" above.

### Face Recognition for Reachy Mini

Design doc: [tasks/face-recognition-design.md](face-recognition-design.md)

Shipped: `src/face_service.py` (camera capture + identify), enrollment
CLI at `scripts/enroll_faces.py`, unit tests in `tests/test_face_service.py`,
`requirements-robot.txt` updated with `face_recognition` and
`face_recognition_models`, `.gitignore` excludes `faces/` and
`**/.myra/faces.pkl`. On-device verification done.

### Persistent Memory v1 — markdown file + profile concat

Design doc: [tasks/plan-persistent-memory.md](plan-persistent-memory.md)

Shipped: `src/memory_file.py` with `read_for_prompt()`, `append_note()`,
`remove_notes_starting_with()` (atomic write, flock, missing-file → empty);
`src/kids_teacher_profile.py:98` concatenates memory text into the
`instructions` payload; soft cap raised to 8 KB with a warning log when
exceeded; `tests/test_kids_teacher_profile.py:102` asserts memory shows
up in the assembled session payload. `src/text_llm.py` provider dispatch
(ollama / openai / gemini) is also in. v2 summarizer + session wiring
tracked above.
