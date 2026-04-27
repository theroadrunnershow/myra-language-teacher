# Plan: Language-lesson skill inside kids-teacher

## Context

Myra (age 4) currently uses the kids-teacher mode for open-ended,
LLM-driven preschool conversation on the robot. The realtime session
runs on Reachy via `kids_teacher_flow.py` → `kids_teacher_realtime.py`
→ `kids_teacher_backend.py` (OpenAI Realtime or Gemini Live). The
persona is loaded from `profiles/kids_teacher/instructions.txt`.

We want a structured *language-lesson* behavior the child or a
grown-up can ask for mid-session. When triggered, the model runs a
fixed loop — silly story → teach one word → ask the child to repeat
→ listen → encourage / correct → next word — in **Telugu only for
v1**. The lesson ends naturally after 5–8 words, or sooner if the
child wants to switch topics, and the session returns to free
preschool chat.

Tamil, Assamese, and Malayalam are explicitly deferred to a future
iteration to keep the v1 diff minimal. The pipeline already supports
Telugu end-to-end (`KIDS_SUPPORTED_LANGUAGES = {english, telugu}`,
`KIDS_DEFAULT_EXPLANATION_LANGUAGE` defaults work, the existing
persona already names Telugu on lines 41 and 100), so adding the
lesson-mode behavior keeps the v1 diff small.

This is **only** for the kids-teacher flow. Not the index page word
loop. Not a new template, route, or page. Robot-only delivery.

The lesson-mode prompt lives in **its own file**
(`profiles/kids_teacher/language_lesson.txt`) rather than appended
to `instructions.txt`. `instructions.txt` is already 178 lines of
locked base persona; bolting another ~100-line section onto it
hurts readability and makes future skills (counting mode, color
mode, etc.) harder to add cleanly. The loader concatenates the
two at session-build time, so the model sees a single system
prompt as before.

Critical files:
- `profiles/kids_teacher/language_lesson.txt` — new file, the
  lesson-mode skill prompt
- `profiles/kids_teacher/instructions.txt` — unchanged in v1
- `src/kids_teacher_profile.py` — `load_profile()` extended to
  read and concatenate `language_lesson.txt` if present

---

## Scope (V1)

A new sibling file `profiles/kids_teacher/language_lesson.txt`
containing the lesson-mode skill prompt, plus a small loader change
in `kids_teacher_profile.py` that concatenates it onto `instructions`
at session-build time. Scoped to Telugu only.

In:
- Entry triggers in English or Telugu ("teach me Telugu," "Telugu
  lesson," equivalents in Telugu, unambiguous variations)
- Lesson opener: one short, silly 3–5 sentence English story
- Per-word loop: teach → ask → listen → encourage / correct
- Hard cap of 3 attempts per word, then move on positively
- Exit triggers: child says stop / wants a different topic /
  goes quiet for a while / lesson reaches 5–8 words. After the 5th
  word the model asks whether to keep going or do something else.

Out of scope (explicit, with rationale below):
- **Tamil, Assamese, Malayalam.** Deferred to a follow-up iteration
  to keep the v1 diff to a single file. Adding them later is purely
  prompt + the `KIDS_SUPPORTED_LANGUAGES` widening already explored
  earlier in this plan; no architectural unknowns remain.
- A `get_lesson_word(...)` tool — the realtime tool layer in
  `kids_teacher_backend.py:136-141` is still placeholder-only, deferred
  to a future "Intern 2 / integration phase". Building the entire tool
  layer to support one tool is much bigger than this skill.
- A `speak_word(text, language)` tool routed through gTTS — same
  blocker, plus latency cost. Telugu pronunciation in OpenAI Realtime
  / Gemini Live is acceptable in practice, so v1 lives without it.
- Web frontend / browser realtime endpoint. `kids_teacher_routes.py:4`
  explicitly defers this; the kids-teacher session still runs on the
  robot only.
- Cross-lesson memory ("words Myra has practiced"). `memory.md` already
  flows through `kids_teacher_profile.load_profile(memory_file_path=…)`,
  so a parent can seed it manually if they want continuity, but no
  automatic persistence of lesson outcomes in v1.
- Curated curriculum from `words_db.py`. The model uses its own
  vocabulary in v1. Curriculum alignment with the index page is a
  v2 concern, blocked on the tool layer.

---

## Why pure system-prompt is the only viable v1

`kids_teacher_backend.py:136-141` ships tool specs as
`{"type": "function", "name": name}` with no parameter schemas and
no executor. The comment is explicit: "Tool-spec lookup is
deliberately NOT part of V1 — Intern 2 / integration phase will
replace this with real tool specs when any tool is actually
enabled." A `get_lesson_word(language, category)` tool would
require building that whole layer.

So the lesson loop has to live in the system prompt. The model
handles entry detection, story generation, word selection, attempt
counting, evaluation, exit. The prompt has to be tight enough that
the loop terminates and the safety rules still apply.

This isn't ideal — LLMs are unreliable at counting attempts and
terminating loops. We accept the risk in v1 and revisit with a
real tool layer in v2 if it's flaky in practice.

---

## Decisions locked in (2026-04-26)

1. **Lesson length**: 5–8 words per lesson. After word 5 the model
   asks whether to keep going or switch topics. Hard cap at 8.
2. **Language scope**: Telugu only for v1. Tamil / Assamese /
   Malayalam deferred. Even though the skill prompt referenced four
   languages, scoping to Telugu collapses the diff to a single file
   and avoids the supported-languages widening.
3. **If the child asks for a non-Telugu language**: model gently
   says "We can do Telugu for now — want to start there?" and steers
   to Telugu, instead of starting a lesson it can't actually run.
4. **Cross-lesson memory**: out of scope. Don't persist lesson
   outcomes to `memory.md` automatically.

---

## File-level changes

### `profiles/kids_teacher/language_lesson.txt` (new file)

The whole lesson-mode skill prompt, written as a single top-level
section starting with `# Language lesson mode`. The section follows
the original skill's 8-step shape, scoped to Telugu and a 5–8 word
cap (see "Reconciliation with original skill prompt" below for what
changed and why).

The new file should match the existing house style of
`instructions.txt` — short prose rules, concrete examples in quotes,
plain-text bullets, markdown headings up to `####`. Length budget:
~80–110 lines so the full step structure fits without bloating the
realtime session payload.

Filename uses `.txt` for consistency with the other files in
`profiles/kids_teacher/` (`instructions.txt`, `voice.txt`,
`tools.txt`). Content is markdown-flavored prose, same as
`instructions.txt`.

Section outline (each step gets a few short prose rules + at least
one example quote, mirroring how the existing persona handles
"camera-aware" and "restricted topics" sections):

#### Voice & personality (preface)

Warm, playful, energetic. Speak like a storyteller plus teacher.
Short, clear sentences. Always encouraging, never critical.
Celebrate effort, not just correctness.

#### Entry triggers

- Child or grown-up says "teach me Telugu," "Telugu lesson," "let's
  learn Telugu words," Telugu equivalents, unambiguous variations.
- If the child asks for Tamil / Assamese / Malayalam / any other
  language, kindly say "We can do Telugu for now — want to start
  there?" Steer to Telugu instead of starting a lesson the system
  can't run yet.

#### STEP 1 — Fun story intro (3–5 sentences)

One short, silly English story using simple characters. Example to
include verbatim in the prompt:

> "Today, a little dog tried to meow like a cat… It said 'meow
> meow!'… oh no! That's so silly!"

#### STEP 2 — Teach word (one at a time)

Say the English word, then say it slowly in Telugu (native script
spoken; romanization stays internal), then ask the child to repeat.
One word per round; never batch. Example to include:

> "Let's learn the word… Dog. In Telugu, we say… కుక్క… (kukka)…
> Say kukka…"

#### STEP 3 — Listen loop (four IF-branches)

After prompting, wait for the child's voice. Then respond based on
what was heard. Each branch needs an example quote so the model
copies tone, not just rules:

- **IF CORRECT (or close enough)**:
  > "Yayyy! That was amazing! You said kukka! Great job! Let's say
  > it one more time… kukka…"

- **IF PARTIALLY CORRECT**:
  > "Good try! Let's say it slowly together… ku…kka… Your turn…"

- **IF INCORRECT**:
  > "That's okay! Let's try together! Listen… kukka… Now you say
  > it…"

- **IF NO RESPONSE / SILENCE**:
  > "Can you try saying it?… Let's say it together… kukka…"

Hard cap **3 attempts** per word. After 3, move on positively no
matter what. Never say "wrong." Never discourage. Never repeat past
3.

#### STEP 4 — (skipped in v1 — Telugu only)

Original skill step 4 was "repeat for all languages (Tamil,
Assamese, Malayalam)." V1 is Telugu-only, so this step is dropped.

#### STEP 5 — Mini game (voice interaction)

After 2–3 words taught, mix in one short spoken question for variety.
Examples to include:

> "Can you say dog in Telugu?…"
> "Which one is kukka? Dog or cat?…"

Wait for response. Praise correct, gently guide if off.

#### STEP 6 — Repetition loop

Halfway through the lesson, prompt the child to echo all the Telugu
words taught so far together. Example:

> "Let's say them all together! Kukka… pilli… aavu…"

Listen for the echo, encourage the attempt regardless of accuracy.

#### STEP 7 — Recap

When the lesson is wrapping up, recap the words taught. Example:

> "Today we learned: Dog — kukka! Cat — pilli! Cow — aavu!"

#### STEP 8 — Cheerful ending

End warmly. Example:

> "You did amazing today! See you next time for more fun!"

#### Lesson length

5 words minimum, 8 words maximum. After word 5, ask whether to keep
going or do something else. Hard cap at 8 — after the 8th word, do
the recap and cheerful ending no matter what.

#### Real-time response rules

- Always wait after asking the child to speak.
- Treat every child input as a spoken attempt at the target word.
- Be tolerant of mispronunciations.
- Focus on encouragement over accuracy.
- Keep the interaction flowing quickly; don't lecture.
- No grammar explanations. No long sentences.

#### Error handling inside lesson mode

- **Unclear input**: "I didn't hear that clearly… let's try again!"
- **Silence**: gentle reprompt (see IF NO RESPONSE branch above).
- **Off-topic**: redirect playfully back to the current word, e.g.
  "Ooh, we can talk about that after! Right now we're on kukka —
  can you say it?"

#### Exit triggers

The lesson ends when any of these is true. On exit, do the recap
(STEP 7) and cheerful ending (STEP 8), then resume free preschool
chat:

- Child says stop / "I'm done" / "no more."
- Child asks for a different topic.
- Child goes quiet for a long stretch even after a re-prompt.
- 8-word hard cap reached.

#### Safety reminder

All "topics you must not discuss," personal-data, restricted-topic,
and silence-handling rules from earlier sections still apply inside
lesson mode. Lesson mode is a behavior, not an exception. If the
child raises an unsafe topic mid-lesson, redirect first, then offer
to continue the lesson.

No changes to lines 41, 100, or 103 of `instructions.txt` — the
existing "English and Telugu" wording is already correct for v1.

### `src/kids_teacher_profile.py` (loader change)

After loading and validating `instructions.txt`, read
`language_lesson.txt` from the same profile dir if it exists, strip
trailing whitespace, and append it to `instructions` with a blank
line separator — exactly the same pattern used today for
`memory.md`. The order is: base persona → lesson-mode skill →
memory → present-people note. (Skill prompt before memory means
parent-curated memory facts can still override or qualify the skill
if needed; present-people is always last so the model sees it
freshest.)

The new file is **optional**. If it's missing, `load_profile()`
behaves exactly as today — no error, no warning. This keeps the
loader change zero-risk for environments that don't ship the file
yet (e.g. older robot images during rollout).

Implementation shape (~6 lines, inline, no new helper or constant
tuple — speculative extensibility for "future skills" is out of
scope per the project's simplicity rule):

```python
lesson_path = os.path.join(base_dir, _LANGUAGE_LESSON_FILENAME)
lesson_raw = _read_text_file(lesson_path)
if lesson_raw is not None:
    lesson_text = lesson_raw.strip()
    if lesson_text:
        instructions = f"{instructions}\n\n{lesson_text}"
```

A new module-level constant `_LANGUAGE_LESSON_FILENAME =
"language_lesson.txt"` mirrors the existing
`_INSTRUCTIONS_FILENAME` / `_VOICE_FILENAME` / `_TOOLS_FILENAME`
pattern.

---

## Reconciliation with original skill prompt

The original "Real-Time Voice Language Tutor for Toddlers" skill
prompt is the source of truth for behavior. V1 deviates from it in
two places, both per explicit user direction on 2026-04-26:

| Skill prompt says | V1 implements | Why |
|---|---|---|
| "Max 3 words per lesson" (`## RULES`) | 5–8 words | User direction. Telugu-only means a single-language lesson; 3 words feels too short for a single-language session. |
| Teach Telugu, Tamil, Assamese, Malayalam (STEP 4, STEP 6) | Telugu only | User direction to keep v1 diff minimal. Other three languages are a follow-up that only needs prompt edits + the `KIDS_SUPPORTED_LANGUAGES` widening. |

Everything else from the skill prompt — voice & personality, the
8-step shape (with STEP 4 collapsed), the four IF-branches with their
exact example phrasings, real-time response rules, error handling,
the goal ("Make the child speak confidently, feel happy, learn
through play; this should feel like a fun conversation, not a
lesson") — lands verbatim or near-verbatim in the new section.

### Tests

Two test files now: extended loader-merge cases in the existing
`tests/test_kids_teacher_profile.py`, plus a new
`tests/test_kids_teacher_lesson_mode.py` for the lesson-mode content
contract.

#### Loader-merge cases — extend `tests/test_kids_teacher_profile.py`

These cover the new `kids_teacher_profile.py` behavior, isolated
from the lesson-mode prose. Use `tmp_path` profile dirs so the cases
don't depend on the shipped real `language_lesson.txt`.

- `language_lesson.txt` present and non-empty → loaded `instructions`
  contains both the base instructions text and the lesson text,
  separated by a blank line, in that order.
- `language_lesson.txt` missing entirely → loader behaves exactly as
  today (no error, no warning).
- `language_lesson.txt` present but empty / whitespace-only →
  silently skipped (no extra blank lines glued onto `instructions`).
- Order is base → lesson → memory → present-people: when memory and
  language_lesson are both present, lesson text appears *before* the
  memory section in the final string.

#### Lesson-mode content contract — new `tests/test_kids_teacher_lesson_mode.py`

Each test asserts on the *loaded profile instructions* (i.e. via
`load_profile(DEFAULT_PROFILE_DIR)`), not the raw file, so we
exercise the real loader path against the shipped
`language_lesson.txt`.

- Loaded instructions contain a `# Language lesson mode` section
  header.
- The section appears *after* the existing safety / restricted-topics
  sections in `instructions.txt` so the model reads safety rules
  first.
- **Step coverage** — the section names each of: story intro,
  teach word, listen loop, mini game, repetition, recap, cheerful
  ending. (Either via the literal "STEP N" markers or via stable
  keywords — pick whichever is more robust to minor wording tweaks.)
- **Branch coverage** — the four IF-branches each appear with at
  least one example quote: correct, partially correct, incorrect,
  no response / silence.
- **Numbers** — the 3-attempt cap and the 5–8 word range are both
  present and unambiguous (so neither can drift to e.g. "5 attempts"
  or "3 words" without a test failing).
- **Telugu scoping** — Telugu is named; Tamil / Assamese / Malayalam
  are *not* named as supported lesson languages (only as redirect
  targets). This guards the v1 scope from accidental drift when the
  follow-up adds the other languages.
- **Error-handling rules** — the "unclear input," "silence," and
  "off-topic" handling lines are present.

---

## Validation

Before declaring v1 done:

1. `pytest` (full suite) — must be clean.
2. **Robot smoke test** (manual, with audio recording):
   - Start a kids-teacher session. Say "teach me Telugu."
   - Confirm: silly intro story → first word → listen → encourage
     → second word, etc.
   - Confirm hard cap at 3 attempts on a deliberately mispronounced
     word.
   - Confirm "I'm done" exits cleanly.
   - Confirm "after word 5" prompt: keep going / stop.
   - Confirm: asking for "Tamil lesson" or "Malayalam lesson" gets
     a kind redirect to Telugu, not a broken half-lesson.
3. **Audio judgment** — listen back to the model speaking the
   Telugu words. Judge whether a 4-year-old can learn from the
   pronunciation. Telugu is generally fine in OpenAI Realtime /
   Gemini Live; this check is the safety net.
4. **Negative test** — mid-lesson, ask the model something that
   would normally be redirected (a restricted topic). Confirm it
   redirects rather than getting stuck in lesson-mode tunnel vision.

---

## Risks

- **Loop termination drift.** The model is supposed to count to 3 on
  retries and 5–8 on words. LLMs miscount. Mitigation: explicit
  "after the 3rd attempt, move on no matter what" wording, and
  observation in the smoke test. If drift is real in practice, v2
  adds a state-line injection per turn.
- **Telugu pronunciation.** Generally acceptable in OpenAI Realtime /
  Gemini Live, but worth a manual audio check before declaring done.
- **Prompt bloat.** `instructions.txt` is already 178 lines. Adding
  ~60 more is fine, but the section has to stay tight or it crowds
  out the rest of the persona in the realtime session payload.
- **Safety regression in lesson mode.** A long, specialized section
  could distract the model from the existing safety rules.
  Mitigation: explicit reminder line inside the new section, plus
  the negative test in validation.
- **Asks for Tamil / Assamese / Malayalam.** Without the redirect
  rule, the model would happily improvise a lesson in a language the
  rest of the kids-teacher pipeline doesn't fully support. The
  redirect line above keeps v1 honest until those languages are
  added in a follow-up.

---

## v2 candidates (deferred, not part of this plan)

- **Tamil, Assamese, Malayalam.** Widen `KIDS_SUPPORTED_LANGUAGES` to
  include them, update lines 41 / 100 / 103 of `instructions.txt`,
  and remove the "steer to Telugu" redirect rule. Pure prose +
  one-line constant change; no architectural unknowns.
- `get_lesson_word(language, category)` tool — once the tool layer
  is real, source words from `words_db.py` for curriculum
  consistency with the index page.
- `speak_word(text, language)` tool — only if pronunciation in v1
  is unusable. Adds latency.
- Per-turn state-line injection (`attempt N of 3`, `word N of 5–8`)
  — if the pure-prompt loop drifts.
- Lesson-outcome persistence into `memory.md` — currently
  parent-curated only.
