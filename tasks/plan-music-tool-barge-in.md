# Plan: `play_music` tool with barge-in for kids-teacher mode

## Context

Myra (age 4) currently uses the kids-teacher mode for open-ended conversation
with the mascot/robot. The OpenAI Realtime API powers that session and supports
function-calling tools, but today no tools are enabled (`profiles/kids_teacher/tools.txt`
is empty, and `build_session_payload` in `src/kids_teacher_backend.py` emits
only a stub `{"type": "function", "name": ...}` with no schema). We want the
child to be able to ask for a song and have the robot play it — but **if she
starts talking during the song, the music must stop immediately and the
assistant must answer her**.

The good news: the codebase already has a clean design for this kind of
behavior. Server-side VAD from OpenAI emits `input.speech_started`, and
`KidsTeacherRealtimeHandler._on_speech_started` (src/kids_teacher_realtime.py:180)
already cancels in-flight *assistant* responses. We just need to extend that
barge-in path to also stop *music*, and wire a new function tool end-to-end.

Critical files:
- `src/kids_teacher_realtime.py` — session/event loop, current barge-in
- `src/kids_teacher_backend.py` — OpenAI event normalization + session payload
- `src/kids_teacher_robot_bridge.py` — robot playback thread
- `src/kids_teacher_types.py` — hook protocol + event types
- `src/kids_teacher_flow.py` — orchestration
- `profiles/kids_teacher/tools.txt` — tool allowlist

---

## Scope (V1)

- One new tool: `play_music(song_query: str)` — Alexa-style free-text input
- Curated catalog of ~20 MP3 songs covering the 3 lesson languages, stored
  in GCS and lazy-loaded with a local cache (mirrors `dynamic_words_store.py`).
  V1 searches *only* the curated catalog; server does fuzzy match via
  `rapidfuzz` (already a dep, used by the language lesson). Future V2 can back
  the same tool signature with a licensed children's-music API — see
  "Why not YouTube/Spotify" below.
- Barge-in: any VAD `input.speech_started` event while music is playing stops
  music, and the child's next utterance is handled normally (VAD → transcript →
  assistant response)
- User-initiated `handler.interrupt()` also stops music
- `handler.stop()` and error paths drain music cleanly

Out of scope: open internet search, playlists, volume control, cross-fade,
browser-side playback (the realtime session runs on the robot — the browser
UI is status-only).

### Why not YouTube/Spotify in V1
- `yt-dlp` technically works for YouTube audio but violates YouTube ToS,
  and "kids" content on YouTube has a documented history of inappropriate
  material — directly bypasses the `kids_safety.py` profile.
- `spotipy` is metadata-only; actual playback needs Spotify Premium + the
  Spotify Connect SDK, which isn't available for the Reachy runtime.
- Licensed children's-music APIs (Jamendo, ccMixter, Internet Archive) have
  inconsistent coverage for Telugu/Assamese rhymes.
- The tool signature (`song_query: str` → opaque server-side resolution)
  lets us swap the backing source later without touching the LLM contract.

---

## Design

### 1. Tool definition (function-call schema)

`src/kids_teacher_backend.py` `build_session_payload` currently emits a stub
tool spec. Replace the list-comprehension with a small registry so each
allowlisted tool carries a real schema:

```python
_TOOL_SPECS = {
    "play_music": {
        "type": "function",
        "name": "play_music",
        "description": (
            "Play a short children's song for the child. Call this only "
            "when the child clearly asks to hear a song. Pass the child's "
            "own words describing the song (or an English/native title) "
            "as song_query; the server will fuzzy-match to a small library."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "song_query": {
                    "type": "string",
                    "description": (
                        "Free-text description of the song, e.g. "
                        "'twinkle twinkle', 'the one about the moon', "
                        "'chanda mama'. Server fuzzy-matches to the catalog."
                    ),
                },
            },
            "required": ["song_query"],
        },
    },
}
```

Unknown allowlisted names fall through to the existing stub shape so the
locked-profile contract still holds.

Add `play_music` to `profiles/kids_teacher/tools.txt`.

### 2. Music catalog + fuzzy search

MP3 files live in **GCS, not the repo** — mirrors `dynamic_words_store.py`
and keeps the Docker image small. Adding a song = upload the MP3 to the
bucket + append ~5 lines of metadata to the in-code catalog. Songs the
user plans to provide: ~20, covering Telugu, Assamese, and English.

Create `src/kids_teacher_music.py`:

```python
@dataclass(frozen=True)
class Song:
    song_id: str
    title: str
    aliases: tuple[str, ...]   # native-script + romanizations + common names
    language: str
    gcs_object: str            # e.g. "songs/twinkle_en.mp3"

MUSIC_CATALOG: tuple[Song, ...] = ( ... )  # ~20 entries, hard-coded metadata

def resolve_song(query: str) -> Optional[Song]:
    """Fuzzy-match a free-text query to a catalog entry.

    Uses rapidfuzz.process.extractOne against each song's title + aliases.
    Returns the best match when the score >= SCORE_THRESHOLD, else None.
    """

class MusicStore:
    """Lazy GCS loader + local LRU cache. Mirrors dynamic_words_store.py."""

    def __init__(self, *, bucket_name: str, cache_dir: str, max_cache_mb: int = 200):
        ...

    def load(self, song: Song) -> bytes:
        """Return MP3 bytes. Cache hit → disk read. Cache miss → GCS download,
        write-through to disk, return bytes."""
```

- Catalog metadata stays immutable in code (matches `words_db.py` pattern).
- `MusicStore` is wired as an `app.state` singleton in `main.py`, same as
  `kids_review_store`.
- Cache dir defaults to a tmpfs path on the robot / `/tmp/myra-music` on
  Cloud Run; an LRU eviction pass keeps it under `max_cache_mb`.
- `rapidfuzz` is already a dependency; reuse `token_sort_ratio` as the
  language lesson does. Threshold tuning target: ~70, verified with test
  cases like `"the twinkle song" -> twinkle`, `"chanda mama" ->
  telugu_chanda_mama`.
- Env vars: `KIDS_MUSIC_BUCKET`, `KIDS_MUSIC_CACHE_DIR`, `KIDS_MUSIC_CACHE_MB`.
  When `KIDS_MUSIC_BUCKET` is unset, `MusicStore` is disabled and the tool
  always returns `no_match` (graceful degradation for local dev without GCS
  creds).

### 3. Backend event: surface tool calls

Extend `OpenAIRealtimeBackend._normalize_event` to emit a new normalized
event when OpenAI sends a complete function call:

- `response.function_call_arguments.done` →
  `{"type": "tool_call", "call_id": str, "name": str, "arguments": str}`

Extend `RealtimeBackend` protocol with:
- `async def send_tool_result(self, call_id: str, output: str) -> None`
  (sends `conversation.item.create` with `function_call_output` role, then
  `response.create()` so the model can react).

Update `FakeRealtimeBackend` in `src/kids_teacher_fakes.py` to support the
new event shape and record `send_tool_result` calls (mirrors how the fake
already records `cancel_response`).

### 4. Hook protocol: music playback

Extend `KidsTeacherRuntimeHooks` in `src/kids_teacher_types.py`:

```python
def start_music_playback(self, audio_bytes: bytes, *, song_id: str) -> None: ...
def stop_music_playback(self) -> None: ...
```

Both `NullRuntimeHooks` and `RecordingRuntimeHooks` in `kids_teacher_flow.py`
get no-op / recording implementations. `_ReviewStoreHooks` and `_SafetyHooks`
forward through.

In `KidsTeacherRobotHooks` (`src/kids_teacher_robot_bridge.py`):
- Reuse the existing playback thread. Add a `_music_active` flag and a
  separate music deque, so music chunks aren't mixed with assistant audio.
- `start_music_playback`: decode MP3 via `mp3_bytes_to_robot_samples` once
  (whole song), chunk into ~80ms frames matching the existing contract,
  append to the queue, set the flag.
- `stop_music_playback`: clear the queue, clear flag, `listen()` animation.
- On barge-in, `_flush_playback()` flushes *both* assistant and music queues.

### 5. Realtime handler: dispatch + barge-in

In `src/kids_teacher_realtime.py`:

**New state:** `self._music_active: bool = False`

**New dispatch branch** in `_dispatch`:
```python
elif event_type == "tool_call":
    await self._on_tool_call(event)
```

**`_on_tool_call`:**
1. Parse `name` + `arguments` (JSON).
2. If `name == "play_music"`:
   - `song = resolve_song(arguments["song_query"])`.
   - On match: `bytes = music_store.load(song)` (GCS fetch + cache on miss),
     `self._hooks.start_music_playback(bytes, song_id=song.song_id)`,
     set `self._music_active = True`, send
     `await self._backend.send_tool_result(call_id, json.dumps({"status": "playing", "title": song.title, "language": song.language}))`.
     The model can then speak a short intro like *"Okay, here's Twinkle Twinkle!"*
   - No match (or store disabled because `KIDS_MUSIC_BUCKET` unset): send
     `send_tool_result(call_id, json.dumps({"status": "no_match", "known_titles": [...]}))`.
     The model then apologizes and offers alternatives ("I don't know that one
     yet — want to try Twinkle Twinkle?"). No music playback.
   - GCS fetch error (network/auth): send `{"status": "error"}`. `_music_active`
     stays False.
3. Unknown tool name: log + send `{"status": "error", "reason": "unknown_tool"}`.

GCS fetch is wrapped in `asyncio.to_thread` so the event loop isn't blocked
on a download. On a cache hit this is trivially fast; on a cache miss (3–5s
for ~2MB), the model's pre-tool-call utterance ("Okay, one second!") covers
the latency.

**Extend `_on_speech_started`** — this is the critical barge-in path:
```python
async def _on_speech_started(self) -> None:
    if self._assistant_active:
        await self._cancel_active_response()
    if self._music_active:
        await self._stop_music()
```

**Extend `_cancel_active_response`** and the error/`stop()` paths to also
call `_stop_music()` so every teardown route flushes music.

**New `_stop_music`:** sets flag false, calls `hooks.stop_music_playback()`.

Response ordering note: while music is playing there is no `_assistant_active`
response in flight (the model finished its "here's your song" utterance before
the tool call). The child's next `input.speech_started` cleanly stops music;
OpenAI then transcribes the utterance and emits a fresh assistant response.
No special re-sequencing needed.

### 6. Safety layer

`_SafetyHooks` in `kids_teacher_flow.py` already interrupts the handler on
unsafe child input — because `_stop_music` is folded into the existing
`handler.interrupt()` path, unsafe requests mid-song also stop music. No
new safety code required, but add one test to prove this.

---

## Tests

All new code ships with tests. Use the existing `FakeRealtimeBackend` pattern.

**`tests/test_kids_teacher_music.py`** (new):
- `resolve_song` finds the expected song for representative queries per
  language (English title, native title, child-phrasing like "the moon song").
- `resolve_song` returns `None` for clearly-unrelated queries ("elephant",
  "pizza") so the no-match branch is exercised.
- Threshold boundary: one just-above and one just-below case per song.
- `MusicStore` with an injected fake GCS client: cache miss → downloads
  bytes + writes to a temp cache dir; cache hit → no GCS call, returns
  bytes from disk; LRU eviction kicks in once `max_cache_mb` is exceeded.
- `MusicStore` disabled (bucket unset) → `load()` raises a specific
  `MusicStoreDisabled` exception so the tool layer can map it to `no_match`.

**`tests/test_kids_teacher_backend.py`** (extend):
- `build_session_payload` emits full schema (description + enum params) when
  `play_music` is allowlisted.
- `_normalize_event` translates `response.function_call_arguments.done` →
  `tool_call`.

**`tests/test_kids_teacher_realtime.py`** (extend — **critical**):
- Script: backend emits `tool_call(play_music, {"song_query": "twinkle"})` →
  handler calls `hooks.start_music_playback` once and `backend.send_tool_result`
  once with status=playing; `_music_active` becomes True.
- Script: music playing → backend emits `input.speech_started` → handler
  calls `hooks.stop_music_playback` once; `_music_active` becomes False.
  (This is the "barge-in during song" guarantee.)
- Script: music playing → `handler.interrupt()` → music stopped.
- Script: music playing → `handler.stop()` → music stopped.
- Script: unresolvable query ("play me pizza") → handler sends
  `send_tool_result` with status=no_match, does not call `start_music_playback`,
  `_music_active` stays False.

**`tests/test_kids_teacher_robot_bridge.py`** (extend):
- `start_music_playback` enqueues chunks using the injected `play_chunk`.
- `stop_music_playback` flushes the music queue and restores `listen()`.

**`tests/test_kids_teacher_profile.py`** (extend):
- `tools.txt` with `play_music` loads and validates through `validate_tool_names`.

Per CLAUDE.md: do not modify existing tests — only extend with new cases.

---

## Verification

End-to-end check (local, no robot):

1. `pytest` — all new + existing tests pass.
2. Run `PYTHONPATH=src python src/main.py` and open `/kids-teacher` page —
   `/api/kids-teacher/status` now reports `tool_count: 1`.
3. Unit-level integration: write a short script (or REPL) that wires
   `run_kids_teacher_session()` with `FakeRealtimeBackend` pre-loaded with
   the tool_call + speech_started sequence, using `RecordingRuntimeHooks`.
   Assert `start_music_playback`, then `stop_music_playback`, fired in that
   order, with the child transcript arriving after.

Robot-side smoke (optional, requires hardware): flash the branch, say
"play me a song", verify the song starts, interrupt mid-song, verify the
robot responds to the interruption.

---

## Files touched

Modified:
- `src/kids_teacher_backend.py` — tool spec registry, new normalized event, `send_tool_result`
- `src/kids_teacher_realtime.py` — tool_call dispatch, `_music_active`, extended barge-in
- `src/kids_teacher_robot_bridge.py` — music playback paths
- `src/kids_teacher_types.py` — hook protocol additions
- `src/kids_teacher_flow.py` — hook wrappers forward new methods
- `src/kids_teacher_fakes.py` — FakeRealtimeBackend recording of tool events
- `profiles/kids_teacher/tools.txt` — allowlist `play_music`

New:
- `src/kids_teacher_music.py` — immutable catalog + fuzzy search + `MusicStore` (GCS + LRU cache)
- `tests/test_kids_teacher_music.py`

Ops (outside the code plan):
- Create a GCS bucket (or reuse an existing one) and upload the ~20 MP3s
  under a `songs/` prefix. Sourcing and licensing of the MP3s is your job;
  the code treats them as opaque bytes.
- Set `KIDS_MUSIC_BUCKET` (+ optional `KIDS_MUSIC_CACHE_DIR`,
  `KIDS_MUSIC_CACHE_MB`) in the Cloud Run / robot env.
- `infra/` Terraform: grant the Cloud Run service account read access to
  the music bucket. The existing review-store bucket pattern is the model.

Test extensions:
- `tests/test_kids_teacher_backend.py`
- `tests/test_kids_teacher_realtime.py` (the barge-in test is the headline proof)
- `tests/test_kids_teacher_robot_bridge.py`
- `tests/test_kids_teacher_profile.py`
