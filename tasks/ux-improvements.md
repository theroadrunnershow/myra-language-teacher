# 🦕 Myra Language Teacher — Pink Dino UX Design Specification
*A Disney Imagineer-Quality Child Experience Design Document*

---

## Context

Myra is 4 years old. She's learning Telugu and Assamese through a pink dino companion. The app already has a solid technical foundation — SVG dino, 5 animation states, gTTS audio, Whisper STT, fuzzy matching — but the emotional experience is underdeveloped. The dino is functional, not yet *alive*. This document defines what it means to make her *alive*.

This spec covers: who the dino IS, how she speaks, how she moves, how she sounds, and the complete emotional arc of every interaction — from the moment Myra taps the screen to the moment she gets the word right and the dino leaps into the air.

---

## 1. Character Personality Spec — "Dino" (Name: Roo)

### Core Identity
- **Name**: Roo (short, toddler-pronounceable, soft consonants)
- **Species**: Imaginary "Roar-asaurus" — a dinosaur that traded roaring for singing
- **Age**: Playfully ageless — but acts like a 5-year-old, always slightly ahead of Myra but never intimidating
- **Personality pillars**:
  - **Enthusiastic but not overwhelming** — celebrates loudly for 2 seconds, then calms
  - **Gently silly** — makes up words, giggles at mistakes, creates safety through humor
  - **Unconditionally encouraging** — never sighs, never sounds tired, never sounds annoyed
  - **Curious and wondering** — "Ooooh I wonder what word comes next?!"
  - **Companion, not teacher** — "Let's find out TOGETHER!"

### Voice Character
- Gender: Neutral to slightly feminine, warm
- Pitch: Slightly higher than adult speech, but NOT squeaky
- Tempo: 80% of adult speaking speed — clear, never rushed
- Prosody: Exaggerated emotional peaks (BIG highs on celebrations, soft valleys on corrections)
- Accent: Neutral / international English — no regional markers
- Feel: Like a beloved kindergarten teacher crossed with a cartoon character from Bluey

### Non-Negotiable Rules for Roo's Voice
1. **Never sound flat or robotic** — every line has arc (rise-peak-fall)
2. **Never rush** — especially on target language words (slow, deliberate)
3. **Never shame** — "Oopsie!" not "That's wrong"
4. **Never over-explain** — 6 words max per sentence for under-5s
5. **Always trail into joy** — even corrections end on a rising hopeful note

---

## 2. Voice Script Examples (20+ Lines)

### 🌅 Welcome / Session Start
| Context | Line | Pacing |
|---------|------|--------|
| First launch | "Hiiiii Myra! I'm Roo! Let's learn some words!" | 2.5s, HUGE emphasis on "Hiiiii" |
| Returning user | "You're back! Yay yay yay! Let's do this!" | 1.8s, triple "yay" bouncy rhythm |
| New word appearing | "Ooooh! Look at THIS one!" | 1.2s, voice drops then rises on "this" |
| Category animals | "It's an animal word — I LOVE animals!" | 1.8s, elongated "love" |
| Category colors | "Ooh a color word! My favorite color is PINK! Surprise!" | 2.2s |

### ✅ Correct Answer Celebrations (Escalating)
| Attempt | Line | Pacing | Emotion |
|---------|------|--------|---------|
| First try correct | "ROOOAR-mazing!! You did it!! I knew you could!" | 2.8s | Explosive joy, voice cracks slightly on "it" |
| First try, fast | "WHOOOOA! So fast! You're incredible, Myra!" | 2.2s | Breathless surprise → pride |
| Second try correct | "YES! You kept trying and YOU GOT IT! That's my Myra!" | 2.8s | Relief → explosion of joy |
| Third try correct | "You didn't give up! That's the bravest thing ever!" | 2.5s | Soft emotional pride, voice slightly warm/tearful |
| Streak 3 in a row | "Three in a ROWWW! Roo is going to EXPLODE from happy!" | 2.8s | Escalating silliness |
| Streak 5 in a row | "FIVE! FIVE WORDS! Somebody get this dino a trophy!" | 2.5s | Absurdist enthusiasm |

### ❌ Incorrect Answer — Gentle Correction
| Attempt # | Line | Pacing | Tone |
|-----------|------|--------|------|
| 1st wrong | "Oopsie! Close though! Let's try one more time!" | 2s | Warm, playful, no gravity |
| 2nd wrong | "Hmmmm! I believe in you SO much. One more?" | 2s | Curious + encouraging, extra warmth |
| 3rd wrong (out of attempts) | "Awww! It's a tricky one! The word is… [word]. You'll get it next time, I promise!" | 3.5s | Gentle, soft, reassuring cadence |

### 🔁 Encouragement / Retry Prompts
| Context | Line | Pacing |
|---------|------|--------|
| Before recording | "[Name], let's say it together! Ready? 3… 2… 1…" | 2.5s, countdown is musical |
| Mic listening | "I'm all ears! Literally! Big ears!" | 1.5s, self-deprecating giggle |
| Recording too quiet | "A little louder! Roo's ears are tiny!" | 1.5s, playful |
| Processing | "Hmmmm let me think… I'm thinking… almost…" | 2s, theatrical suspense |
| Skip | "Ooh next one! Here it comes! Zoooom!" | 1.5s, speed-reading energy |

### 🎤 Tone Guidance for TTS/Voice Actors
```
Overall prosody direction:
- Start most lines LOW, crescendo to a HIGH peak in the middle,
  then land soft and warm at the end
- Celebrations: Start medium, EXPLODE at peak word ("ROOOAR-mazing"),
  land gently on close ("I knew you could")
- Corrections: Never start with energy — start slow, rise to the
  encouragement word, land on hope
- Always place a MICRO-PAUSE (0.15s) before the child's name —
  makes it feel personal and intentional
- Rhythmic lines ("yay yay yay") should feel like a song, not speech
```

---

## 3. Animation Choreography Breakdown

### Current State Machine (Keep All 5 States)
| State | CSS Class | Duration | Current Use |
|-------|-----------|----------|-------------|
| idle | `dino-idle` | 3s loop | Waiting, loading |
| celebrate | `dino-celebrate` | 0.8s × 2 | Correct answer |
| shake | `dino-shake` | 0.6s | Wrong answer |
| talk | `dino-talk` | 0.3s infinite | TTS playing |
| ask | `dino-ask` | 3s loop | Prompting for repeat |

### New States to Add
| New State | CSS Class | Duration | Trigger |
|-----------|-----------|----------|---------|
| Blink | `dino-blink` | 0.25s | Every 3–5s randomly during idle |
| Tail wag | `dino-tailwag` | 1s loop | During celebrations |
| Head tilt (curious) | `dino-tilt` | 0.5s | Wrong answer — 1st attempt |
| Excited bounce | `dino-bounce` | 0.4s × 3 | Streak rewards |

### Animation Timing Philosophy (Disney Principles Applied)
```
ANTICIPATION before every action:
  - Before jump: 1 frame squish DOWN (50ms)
  - Before spin: 1 frame lean INTO spin direction (80ms)
  - Before celebration: freeze for 80ms, then EXPLODE

EASE IN / EASE OUT:
  - All bounces: cubic-bezier(0.34, 1.56, 0.64, 1) — overshoot spring
  - All shakes: cubic-bezier(0.5, 0, 0.5, 1) — snappy with brake
  - Mouth opens: ease-in-out, 130ms per frame

FOLLOW-THROUGH:
  - After jump: body lands, compresses slightly, springs back
  - After shake: comes to rest with 1 small final wobble
  - After celebration: tail continues wagging 2s after body stops

SQUASH AND STRETCH:
  - On jump peak: scaleX 0.92, scaleY 1.1 (tall and thin = stretched)
  - On landing: scaleX 1.1, scaleY 0.88 (wide and flat = squashed)
  - Timing: stretch for 150ms at peak, squash for 100ms on land
```

### Complete Correct Answer Choreography (2.8s total)
```
T=0ms      Anticipation squish (scaleY 0.88, 80ms)
T=80ms     JUMP: translateY -35px + scaleY 1.1 + scaleX 0.92 (200ms, spring ease)
T=280ms    Peak: freeze 60ms, mouth opens wide, teeth show, eye pupils grow 20%
T=340ms    Confetti burst fires (60 pieces)
T=340ms    Landing squash: translateY 0px + scaleY 0.86 + scaleX 1.12 (120ms)
T=460ms    Spring recovery to normal scale (220ms, overshoot +5%)
T=680ms    Tail wag begins (1s loop, continues 2s)
T=680ms    Sparkles appear around dino (8 sparkles, 0.6s fade)
T=800ms    Optional second jump (smaller: 20px) if streak ≥ 3
T=2200ms   Begin idle transition, tail wag fades
T=2800ms   Full idle — ready for next word
```

### Complete Wrong Answer Choreography (1.0s total)
```
T=0ms      Shake begins: translateX +12px, -4° rotation (80ms)
T=80ms     Reverse: translateX -12px, +4° rotation (80ms)
T=160ms    Smaller echo: translateX +7px, -2° (80ms)
T=240ms    Reverse: translateX -7px, +2° (80ms)
T=320ms    Final wobble: translateX +3px (60ms), then settle to 0
T=380ms    Head tilts right 8° (curious expression) — 500ms hold
T=380ms    Pupil shifts direction (SVG translate, appears thoughtful)
T=500ms    Beeeep sound completes
T=880ms    Head returns to neutral
T=1000ms   Ask state begins if retrying
```

### Idle Breathing (Baseline — Always Active)
```
Keyframe:
  0%   → transform: translateY(0px) scaleY(1.0)
  40%  → transform: translateY(-8px) scaleY(1.02)   [float up + slight chest rise]
  70%  → transform: translateY(-6px) scaleY(1.01)   [hold top]
  100% → transform: translateY(0px) scaleY(1.0)     [settle back]

Duration: 3.0s, ease-in-out, infinite
Add: random blink every 3–5s (separate 250ms animation)
Blink: scaleY of eye from 1.0 → 0.05 → 1.0, cubic-bezier(0.4,0,0.6,1)
```

---

## 4. Sound Design Guidelines

### Sound Palette Philosophy
```
Aesthetic: "Pastel Studio" — warm, rounded, organic
Inspiration: Kirby games, Bluey, Sesame Street
Anti-pattern: Metal, sharp transients, percussive attacks,
              adult game reward sounds (too "casino-y")

All sounds should feel like they were made with:
  - Marimba / xylophone (warm wood)
  - Glockenspiel (bright but not piercing)
  - Soft choir "ooh" pads
  - Children's hand-drums (bouncy)
  - Musical toy squeaks (NOT annoying toy squeaks)
```

### The "Beeeep" Sound — Specification
```
Current: 0.78x playback of generic beep file
Target design:
  - Pitch: B3 (247 Hz) — low enough to feel "deflated" not alarming
  - Shape: Sine wave with soft attack (30ms), slow decay (400ms)
  - Duration: 500ms total
  - Effect: Slight pitch drop at tail (B3 → A3, last 100ms) — sounds "drooping"
  - Layered: Soft "wah" formant on top (like a cartoon trombone)
  - Volume: 60% of celebration sounds — clearly quieter
  - Feel: "Aw, shucks" not "WRONG"
  - NOT: buzzer, alarm, game-show wrong-answer sound
```

### The "Yaaaaay" Sound — Specification
```
Current: 1.35x playback of generic cheer file
Target design:
  - Pitch: Ascending arpeggio C4 → E4 → G4 → C5 (major chord climb)
  - Instruments: Glockenspiel + choir "aah" + soft drum hit at peak
  - Duration: 2.0s total
  - Shape: Builds for 800ms, peaks at 1.0s, sparkles out over 1.0s
  - Layered: Tiny crowd clap sound (muffled, not stadium — like 4 friends clapping)
  - Volume: 85% of max — present but not startling
  - Feel: "Best day ever!" energy, contained
```

### Additional Sound Effects (Priority Order)
| Sound | Trigger | Description | Duration |
|-------|---------|-------------|----------|
| Button tap | Every button press | Soft "pop" (D5, 80ms, marimba) | 80ms |
| Word reveal | Card appears | Rising 2-note "doo-doot" (G4→B4) | 300ms |
| Recording start | Mic opens | Soft "blip" + gentle whoosh | 200ms |
| Recording end | Mic closes | Reverse of start sound | 200ms |
| Thinking | Processing audio | Soft musical question mark (3 ascending staccato notes) | 600ms |
| Streak unlock | 3-in-a-row, 5-in-a-row | Short celebratory fanfare (brighter than single correct) | 1.5s |
| Milestone (10 words) | Score hits 10 | Full 3s musical celebration | 3.0s |

### Background Ambient Audio (Optional, Low Priority)
```
Concept: "Dino Meadow" — nature sounds from a fantasy world
- Very low volume (5–10% of voice)
- Soft bird chirps, gentle wind, distant musical notes
- Loops seamlessly at 60s
- Pauses during recording (critical — avoids Whisper confusion)
- Can be toggled in settings
```

---

## 5. Emotional Design Principles

### The Dopamine Loop Architecture
```
Correct path:
  Anticipation (hear word) → Effort (record) →
  Suspense (processing) → RELIEF + JOY (correct) →
  Residual warmth (tail wag, stars) → Curiosity (next word?)

This loop is 8–12 seconds. For 4-year-olds, this is EXACTLY right.
Never collapse it below 6s (skips anticipation = no dopamine).
Never stretch above 15s (attention breaks = frustration).
```

### The Safety Net Architecture (Wrong Answers)
```
Principle: Every wrong answer must END in a forward-looking statement.
Never let the interaction terminate on "wrong." Always route to:
  → "One more try!" OR
  → "Here's the answer… next time!" OR
  → "Let's try a different word!"

The child must NEVER feel stuck or shamed. Shame = quit.
```

### Why Voice + Animation Must Be Simultaneous
```
Ages 4–6 process information via TWO channels:
  - Audio (primary for pre-readers)
  - Visual movement (secondary)

When animation leads audio by >300ms: child loses sync → confusion
When audio leads animation by >500ms: child disengages visually

Sweet spot: Animation triggers at T=0, audio starts within 100ms.
All choreography in this doc is written to this timing contract.
```

### How to Prevent Frustration Spiral
```
Rule: After 2 wrong answers on the SAME word:
  1. Dino switches from "Try again!" energy to "It's a tough one!" energy
  2. Voice becomes softer, slower, more intimate
  3. Roo leans in (dino-ask state), whispers-feeling delivery
  4. Offer a visible hint: romanized pronunciation appears with highlight
  5. After 3rd wrong: reveal answer with ZERO shame language
     ("The word is X — you'll get it next time, I KNOW you will!")
  6. Move on within 3 seconds. Don't linger on failure.
```

### Avoiding Addiction Patterns
```
This app is for LEARNING, not engagement maximization.
Design guardrails:
  - No infinite scroll of words (session ends naturally)
  - No "just one more!" dark patterns
  - Celebrations are proportional — don't escalate rewards infinitely
  - Max session encouragement = 15–20 minutes natural attention span
  - No FOMO mechanics ("Your streak will disappear!")
  - Progress is always preserved, never threatened
```

---

## 6. Sample Interaction Flow (Full Walkthrough)

### Scenario: Myra sees "cat / మెకూరి" and gets it right on second try

```
T=0.0s    [WORD APPEARS]
          - Card slides in from right (300ms, spring ease)
          - Word reveal sound: "doo-doot"
          - Roo in idle state
          - Speech bubble: "Ooooh! Look at THIS one!"
          - TTS: plays "Ooooh! Look at THIS one!" (Roo voice, 1.2s)
          - Dino: talk state during TTS

T=1.5s    [AUTO-PLAY WORD]
          - Speech bubble: "Listen carefully! 👂"
          - TTS: plays "మెకూరి" slowly (Telugu, slow=True, ~1.0s)
          - Dino: talk state, mouth syncs

T=2.8s    [IDLE + INVITE]
          - Speech bubble: "Ready to try? 🌟"
          - Dino: idle state, breathing animation
          - Buttons visible: "🔊 Hear It!" "🎤 Say It!"

T=4.0s    [MYRA TAPS "SAY IT!"]
          - Button squish animation (50ms scale 0.92 → 1.0)
          - Button tap sound (D5 marimba pop)
          - Dino → ask state (mirrors, faces Myra)
          - TTS: "Myra, repeat after me!" (1.2s)
          - TTS: "మెకూరి" slow (Telugu, 1.0s)
          - 500ms silence

T=7.0s    [RECORDING BEGINS]
          - Recording indicator: blinking red dot + "Listening… 5s"
          - Mic sound: soft "blip"
          - Speech bubble: "I'm all ears! Literally!" (Roo voice)
          - Dino: ask state continues

T=10.0s   [MYRA SPEAKS — ATTEMPT 1]
          - (Myra says something close but not quite right)
          - Recording auto-stops at 5s OR Myra taps Stop
          - Mic close sound: reverse blip

T=10.2s   [PROCESSING]
          - Speech bubble: "Hmmmm… let me think…" (theatrical)
          - Dino: talk state (mouth loop)
          - Processing sound: 3 ascending staccato notes
          - Tiny spinning indicator inside speech bubble

T=11.5s   [RESULT: WRONG — ATTEMPT 1]
          - Shake animation begins (600ms)
          - Card flashes red glow
          - Beeeep sound (B3 descending, 500ms)
          - Head tilt right — curious expression
          - Speech bubble: "Oopsie! Close though!"
          - TTS: "Oopsie! Close though! Let's try one more time!" (2s)
          - Attempt dot 1: turns red
          - 300ms pause

T=14.2s   [RETRY — ATTEMPT 2]
          - Dino → ask state, extra lean-in toward Myra
          - Speech bubble: "Try again! 💪"
          - TTS: "Myra, one more time!"
          - TTS: "మెకూరి" (slower than before — even more deliberate)
          - Romanized hint text brightens/pulses gently
          - 500ms silence, then recording starts

T=17.5s   [MYRA SPEAKS — ATTEMPT 2]
          - Correct this time!

T=18.0s   [PROCESSING]
          - Same processing UI as before

T=19.0s   [RESULT: CORRECT!]
          T+0ms:   Confetti fires (60 pieces, pastel colors)
          T+0ms:   Sparkle particles appear around dino
          T+80ms:  Anticipation squish (dino compresses)
          T+160ms: JUMP — dino leaps 35px, stretches tall
          T+280ms: Peak hold — mouth WIDE open, pupils dilate
          T+340ms: Yaaaaay sound begins (ascending arpeggio)
          T+460ms: Landing squash
          T+680ms: Spring recovery + tail wag begins
          T+800ms: Score increments visually (+ 1 ★ pops up)
          T+900ms: Speech bubble: "ROOOAR-mazing!! You did it!!"
          T+900ms: TTS: "ROOOAR-mazing!! You did it!! I knew you could!" (2.8s)
          T+3700ms: Confetti fades
          T+3900ms: Tail wag slows

T+4200ms  [TRANSITION TO NEXT WORD]
          - Speech bubble: "Next word coming… 🚀"
          - TTS: "Ooh next one! Here it comes! Zoooom!" (1.5s)
          - Current card slides out left
          - Dino → idle (anticipatory breathing)
          - New word card slides in
```

---

## 7. Do's and Don'ts for Ages 4–6

### ✅ DO
- **DO** use the child's name every 2–3 interactions — it activates attention
- **DO** give audio feedback within 200ms of any interaction
- **DO** let celebrations run their full duration — don't rush to next word
- **DO** use rising intonation at sentence end (sounds positive)
- **DO** vary the celebration lines — same line 3x = invisible
- **DO** make the "wrong" sound feel different from error sounds in apps they use
- **DO** show what the correct answer was — curiosity is productive
- **DO** use rhythm in your lines — kids process rhythmic speech easier
- **DO** let the dino be silly — humor = safety = learning
- **DO** pre-load TTS audio for the next word during the celebration window
- **DO** pause ALL audio during recording (ambient + background)
- **DO** animate mouth during ALL speech (builds trust, feels alive)

### ❌ DON'T
- **DON'T** let silence exceed 1.5s without audio or animation
- **DON'T** use buzzer/alarm sounds for wrong answers — ever
- **DON'T** end any negative state without routing forward ("try again" or "next word")
- **DON'T** make celebrations too similar — monotony = checked-out
- **DON'T** auto-advance within 1.8s of correct — let the joy breathe
- **DON'T** use "Good girl/boy" — too gendered and adult-coded
- **DON'T** show score prominently during failure streaks — shame blocker
- **DON'T** interrupt Roo's voice with the next state — always let audio complete
- **DON'T** use adult "success" sounds (casino ding, achievement unlocked sounds)
- **DON'T** repeat the SAME encouragement line twice in one session
- **DON'T** play ambient music during mic recording (whisper confusion)
- **DON'T** let any single interaction flow exceed 15s without feedback

---

## 8. Accessibility Suggestions

### For Speech Delay / Late Talkers
```
- Add "Tap mode": Child taps the correct word from 2 picture options
  → Same dino celebration, no speech recognition required
- Lower similarity threshold to 30 for non-native speakers
- Extend recording window to 8s (settings option)
- Add visual countdown bar (not just number) — easier for pre-readers
- Whisper model already handles quieter speech fairly well — no change needed
```

### For Sensory Sensitivity
```
- "Calm mode" toggle in settings:
  - Confetti disabled (motion-sensitive children)
  - Celebration sound at 40% volume
  - Dino still celebrates (animated) but no confetti burst
  - Background ambient disabled
  - All transitions slower: 1.5× timing multiplier
- All sound effects individually toggleable
- Screen brightness: no flashing more than 3Hz (WCAG 2.3.1 compliance)
```

### For Visual Impairment
```
- All button functions announced via aria-label
- Dino state changes trigger aria-live announcements
- "Roo says: [message]" in aria-live region on each speech bubble update
- Minimum touch target: 48×48px (current buttons appear compliant)
```

---

## Implementation Roadmap (Priority Order)

### Phase 1 — Voice Layer (Highest Impact, Moderate Effort)
1. Add `/api/dino-voice` endpoint — gTTS for Roo's English lines
2. Expand MESSAGES dict with 20 lines per state (vs current 4)
3. Play Roo's voice lines (English) via TTS on all state transitions
4. Add "Myra, repeat after me!" voice before EVERY recording prompt
5. Expand reaction sounds: beeeep (sad trombone) + yaaaaay (arpeggio)

### Phase 2 — Animation Depth (High Impact, Moderate Effort)
6. Add `dino-blink` CSS animation + random JS timer (every 3–5s)
7. Add `dino-tilt` state for 1st wrong answer (head tilt = curious not sad)
8. Enhance `dino-celebrate` with squash/stretch keyframes
9. Add tail wag animation (SVG transform on tail path)
10. Add sparkle burst on correct (DOM particles, not just confetti)

### Phase 3 — Sound Design (High Impact, Lower Effort)
11. Create/source beeeep.mp3 (B3 descending, 500ms)
12. Create/source yaaaaay.mp3 (ascending arpeggio, 2s)
13. Add button tap sound (marimba pop, 80ms)
14. Add word reveal sound (doo-doot, 300ms)
15. Add streak sounds for 3-in-a-row and 5-in-a-row

### Phase 4 — Engagement Layer (Medium Impact, Higher Effort)
16. Streak counter (3-in-a-row, 5-in-a-row, 10-in-a-row notifications)
17. Session score star display (bottom bar, animated +1 on correct)
18. Milestone celebration at 10 words learned in session
19. Romanized hint animation (brightens/pulses before retry)
20. "Calm mode" accessibility toggle in settings

---

## Critical Files to Modify

| File | Changes |
|------|---------|
| [static/js/app.js](../static/js/app.js) | Expand MESSAGES, add voice TTS calls, blink timer, streak logic |
| [static/css/style.css](../static/css/style.css) | Add dino-blink, dino-tilt, dino-tailwag, enhance celebrate |
| [templates/index.html](../templates/index.html) | Add sparkle DOM elements, streak display, calm mode toggle |
| [main.py](../main.py) | Add `/api/dino-voice` endpoint |
| [static/sounds/](../static/sounds/) | Add beeeep.mp3, yaaaaay.mp3, tap.mp3, reveal.mp3 |

---

## Verification Checklist

After implementation, walk through this checklist manually:

- [ ] Roo's voice plays on every state transition (not just text)
- [ ] Mouth animates during ALL voice lines
- [ ] Correct answer: jump → confetti → voice → tail wag → next word (in that order)
- [ ] Wrong answer: shake → head tilt → soft beeeep → encouraging voice line
- [ ] Blink fires randomly during idle (watch for 10 seconds)
- [ ] Button tap sound plays on every tap
- [ ] No audio plays during microphone recording window
- [ ] Celebration voice line is different from previous 3 celebrations
- [ ] 3-in-a-row streak triggers special celebration
- [ ] All sounds stop when Stop button is pressed
- [ ] Settings "Calm Mode" disables confetti + reduces sound
