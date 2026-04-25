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
- ``validate_output`` consults visual-redirect keywords too, so an
  assistant slip that names a dangerous object is replaced by the soft
  fallback (regression guard for merged_bug_002 — the SR-KID-3 backstop
  now covers BOTH the child-input and assistant-output paths).
- Standalone ``matches`` / ``lighter`` no longer trigger redirects on
  benign preschool speech (regression guard for the false-positive half
  of merged_bug_002).
"""

from __future__ import annotations

from pathlib import Path

from kids_safety import TopicDecision, classify_topic, validate_output


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


def test_safety_visual_redirect_keyword_lighter_fluid_triggers_redirect() -> None:
    # merged_bug_002: dropped standalone "lighter" (false-positives on
    # benign "feathers are lighter than rocks"); the multi-word
    # "lighter fluid" form still routes to REDIRECT.
    result = classify_topic("I see lighter fluid near the couch.")
    assert result.decision == TopicDecision.REDIRECT
    assert result.category == "visual_redirect"
    assert "lighter fluid" in result.matched_terms


def test_safety_visual_redirect_keyword_pills_triggers_redirect() -> None:
    result = classify_topic("Those look like pills.")
    assert result.decision == TopicDecision.REDIRECT
    assert result.category == "visual_redirect"
    assert "pills" in result.matched_terms


# ---------------------------------------------------------------------------
# merged_bug_002: false-positive guards
# ---------------------------------------------------------------------------


def test_benign_matches_phrase_does_not_trigger_redirect() -> None:
    """Color/matching games are explicitly supported preschool topics."""
    result = classify_topic("This matches your shirt!")
    assert result.decision == TopicDecision.ALLOW


def test_benign_lighter_phrase_does_not_trigger_redirect() -> None:
    """Weight comparisons are explicitly supported preschool topics."""
    result = classify_topic("Feathers are lighter than rocks.")
    assert result.decision == TopicDecision.ALLOW


# ---------------------------------------------------------------------------
# merged_bug_002: assistant-output backstop (SR-KID-3 wired into validate_output)
# ---------------------------------------------------------------------------


def test_validate_output_replaces_assistant_slip_naming_medication() -> None:
    text, replaced = validate_output("Here is your medication.")
    assert replaced is True
    assert "medication" not in text.lower()


def test_validate_output_replaces_assistant_slip_naming_pills() -> None:
    text, replaced = validate_output("I see some pills on the counter.")
    assert replaced is True
    assert "pills" not in text.lower()


def test_validate_output_passes_through_safe_text() -> None:
    text, replaced = validate_output("That is a red apple!")
    assert replaced is False
    assert text == "That is a red apple!"
