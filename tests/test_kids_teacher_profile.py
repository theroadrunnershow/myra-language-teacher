"""Tests for the locked kids-teacher profile loader."""

from __future__ import annotations

import os

import pytest

from kids_teacher_profile import (
    DEFAULT_PROFILE_DIR,
    DEFAULT_VOICE,
    PROFILE_NAME,
    ProfileValidationError,
    load_profile,
    validate_tool_names,
)


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def test_load_profile_happy_path_from_real_profile_dir() -> None:
    profile = load_profile(DEFAULT_PROFILE_DIR)

    assert profile.name == PROFILE_NAME
    assert profile.instructions.strip() != ""
    # voice.txt on disk currently says "alloy"
    assert profile.voice == "alloy"
    # V1 ships with no tools allowlisted
    assert profile.allowed_tools == ()
    # locked is True by default
    assert profile.locked is True


def test_load_profile_locked_default_is_true(tmp_path) -> None:
    _write(tmp_path / "instructions.txt", "Be kind.")
    profile = load_profile(str(tmp_path))
    assert profile.locked is True


def test_load_profile_locked_can_be_overridden(tmp_path) -> None:
    _write(tmp_path / "instructions.txt", "Be kind.")
    profile = load_profile(str(tmp_path), locked=False)
    assert profile.locked is False


def test_missing_instructions_raises(tmp_path) -> None:
    with pytest.raises(ProfileValidationError, match="Missing required instructions"):
        load_profile(str(tmp_path))


def test_empty_instructions_raises(tmp_path) -> None:
    _write(tmp_path / "instructions.txt", "   \n\n  \n")
    with pytest.raises(ProfileValidationError, match="empty"):
        load_profile(str(tmp_path))


def test_missing_voice_falls_back_to_default(tmp_path, caplog) -> None:
    _write(tmp_path / "instructions.txt", "Be kind.")
    with caplog.at_level("WARNING"):
        profile = load_profile(str(tmp_path))
    assert profile.voice == DEFAULT_VOICE


def test_empty_voice_falls_back_to_default(tmp_path) -> None:
    _write(tmp_path / "instructions.txt", "Be kind.")
    _write(tmp_path / "voice.txt", "\n   \n")
    profile = load_profile(str(tmp_path))
    assert profile.voice == DEFAULT_VOICE


def test_voice_file_reads_first_non_empty_line(tmp_path) -> None:
    _write(tmp_path / "instructions.txt", "Be kind.")
    _write(tmp_path / "voice.txt", "shimmer\n# comment\n")
    profile = load_profile(str(tmp_path))
    assert profile.voice == "shimmer"


def test_tools_file_parses_skipping_comments_and_blanks(tmp_path) -> None:
    _write(tmp_path / "instructions.txt", "Be kind.")
    tools_content = (
        "# leading comment\n"
        "\n"
        "safe_gesture\n"
        "   \n"
        "# another comment\n"
        "look_around\n"
    )
    _write(tmp_path / "tools.txt", tools_content)
    profile = load_profile(str(tmp_path))
    assert profile.allowed_tools == ("safe_gesture", "look_around")


def test_missing_tools_file_yields_empty_allowlist(tmp_path) -> None:
    _write(tmp_path / "instructions.txt", "Be kind.")
    profile = load_profile(str(tmp_path))
    assert profile.allowed_tools == ()


def test_load_profile_appends_memory_markdown_to_instructions(tmp_path) -> None:
    _write(tmp_path / "instructions.txt", "Be kind.")
    memory_path = tmp_path / "memory.md"
    _write(
        memory_path,
        "# Things to remember about the child\n\n- Her name is Aanya",
    )

    profile = load_profile(str(tmp_path), memory_file_path=str(memory_path))

    assert profile.instructions == (
        "Be kind.\n\n"
        "# Things to remember about the child\n\n- Her name is Aanya"
    )


def test_validate_tool_names_accepts_known() -> None:
    assert validate_tool_names(["wave"], {"wave", "nod"}) == ["wave"]


def test_validate_tool_names_rejects_unknown() -> None:
    with pytest.raises(ProfileValidationError, match="Unknown tool names"):
        validate_tool_names(["wave", "launch_rockets"], {"wave"})


def test_validate_tool_names_empty_input_is_ok() -> None:
    assert validate_tool_names([], {"wave"}) == []


def test_validate_tool_names_empty_known_rejects_any_name() -> None:
    with pytest.raises(ProfileValidationError):
        validate_tool_names(["wave"], set())


def test_profile_files_exist_on_disk() -> None:
    # Guardrail: Intern 2's deliverable is that these files actually exist.
    assert os.path.exists(os.path.join(DEFAULT_PROFILE_DIR, "instructions.txt"))
    assert os.path.exists(os.path.join(DEFAULT_PROFILE_DIR, "tools.txt"))
    assert os.path.exists(os.path.join(DEFAULT_PROFILE_DIR, "voice.txt"))
