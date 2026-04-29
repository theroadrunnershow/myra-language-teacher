"""Locked kids-teacher profile loader.

Reads the preschool-safe persona, voice, and tool allowlist from
``profiles/kids_teacher/`` on disk. Instructions are required; voice and
tools fall back sensibly if missing. Tool validation is kept as a separate
helper so the profile loader does not own tool registration.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable, Optional

from kids_teacher_types import KidsTeacherProfile
from memory_file import read_for_prompt as read_memory_for_prompt
from words_db import get_lesson_seed_vocabulary

logger = logging.getLogger(__name__)

# Resolve profile dir relative to the repo root (parent of src/).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
DEFAULT_PROFILE_DIR = os.path.join(_REPO_ROOT, "profiles", "kids_teacher")

DEFAULT_VOICE = "alloy"
DEFAULT_LANGUAGE_CODE = "en-IN"
PROFILE_NAME = "kids_teacher"

_INSTRUCTIONS_FILENAME = "instructions.txt"
_VOICE_FILENAME = "voice.txt"
_TOOLS_FILENAME = "tools.txt"
_LANGUAGE_LESSON_FILENAME = "language_lesson.txt"
_LANGUAGE_CODE_FILENAME = "language_code.txt"

_VOCAB_HEADING = "# Telugu starter vocabulary (seed pool for lesson stories)"
_VOCAB_INTRO = (
    "Use this list as the seed for lesson stories. Pick a theme for the "
    "lesson, then choose 3 to 4 seed words from one category below. Vary "
    "the theme across sessions so the child does not hear the same words "
    "every time. After the seed words are taught, you may add 2 to 3 more "
    "Telugu words that come up naturally in the story — translate those "
    "yourself; they do not need to be on this list."
)

_CATEGORY_TITLES: tuple[tuple[str, str], ...] = (
    ("animals", "Animals"),
    ("food", "Food"),
    ("colors", "Colors"),
    ("body_parts", "Body parts"),
    ("numbers", "Numbers"),
    ("common_objects", "Common objects"),
    ("verbs", "Simple verbs"),
    ("core_phrases", "Common phrases"),
)


class ProfileValidationError(Exception):
    """Raised when the kids-teacher profile cannot be loaded safely."""


def _read_text_file(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise ProfileValidationError(f"Failed to read {path}: {exc}") from exc


def _format_vocab_entry(entry: dict) -> str:
    english = entry.get("english", "").strip()
    telugu = entry.get("telugu", "").strip()
    roman = entry.get("tel_roman", "").strip()
    if telugu and roman:
        return f"- {english} — {telugu} ({roman})"
    if telugu:
        return f"- {english} — {telugu}"
    return f"- {english}"


def format_telugu_lesson_vocabulary() -> str:
    """Render the kids-teacher Telugu seed pool as a markdown block.

    The block is appended after ``language_lesson.txt`` so the realtime
    model can pick concrete seed words from a categorized list instead
    of leaning on the same hardcoded examples each session.
    """
    seed = get_lesson_seed_vocabulary()
    lines: list[str] = [_VOCAB_HEADING, "", _VOCAB_INTRO]
    for key, title in _CATEGORY_TITLES:
        entries = seed.get(key) or []
        if not entries:
            continue
        lines.append("")
        lines.append(f"## {title}")
        lines.extend(_format_vocab_entry(entry) for entry in entries)
    return "\n".join(lines)


def _parse_tools(raw: str) -> tuple[str, ...]:
    tools: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tools.append(stripped)
    return tuple(tools)


def load_profile(
    profile_dir: Optional[str] = None,
    *,
    locked: bool = True,
    memory_file_path: Optional[str] = None,
    present_names: Optional[list[str]] = None,
) -> KidsTeacherProfile:
    """Load the locked kids-teacher profile from disk.

    Raises ``ProfileValidationError`` if ``instructions.txt`` is missing or
    empty. Missing ``voice.txt`` falls back to ``DEFAULT_VOICE`` with a
    warning. Missing ``tools.txt`` is treated as an empty allowlist.

    ``present_names`` is the deduped list of people the face-rec session-start
    sweep just confirmed in the camera frame (FR-KID-15 / FR-KID-22). When
    non-empty, a one-line note is appended after ``memory.md`` so the model
    can greet by name. When ``None`` or empty, the section is omitted
    entirely (FR-KID-18 / FR-KID-25).
    """
    base_dir = profile_dir or DEFAULT_PROFILE_DIR

    instructions_path = os.path.join(base_dir, _INSTRUCTIONS_FILENAME)
    instructions_raw = _read_text_file(instructions_path)
    if instructions_raw is None:
        raise ProfileValidationError(
            f"Missing required instructions file at {instructions_path}"
        )
    instructions = instructions_raw.strip()
    if not instructions:
        raise ProfileValidationError(
            f"Instructions file is empty at {instructions_path}"
        )

    lesson_path = os.path.join(base_dir, _LANGUAGE_LESSON_FILENAME)
    lesson_raw = _read_text_file(lesson_path)
    if lesson_raw is not None:
        lesson_text = lesson_raw.strip()
        if lesson_text:
            instructions = f"{instructions}\n\n{lesson_text}"
            instructions = (
                f"{instructions}\n\n{format_telugu_lesson_vocabulary()}"
            )

    try:
        memory_text = read_memory_for_prompt(memory_file_path)
    except OSError as exc:
        logger.warning(
            "[kids_teacher_profile] memory file unreadable at %s: %s",
            memory_file_path or "default path",
            exc,
        )
        memory_text = ""
    if memory_text:
        instructions = f"{instructions}\n\n{memory_text}"

    if present_names:
        # Stable order so logs and tests are deterministic across runs.
        ordered = sorted({name.strip() for name in present_names if name and name.strip()})
        if ordered:
            joined = ", ".join(ordered)
            instructions = (
                f"{instructions}\n\n"
                f"# People you can currently see\n"
                f"You can currently see: {joined}."
            )

    voice_path = os.path.join(base_dir, _VOICE_FILENAME)
    voice_raw = _read_text_file(voice_path)
    if voice_raw is None:
        logger.warning(
            "[kids_teacher_profile] voice file missing at %s; falling back to %s",
            voice_path,
            DEFAULT_VOICE,
        )
        voice = DEFAULT_VOICE
    else:
        voice = voice_raw.strip().splitlines()[0].strip() if voice_raw.strip() else ""
        if not voice:
            logger.warning(
                "[kids_teacher_profile] voice file empty at %s; falling back to %s",
                voice_path,
                DEFAULT_VOICE,
            )
            voice = DEFAULT_VOICE

    tools_path = os.path.join(base_dir, _TOOLS_FILENAME)
    tools_raw = _read_text_file(tools_path)
    allowed_tools = _parse_tools(tools_raw) if tools_raw is not None else ()

    language_code_path = os.path.join(base_dir, _LANGUAGE_CODE_FILENAME)
    language_code_raw = _read_text_file(language_code_path)
    if language_code_raw is None:
        language_code = DEFAULT_LANGUAGE_CODE
    else:
        language_code = (
            language_code_raw.strip().splitlines()[0].strip()
            if language_code_raw.strip()
            else ""
        )
        if not language_code:
            language_code = DEFAULT_LANGUAGE_CODE

    return KidsTeacherProfile(
        name=PROFILE_NAME,
        instructions=instructions,
        voice=voice,
        allowed_tools=allowed_tools,
        locked=locked,
        language_code=language_code,
    )


def validate_tool_names(
    names: Iterable[str],
    known_tools: set[str],
) -> list[str]:
    """Ensure every requested tool name is in the runtime allowlist.

    Returns the filtered list preserving input order. Raises
    ``ProfileValidationError`` if any name is not in ``known_tools``.
    """
    requested = [name.strip() for name in names if name and name.strip()]
    unknown = [name for name in requested if name not in known_tools]
    if unknown:
        raise ProfileValidationError(
            f"Unknown tool names in profile: {sorted(set(unknown))}"
        )
    return requested
