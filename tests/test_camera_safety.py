"""Tests for the Chunk C camera-safety extensions.

Covers:
- The locked-profile vision section is appended to ``instructions.txt``
  (regression guard for FR-KID-9).
- The visual-task rules apply to the child or a grown-up
  (regression guard for FR-KID-7A).
- The visual-redirect keyword backstop in ``classify_topic`` routes
  assistant transcripts naming unsafe nearby objects to the REDIRECT
  path (SR-KID-3).
- The locked profile forbids describing the child's appearance
  (regression guard for SR-KID-1).
"""

from __future__ import annotations

from pathlib import Path

from kids_safety import TopicDecision, classify_topic


PROFILE_PATH = (
    Path(__file__).resolve().parent.parent
    / "profiles"
    / "kids_teacher"
    / "instructions.txt"
)


def _load_instructions() -> str:
    return PROFILE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Locked profile regression guards
# ---------------------------------------------------------------------------


def test_instructions_contains_vision_section() -> None:
    text = _load_instructions()
    assert "# When you can see the child" in text


def test_instructions_contains_visual_command_rules() -> None:
    text = _load_instructions()
    # FR-KID-7A: the visual-task rules apply to the child OR a grown-up.
    assert "read me this book" in text
    assert "child or a grown-up" in text


def test_instructions_forbids_describing_appearance() -> None:
    text = _load_instructions()
    # SR-KID-1: never describe the child's body, face, clothes, or hair.
    assert (
        "Do not describe the child's body, face, clothes, or hair." in text
    )


# ---------------------------------------------------------------------------
# Classifier backstop for visual redirects (SR-KID-3)
# ---------------------------------------------------------------------------


def test_safety_visual_redirect_keyword_medication_triggers_redirect() -> None:
    result = classify_topic("Here is a medication bottle on the table.")
    assert result.decision == TopicDecision.REDIRECT
    assert result.category == "visual_redirect"
    assert "medication" in result.matched_terms


def test_safety_visual_redirect_keyword_lighter_triggers_redirect() -> None:
    result = classify_topic("I see a lighter near the couch.")
    assert result.decision == TopicDecision.REDIRECT
    assert result.category == "visual_redirect"
    assert "lighter" in result.matched_terms


def test_safety_visual_redirect_keyword_pills_triggers_redirect() -> None:
    result = classify_topic("Those look like pills.")
    assert result.decision == TopicDecision.REDIRECT
    assert result.category == "visual_redirect"
    assert "pills" in result.matched_terms
