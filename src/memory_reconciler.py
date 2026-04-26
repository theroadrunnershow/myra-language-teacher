"""Add free-form memory notes with relevance-filtered LLM reconciliation.

Flow when a new note arrives:

1. If there are fewer than ``min_existing_for_llm`` notes already, just
   append — there's nothing to dedup against.
2. Otherwise, find the top-K most-similar existing notes via rapidfuzz
   (``token_set_ratio`` — order-insensitive, robust to phrasing drift).
3. Send only those K + the new note to a small text LLM and ask it to
   pick one of: ``skip`` | ``append`` | ``merge`` | ``replace``.
4. Apply the decision atomically via ``memory_file``.

LLM failures fall back to plain append. The session never blocks.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Callable, Optional

import memory_file
import text_llm

logger = logging.getLogger(__name__)

DEFAULT_RELEVANCE_K = 5
DEFAULT_RELEVANCE_SCORE_CUTOFF = 30
DEFAULT_MIN_EXISTING_FOR_LLM = 3

_SYSTEM_PROMPT = """\
You curate a small memory profile about one young child. The model that talks
to the child has just observed a new fact. Decide how it should be merged with
existing similar notes.

Return JSON only, with this shape:
  {"action": "skip"|"append"|"merge"|"replace",
   "remove": [<1-based indices of existing notes to drop>],
   "text": "<final note text to keep, or empty when action=skip>"}

Rules:
- "skip": the new observation is already fully captured by an existing note.
  remove=[], text="".
- "append": the new observation adds something not covered by existing notes.
  remove=[], text=<the new note, possibly cleaned up>.
- "merge": the new observation extends or refines one or more existing notes
  about the SAME subject. remove=[<their indices>], text=<one combined note>.
- "replace": the new observation contradicts or supersedes existing note(s)
  about the SAME subject. remove=[<their indices>], text=<the new fact>.

Hard constraint: notes about different proper-name subjects (e.g. "Priya is
Myra's aunt" vs "Sara is Myra's aunt") are always distinct facts. Never merge
or replace across different named subjects — use "append" instead. The same
person may be referred to by short or long forms (e.g. "Priya" / "Aunt Priya"
/ "Aunt Priya Sharma"); treat those as the same subject.

Keep text short, third-person, factual. No explanations outside the JSON.
"""


def add_note(
    text: str,
    *,
    path: str | os.PathLike[str] | None = None,
    completer: Optional[Callable[..., str]] = None,
    min_existing_for_llm: int = DEFAULT_MIN_EXISTING_FOR_LLM,
    relevance_k: int = DEFAULT_RELEVANCE_K,
    relevance_score_cutoff: int = DEFAULT_RELEVANCE_SCORE_CUTOFF,
) -> str:
    """Add a note with LLM-assisted dedup. Returns the action taken."""
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return "skipped"

    existing = memory_file.list_notes(path)
    if len(existing) < min_existing_for_llm:
        memory_file.append_note(cleaned, path)
        return "appended_no_llm"

    relevant = find_relevant_notes(
        cleaned,
        existing,
        k=relevance_k,
        score_cutoff=relevance_score_cutoff,
    )
    if not relevant:
        memory_file.append_note(cleaned, path)
        return "appended_no_llm"

    decision = _ask_llm(completer or text_llm.complete, cleaned, relevant)
    return _apply_decision(decision, relevant, cleaned, path)


def find_relevant_notes(
    new_note: str,
    existing: list[str],
    *,
    k: int = DEFAULT_RELEVANCE_K,
    score_cutoff: int = DEFAULT_RELEVANCE_SCORE_CUTOFF,
) -> list[tuple[int, str]]:
    """Return ``[(original_index, text), …]`` ordered by relevance."""
    if not existing:
        return []
    if len(existing) <= k:
        return list(enumerate(existing))
    from rapidfuzz import fuzz, process

    matches = process.extract(
        new_note,
        existing,
        scorer=fuzz.token_set_ratio,
        limit=k,
        score_cutoff=score_cutoff,
    )
    return [(match[2], match[0]) for match in matches]


def _ask_llm(
    completer: Callable[..., str],
    new_note: str,
    relevant: list[tuple[int, str]],
) -> dict:
    bullets = "\n".join(f"{i + 1}. {text}" for i, (_, text) in enumerate(relevant))
    user_prompt = (
        f"Existing notes (1-based):\n{bullets}\n\n"
        f"New observation: {new_note}\n\n"
        "Return the JSON object only."
    )
    try:
        raw = completer(
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            temperature=0.0,
            json_mode=True,
        )
    except Exception as exc:
        logger.warning("[memory_reconciler] LLM call failed: %s", exc)
        return {"action": "append", "remove": [], "text": new_note}
    try:
        decoded = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        logger.warning(
            "[memory_reconciler] LLM returned invalid JSON: %s; raw=%r",
            exc,
            (raw or "")[:200],
        )
        return {"action": "append", "remove": [], "text": new_note}
    if not isinstance(decoded, dict):
        logger.warning("[memory_reconciler] LLM JSON not an object: %r", decoded)
        return {"action": "append", "remove": [], "text": new_note}
    return decoded


def _apply_decision(
    decision: dict,
    relevant: list[tuple[int, str]],
    new_note: str,
    path: str | os.PathLike[str] | None,
) -> str:
    action = (decision.get("action") or "append").strip().lower()
    text = (decision.get("text") or "").strip()
    remove_raw = decision.get("remove") or []
    indices_to_remove: list[int] = []
    for one_based in remove_raw:
        try:
            idx = int(one_based) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(relevant):
            indices_to_remove.append(relevant[idx][0])

    if action == "skip":
        return "skipped"
    if action == "append" or not indices_to_remove:
        memory_file.append_note(text or new_note, path)
        return "appended"
    if action in ("merge", "replace"):
        memory_file.replace_notes(
            removed_indices=indices_to_remove,
            new_text=text or new_note,
            path=path,
        )
        return action
    logger.warning("[memory_reconciler] unknown action %r; falling back to append", action)
    memory_file.append_note(new_note, path)
    return "appended"
