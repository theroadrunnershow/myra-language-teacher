"""Contract tests for the language-lesson skill prompt.

These assert on the *loaded profile instructions* (via ``load_profile``)
rather than reading ``language_lesson.txt`` directly, so the loader path
is exercised end-to-end. Every assertion guards a specific decision
recorded in ``tasks/plan-kids-tutor-skill.md`` so the v1 contract
cannot drift silently.
"""

from __future__ import annotations

import re

import pytest

from kids_teacher_profile import DEFAULT_PROFILE_DIR, load_profile


@pytest.fixture(scope="module")
def instructions() -> str:
    return load_profile(DEFAULT_PROFILE_DIR).instructions


@pytest.fixture(scope="module")
def lesson_section(instructions: str) -> str:
    """Slice of the loaded instructions that follows the lesson-mode header."""
    marker = "# Language lesson mode"
    idx = instructions.find(marker)
    assert idx != -1, "lesson-mode section is missing entirely"
    return instructions[idx:]


# ---------------------------------------------------------------------------
# Structural contract
# ---------------------------------------------------------------------------


def test_lesson_mode_section_is_present(instructions: str) -> None:
    assert "# Language lesson mode" in instructions


def test_lesson_mode_section_follows_safety_sections(instructions: str) -> None:
    safety_idx = instructions.find("# Topics you must not discuss")
    restricted_idx = instructions.find("# Restricted topics")
    lesson_idx = instructions.find("# Language lesson mode")
    assert safety_idx != -1 and restricted_idx != -1 and lesson_idx != -1
    # Safety + restricted-topic rules must appear before lesson mode so the
    # model reads them first.
    assert safety_idx < lesson_idx
    assert restricted_idx < lesson_idx


# ---------------------------------------------------------------------------
# Step coverage — the 8-step shape from the original skill prompt,
# with STEP 4 collapsed (Telugu-only). Use distinctive keywords rather
# than literal "STEP N" markers so the prose stays editable.
# ---------------------------------------------------------------------------


def test_step1_silly_story_intro(lesson_section: str) -> None:
    # Distinctive phrase from the skill's example story.
    assert "silly" in lesson_section.lower()
    assert "story" in lesson_section.lower()


def test_step2_teach_word_uses_kukka_example(lesson_section: str) -> None:
    # The skill prompt teaches "Dog → kukka" as the canonical example.
    assert "kukka" in lesson_section.lower()


def test_step5_mini_game_present(lesson_section: str) -> None:
    assert "mini game" in lesson_section.lower()


def test_step6_repetition_loop_present(lesson_section: str) -> None:
    # Distinctive phrase from the skill prompt's STEP 6.
    assert "say them all together" in lesson_section.lower()


def test_step7_recap_present(lesson_section: str) -> None:
    assert "today we learned" in lesson_section.lower()


def test_step8_cheerful_ending_present(lesson_section: str) -> None:
    assert "see you next time" in lesson_section.lower()


# ---------------------------------------------------------------------------
# Branch coverage — the four IF-branches each need at least one example
# quote so the model copies tone, not just rules.
# ---------------------------------------------------------------------------


def test_branch_correct_has_example(lesson_section: str) -> None:
    # "Yayyy" (3+ y's) is distinctive — base persona uses "Nice thinking"
    # and "Good try", not yay-style celebration.
    assert re.search(r"yay+", lesson_section.lower())


def test_branch_partially_correct_has_example(lesson_section: str) -> None:
    # "Slowly together" is distinctive to the partial-credit branch.
    assert "slowly together" in lesson_section.lower()


def test_branch_incorrect_has_example(lesson_section: str) -> None:
    # "Let's try together" is distinctive to the incorrect branch.
    assert "let's try together" in lesson_section.lower()


def test_branch_no_response_has_example(lesson_section: str) -> None:
    assert "can you try saying" in lesson_section.lower()


# ---------------------------------------------------------------------------
# Hard numeric thresholds — these must not drift.
# ---------------------------------------------------------------------------


def test_three_attempt_cap_is_present(lesson_section: str) -> None:
    # Either "3 attempts" or "3 tries" wording is acceptable; both must
    # name the number 3 explicitly.
    assert re.search(r"\b3\s+(attempts|tries)\b", lesson_section.lower())


def test_word_range_5_to_8_is_present(lesson_section: str) -> None:
    # "5 words" minimum and "8 words" hard cap must both appear.
    text = lesson_section.lower()
    assert re.search(r"\b5\s+words?\b", text)
    assert re.search(r"\b8\s+words?\b", text)


# ---------------------------------------------------------------------------
# Telugu-only scoping — Tamil/Assamese/Malayalam may appear ONLY in the
# redirect rule, not as supported lesson languages. The redirect rule
# is one line; if any of those language names show up multiple times,
# the section has drifted out of v1 scope.
# ---------------------------------------------------------------------------


def test_telugu_is_named_multiple_times(lesson_section: str) -> None:
    # Telugu should appear in entry triggers, teaching steps, redirect, etc.
    assert lesson_section.lower().count("telugu") >= 3


def test_other_languages_appear_only_in_redirect(lesson_section: str) -> None:
    text = lesson_section.lower()
    # Each of Tamil / Assamese / Malayalam should appear at most twice
    # (once in entry-trigger redirect, once optionally in a "no Tamil yet"
    # explanation). Anything more means the prompt is treating them as
    # supported lesson languages, which v1 doesn't.
    for name in ("tamil", "assamese", "malayalam"):
        assert text.count(name) <= 2, (
            f"{name!r} appears more than twice — looks like a v1 scope drift"
        )


# ---------------------------------------------------------------------------
# Error-handling rules from the skill's "ERROR HANDLING" section.
# ---------------------------------------------------------------------------


def test_unclear_input_handling_present(lesson_section: str) -> None:
    # The skill's wording: "I didn't hear that clearly..."
    assert "didn't hear" in lesson_section.lower() or "did not hear" in lesson_section.lower()


def test_off_topic_redirect_present(lesson_section: str) -> None:
    # Off-topic redirect should keep the kid on the current word.
    text = lesson_section.lower()
    assert "off-topic" in text or "off topic" in text or "right now we're on" in text
