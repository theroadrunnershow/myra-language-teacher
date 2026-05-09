"""Detect canned LLM refusals so the realtime handler can intercept them.

Gemini Live can fall out of the "Teacher Robot" persona and emit a generic
identity refusal ("I'm just a language model and can't help with that.")
that then self-reinforces across turns once it lands in the assistant
transcript. The realtime handler runs every assistant partial through
:func:`is_refusal` to catch the refusal *before* its audio plays — the
transcript stream leads audio by 200-500 ms (see
``kids_teacher_realtime`` for the offset note), giving us a window to
hard-cut playback and force a fresh session.

Pure helper. No I/O, no state — the per-session escalation timer lives
on the realtime handler. Patterns are matched as case-insensitive
substrings against the *accumulated* assistant text for the current
turn, not the raw delta, so a phrase split across deltas (the common
case — Gemini ships short fragments like ``"I'm just"`` then
``" a language"``) still trips the detector on the second fragment.
"""

from __future__ import annotations

# Substring patterns. Lowercased; the matcher lowercases the input.
#
# - "i'm just" — strong domain signal: a teacher-robot persona never
#   opens a sentence this way.
# - "language model" — direct identity leak.
# - "can't help with that" / "cannot help with that" — literal refusal
#   tail observed in the production log.
# - "as an ai" — secondary identity leak we've seen on adjacent failure
#   modes; cheap to add and same recovery path applies.
_REFUSAL_PATTERNS: tuple[str, ...] = (
    "i'm just",
    "language model",
    "can't help with that",
    "cannot help with that",
    "as an ai",
)


def is_refusal(text: str) -> bool:
    """Return True if ``text`` contains any known refusal phrase.

    Empty / falsy input returns False. Input is lowercased once; patterns
    are stored lowercase. ``text`` is expected to be the accumulated
    assistant transcript for the current turn — pass deltas concatenated
    in arrival order, not individual deltas.
    """
    if not text:
        return False
    haystack = text.lower()
    for pattern in _REFUSAL_PATTERNS:
        if pattern in haystack:
            return True
    return False


__all__ = ["is_refusal"]
