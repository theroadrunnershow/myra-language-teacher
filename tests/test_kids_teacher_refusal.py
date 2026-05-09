"""Tests for src/kids_teacher_refusal.py."""

from __future__ import annotations

from kids_teacher_refusal import is_refusal


# ---------------------------------------------------------------------------
# Negative cases — benign assistant text
# ---------------------------------------------------------------------------


def test_empty_string_is_not_refusal() -> None:
    assert is_refusal("") is False


def test_whitespace_is_not_refusal() -> None:
    assert is_refusal("   \n\t") is False


def test_normal_teacher_response_is_not_refusal() -> None:
    """A typical kids-teacher reply must not trip the detector."""
    text = "Kangaroos are plant eaters! They like to munch on grass and leaves."
    assert is_refusal(text) is False


def test_word_just_alone_is_not_refusal() -> None:
    """The detector keys on the leading-clause shape ``i'm just``, not
    the bare word ``just`` — otherwise everyday phrases like
    ``It's just a joey`` would false-positive."""
    assert is_refusal("It's just a joey, the baby kangaroo.") is False
    assert is_refusal("Just like that!") is False


# ---------------------------------------------------------------------------
# Positive cases — observed and adjacent refusal phrases
# ---------------------------------------------------------------------------


def test_canonical_refusal_from_log_trips_detector() -> None:
    """The exact phrase from the 2026-05-09 incident."""
    assert is_refusal("I'm just a language model and can't help with that.") is True


def test_partial_first_fragment_trips_detector() -> None:
    """The accumulated buffer trips on ``i'm just`` alone — the realtime
    handler relies on this so the very first delta short-circuits before
    audio plays."""
    assert is_refusal("I'm just") is True


def test_language_model_alone_trips_detector() -> None:
    """Some refusal variants drop the ``I'm just`` prefix and just say
    ``As a language model...``. Cover that branch."""
    assert is_refusal("As a language model, I cannot answer that.") is True


def test_cannot_help_variant_trips_detector() -> None:
    assert is_refusal("Sorry, I cannot help with that request.") is True


def test_as_an_ai_variant_trips_detector() -> None:
    assert is_refusal("As an AI, I don't have personal opinions.") is True


# ---------------------------------------------------------------------------
# Case-insensitivity
# ---------------------------------------------------------------------------


def test_uppercase_input_trips_detector() -> None:
    assert is_refusal("I'M JUST A LANGUAGE MODEL") is True


def test_mixed_case_input_trips_detector() -> None:
    assert is_refusal("I'm Just A Language Model") is True
