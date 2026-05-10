"""Contract tests for the math-lesson skill prompt.

These assert on the *loaded profile instructions* (via ``load_profile``)
rather than reading ``math_lesson.txt`` directly, so the loader path is
exercised end-to-end. Each assertion guards a specific decision recorded
in ``tasks/plan-math-teacher.md`` so the v1 curriculum cannot drift
silently.
"""

from __future__ import annotations

import re

import pytest

from kids_teacher_profile import DEFAULT_PROFILE_DIR, load_profile


@pytest.fixture(scope="module")
def instructions() -> str:
    return load_profile(DEFAULT_PROFILE_DIR).instructions


@pytest.fixture(scope="module")
def math_section(instructions: str) -> str:
    """Slice of the loaded instructions that follows the math-lesson header."""
    marker = "# Math lesson mode"
    idx = instructions.find(marker)
    assert idx != -1, "math-lesson section is missing entirely"
    return instructions[idx:]


# ---------------------------------------------------------------------------
# Structural contract — section exists and sits in the right place
# ---------------------------------------------------------------------------


def test_math_lesson_section_is_present(instructions: str) -> None:
    assert "# Math lesson mode" in instructions


def test_math_lesson_follows_safety_sections(instructions: str) -> None:
    safety_idx = instructions.find("# Topics you must not discuss")
    restricted_idx = instructions.find("# Restricted topics")
    math_idx = instructions.find("# Math lesson mode")
    assert safety_idx != -1 and restricted_idx != -1 and math_idx != -1
    # Safety + restricted-topic rules must appear before lesson mode.
    assert safety_idx < math_idx
    assert restricted_idx < math_idx


def test_math_lesson_follows_language_lesson(instructions: str) -> None:
    # Language lesson is loaded first; math lesson is appended after it
    # (and after the Telugu vocab block).
    lang_idx = instructions.find("# Language lesson mode")
    vocab_idx = instructions.find("# Telugu starter vocabulary")
    math_idx = instructions.find("# Math lesson mode")
    assert lang_idx != -1 and vocab_idx != -1 and math_idx != -1
    assert lang_idx < math_idx
    assert vocab_idx < math_idx


# ---------------------------------------------------------------------------
# Pedagogical thesis — the load-bearing idea must be in the prompt
# ---------------------------------------------------------------------------


def test_pedagogical_thesis_present(math_section: str) -> None:
    # The single most important sentence in the curriculum.
    text = math_section.lower()
    assert "property of a set" in text
    assert "not a position in a chant" in text


def test_concrete_pictorial_abstract_principle(math_section: str) -> None:
    text = math_section.lower()
    assert "concrete" in text and "pictorial" in text and "abstract" in text


def test_quantity_before_symbol_principle(math_section: str) -> None:
    assert "quantity before symbol" in math_section.lower()


def test_composition_is_bridge_to_arithmetic(math_section: str) -> None:
    assert "composition" in math_section.lower()


# ---------------------------------------------------------------------------
# Entry triggers — kid invokes math by voice, like the language lesson
# ---------------------------------------------------------------------------


def test_entry_triggers_present(math_section: str) -> None:
    text = math_section.lower()
    # At least these two unambiguous phrasings must be in the trigger list.
    assert "teach me math" in text
    assert "let's do math" in text


# ---------------------------------------------------------------------------
# STEP 0 — calibration ritual is mandatory
# ---------------------------------------------------------------------------


def test_setup_ritual_asks_for_paper(math_section: str) -> None:
    text = math_section.lower()
    assert "piece of paper" in text or "paper and a crayon" in text


def test_setup_ritual_calibration_dot(math_section: str) -> None:
    # The calibration check is "draw one dot, can I see it?".
    text = math_section.lower()
    assert "draw one" in text and "dot" in text


def test_calibration_retry_cap_is_three(math_section: str) -> None:
    # Hard cap of 3 retries before falling back to audio-only.
    text = math_section.lower()
    assert re.search(r"\b3\s+(calibration\s+)?retries\b", text) or re.search(
        r"hard cap of 3", text
    )


def test_audio_only_fallback_present(math_section: str) -> None:
    assert "audio-only" in math_section.lower()


# ---------------------------------------------------------------------------
# Skill ladder — v1 stages 0–7 must all be named
# ---------------------------------------------------------------------------


def test_stage_subitizing_present(math_section: str) -> None:
    assert "subitizing" in math_section.lower()


def test_stage_counting_principles_present(math_section: str) -> None:
    text = math_section.lower()
    assert "one-to-one" in text
    assert "cardinal" in text  # cardinality / cardinal principle


def test_stage_conservation_present(math_section: str) -> None:
    assert "conservation" in math_section.lower()


def test_stage_order_irrelevance_present(math_section: str) -> None:
    assert "order irrelevance" in math_section.lower()


def test_stage_comparison_present(math_section: str) -> None:
    text = math_section.lower()
    assert "comparison" in text
    # Both more/less directions must be named.
    assert "more" in text and "less" in text


def test_stage_number_bonds_present(math_section: str) -> None:
    text = math_section.lower()
    assert "number bonds" in text
    # Decompositions of 5 are spelled out so the model has concrete examples.
    assert "2+3" in math_section or "2 + 3" in math_section


def test_v1_does_not_introduce_addition_subtraction(math_section: str) -> None:
    # Stages 8+ (addition, subtraction, missing-addend) are explicitly
    # out of scope for v1. The prompt must say so.
    text = math_section.lower()
    assert "not in v1" in text or "out of v1" in text or "do not introduce them yet" in text


# ---------------------------------------------------------------------------
# Per-session flow — the seven-step shape with a silly break
# ---------------------------------------------------------------------------


def test_session_flow_has_warm_up(math_section: str) -> None:
    assert "warm-up" in math_section.lower() or "warm up" in math_section.lower()


def test_session_flow_has_spiral_review(math_section: str) -> None:
    assert "spiral review" in math_section.lower()


def test_session_flow_has_silly_break(math_section: str) -> None:
    assert "silly break" in math_section.lower()


def test_session_flow_has_story_problem(math_section: str) -> None:
    assert "story problem" in math_section.lower()


def test_session_flow_has_closing_praise(math_section: str) -> None:
    text = math_section.lower()
    # Specific praise example: "you saw it without counting".
    assert "without counting" in text


# ---------------------------------------------------------------------------
# Engagement techniques — the three rules that make the lesson deep
# ---------------------------------------------------------------------------


def test_mascot_makes_mistakes_on_purpose(math_section: str) -> None:
    text = math_section.lower()
    assert "mistakes on purpose" in text or "miscount" in text


def test_role_reversal_present(math_section: str) -> None:
    # "Be the student sometimes" — child teaches the mascot.
    text = math_section.lower()
    assert "can you teach me" in text


def test_name_the_strategy_present(math_section: str) -> None:
    assert "name the strategy" in math_section.lower()


def test_one_new_thing_per_session(math_section: str) -> None:
    text = math_section.lower()
    assert "one new thing per session" in text


def test_trust_child_over_camera(math_section: str) -> None:
    text = math_section.lower()
    assert "trust the child" in text


# ---------------------------------------------------------------------------
# Response branches — match the tone shape from the language lesson
# ---------------------------------------------------------------------------


def test_correct_with_strategy_branch(math_section: str) -> None:
    # Distinct praise for "saw it without counting" vs counted.
    assert "didn't even count" in math_section.lower() or "you just saw it" in math_section.lower()


def test_incorrect_branch_curiosity_not_correction(math_section: str) -> None:
    # Mascot's reflex on errors is curiosity, not "wrong".
    text = math_section.lower()
    assert "show me how you got that" in text
    # The word "wrong" must NOT be presented as a label for the child.
    assert 'never say "wrong"' in text


def test_no_response_branch_present(math_section: str) -> None:
    # "Take your time" is distinctive to the no-response branch.
    assert "take your time" in math_section.lower()


def test_three_attempt_cap_is_present(math_section: str) -> None:
    # Same hard cap pattern as the language lesson.
    assert re.search(r"\b3\s+(attempts|tries)\b", math_section.lower())


# ---------------------------------------------------------------------------
# Mastery checks — robustness probes are non-negotiable
# ---------------------------------------------------------------------------


def test_mastery_requires_three_sessions(math_section: str) -> None:
    text = math_section.lower()
    assert "three different sessions" in text or "three sessions" in text


def test_mastery_includes_explanation_check(math_section: str) -> None:
    text = math_section.lower()
    assert "how did you know" in text


def test_mastery_includes_robustness_probe(math_section: str) -> None:
    assert "robustness probe" in math_section.lower()


def test_robustness_probe_for_cardinality(math_section: str) -> None:
    text = math_section.lower()
    # The classic cardinality miscue: child recounts instead of stating
    # the cardinal.
    assert "recount" in text


def test_robustness_probe_for_comparison_size_trap(math_section: str) -> None:
    # Big-dots-vs-many-dots probe — does she pick more-numerous or
    # bigger-looking?
    text = math_section.lower()
    assert "big dots" in text or "bigger-looking" in text


# ---------------------------------------------------------------------------
# Psychology rules — process praise, autonomy, asymmetric celebration
# ---------------------------------------------------------------------------


def test_psychology_praise_process_not_trait(math_section: str) -> None:
    text = math_section.lower()
    # Either explicit phrasing ("praise the process") or the pattern
    # ("you kept trying" beats "you're so smart").
    assert "praise the process" in text or "trait praise" in text


def test_psychology_autonomy_present(math_section: str) -> None:
    text = math_section.lower()
    assert "autonomy" in text


def test_psychology_asymmetric_celebration(math_section: str) -> None:
    assert "asymmetric celebration" in math_section.lower()


def test_psychology_stop_while_fun(math_section: str) -> None:
    text = math_section.lower()
    assert "stop while it's still fun" in text or "stop while it is still fun" in text


# ---------------------------------------------------------------------------
# Error handling — hooks for the camera-fail path mid-lesson
# ---------------------------------------------------------------------------


def test_unclear_input_handling_present(math_section: str) -> None:
    text = math_section.lower()
    assert "didn't hear" in text or "did not hear" in text


def test_off_topic_redirect_present(math_section: str) -> None:
    text = math_section.lower()
    assert "off-topic" in text or "off topic" in text or "let's finish our dots" in text


def test_camera_loses_paper_mid_lesson(math_section: str) -> None:
    text = math_section.lower()
    assert "can't quite see your paper" in text or "cannot see your paper" in text


def test_child_wants_to_stop_is_honored(math_section: str) -> None:
    text = math_section.lower()
    assert "want to come back to math later" in text or "always honor" in text
