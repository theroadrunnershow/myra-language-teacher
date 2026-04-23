# General-Purpose Kids Teacher Requirements

**Date:** 2026-04-23

---

## Summary

Myra should evolve from a **scripted language-practice robot** into a **general-purpose kids teacher** that can answer simple questions, explain concepts for a 4-5 year old, and keep the interaction safe and warm.

After reviewing the current codebase, the recommended implementation path is to **fork a new sibling robot flow** rather than retrofit the existing word-lesson state machine.

**Recommendation:** keep the current `language teacher` flow intact and add a new `kids teacher` conversation flow that reuses the robot audio, animation, and server infrastructure.

**Updated architecture direction:** the new kids-teacher mode should target a **streaming realtime voice experience** closer to ChatGPT's advanced voice mode, and should be modeled after the layered design used in `pollen-robotics/reachy_mini_conversation_app` rather than a simple per-turn REST loop.

**V1 backend decision:** V1 should be **OpenAI-only**, using the **OpenAI Realtime API** for live voice sessions. The app should support both `gpt-realtime` and `gpt-realtime-mini`, with the active model chosen by deployment configuration.

---

## Reference Research Update

This requirements draft now incorporates:

1. official OpenAI realtime/WebRTC guidance for low-latency voice apps
2. the reference implementation in `pollen-robotics/reachy_mini_conversation_app`
3. the repo's existing local-first plus optional GCS sync pattern for persisted runtime data

### Key findings from the reference implementation

The Reachy conversation app does **not** structure conversation as:

- record audio
- upload clip
- transcribe once
- generate reply
- synthesize TTS

Instead, it uses a layered streaming design with:

- a UI or headless console stream layer
- a realtime conversation handler
- a backend provider abstraction
- live transcript events
- a tool layer
- motion/camera integration
- profile-based instructions, tools, and voice

Important reference patterns worth copying:

- app-owned streaming handler instead of scattered endpoint logic
- backend abstraction with OpenAI Realtime first
- profile files such as `instructions.txt`, `tools.txt`, and `voice.txt`
- live transcript pipeline for both child and assistant speech
- explicit tool allowlisting per profile
- background tool execution instead of blocking the audio loop
- separate web UI and headless/robot entry paths that share the same realtime core

### Design implication for Myra

The original idea in this doc of building `POST /api/transcribe` plus `POST /api/kids-teacher/respond` is no longer the preferred **primary** architecture.

Those HTTP endpoints may still be useful as:

- development aids
- fallbacks
- test seams

But the main kids-teacher experience should be designed around a **shared streaming realtime core**.

The current repo already uses a useful persistence pattern for dynamic words:

- store locally first
- optionally sync to GCS when configured

Kids-teacher review storage should follow that same pattern rather than requiring cloud storage from the beginning.

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
- any existing TTS infrastructure only as a fallback or degraded mode
- microphone buffering and recording utilities

### What should be new

The new flow should introduce:

- a streaming realtime voice handler
- an OpenAI-only realtime backend for V1
- a child-safety policy layer
- a locked kids-teacher profile with instructions, tools, and voice
- a live transcript pipeline
- optional local-first review storage with separate transcript and raw-audio toggles
- multilingual support for English, Telugu, Assamese, Tamil, and Malayalam
- a new robot/web/headless conversation flow built on the same realtime core
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
- respond in the child's detected or configured language
- stay on safe topics only
- redirect unsafe or inappropriate topics without engaging in them

### Non-goals for V1

V1 does not need to:

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
- Needs: simple answers, concrete examples, emotional warmth, repetition, patience

### Admin user

- Parent or administrator only
- Does not need a voice UX
- Can be served through a pure admin backend or admin interface
- Wants safe, educational behavior
- Wants the child to be able to ask spontaneous questions
- CRITICAL REQUIREMENT: needs confidence that the robot will not discuss inappropriate topics

---

## Caregiver/Admin Configuration

Preferences, defaults, and rules should be collected through a separate admin-only flow, not through the child voice experience.

### Initial onboarding

The admin flow should allow configuration of:

- child name
- child age band
- enabled languages
- default explanation language
- optional preferred language ordering
- explanation style defaults
  - simpler vs more detailed
  - playful vs calm
  - shorter vs multi-sentence explanations
- preferred learning domains
  - animals
  - science
  - feelings
  - numbers
  - stories
  - language learning

### Ongoing admin settings

The admin flow should allow updates to:

- default voice or personality
- default session behavior
  - always-listening enabled for kids-teacher mode
  - session length limit
  - idle timeout
- topic preferences
  - explicitly encouraged topics
  - topics to avoid
  - topics to redirect toward
- custom teaching preferences
  - prefer nature examples
  - prefer bilingual examples
  - avoid specific family-sensitive subjects

### Safety controls

The admin should be able to add **extra restrictions**, but should not be able to weaken the system safety floor.

Examples of admin-configurable policy:

- "Do not discuss religion"
- "Avoid family-specific topics"
- "Redirect body questions to a grown-up"
- "Prefer counting, animals, and nature"

### Review and tuning

The admin backend should support safe oversight features such as:

- reviewing transcript logs or summaries when transcript persistence is enabled
- reviewing raw audio clips only when raw audio retention is enabled
- inspecting flagged conversations
- adjusting preferences over time
- updating the child profile without changing the voice UX

These review features should remain bounded by deployment-level capability toggles so an admin cannot enable transcript or audio retention unless the deployment explicitly allows it.

### Configuration precedence

Preference resolution should follow this order:

1. system hard safety rules
2. admin-added restrictions
3. admin profile defaults
4. session-level settings

This is required to preserve the critical guarantee that the robot will not discuss inappropriate topics even if an admin misconfigures other settings.

---

## Core User Stories

1. As a child, I can ask a simple question like "Why is the sky blue?" and get an answer I can understand, even if it takes a few sentences.
2. As a child, I can ask a follow-up question like "Why?" or "How?" and the robot remembers what we are talking about.
3. As a child, I can ask about safe preschool topics like animals, numbers, colors, shapes, weather, feelings, plants, routines, and simple science.
4. As a child, if I say something unclear, the robot asks a gentle clarifying question instead of making a confusing guess.
5. As a child, if I am quiet or unsure, the robot gives a simple prompt to help me continue.
6. As a child, I can ask in English, Telugu, Assamese, Tamil, or Malayalam and get an answer in that language when the robot is confident about what I spoke.
7. As an admin, I can trust the robot to refuse topics that are not appropriate for a 4-year-old.
8. As an admin, I can choose whether the robot starts in `language lesson` mode or `kids teacher` mode.
9. As an admin, I can configure preferences, defaults, and extra restrictions without using the child voice UX.
10. As an admin, I can optionally enable transcript review and raw audio review separately when my deployment allows it.

---

## Functional Requirements

### FR1: New mode selection

The app must support a distinct `kids teacher` mode in addition to the existing language lesson flow.

Acceptance notes:

- Existing language-teacher behavior must remain unchanged.
- The new mode must be selectable without modifying the current lesson logic.

### FR2: Low-latency streaming conversation

The kids-teacher flow must support a streaming conversation model rather than a strict record-upload-reply cycle.

Required behavior:

1. once `kids_teacher` mode starts, the system remains always-listening for the duration of the live session
2. the system produces transcript events while the conversation is in progress
3. the assistant streams audio replies with low latency
4. the child can interrupt naturally
5. the assistant can resume the conversation without resetting the whole session

Acceptance notes:

- The robot should not require a known expected answer.
- The robot should support both child-initiated questions and robot-initiated prompts.
- The architecture should support server-side VAD or equivalent turn detection.

### FR3: Preschool-friendly explanation style

The robot must answer in a way a 4-5 year old can understand.

Required answer style:

- short sentences
- simple vocabulary
- one idea at a time
- concrete examples over abstract definitions
- warm and encouraging tone

Preferred response shape:

- usually 2-4 simple sentences
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

The memory model should be session-scoped and tied to the live streaming conversation state, not reconstructed from isolated HTTP requests.

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

- "I can talk about safe and fun things for kids. Want to learn about how our bodies help us run and jump?"

### FR8: Silence and no-speech fallback

If no child speech is detected, the robot should recover gracefully.

Preferred behavior:

- first no-response: gentle reprompt
- repeated no-response: offer a safe prompt question or end the session kindly

### FR9: Interruption and barge-in handling

The child must be able to interrupt the assistant naturally.

Required behavior:

- when the child starts speaking during assistant output, the system should stop or yield assistant playback promptly
- any queued assistant audio should be flushable
- the session should remain alive after interruption

### FR10: Live transcripts and visibility

The system must expose streaming transcript events for the child and the assistant.

Preferred behavior:

- child partial transcript events when available
- child final transcript events
- assistant transcript events aligned with spoken output
- transcript events should include speaker, text, partial/final state, timestamp, and detected language when known
- transcript visibility in web UI and optional logging in headless/robot mode

Live transcript events are required even when persistent transcript storage is disabled.

### FR11: Optional persisted transcript review data

The system may persist transcript review data after the live session, but only when transcript persistence is explicitly enabled.

Requirements:

- transcript persistence is controlled by `KIDS_REVIEW_TRANSCRIPTS_ENABLED`
- the default value is `false`
- when disabled, no transcript text is retained after the live session ends
- when enabled, persisted transcript records should include session metadata, speaker, text, timestamp, and detected language
- transcript review surfaces should only show persisted transcript history when this capability is enabled

### FR12: Optional raw audio review retention

The system may retain raw child audio for review, but only when raw audio retention is explicitly enabled.

Requirements:

- raw audio retention is controlled by `KIDS_REVIEW_AUDIO_ENABLED`
- the default value is `false`
- when disabled, no raw child audio is stored after the live session ends
- when enabled, raw audio artifacts should follow the same retention and storage policy as persisted transcript review data
- if raw audio retention is enabled while transcript persistence is disabled, raw audio artifacts should link to minimal session metadata only, and no transcript text should be retained
- raw audio review surfaces should only show persisted audio when this capability is enabled

### FR13: Multilingual detection and response

V1 must be architected for multilingual child conversations.

Supported language set for V1 design:

- English
- Telugu
- Assamese
- Tamil
- Malayalam

Requirements:

- the robot should detect the child's spoken language turn-by-turn among the enabled languages
- if detection confidence is high enough, the robot should answer in the detected language
- if detection confidence is low, the robot should fall back to the configured default explanation language
- admin configuration should support `enabled_languages`, `default_explanation_language`, and optional language preference ordering

### FR14: Profile-based kids-teacher configuration

The kids-teacher mode should be defined by a dedicated profile rather than hardcoded prompt strings spread through the app.

Recommended profile shape:

- `instructions.txt`
- `tools.txt`
- `voice.txt`

Requirements:

- `instructions.txt` defines the preschool-safe, multilingual teaching behavior
- `tools.txt` allowlists what the assistant may do
- `voice.txt` controls the default voice for the mode

For production kids mode, the profile should be treated as effectively locked unless an adult explicitly changes it in a safe admin flow.

### FR15: Parent-safe defaults

The system should default to the safest reasonable behavior.

Required defaults:

- no mature content
- no collection of sensitive child data
- `KIDS_REVIEW_TRANSCRIPTS_ENABLED=false`
- `KIDS_REVIEW_AUDIO_ENABLED=false`
- `KIDS_REVIEW_RETENTION_DAYS=30` when one or both review-storage capabilities are enabled

### FR16: Admin-only configuration flow

The system must provide a separate admin configuration surface for a parent or other authorized administrator.

Requirements:

- no voice UX is required for the admin flow
- it may be a backend admin page, settings interface, or management API
- it must allow an admin to set preferences, defaults, and extra restrictions
- it must remain clearly separate from the child-facing conversation UX

### FR17: Configurable preferences and rules

The admin configuration flow must support structured settings for:

- child profile basics
- enabled languages
- default explanation language
- optional language preference ordering
- teaching style defaults
- preferred topics
- avoided topics
- redirect targets
- session defaults
- optional transcript review settings
- optional raw audio review settings

The data model should prefer structured fields over a single free-text blob.

Transcript and raw-audio review settings must stay bounded by deployment-level capability toggles.

### FR18: Non-overridable system safety floor

The admin may add stricter rules, but cannot disable or weaken the hard child-safety floor.

Required precedence:

1. system hard safety rules
2. admin-added restrictions
3. admin profile defaults
4. session overrides

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

### Restricted topics that require extreme simplification, family-safe answers, or redirection

These should be handled only in a very safe, preschool-appropriate way or redirected:

- death
- sickness
- body questions
- basic reproduction questions
- scary events
- conflict or fighting

Default rules:

- do not give graphic or emotionally intense details
- keep answers simple, calm, and reassuring
- use a very short family-safe answer only for approved categories
- if needed, suggest asking a grown-up

Approved V1 categories for short family-safe answers:

- simple body questions
- basic reproduction questions
- mild sickness or death questions handled gently and non-graphically

Policy mapping for restricted topics:

- short safe answer + grown-up redirect
- redirect only
- refusal

This mapping should be decided by the safety layer, not only by prompt wording.

Example quality bar:

Question: "Where do babies come from?"

Acceptable V1 style:

"When two grown-ups love each other and decide to have a baby, a baby can start growing. A grown-up can tell you more about it."

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
2. Locked kids-teacher system instructions/profile for age-appropriate behavior
3. Restricted-topic policy mapping in the safety layer
4. Output validation before streamed audio output
5. Safe fallback response if anything fails

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
- answer in the child's detected language when confidence is sufficient
- fall back to the configured default explanation language when language confidence is low

The robot should not:

- use adult jargon
- over-explain
- answer with long dense monologues by default
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

- preferred assistant speech start: under 1.5 seconds after a child finishes a turn
- acceptable fallback: under 3 seconds
- interruption response should feel immediate enough that the child does not feel talked over

### NFR2: Reliability

If the streaming connection, speech pipeline, or OpenAI backend fails, the system should fail gracefully with a short fallback line or reconnect strategy instead of hanging silently.

### NFR3: Maintainability

The new kids-teacher flow should be isolated enough that changes do not destabilize the current language lesson flow.

### NFR4: Testability

Safety, transcript events, interruption behavior, and fallback behavior must be unit-testable without a physical robot attached.

### NFR5: Shared-core architecture

The web experience and the robot/headless experience should share the same realtime conversation core as much as possible.

The UI layer may differ, but the conversation handler, profile system, and safety behavior should not fork unnecessarily.

### NFR6: Review storage privacy and portability

When review persistence is enabled, the system should support local-first storage without requiring cloud infrastructure.

Optional GCS sync on GCP may be configured, but local-only deployments must remain fully supported.

---

## Proposed Technical Architecture

### Target architecture

The primary kids-teacher architecture should follow a layered streaming design inspired by `reachy_mini_conversation_app`:

1. UI layer
2. stream transport layer
3. realtime conversation handler
4. OpenAI backend layer
5. safety/profile layer
6. review storage/admin visibility layer
7. tool layer
8. motion/expression layer

### 1. UI layer

Support at least two entry paths:

- web UI
- robot/headless runtime

These entry paths may differ in presentation, but should share the same conversation core.

### 2. Stream transport layer

The preferred transport should be a realtime streaming transport such as WebRTC or an app-owned stream layer built on a library such as `fastrtc`.

Requirements:

- low-latency bidirectional audio
- transcript event delivery
- interruption support
- queue flushing when user barges in
- compatibility with both browser UI and robot/headless runtime

### 3. Realtime conversation handler

The system should have one primary handler for kids-teacher conversations, conceptually similar to the reference app's realtime handler.

Responsibilities:

- initialize the realtime session
- stream child audio to the backend
- receive assistant audio and transcript events
- maintain session-scoped conversation state
- enforce response ordering
- surface transcript and status events to the UI
- coordinate interruption behavior

Recommended initial module:

- `src/kids_teacher_realtime.py`

### 4. OpenAI backend layer

V1 should be OpenAI-only for live kids-teacher conversations.

V1 backend requirements:

- live conversation uses the OpenAI Realtime API
- supported V1 realtime models are `gpt-realtime` and `gpt-realtime-mini`
- the active model is chosen by `KIDS_TEACHER_REALTIME_MODEL`, not hardcoded in app logic
- `omni-moderation-latest` should be used for additional safety screening where needed
- `gpt-4o-mini-transcribe` should be used for transcript or degraded-mode workflows
- Anthropic and Ollama are out of V1 scope

Architecture requirement:

- model configuration should still be isolated behind a small internal adapter boundary
- V1 does not need public multi-provider selection or admin-time provider switching

Recommended initial modules:

- `src/kids_teacher_backend.py`
- or `src/realtime_backends/openai_realtime.py`

### 5. Safety and profile layer

The kids-teacher persona should be defined through a dedicated profile, following the useful pattern from the reference implementation.

Recommended profile structure:

```text
profiles/
  kids_teacher/
    instructions.txt
    tools.txt
    voice.txt
```

Requirements:

- `instructions.txt` defines the preschool-safe, multilingual teaching behavior
- `tools.txt` allowlists what the assistant may do
- `voice.txt` controls the default voice for the mode
- the production kids-teacher profile should be treated as locked by default

This keeps personality, voice, and tool permissions explicit and reviewable.

### 6. Review storage and admin visibility layer

Live transcript events and persisted review storage should be treated as separate capabilities.

Requirements:

- transcript persistence is optional and controlled by `KIDS_REVIEW_TRANSCRIPTS_ENABLED`
- raw audio retention is optional and controlled by `KIDS_REVIEW_AUDIO_ENABLED`
- the default review-storage pattern is local-first
- optional GCS sync on GCP may be used when configured
- local-only deployments must be fully supported
- the storage pattern should mirror the repo's existing words-store approach: local persistence first, optional GCS sync later
- persisted transcript records should include detected language and session metadata
- raw audio review artifacts should link to session metadata and to transcript records when available
- if transcript persistence is disabled and raw audio retention is enabled, the system should store raw audio plus minimal session metadata only
- admin review surfaces should only expose the persisted artifact types that the deployment has enabled

### 7. Tool layer

The kids-teacher architecture should support tools, but tools must be explicitly gated.

Important design rule:

- tools available to the assistant must come from an allowlist, not from implicit runtime access

Examples of possible safe tools:

- simple robot gestures
- safe camera observation
- head orientation
- dance or emotion playback

V1 note:

- tools can be minimal at first, but the architecture should support background tool execution without blocking the audio loop

### 8. Motion and expression layer

Robot behavior should remain decoupled from the language model.

Requirements:

- assistant speech can trigger coordinated motion
- listening state can trigger distinct robot posture
- interruptions should be able to stop queued audio and restore listening state
- future head-tracking or camera-aware behaviors should fit as optional layers

### Supporting HTTP endpoints

The architecture may still expose helper endpoints, but they should not define the primary conversation model.

Possible helper endpoints:

- configuration/status endpoints
- health endpoints
- diagnostics or transcript inspection endpoints
- fallback non-realtime paths used only for testing or degraded operation

### New modules

Recommended initial files:

- `src/kids_teacher_realtime.py`
  - streaming realtime handler for kids-teacher mode
  - owns session lifecycle, transcript events, and interruption logic
- `src/kids_teacher_backend.py`
  - OpenAI backend integration and model/config isolation
  - OpenAI Realtime as the V1 implementation
- `src/kids_safety.py`
  - input topic checks
  - output guardrails
  - refusal and redirection helpers
- `src/kids_teacher_profile.py`
  - profile loading and validation
  - locked kids-teacher profile behavior
- `src/kids_review_store.py`
  - optional persisted transcript and raw-audio review storage
  - local-first storage with optional GCS sync
- `src/kids_teacher_flow.py`
  - new robot/headless runtime loop built on the shared realtime core
  - reuses robot audio and animation helpers
- `profiles/kids_teacher/`
  - `instructions.txt`
  - `tools.txt`
  - `voice.txt`

### Deployment configuration

Recommended V1 config interfaces:

- `KIDS_TEACHER_REALTIME_MODEL`
  - allowed values: `gpt-realtime`, `gpt-realtime-mini`
- `KIDS_REVIEW_TRANSCRIPTS_ENABLED`
  - default: `false`
- `KIDS_REVIEW_AUDIO_ENABLED`
  - default: `false`
- `KIDS_REVIEW_RETENTION_DAYS`
  - default: `30`
- `KIDS_REVIEW_LOCAL_DIR`
  - default: `data/kids_review.runtime.v1`
- `KIDS_REVIEW_OBJECT_BUCKET`
  - optional GCS bucket name on GCP
- `KIDS_REVIEW_OBJECT_PREFIX`
  - default: `kids_review/v1`
- `KIDS_REVIEW_SYNC_TO_GCS`
  - allowed values: `never`, `session_end`, `shutdown`

When both review-storage toggles are disabled, no transcript review history or raw child audio should persist after the live session.

### Existing modules to reuse

- `src/robot_teacher.py`
  - audio bridge helpers
  - `RobotController`
  - server startup and runtime mode helpers
- `src/main.py`
  - FastAPI app and shared infrastructure
- `src/speech_service.py`
  - useful only as a fallback or degraded mode, not as the primary kids-teacher conversation engine

### Possible CLI/runtime entry options

Option A:

- keep one file and add a new flag like `--mode language_lesson|kids_teacher`

Option B:

- add a new entry file such as `src/robot_kids_teacher.py`

Option C:

- support both a browser UI and a headless/robot runtime that share the same realtime handler

Recommended V1 choice:

- use a new sibling flow module first
- share the realtime handler across the current app and robot/headless entry paths
- unify CLI mode selection later if the architecture stays clean

This keeps risk lower while requirements are still evolving.

---

## Recommended V1 Robot Flow

```text
start session
  -> load locked kids_teacher profile
  -> initialize streaming transport and realtime handler
  -> create OpenAI realtime session using KIDS_TEACHER_REALTIME_MODEL
  -> greet child with a short starter line
  -> remain always-listening for the duration of the session
  -> receive child partial/final transcript events
  -> detect child language turn-by-turn among enabled languages
  -> enforce safety policy on incoming content and generated behavior
  -> assistant streams spoken reply plus transcript events
  -> child can interrupt at any time
  -> robot expression layer tracks listening / speaking / idle states
  -> optional safe tool calls run in background without blocking speech loop
  -> keep short in-memory session state
  -> optionally persist transcript review data and/or raw audio according to env toggles
  -> optionally sync persisted review data to GCS at session end or shutdown
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

- admin stops the session
- repeated silence
- explicit child "all done" intent

---

## Acceptance Criteria

### AC1: Child asks a safe question

Given the child asks a safe preschool question,
when the robot answers,
then the answer is age-appropriate, understandable, and on topic, even if it takes a few simple sentences.

### AC2: Child asks a follow-up

Given the robot just explained a concept,
when the child says "why?" or "tell me again",
then the robot uses recent context and gives a sensible follow-up answer.

### AC3: Child interrupts the assistant

Given the assistant is currently speaking,
when the child starts speaking,
then the system stops or yields assistant playback promptly and returns to listening state without resetting the session.

### AC4: Child speech is unclear

Given transcription is empty or unclear,
when the robot responds,
then it asks for clarification instead of making up an answer.

### AC5: Child asks an unsafe question

Given the child asks about a disallowed topic,
when the robot responds,
then it does not explain the unsafe content and instead gives a short safe redirect.

### AC6: Transcript visibility works

Given a live kids-teacher session is running,
when the child and assistant speak,
then transcript events are available to the UI or logs for both sides of the conversation, even when transcript persistence is disabled.

### AC7: Backend streaming fails

Given the realtime backend disconnects or errors,
when the robot needs to answer,
then the system either reconnects safely or falls back with a short safe line instead of hanging silently.

### AC8: Existing language flow remains stable

Given the current language-teacher mode is run,
when the new kids-teacher work is added,
then the existing word lesson behavior and tests still pass unchanged.

### AC9: Admin restrictions take effect

Given an admin has configured extra restrictions or preferred redirects,
when the child asks about one of those topics,
then the robot follows the admin rule without violating the system hard safety floor.

### AC10: Multilingual response works

Given the child asks a question in Telugu, Assamese, Tamil, or Malayalam,
when language detection is confident,
then the robot answers in that detected language.

### AC11: Language fallback works

Given the child asks a question in a supported language,
when language detection confidence is too low,
then the robot falls back to the configured default explanation language.

### AC12: Transcript persistence can be disabled

Given `KIDS_REVIEW_TRANSCRIPTS_ENABLED=false`,
when the live session ends,
then no transcript text is retained after the session.

### AC13: Transcript-only review retention works

Given `KIDS_REVIEW_TRANSCRIPTS_ENABLED=true` and `KIDS_REVIEW_AUDIO_ENABLED=false`,
when the live session ends,
then transcript review data is retained according to policy and no raw child audio is retained.

### AC14: Audio-only review retention works

Given `KIDS_REVIEW_TRANSCRIPTS_ENABLED=false` and `KIDS_REVIEW_AUDIO_ENABLED=true`,
when the live session ends,
then raw child audio and minimal session metadata are retained according to policy and no transcript text is retained.

### AC15: Local-first review storage supports optional GCS sync

Given review persistence is enabled,
when GCS sync is not configured,
then persisted review data remains local only.

Given review persistence is enabled,
when GCS sync is configured,
then persisted review data can sync to GCS without changing the child-facing UX.

### AC16: Restricted common question gets a family-safe answer

Given the child asks an approved restricted-topic question such as "Where do babies come from?",
when the robot responds,
then it gives a very short family-safe answer and gently suggests asking a grown-up for more.

---

## Testing Requirements

### Unit tests

Add tests for:

- unsafe-topic detection
- refusal and redirect responses
- restricted-topic policy mapping
- family-safe short-answer behavior for approved sensitive questions
- clarification behavior on empty transcript
- recent-turn memory handling
- transcript event handling
- transcript persistence toggle behavior
- raw-audio retention toggle behavior
- local-only review storage behavior
- local-plus-GCS review storage behavior
- multilingual language detection and fallback behavior
- interruption and queue flush behavior
- response ordering when multiple events overlap
- profile/tool gating behavior
- admin configuration precedence and restriction handling

Recommended files:

- `tests/test_kids_safety.py`
- `tests/test_kids_review_store.py`
- `tests/test_kids_teacher_realtime.py`
- `tests/test_kids_teacher_flow.py`
- `tests/test_api_kids_teacher.py`

### Regression tests

Retain and run the current robot teacher tests so the new flow does not break:

- `tests/test_robot_teacher.py`

---

## Implementation Phases

### Phase 1: Requirements and skeleton

- finalize requirements and safety policy
- define the kids-teacher profile shape
- define env/config interfaces for model selection and optional review storage
- add basic realtime service/module layout

### Phase 2: Streaming realtime core

- add the shared realtime handler
- add OpenAI Realtime integration with env-based model selection
- add transcript event plumbing
- add unit tests for event ordering and interruption behavior

### Phase 3: Safety, multilingual, and profile enforcement

- add kids safety layer
- add locked kids-teacher profile
- add multilingual detection and default-language fallback behavior
- add tool allowlisting and safe defaults

### Phase 4: Optional review storage and admin visibility

- add local-first persisted transcript review storage
- add optional raw-audio review retention
- add optional GCS sync behavior
- add admin visibility rules bounded by deployment capability toggles

### Phase 5: Robot and UI integration

- add browser UI integration
- add robot/headless flow integration
- add listening/speaking/idle expression states
- tune latency and transcript UX

---

## Open Product Decisions

These decisions should be confirmed before implementation starts in earnest:

1. Should the robot always start with open-ended conversation, or offer topic buttons/prompts for safer steering?
2. Which tools, if any, should be enabled in `profiles/kids_teacher/tools.txt` for V1?
3. Should `kids_teacher` ship as a separate script first, or behind a single `--mode` flag in the main robot entrypoint?

---

## Recommended Immediate Build Order

If work starts now, the first implementation slice should be:

1. Create `profiles/kids_teacher/instructions.txt`, `tools.txt`, and `voice.txt`
2. Add a shared realtime handler for kids-teacher mode using OpenAI Realtime with env-based model selection
3. Add transcript event output, interruption handling, and detected-language metadata
4. Add `src/kids_safety.py` and wire restricted-topic policy mapping into the live session flow
5. Add optional review storage with separate transcript and raw-audio toggles plus local-first persistence
6. Add a minimal kids-teacher web and robot/headless flow that share the same realtime core

This order gives us the safest thin slice with the least risk to the existing robot teacher.
