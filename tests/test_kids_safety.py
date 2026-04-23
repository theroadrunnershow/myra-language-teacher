"""Unit tests for the kids-teacher safety layer."""

from __future__ import annotations

from kids_safety import (
    MAX_RESPONSE_CHARS,
    TopicDecision,
    choose_reply_language,
    clarification_response,
    classify_topic,
    family_safe_response,
    merge_policy,
    redirect_response,
    refusal_response,
    silence_reprompt,
    validate_output,
)
from kids_teacher_types import KidsTeacherAdminPolicy, LanguageDetection


# ---------------------------------------------------------------------------
# Topic classification
# ---------------------------------------------------------------------------


def test_classify_topic_allows_safe_topic() -> None:
    result = classify_topic("tell me about puppies")
    assert result.decision == TopicDecision.ALLOW
    assert result.category == "allowed"


def test_classify_topic_refuses_weapon_keyword() -> None:
    result = classify_topic("tell me about a gun")
    assert result.decision == TopicDecision.REFUSE
    assert result.category == "disallowed:weapons"
    assert "gun" in result.matched_terms


def test_classify_topic_refuses_drug_keyword() -> None:
    result = classify_topic("what is cocaine")
    assert result.decision == TopicDecision.REFUSE
    assert result.category == "disallowed:drugs"
    assert "cocaine" in result.matched_terms


def test_classify_topic_reproduction_gets_family_safe_answer() -> None:
    result = classify_topic("where do babies come from?")
    assert result.decision == TopicDecision.FAMILY_SAFE_ANSWER
    assert result.category == "reproduction"


def test_classify_topic_sickness_gets_family_safe_answer() -> None:
    result = classify_topic("why did grandma get sick?")
    assert result.decision == TopicDecision.FAMILY_SAFE_ANSWER
    assert result.category == "sickness"


def test_classify_topic_death_gets_family_safe_answer() -> None:
    result = classify_topic("what happens when we die?")
    assert result.decision == TopicDecision.FAMILY_SAFE_ANSWER
    assert result.category == "death"


def test_classify_topic_scary_monster_gets_redirect() -> None:
    result = classify_topic("tell me about the scary monster in the movie")
    assert result.decision == TopicDecision.REDIRECT
    assert result.category == "scary"


def test_admin_policy_upgrades_allowed_topic_to_redirect() -> None:
    admin = KidsTeacherAdminPolicy(avoid_topics=("religion",))
    result = classify_topic("tell me about religion", admin_policy=admin)
    assert result.decision == TopicDecision.REDIRECT
    assert "religion" in result.matched_terms


def test_admin_policy_cannot_weaken_refuse() -> None:
    # "gun" is already REFUSE; admin adding it must not downgrade to REDIRECT.
    admin = KidsTeacherAdminPolicy(avoid_topics=("gun",))
    result = classify_topic("tell me about a gun", admin_policy=admin)
    assert result.decision == TopicDecision.REFUSE


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def test_family_safe_response_reproduction_matches_doc() -> None:
    expected = (
        "When two grown-ups love each other and decide to have a baby, "
        "a baby can start growing. A grown-up can tell you more about it."
    )
    assert family_safe_response("reproduction") == expected


def test_refusal_response_mentions_safe() -> None:
    text = refusal_response()
    assert text
    assert "safe" in text.lower()


def test_redirect_response_uses_first_target() -> None:
    text = redirect_response(redirect_to=("animals",))
    assert "animals" in text.lower()


def test_clarification_response_non_empty() -> None:
    assert clarification_response().strip() != ""
    # Translation lookup returns a non-empty phrase for supported languages.
    assert clarification_response("telugu").strip() != ""


def test_silence_reprompt_escalates() -> None:
    first = silence_reprompt(1)
    second = silence_reprompt(2)
    third = silence_reprompt(3)
    assert first != second
    assert "bye" in third.lower()


# ---------------------------------------------------------------------------
# Policy precedence
# ---------------------------------------------------------------------------


def test_merge_policy_unions_avoid_topics() -> None:
    system = KidsTeacherAdminPolicy(avoid_topics=("weapons",))
    admin = KidsTeacherAdminPolicy(avoid_topics=("religion",))
    session = KidsTeacherAdminPolicy(avoid_topics=("family_finances",))

    merged = merge_policy(system, admin, session)
    assert "weapons" in merged.avoid_topics
    assert "religion" in merged.avoid_topics
    assert "family_finances" in merged.avoid_topics


def test_merge_policy_does_not_allow_lower_layer_to_remove_higher_entry() -> None:
    system = KidsTeacherAdminPolicy(avoid_topics=("weapons",))
    # admin and session do not list "weapons" — it must still survive.
    admin = KidsTeacherAdminPolicy(avoid_topics=("religion",))
    session = KidsTeacherAdminPolicy(avoid_topics=())

    merged = merge_policy(system, admin, session)
    assert "weapons" in merged.avoid_topics


def test_merge_policy_redirect_prefers_session() -> None:
    system = KidsTeacherAdminPolicy(redirect_to=("science",))
    admin = KidsTeacherAdminPolicy(redirect_to=("animals",))
    session = KidsTeacherAdminPolicy(redirect_to=("counting",))

    merged = merge_policy(system, admin, session)
    assert merged.redirect_to == ("counting",)


# ---------------------------------------------------------------------------
# Language selection
# ---------------------------------------------------------------------------


def test_choose_reply_language_uses_high_confidence_detection() -> None:
    detection = LanguageDetection(language="telugu", confidence=0.9)
    chosen = choose_reply_language(
        detection,
        enabled_languages=("english", "telugu"),
        default_language="english",
    )
    assert chosen == "telugu"


def test_choose_reply_language_falls_back_on_low_confidence() -> None:
    detection = LanguageDetection(language="telugu", confidence=0.3)
    chosen = choose_reply_language(
        detection,
        enabled_languages=("english", "telugu"),
        default_language="english",
    )
    assert chosen == "english"


def test_choose_reply_language_ignores_unenabled_detected_language() -> None:
    detection = LanguageDetection(language="tamil", confidence=0.95)
    chosen = choose_reply_language(
        detection,
        enabled_languages=("english", "telugu"),
        default_language="english",
    )
    assert chosen == "english"


def test_choose_reply_language_consults_preference_order() -> None:
    chosen = choose_reply_language(
        None,
        enabled_languages=("english", "telugu", "tamil"),
        default_language="english",
        preference_order=("tamil", "telugu"),
    )
    assert chosen == "tamil"


# ---------------------------------------------------------------------------
# Output validation
# ---------------------------------------------------------------------------


def test_validate_output_replaces_too_long_text() -> None:
    too_long = "a" * (MAX_RESPONSE_CHARS + 1)
    text, replaced = validate_output(too_long)
    assert replaced is True
    assert text != too_long


def test_validate_output_replaces_empty_text() -> None:
    text, replaced = validate_output("   ")
    assert replaced is True
    assert text.strip() != ""


def test_validate_output_replaces_text_with_refuse_keyword() -> None:
    text, replaced = validate_output("you should get a gun")
    assert replaced is True
    assert "gun" not in text.lower()


def test_validate_output_passes_safe_text_unchanged() -> None:
    original = "Plants drink water with their roots."
    text, replaced = validate_output(original)
    assert replaced is False
    assert text == original
