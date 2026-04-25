"""Keyword tables for the kids-teacher safety layer.

Split out from ``kids_safety.py`` to keep that module under the 350-line
soft cap. These lists are a deliberately short first-cut classifier; the
input-screening layer can enhance them later.

Every term is a lowercased substring. Single-word terms are matched with
word boundaries in ``kids_safety._contains_term``; multi-word or
hyphenated terms use plain substring matching.
"""

from __future__ import annotations


# Disallowed topics → REFUSE.
DISALLOWED_CATEGORIES: dict[str, tuple[str, ...]] = {
    "disallowed:sex": (
        "sex", "sexual", "porn", "pornography", "nude", "naked",
        "intercourse", "orgasm",
    ),
    "disallowed:gore": (
        "gore", "gory", "bloody", "mutilate", "mutilation", "dismember",
        "decapitate",
    ),
    "disallowed:violence": (
        "murder", "kill", "killing", "stab", "stabbing", "shoot", "shooting",
        "behead", "torture", "massacre", "assault",
    ),
    "disallowed:weapons": (
        "gun", "guns", "rifle", "pistol", "bomb", "grenade", "knife attack",
        "firearm", "ammunition", "explosive",
    ),
    "disallowed:drugs": (
        "cocaine", "heroin", "meth", "marijuana", "weed", "drugs", "smoke",
        "smoking", "cigarette", "cigar", "alcohol", "beer", "wine", "vodka",
        "whiskey", "drunk", "intoxicated", "high on",
    ),
    "disallowed:self_harm": (
        "suicide", "self-harm", "self harm", "kill myself", "kill yourself",
        "cut myself", "hang myself",
    ),
    "disallowed:abuse": (
        "abuse", "molest", "molestation", "rape", "trafficking", "exploit",
    ),
    "disallowed:crime_howto": (
        "how to steal", "how to rob", "how to hack", "how to break in",
        "how to make a bomb", "how to poison",
    ),
    "disallowed:horror": (
        "demon", "satan", "zombie", "haunted", "possessed", "exorcism",
        "slasher",
    ),
}


# Restricted topics that get a short family-safe answer for approved cases.
FAMILY_SAFE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "reproduction": (
        "where do babies come from", "how are babies made",
        "how babies are made", "where babies come from",
        "how do you make a baby",
    ),
    "body": (
        "private parts", "private part", "my body parts", "body parts",
    ),
    "sickness": (
        "why did grandma get sick", "why is grandpa sick",
        "why are people sick", "why do people get sick",
        "why did mommy get sick", "why did daddy get sick",
        "grandma got sick", "grandpa got sick",
    ),
    "death": (
        "what happens when we die", "what happens when you die",
        "what happens after we die", "why did grandma die",
        "why did grandpa die", "is grandma dead", "is grandpa dead",
    ),
}


# Restricted topics that should just be redirected.
REDIRECT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "scary": (
        "scary monster", "scary movie", "nightmare", "ghost", "monster",
        "creepy",
    ),
    "conflict": (
        "fight", "fighting", "punch", "kick someone", "hit someone", "war",
        "battle",
    ),
}


# Visual-redirect backstop (SR-KID-3): if the assistant verbally names a
# camera-visible object that is not safe for young children, route to the
# REDIRECT path. The locked profile already instructs the model to refuse
# describing these; this set is a defense-in-depth keyword filter on the
# assistant transcript.
VISUAL_REDIRECT_KEYWORDS: tuple[str, ...] = (
    "medication", "alcohol", "weapon", "gun", "knife", "lighter", "matches",
    "pills",
)


# Family-safe answer copy for each approved category.
FAMILY_SAFE_TEXT: dict[str, str] = {
    "reproduction": (
        "When two grown-ups love each other and decide to have a baby, "
        "a baby can start growing. A grown-up can tell you more about it."
    ),
    "body": (
        "Our bodies have lots of parts that help us play and learn. "
        "A grown-up can tell you more about our bodies."
    ),
    "sickness": (
        "Sometimes people feel sick, and they rest to feel better. "
        "A grown-up can help when someone is sick."
    ),
    "death": (
        "Sometimes living things stop living, and grown-ups can help us "
        "understand. Do you want to talk about something else for now?"
    ),
}


# Short phrases we are confident translating. English is always present;
# other languages fall back to English when absent.
SHORT_PHRASES: dict[str, dict[str, str]] = {
    "clarification": {
        "english": "Can you say that again?",
        "telugu": "మళ్లీ చెప్పగలవా?",
        "assamese": "আকৌ কব পাৰিবানে?",
        "tamil": "மீண்டும் சொல்ல முடியுமா?",
        "malayalam": "ഒന്നു കൂടി പറയാമോ?",
    },
}


REFUSAL_TEXT = (
    "I can talk about safe and fun things for kids. "
    "Want to learn about how our bodies help us run and jump?"
)

SOFT_FALLBACK = "Let's talk about something else fun."
