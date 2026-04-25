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
from memory_file import read as read_memory_file

logger = logging.getLogger(__name__)

# Resolve profile dir relative to the repo root (parent of src/).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
DEFAULT_PROFILE_DIR = os.path.join(_REPO_ROOT, "profiles", "kids_teacher")

DEFAULT_VOICE = "alloy"
PROFILE_NAME = "kids_teacher"

_INSTRUCTIONS_FILENAME = "instructions.txt"
_VOICE_FILENAME = "voice.txt"
_TOOLS_FILENAME = "tools.txt"


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
    try:
        memory_text = read_memory_file(memory_file_path)
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

    return KidsTeacherProfile(
        name=PROFILE_NAME,
        instructions=instructions,
        voice=voice,
        allowed_tools=allowed_tools,
        locked=locked,
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
