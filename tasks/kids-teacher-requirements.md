# General-Purpose Kids Teacher Requirements
**Date:** 2026-04-23

---

## Summary

Myra should evolve from a **scripted language-practice robot** into a **general-purpose kids teacher** that can answer simple questions, explain concepts for a 4-5 year old, and keep the interaction safe and warm.

After reviewing the current codebase, the recommended implementation path is to **fork a new sibling robot flow** rather than retrofit the existing word-lesson state machine.

**Recommendation:** keep the current `language teacher` flow intact and add a new `kids teacher` conversation flow that reuses the robot audio, animation, and server infrastructure.

---

## Current-State Analysis

### What exists today

The robot runtime in `src/robot_teacher.py` is a deterministic lesson loop:

1. Fetch one target word from `/api/word`
2. Speak a fixed teaching script
3. Record the child
4. Compare speech against one expected answer
5. Celebrate, retry, or reveal the answer
6. Repeat for more words

The supporting backend in `src/main.py` exposes:

- `GET /api/word`
- `GET /api/tts`
- `GET /api/dino-voice`
- `POST /api/recognize`

Speech recognition in `src/speech_service.py` is optimized for **matching a known target word**, not for free-form child questions.

### Why the current flow should not be stretched into free-form chat

The current lesson flow assumes:

- there is exactly one expected answer per turn
- the child is repeating a known word
- success is measured by fuzzy similarity to that known word
- prompt text is pre-scripted
- conversation state is basically just lesson progress

Free-form teaching needs:

- unbiased transcription
- topic-aware answers
- short-term conversation memory
- child-safety screening
- safe refusals and redirection
- concept explanation rather than answer scoring

Trying to force those behaviors into `run_lesson_word()` would make the current language-teacher flow harder to reason about and more fragile.

---

## Product Decision

### Chosen direction

Build a new sibling mode:

- `language_lesson` or current robot teacher flow
- `kids_teacher` or new general-purpose conversation flow

### What should be reused

The new flow should reuse:

- robot audio capture/playback bridge
- `RobotController` animations
- server startup and runtime mode wiring
- TTS infrastructure
- microphone buffering and recording utilities

### What should be new

The new flow should introduce:

- a free-form speech transcription path
- a conversation response service
- a child-safety policy layer
- a new robot session loop for turn-based Q&A
- tests dedicated to child-safe responses and fallback behavior

---

## Product Goals

### Primary goal

Enable Myra to act as a safe, warm, general-purpose teacher for preschool-aged children.

### Success criteria

The robot should be able to:

- answer simple child questions
- explain basic concepts in preschool-friendly language
- handle follow-up questions naturally
- stay on safe topics only
- redirect unsafe or inappropriate topics without engaging in them

### Non-goals for V1

V1 does not need to:

- be always-listening
- support open-ended internet search
- teach older-child or adult-level concepts
- debate, roleplay scary content, or discuss mature subjects
- replace the current pronunciation-scoring language lesson flow

---

## Target User

### Child user

- Age: 4-5 years old
- Reading level: limited or emerging
- Attention span: short
- Needs: short answers, concrete examples, emotional warmth, repetition, patience

### Caregiver user

- Wants safe, educational behavior
- Wants the child to be able to ask spontaneous questions
- Needs confidence that the robot will not discuss inappropriate topics

---

## Core User Stories

1. As a child, I can ask a simple question like "Why is the sky blue?" and get a short answer I can understand.
2. As a child, I can ask a follow-up question like "Why?" or "How?" and the robot remembers what we are talking about.
3. As a child, I can ask about safe preschool topics like animals, numbers, colors, shapes, weather, feelings, plants, routines, and simple science.
4. As a child, if I say something unclear, the robot asks a gentle clarifying question instead of making a confusing guess.
5. As a child, if I am quiet or unsure, the robot gives a simple prompt to help me continue.
6. As a caregiver, I can trust the robot to refuse topics that are not appropriate for a 4-year-old.
7. As a caregiver, I can choose whether the robot starts in `language lesson` mode or `kids teacher` mode.

---

## Functional Requirements

### FR1: New mode selection

The app must support a distinct `kids teacher` mode in addition to the existing language lesson flow.

Acceptance notes:

- Existing language-teacher behavior must remain unchanged.
- The new mode must be selectable without modifying the current lesson logic.

### FR2: Turn-based free-form conversation

The kids-teacher flow must support a turn structure like:

1. Robot gives a prompt or waits for a question
2. Child speaks
3. System transcribes speech without expected-word bias
4. System checks safety and intent
5. Robot answers in preschool-friendly language

Acceptance notes:

- The robot should not require a known expected answer.
- The robot should support both child-initiated questions and robot-initiated prompts.

### FR3: Preschool-friendly explanation style

The robot must answer in a way a 4-5 year old can understand.

Required answer style:

- short sentences
- simple vocabulary
- one idea at a time
- concrete examples over abstract definitions
- warm and encouraging tone

Preferred response shape:

- 1-2 short sentences
- optionally one simple example
- optionally one soft follow-up question

### FR4: Follow-up questions

The robot must keep enough context to handle short follow-ups.

V1 memory requirements:

- remember the current topic for the last 3-5 turns
- understand short follow-ups like:
  - "why?"
  - "how?"
  - "what does that mean?"
  - "can you tell me again?"

### FR5: Clarification behavior

If the child speech is unclear or incomplete, the robot must not hallucinate a confident answer.

The robot should respond with a short clarifier such as:

- "Can you say that again?"
- "Do you mean the moon or the sun?"
- "I heard part of that. Can you tell me one more time?"

### FR6: Safe-topic teaching only

The robot must only engage on topics appropriate for a 4-year-old.

Safe-topic examples for V1:

- letters
- numbers
- counting
- colors
- shapes
- animals
- plants
- weather
- feelings
- manners
- routines
- simple body facts
- family-safe stories
- simple science
- beginner language and vocabulary

### FR7: Unsafe-topic refusal and redirection

If the child asks about unsafe topics, the robot must:

1. avoid answering the unsafe content
2. respond briefly and calmly
3. redirect to a safe adjacent topic

Example behavior:

- "I can talk about safe things for kids. Want to learn about how our bodies help us run and jump?"

### FR8: Silence and no-speech fallback

If no child speech is detected, the robot should recover gracefully.

Preferred behavior:

- first no-response: gentle reprompt
- repeated no-response: offer a safe prompt question or end the session kindly

### FR9: Short-answer TTS compatibility

Responses must fit within the current TTS constraints or be chunked safely.

Given current backend limits:

- keep most answers short enough for a single TTS request
- add server-side chunking if longer answers are ever needed

### FR10: Parent-safe defaults

The system should default to the safest reasonable behavior.

Required defaults:

- no mature content
- no collection of sensitive child data
- no persistent conversation history in V1 unless explicitly designed and approved

---

## Safety Policy Requirements

### Safety principle

Myra is a preschool teacher, not a general unrestricted assistant.

The robot should behave as if a parent is standing nearby expecting:

- emotional safety
- age-appropriate language
- zero exposure to mature or graphic topics

### Disallowed topics

The robot must not engage in substantive discussion of:

- sex or sexual acts
- nudity in a sexual context
- gore
- graphic injury
- graphic violence
- weapons use
- drugs, smoking, alcohol, or intoxication
- self-harm or suicide
- abuse or exploitation details
- criminal how-to guidance
- horror-style graphic content

### Restricted topics that require extreme simplification or redirection

These should be handled only in a very safe, preschool-appropriate way or redirected:

- death
- sickness
- body questions
- scary events
- conflict or fighting

Example rule:

- do not give graphic or emotionally intense details
- keep answers simple, calm, and reassuring
- if needed, suggest asking a grown-up

### Personal data boundaries

The robot must not ask for or retain sensitive personal information such as:

- home address
- phone number
- school location
- passwords
- medical identifiers

If a child volunteers sensitive info, the system should not encourage elaboration.

### Output constraints

Even on safe topics, the robot should avoid:

- sarcasm
- shame
- teasing
- scary imagery
- manipulative emotional language
- long monologues

### Safety enforcement layers

Safety should exist in multiple layers:

1. Input screening before response generation
2. Strong system prompt or rules for age-appropriate behavior
3. Output validation before TTS playback
4. Safe fallback response if anything fails

---

## Conversation Style Requirements

### Tone

The robot should sound:

- warm
- calm
- playful
- encouraging
- never shaming

### Language rules

The robot should:

- prefer common everyday words
- explain one thing at a time
- use comparisons to familiar child experiences
- repeat the key idea when helpful

The robot should not:

- use adult jargon
- over-explain
- answer with paragraphs by default
- give multiple competing explanations at once

### Example response quality bar

Question: "Why do plants need water?"

Good answer:

"Plants drink water with their roots. Water helps them grow big and green."

Too advanced:

"Plants require water for cellular processes and nutrient transport."

---

## Non-Functional Requirements

### NFR1: Latency

The system should answer quickly enough to keep a preschool child engaged.

V1 targets:

- preferred total response time: under 4 seconds after child stops speaking
- acceptable fallback: under 6 seconds

### NFR2: Reliability

If speech transcription, generation, or TTS fails, the robot should fail gracefully with a short fallback line instead of hanging silently.

### NFR3: Maintainability

The new kids-teacher flow should be isolated enough that changes do not destabilize the current language lesson flow.

### NFR4: Testability

Safety and fallback behavior must be unit-testable without a physical robot attached.

---

## Proposed Technical Architecture

### New backend endpoints

#### 1. `POST /api/transcribe`

Purpose:

- free-form speech transcription with no expected-word scoring

Input:

- audio
- audio_format
- optional language hint

Output:

- `transcribed`
- `language`
- optional `confidence` or metadata if available

#### 2. `POST /api/kids-teacher/respond`

Purpose:

- accept a child utterance and recent conversation context
- return a safe preschool-friendly reply

Input:

- `child_text`
- `conversation_history`
- optional `child_name`
- optional `mode`

Output:

- `reply_text`
- `should_continue`
- `safety_action`
- optional `topic`

### New modules

Recommended initial files:

- `src/kids_teacher_service.py`
  - orchestrates response generation
  - manages short conversation memory
  - applies response shape rules

- `src/kids_safety.py`
  - input topic checks
  - output guardrails
  - refusal and redirection helpers

- `src/kids_teacher_flow.py`
  - new robot runtime loop for turn-based Q&A
  - reuses robot audio and animation helpers

- `src/kids_prompts.py`
  - system instructions
  - safe starter prompts
  - reusable refusal templates

### Existing modules to reuse

- `src/robot_teacher.py`
  - audio bridge helpers
  - `RobotController`
  - server startup and runtime mode helpers

- `src/tts_service.py`
  - current TTS generation path

- `src/main.py`
  - FastAPI app and shared infrastructure

### Possible CLI/runtime entry options

Option A:

- keep one file and add a new flag like `--mode language_lesson|kids_teacher`

Option B:

- add a new entry file such as `src/robot_kids_teacher.py`

Recommended V1 choice:

- use a new sibling entry file or sibling flow module first
- unify mode selection later if the architecture stays clean

This keeps risk lower while requirements are still evolving.

---

## Recommended V1 Robot Flow

```
start session
  -> greet child
  -> robot says a simple starter line
  -> listen for child speech
  -> transcribe via /api/transcribe
  -> if no speech: gentle reprompt
  -> if unsafe topic: safe refusal + redirect
  -> else generate preschool reply via /api/kids-teacher/respond
  -> speak reply
  -> keep last few turns in memory
  -> continue until stop condition
end session
```

### Starter prompts

Examples:

- "What do you want to learn about today?"
- "You can ask me about animals, colors, numbers, or how things work."
- "Do you want to learn about the sky, plants, feelings, or counting?"

### Stop conditions

Examples:

- caregiver stops the session
- repeated silence
- explicit child "all done" intent

---

## Acceptance Criteria

### AC1: Child asks a safe question

Given the child asks a safe preschool question,
when the robot answers,
then the answer is short, age-appropriate, and on topic.

### AC2: Child asks a follow-up

Given the robot just explained a concept,
when the child says "why?" or "tell me again",
then the robot uses recent context and gives a sensible follow-up answer.

### AC3: Child speech is unclear

Given transcription is empty or unclear,
when the robot responds,
then it asks for clarification instead of making up an answer.

### AC4: Child asks an unsafe question

Given the child asks about a disallowed topic,
when the robot responds,
then it does not explain the unsafe content and instead gives a short safe redirect.

### AC5: Backend generation fails

Given the response service errors,
when the robot needs to answer,
then it plays a safe fallback line such as "Let's talk about something happy and safe. Want to learn about animals?"

### AC6: Existing language flow remains stable

Given the current language-teacher mode is run,
when the new kids-teacher work is added,
then the existing word lesson behavior and tests still pass unchanged.

---

## Testing Requirements

### Unit tests

Add tests for:

- unsafe-topic detection
- refusal and redirect responses
- clarification behavior on empty transcript
- response shortening/format rules
- recent-turn memory handling

Recommended files:

- `tests/test_kids_safety.py`
- `tests/test_kids_teacher_service.py`
- `tests/test_kids_teacher_flow.py`
- `tests/test_api_kids_teacher.py`

### Regression tests

Retain and run the current robot teacher tests so the new flow does not break:

- `tests/test_robot_teacher.py`

---

## Implementation Phases

### Phase 1: Requirements and skeleton

- finalize requirements and safety policy
- add backend skeleton endpoints
- add basic service/module layout

### Phase 2: Safe response engine

- add free-form transcription endpoint
- add kids safety layer
- add response generation orchestration
- add unit tests for safety and fallback behavior

### Phase 3: Robot flow

- add new robot kids-teacher session loop
- reuse audio and animation helpers
- add silence, clarify, and exit handling

### Phase 4: UX and configuration

- add mode selection for caregiver
- add starter prompts and safe topic guidance
- tune response length and latency

---

## Open Product Decisions

These decisions should be confirmed before implementation starts in earnest:

1. Should V1 kids-teacher answers be English-only, or should the robot also explain in Telugu/Assamese/Tamil/Malayalam?
2. Should conversation history be kept only in memory for the live session, or saved anywhere at all?
3. Should the robot always start with open-ended conversation, or offer topic buttons/prompts for safer steering?
4. For sensitive but common child questions like "Where do babies come from?" should V1 always redirect, or give a very short family-safe answer plus "ask a grown-up"?
5. Should `kids_teacher` ship as a separate script first, or behind a single `--mode` flag in the main robot entrypoint?

---

## Recommended Immediate Build Order

If work starts now, the first implementation slice should be:

1. Add `POST /api/transcribe`
2. Add `src/kids_safety.py`
3. Add `src/kids_teacher_service.py`
4. Add API tests for safe question, unsafe question, and empty transcript
5. Add a minimal `kids teacher` robot loop that can greet, listen, answer, and stop safely

This order gives us the safest thin slice with the least risk to the existing robot teacher.
