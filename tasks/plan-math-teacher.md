# Math teacher skill — design & plan

Pedagogical design for a math-teaching skill that runs alongside the
existing language-lesson skill in the kids-teacher profile. Goal: build
**deep conceptual understanding of foundational math** in a 4–5 year old
through play, paper manipulation, and Socratic prompting — never rote
memorization. Linked to issue #54.

## 1. Pedagogical thesis

A 4-year-old who recites "1, 2, 3… 20" has memorized a song. She becomes
a mathematician — eventually — when she internalizes one idea:

> A number is a property of a set, not a position in a chant.

"3" means *this many things*, regardless of what they are, how they're
arranged, or what order you count them in. Every later skill (arithmetic,
place value, fractions, algebra) collapses without this foundation. The
v1 skill builds this and its immediate consequences.

Five derived principles:

1. **Concrete → pictorial → abstract.** Dots and fingers before drawings,
   drawings before numerals. The numeral "5" is the *last* layer to add,
   and only as a label for something she already understands.
2. **Quantity before symbol.** She should *feel* "fiveness" — five claps,
   five fingers, five dots — before she sees "5" on paper.
3. **Composition is the bridge to arithmetic.** Addition = combining
   parts. Subtraction = separating parts. Both presuppose that 5 can be
   *seen* as 2-and-3 (and 4-and-1, and 5-and-0) simultaneously.
4. **Productive struggle, with a safety net.** Don't rescue too fast. A
   wrong answer is a window into the child's thinking, not a failure.
   Mascot's reflex is *curiosity* — "show me how you got that."
5. **Autonomy and play.** She picks the story-problem topic, the drawing
   color, when to take a break. The lesson is hidden inside the play.

## 2. Setup ritual (mandatory)

Math lessons require paper. Every session opens with a calibration loop:

1. Mascot: *"Get a piece of paper and a crayon."*
2. Mascot: *"Now put the paper right in front of me, where my eyes are
   pointing."*
3. **Calibration check** — mascot asks her to draw one dot, then confirms:
   *"I see your dot — perfect!"* If it doesn't see a dot, it nudges the
   paper position. Up to 3 retries.
4. If calibration never lands, fall back to **audio-only mode** (claps,
   fingers, sounds) for that session.
5. Mascot: *"Great, we're ready to play."*

Skipping calibration is non-negotiable — half the activities silently
fail if the paper isn't in frame.

## 3. Skill ladder (12 stages, gated by mastery)

| # | Stage | Core concept | Why foundational |
|---|---|---|---|
| 0 | Calibration & rapport | Setup ritual, paper visible | Without this, nothing else works |
| 1 | Subitizing 1–3 | Instantly seeing "that's 2" without counting | Perceptual root of number |
| 2 | Counting principles to 5 | One-to-one, stable order, cardinality | "How many?" → answer is the last word |
| 3 | Conservation within 5 | 5 spread out = 5 bunched up | Quantity is invariant under rearrangement |
| 4 | Order irrelevance | Counting from any starting object gives the same answer | Quantity is independent of count path |
| 5 | Comparison within 5 | More / less / same | Numbers have an order grounded in quantity |
| 6 | Conceptual subitizing 4–6 | Seeing 5 *as* 2-and-3 without counting | First glimpse of composition |
| 7 | Number bonds within 5 | 5 = 0+5, 1+4, 2+3, 3+2, 4+1, 5+0 | Composition/decomposition becomes explicit |
| 8 | Addition as combining (within 5) | "Put parts together → how many?" | Arithmetic emerges from composition |
| 9 | Subtraction as separating (within 5) | "Take part away → what's left?" | Inverse of addition; reversibility |
| 10 | Counting & bonds 6–10 | Re-do 2–9 in the 6–10 range | Spiral: revisit foundations with bigger numbers |
| 11 | Add/subtract within 10 | Includes missing-addend ("3 + ? = 7") | Real fluency, not direct computation only |
| 12 | Skip counting & ten-frame | 2s, 5s, 10s; ten as a unit | Seeds for place value & multiplication |

**v1 ships stages 0–7.** That is the load-bearing core. Stages 8–12 land
in v2/v3 once we observe how Myra progresses.

Stages doing extra work:

- **Stage 4 (order irrelevance)** is the test that separates
  rote-counters from quantity-understanders. Most curricula skip it.
- **Stage 7 (number bonds)** is the single most important stage. If she
  sees 5 effortlessly as 2-and-3, every later arithmetic skill becomes
  obvious. If she doesn't, every later skill becomes memorization. Spend
  extra time here.
- **Stage 11 missing-addend** ("3 plus what is 7?") is what makes
  subtraction *click* and is the first taste of algebra.

## 4. Per-session flow (~12–15 min)

```
[1] Greeting + warm-up                                ~1 min
    Mascot is silly, asks how she is, sets the tone.

[2] Calibration check (paper visible)                 ~1 min
    "Show me your paper — draw one dot — perfect."

[3] Spiral review of prior skill                      ~2 min
    Light, low-stakes. "Remember yesterday we drew 3 dots?
    Can you draw 3 dots again?"

[4] Today's activity (current stage)                  ~5–7 min
    1–2 activities, never more. Each ends with her
    explaining what she did.

[5] Silly break                                       ~1 min
    Mascot does something absurd — counts its own ears wrong,
    sings a number song badly. Resets attention.

[6] Story problem (transfer)                          ~2 min
    Today's skill, in a story she chose the topic of.

[7] Closing celebration                               ~1 min
    Specific praise: "You showed me how you know 4 — you saw
    it without counting!"
```

Three subtle rules baked into the prompt:

- **Mascot makes mistakes on purpose** in roughly 1 of every 4–5 turns at
  the current skill level. *"1, 2, 3, 5 — wait, did I get that right?"*
  Catching the error proves mastery.
- **Mascot is the student sometimes.** *"I don't remember — can you teach
  me what 3 looks like?"* Role reversal cements understanding.
- **One new thing per session, max.** New skill OR new context, never
  both. Cognitive load is the silent killer at this age.

## 5. Mastery checks (when to advance)

A skill is mastered when the child does **all four** of these across
**at least three different sessions**:

1. **Direct demonstration** — does the thing when asked.
2. **Reverse / unprompted demonstration** — *"How many?"* without
   counting first; she states the cardinal. Or she uses the skill to
   solve a problem we didn't frame as practice.
3. **Explanation** — *"How did you know?"* — gives a reason that maps to
   the concept. For a 4-year-old: *"because 2 and 1 more"* counts.
4. **Robustness probe** — the variant designed to catch the misconception:

| Stage | Robustness probe |
|---|---|
| 1 (subitize) | 3 in a line vs. 3 scattered — same answer, no recount |
| 2 (cardinality) | After she counts, ask "how many?" — does she say the last number, or recount? |
| 3 (conservation) | Spread her 5 dots apart with a finger. *"Now how many?"* If she recounts, not yet mastered. |
| 4 (order irrelevance) | "Start counting from the middle one." Same total? |
| 5 (comparison) | Make the smaller group physically larger (big dots) — does she still pick the more-numerous group? |
| 7 (number bonds) | "Show me 5 a different way." She should produce multiple decompositions. |
| 9 (subtraction) | Reversibility: she knows 2+3=5 — does 5−3=2 follow, or does she recount? |
| 11 (missing addend) | "I have 3, I want 7 — how many more?" Without counting from 1. |

Skipping the robustness probe is how curricula produce kids who "know
math" but can't reason mathematically.

## 6. Psychology, woven through

- **Praise the process, never the trait.** "You kept trying when it was
  tricky" beats "you're so smart." Trait praise creates fragile learners.
- **Errors are interesting, not bad.** *"Show me how you got that."*
- **Autonomy.** She picks the story-problem topic, the drawing color,
  sometimes the activity.
- **Naming the strategy.** When she does something clever, name it for
  her: *"You looked at the dots and just **knew** it was 4 — that's
  called subitizing!"*
- **Asymmetric celebration.** Big celebration for *strategy* moments
  ("you saw it without counting!"), small acknowledgment for "right
  answers." Shifts what she values.
- **Stop while it's still fun.** End sessions one beat *before* she's
  tired.

## 7. Implementation plan

1. **`profiles/kids_teacher/math_lesson.txt`** — encodes stages 0–7,
   session flow, calibration ritual, mastery probes, error handling,
   tone. Loaded by `kids_teacher_profile.load_profile` parallel to
   `language_lesson.txt`.
2. **`src/kids_teacher_profile.py`** — append `math_lesson.txt` after
   `language_lesson.txt` and the Telugu seed-vocabulary block, before
   `memory.md`. No new public API.
3. **Tests** — `tests/test_kids_teacher_math_lesson.py` (mirrors the
   existing `test_kids_teacher_lesson_mode.py` contract style):
   - File appended to instructions when present
   - Missing/empty math file is non-fatal
   - Section ordering: persona → language lesson → math lesson → memory
   - Stage-coverage probes (calibration, subitizing, number bonds, …)
   - Mascot-mistake rule present
   - "One new thing per session" rule present
   - 3-attempt cap on calibration retries

## 8. How the child invokes math mode

Voice. The kid (or a grown-up) says one of:

- "Teach me math."
- "Let's do math."
- "Math lesson, please."
- "I want to do numbers."
- Any unambiguous variant.

The model picks math mode based on the entry triggers in
`math_lesson.txt`. No config UI, no env var, no flag — same pattern
the language lesson uses today.

## 9. Out of scope for v1

- Numeral writing/tracing (legible numerals are a fine-motor skill
  separate from math understanding).
- Multiplication, division, place value beyond planting a seed.
- Persistent progress tracking across sessions (for now the model
  re-reads `memory.md` and the kid; explicit progress storage is a
  later track).
- Multi-language math (v1 is English-only).
