"""Child-safety policy layer for the kids-teacher mode.

Pure functions and small dataclasses so the safety decisions are trivially
unit-testable without a live model, network, or robot attached.

This module covers four concerns:

1. Topic classification via keyword lists (first-cut; the input screening
   layer can enhance this later).
2. Safe-response helpers for refusal, redirection, family-safe answers,
   clarification, and silence reprompts. Responses are English by default;
   a small Telugu/Assamese/Tamil/Malayalam lookup returns translated short
   phrases where we are confident. Longer translations fall back to English
   on purpose rather than inventing wording we are not sure about.
3. Admin policy merge with strict precedence: lower layers can make the
   system stricter but can never remove entries from higher layers.
4. Language selection from a detection result plus output validation that
   rejects oversized, empty, or policy-violating assistant output.

Keyword tables live in ``kids_safety_keywords`` to keep this module small.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from kids_safety_keywords import (
    DISALLOWED_CATEGORIES,
    FAMILY_SAFE_CATEGORIES,
    FAMILY_SAFE_TEXT,
    REDIRECT_CATEGORIES,
    REFUSAL_TEXT,
    SHORT_PHRASES,
    SOFT_FALLBACK,
)
from kids_teacher_types import KidsTeacherAdminPolicy, LanguageDetection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Topic classification
# ---------------------------------------------------------------------------


class TopicDecision(str, Enum):
    ALLOW = "allow"
    FAMILY_SAFE_ANSWER = "family_safe_answer"
    REDIRECT = "redirect"
    REFUSE = "refuse"


@dataclass(frozen=True)
class TopicClassification:
    decision: TopicDecision
    category: str
    matched_terms: tuple[str, ...]


def _contains_term(text_lower: str, term: str) -> bool:
    """Match term in text. Multi-word/hyphenated terms use substring match;
    single words use a word-boundary regex so "gunny" does not hit "gun"."""
    if " " in term or "-" in term:
        return term in text_lower
    return re.search(rf"\b{re.escape(term)}\b", text_lower) is not None


def _find_matches(text_lower: str, terms: tuple[str, ...]) -> list[str]:
    return [t for t in terms if _contains_term(text_lower, t)]


def _first_matching_category(
    text_lower: str,
    categories: dict[str, tuple[str, ...]],
) -> Optional[tuple[str, list[str]]]:
    for category, terms in categories.items():
        matches = _find_matches(text_lower, terms)
        if matches:
            return (category, matches)
    return None


def classify_topic(
    text: str,
    *,
    admin_policy: Optional[KidsTeacherAdminPolicy] = None,
) -> TopicClassification:
    """Classify child input into an allow/family-safe/redirect/refuse bucket.

    V1 uses simple keyword matching. Admin policy ``avoid_topics`` can only
    make a decision stricter — never weaker.
    """
    lowered = (text or "").lower()

    # Disallowed wins over everything else.
    disallowed = _first_matching_category(lowered, DISALLOWED_CATEGORIES)
    if disallowed is not None:
        category, matches = disallowed
        return _apply_admin_policy(
            TopicClassification(TopicDecision.REFUSE, category, tuple(matches)),
            lowered,
            admin_policy,
        )

    family_safe = _first_matching_category(lowered, FAMILY_SAFE_CATEGORIES)
    if family_safe is not None:
        category, matches = family_safe
        return _apply_admin_policy(
            TopicClassification(
                TopicDecision.FAMILY_SAFE_ANSWER, category, tuple(matches)
            ),
            lowered,
            admin_policy,
        )

    redirect = _first_matching_category(lowered, REDIRECT_CATEGORIES)
    if redirect is not None:
        category, matches = redirect
        return _apply_admin_policy(
            TopicClassification(TopicDecision.REDIRECT, category, tuple(matches)),
            lowered,
            admin_policy,
        )

    return _apply_admin_policy(
        TopicClassification(TopicDecision.ALLOW, "allowed", ()),
        lowered,
        admin_policy,
    )


def _apply_admin_policy(
    current: TopicClassification,
    text_lower: str,
    admin_policy: Optional[KidsTeacherAdminPolicy],
) -> TopicClassification:
    """Upgrade a non-REFUSE classification to REDIRECT when admin
    avoid_topics match the text. Never weaken an existing REFUSE."""
    if admin_policy is None or not admin_policy.avoid_topics:
        return current

    admin_matches = [
        term for term in admin_policy.avoid_topics
        if _contains_term(text_lower, term.lower())
    ]
    if not admin_matches:
        return current

    if current.decision == TopicDecision.REFUSE:
        merged = tuple(list(current.matched_terms) + admin_matches)
        return TopicClassification(TopicDecision.REFUSE, current.category, merged)

    return TopicClassification(
        decision=TopicDecision.REDIRECT,
        category=f"admin_avoid:{admin_matches[0]}",
        matched_terms=tuple(admin_matches),
    )


# ---------------------------------------------------------------------------
# 2. Safe-response helpers
# ---------------------------------------------------------------------------


def refusal_response(language: str = "english") -> str:
    """Return the V1 refusal line. Language accepted for API symmetry; V1
    returns English because translating the full paragraph safely is out of
    scope."""
    return REFUSAL_TEXT


def redirect_response(
    language: str = "english",
    redirect_to: tuple[str, ...] = (),
) -> str:
    """If a redirect target is provided, weave the first one into a short
    prompt. Otherwise fall back to the same soft-refusal line."""
    if redirect_to:
        first = redirect_to[0].strip()
        if first:
            return f"Want to learn about {first} instead?"
    return REFUSAL_TEXT


def family_safe_response(category: str, language: str = "english") -> str:
    """Return a short, preschool-safe answer for an approved restricted
    category. Unknown categories fall back to the soft redirect line."""
    return FAMILY_SAFE_TEXT.get(category, REFUSAL_TEXT)


def clarification_response(language: str = "english") -> str:
    """Ask the child to repeat themselves gently."""
    phrases = SHORT_PHRASES["clarification"]
    return phrases.get(language, phrases["english"])


def silence_reprompt(attempt: int, language: str = "english") -> str:
    """Gentle no-speech recovery that escalates across attempts."""
    if attempt <= 1:
        return "I'm still listening. Do you want to ask me something?"
    if attempt == 2:
        return (
            "We can talk about animals, colors, or counting. "
            "What sounds fun?"
        )
    return "I'll be here when you want to chat. Bye-bye for now!"


# ---------------------------------------------------------------------------
# 3. Admin policy merge with precedence
# ---------------------------------------------------------------------------


def _dedup_preserve_order(*sources: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for source in sources:
        for item in source:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
    return tuple(out)


def merge_policy(
    system: KidsTeacherAdminPolicy,
    admin: KidsTeacherAdminPolicy,
    session: KidsTeacherAdminPolicy,
) -> KidsTeacherAdminPolicy:
    """Merge the three policy layers with strict precedence (FR18).

    - ``avoid_topics`` is the union across all three layers. Lower layers can
      never remove an entry from a higher layer.
    - ``redirect_to`` prefers session, then admin, then system.
    - ``extra_rules`` is concatenated system + admin + session, deduped.
    """
    merged_avoid = _dedup_preserve_order(
        system.avoid_topics, admin.avoid_topics, session.avoid_topics,
    )

    if session.redirect_to:
        merged_redirect = session.redirect_to
    elif admin.redirect_to:
        merged_redirect = admin.redirect_to
    else:
        merged_redirect = system.redirect_to

    merged_rules = _dedup_preserve_order(
        system.extra_rules, admin.extra_rules, session.extra_rules,
    )

    return KidsTeacherAdminPolicy(
        avoid_topics=merged_avoid,
        redirect_to=merged_redirect,
        extra_rules=merged_rules,
    )


# ---------------------------------------------------------------------------
# 4. Language selection and output validation
# ---------------------------------------------------------------------------


MIN_LANGUAGE_CONFIDENCE = 0.65
MAX_RESPONSE_CHARS = 400
MAX_RESPONSE_SENTENCES = 6


def choose_reply_language(
    detection: Optional[LanguageDetection],
    *,
    enabled_languages: tuple[str, ...],
    default_language: str,
    preference_order: tuple[str, ...] = (),
) -> str:
    """Pick the reply language for an assistant turn.

    - If detection is confident and in ``enabled_languages``, use it.
    - Else consult ``preference_order`` for the first enabled entry.
    - Else fall back to ``default_language``.
    """
    if (
        detection is not None
        and detection.confidence >= MIN_LANGUAGE_CONFIDENCE
        and detection.language in enabled_languages
    ):
        return detection.language

    for candidate in preference_order:
        if candidate in enabled_languages:
            return candidate

    return default_language


def _count_sentences(text: str) -> int:
    parts = [p.strip() for p in re.split(r"[.!?]+", text) if p.strip()]
    return len(parts)


def _contains_refuse_keyword(text_lower: str) -> bool:
    for terms in DISALLOWED_CATEGORIES.values():
        if _find_matches(text_lower, terms):
            return True
    return False


def validate_output(text: str, *, language: str = "english") -> tuple[str, bool]:
    """Validate assistant output before it is streamed.

    Replaces the text with a soft fallback when the output is empty, too
    long, too many sentences, or contains disallowed-content keywords. Emits
    a warning log on replacement so operators can audit.
    """
    if text is None or not text.strip():
        logger.warning("[kids_safety] empty assistant output replaced with fallback")
        return (SOFT_FALLBACK, True)

    stripped = text.strip()
    if len(stripped) > MAX_RESPONSE_CHARS:
        logger.warning(
            "[kids_safety] assistant output too long (%d chars); replacing",
            len(stripped),
        )
        return (SOFT_FALLBACK, True)

    if _count_sentences(stripped) > MAX_RESPONSE_SENTENCES:
        logger.warning(
            "[kids_safety] assistant output had too many sentences; replacing"
        )
        return (SOFT_FALLBACK, True)

    if _contains_refuse_keyword(stripped.lower()):
        logger.warning(
            "[kids_safety] assistant output contained a REFUSE keyword; replacing"
        )
        return (SOFT_FALLBACK, True)

    return (stripped, False)
