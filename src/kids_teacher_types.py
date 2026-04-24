"""Shared contract types for the kids-teacher mode.

This module is the single source of truth for event shapes, session config,
profile shape, and runtime hook signatures. All other kids-teacher modules
(realtime handler, safety layer, review store, robot flow, routes) import
from here so cross-intern boundaries stay stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol, runtime_checkable


# V1 allowlist: only these OpenAI realtime models may be selected via
# KIDS_TEACHER_REALTIME_MODEL. Rejecting anything else keeps the backend
# swap-proof and prevents accidental use of a non-realtime chat model.
ALLOWED_REALTIME_MODELS: frozenset[str] = frozenset({"gpt-realtime", "gpt-realtime-mini"})

# V1.1 Gemini Live allowlist. Only the GA native-audio Live model is
# approved; preview variants are intentionally excluded.
ALLOWED_GEMINI_MODELS: frozenset[str] = frozenset({"gemini-live-2.5-flash-native-audio"})

# Union of every model id acceptable on KidsTeacherSessionConfig.
ALLOWED_ALL_MODELS: frozenset[str] = ALLOWED_REALTIME_MODELS | ALLOWED_GEMINI_MODELS

# V1.1 multilingual set for kids-teacher. Narrowed from five languages to
# {english, telugu} per the 2026-04-23 Gemini migration amendment:
# Assamese is removed entirely (the standalone language-lesson flow still
# supports it via gTTS); Tamil/Malayalam never shipped on the realtime
# path. English is the quality bar; Telugu is kept pending a listening
# check on Gemini's Telugu voice.
KIDS_SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"english", "telugu"})


class Speaker(str, Enum):
    CHILD = "child"
    ASSISTANT = "assistant"


class SessionStatus(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ENDED = "ended"
    ERROR = "error"


@dataclass(frozen=True)
class KidsTranscriptEvent:
    speaker: Speaker
    text: str
    is_partial: bool
    timestamp_ms: int
    session_id: str
    # Detected language for child speech, or chosen reply language for
    # assistant speech. None when unknown.
    language: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "speaker": self.speaker.value,
            "text": self.text,
            "is_partial": self.is_partial,
            "timestamp_ms": self.timestamp_ms,
            "session_id": self.session_id,
            "language": self.language,
        }


@dataclass(frozen=True)
class KidsStatusEvent:
    status: SessionStatus
    session_id: str
    timestamp_ms: int
    detail: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "session_id": self.session_id,
            "timestamp_ms": self.timestamp_ms,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class LanguageDetection:
    language: str
    confidence: float


@dataclass(frozen=True)
class KidsTeacherProfile:
    """Locked preschool profile loaded from profiles/kids_teacher/*.txt."""

    name: str
    instructions: str
    voice: str
    allowed_tools: tuple[str, ...]
    locked: bool = True


@dataclass(frozen=True)
class KidsTeacherAdminPolicy:
    """Admin-added restrictions. Can only make the system stricter."""

    avoid_topics: tuple[str, ...] = field(default_factory=tuple)
    redirect_to: tuple[str, ...] = field(default_factory=tuple)
    extra_rules: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class KidsTeacherSessionConfig:
    session_id: str
    model: str
    profile: KidsTeacherProfile
    enabled_languages: tuple[str, ...]
    default_explanation_language: str
    language_preference: tuple[str, ...] = field(default_factory=tuple)
    admin_policy: KidsTeacherAdminPolicy = field(default_factory=KidsTeacherAdminPolicy)
    max_session_seconds: Optional[int] = None
    idle_timeout_seconds: Optional[int] = None

    def __post_init__(self) -> None:
        if self.model not in ALLOWED_ALL_MODELS:
            raise ValueError(
                f"Invalid model {self.model!r}. Must be one of "
                f"{sorted(ALLOWED_ALL_MODELS)}."
            )
        if not self.enabled_languages:
            raise ValueError("enabled_languages must not be empty")
        for lang in self.enabled_languages:
            if lang not in KIDS_SUPPORTED_LANGUAGES:
                raise ValueError(
                    f"Unsupported language {lang!r}. Must be one of "
                    f"{sorted(KIDS_SUPPORTED_LANGUAGES)}."
                )
        if self.default_explanation_language not in self.enabled_languages:
            raise ValueError(
                "default_explanation_language must be one of enabled_languages"
            )


@runtime_checkable
class KidsTeacherRuntimeHooks(Protocol):
    """Callbacks the realtime handler invokes to reach the UI or robot.

    Implementations may be a browser SSE bridge, a robot audio bridge, or
    a test fake. Keeping hooks explicit lets the realtime core stay free
    of web-only or robot-only assumptions.
    """

    def start_assistant_playback(self, audio_chunk: bytes) -> None: ...

    def stop_assistant_playback(self) -> None: ...

    def publish_transcript(self, event: KidsTranscriptEvent) -> None: ...

    def publish_status(self, event: KidsStatusEvent) -> None: ...

    def persist_artifact(
        self,
        event: KidsTranscriptEvent,
        audio: Optional[bytes] = None,
    ) -> None: ...
