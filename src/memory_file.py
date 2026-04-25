"""Sectioned markdown-backed persistent memory for kids-teacher mode.

The memory file has three sections:

- ``## Current`` — keyed slots (``name``, ``mom_name``, …). One value per key.
  Setting a key that already exists moves the previous value into ``## History``
  with a "replaced" date so parents can audit how the profile evolved.
- ``## Notes`` — free-form observations the model collected. Reconciler
  may merge or replace bullets here; replaced bullets also flow into
  ``## History``.
- ``## History`` — append-only audit log of superseded values. Never read
  back into the system instruction (would re-introduce the contradiction
  the schema is designed to avoid).

Only ``## Current`` and ``## Notes`` are returned by :func:`read_for_prompt`,
which is what the profile loader concatenates into the system instruction.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

MEMORY_FILE_ENV_VAR = "MYRA_MEMORY_FILE"
DEFAULT_MEMORY_FILE = Path("~/.myra/memory.md")

HEADING = "# Things to remember about the child"
CURRENT_HEADING = "## Current"
NOTES_HEADING = "## Notes"
HISTORY_HEADING = "## History"

MAX_MEMORY_BYTES = 8192

# Single-valued slots a parent or the LLM can set directly. Anything that
# doesn't fit one of these belongs in a free-form note.
ALLOWED_KEYS: tuple[str, ...] = (
    "name",
    "age",
    "pronouns",
    "mom_name",
    "dad_name",
    "favourite_colour",
    "favourite_animal",
    "favourite_food",
    "favourite_book",
)

_DATE_SUFFIX_RE = re.compile(r"\s+_\((?P<inner>[^()]*)\)_\s*$")
_BULLET_PREFIX = "- "


class InvalidKeyError(ValueError):
    """Raised when ``set_key`` receives a key outside :data:`ALLOWED_KEYS`."""


@dataclass
class _State:
    current: dict[str, tuple[str, str]] = field(default_factory=dict)
    notes: list[tuple[str, str]] = field(default_factory=list)
    history: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_memory_file_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    override = os.environ.get(MEMORY_FILE_ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_MEMORY_FILE.expanduser()


def read_raw(path: str | os.PathLike[str] | None = None) -> str:
    """Return the raw markdown contents (or ``""`` if missing)."""
    target = resolve_memory_file_path(path)
    if not target.exists():
        return ""
    with target.open("r", encoding="utf-8") as handle:
        return handle.read().strip()


def read_for_prompt(path: str | os.PathLike[str] | None = None) -> str:
    """Return only ``Current`` + ``Notes`` for system-instruction injection.

    History is intentionally excluded — it represents superseded facts and
    re-injecting it would put contradictions back in front of the model.
    """
    state = _parse(read_raw(path))
    if not state.current and not state.notes:
        return ""
    parts: list[str] = [HEADING, ""]
    if state.current:
        parts.append(CURRENT_HEADING)
        for key, (value, date) in state.current.items():
            parts.append(_format_bullet(f"{key}: {value}", date))
        parts.append("")
    if state.notes:
        parts.append(NOTES_HEADING)
        for text, date in state.notes:
            parts.append(_format_bullet(text, date))
        parts.append("")
    return "\n".join(parts).rstrip()


def list_notes(
    path: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Return current free-form notes (text only, no date suffix)."""
    state = _parse(read_raw(path))
    return [text for text, _ in state.notes]


def set_key(
    key: str,
    value: str,
    path: str | os.PathLike[str] | None = None,
) -> None:
    """Store a keyed slot. Previous value (if any) is moved to History."""
    if key not in ALLOWED_KEYS:
        raise InvalidKeyError(
            f"key {key!r} is not in ALLOWED_KEYS={list(ALLOWED_KEYS)}"
        )
    clean_value = _normalize(value)
    if not clean_value:
        return

    target = resolve_memory_file_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    today = _today_iso()

    with _locked(target):
        state = _parse(read_raw(target))
        previous = state.current.get(key)
        if previous is not None and previous[0] == clean_value:
            return  # idempotent; don't churn the file
        if previous is not None:
            old_value, old_date = previous
            state.history.append(
                _format_bullet(
                    f"{key}: {old_value}",
                    f"{old_date} → {today}" if old_date else f"replaced {today}",
                )
            )
        state.current[key] = (clean_value, today)
        _commit(target, state)


def append_note(
    text: str,
    path: str | os.PathLike[str] | None = None,
) -> None:
    """Append a free-form note. Exact-text duplicates are skipped."""
    clean = _normalize(text)
    if not clean:
        return
    target = resolve_memory_file_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    today = _today_iso()

    with _locked(target):
        state = _parse(read_raw(target))
        if any(existing.casefold() == clean.casefold() for existing, _ in state.notes):
            return
        state.notes.append((clean, today))
        _commit(target, state)


def replace_notes(
    *,
    removed_indices: list[int],
    new_text: str | None,
    path: str | os.PathLike[str] | None = None,
) -> None:
    """Reconciler hook: drop indexed notes (to History) and optionally append a merged one."""
    target = resolve_memory_file_path(path)
    if not target.exists():
        return
    today = _today_iso()

    with _locked(target):
        state = _parse(read_raw(target))
        if not state.notes:
            return
        kept: list[tuple[str, str]] = []
        removed_set = {i for i in removed_indices if 0 <= i < len(state.notes)}
        for idx, (note_text, note_date) in enumerate(state.notes):
            if idx in removed_set:
                state.history.append(
                    _format_bullet(
                        note_text,
                        f"{note_date} → {today}"
                        if note_date
                        else f"replaced {today}",
                    )
                )
            else:
                kept.append((note_text, note_date))
        state.notes = kept
        if new_text:
            clean = _normalize(new_text)
            if clean and not any(
                existing.casefold() == clean.casefold() for existing, _ in state.notes
            ):
                state.notes.append((clean, today))
        _commit(target, state)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today_iso() -> str:
    return _dt.date.today().isoformat()


def _normalize(text: str) -> str:
    return " ".join((text or "").strip().split())


def _format_bullet(body: str, date: str) -> str:
    if not date:
        return f"{_BULLET_PREFIX}{body}"
    return f"{_BULLET_PREFIX}{body} _({date})_"


def _split_date_suffix(body: str) -> tuple[str, str]:
    match = _DATE_SUFFIX_RE.search(body)
    if not match:
        return body.strip(), ""
    return body[: match.start()].strip(), match.group("inner").strip()


def _parse(text: str) -> _State:
    state = _State()
    if not text.strip():
        return state

    section: str | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped == HEADING:
            section = None
            continue
        if stripped == CURRENT_HEADING:
            section = "current"
            continue
        if stripped == NOTES_HEADING:
            section = "notes"
            continue
        if stripped == HISTORY_HEADING:
            section = "history"
            continue
        if stripped.startswith("# ") or stripped.startswith("## "):
            section = "history"  # preserve unknown sections under history
            continue
        if not stripped.startswith(_BULLET_PREFIX):
            continue
        body = stripped[len(_BULLET_PREFIX) :]
        body_clean, date = _split_date_suffix(body)
        if section == "current":
            key, sep, value = body_clean.partition(":")
            key = key.strip()
            value = value.strip()
            if not sep or not key or not value:
                continue
            state.current[key] = (value, date)
        elif section == "notes":
            if body_clean:
                state.notes.append((body_clean, date))
        else:
            state.history.append(raw_line.rstrip())
    return state


def _serialize(state: _State) -> str:
    parts: list[str] = [HEADING, ""]
    if state.current:
        parts.append(CURRENT_HEADING)
        for key, (value, date) in state.current.items():
            parts.append(_format_bullet(f"{key}: {value}", date))
        parts.append("")
    if state.notes:
        parts.append(NOTES_HEADING)
        for text, date in state.notes:
            parts.append(_format_bullet(text, date))
        parts.append("")
    if state.history:
        parts.append(HISTORY_HEADING)
        for line in state.history:
            parts.append(line)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _commit(target: Path, state: _State) -> None:
    new_text = _serialize(state)
    if len(new_text.encode("utf-8")) > MAX_MEMORY_BYTES:
        logger.warning(
            "[memory_file] memory file would exceed %d bytes; skipping write",
            MAX_MEMORY_BYTES,
        )
        return
    _atomic_write(target, new_text)


@contextmanager
def _locked(target: Path) -> Iterator[None]:
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix(f"{target.suffix}.lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write(target: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=target.parent,
        prefix=f"{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    os.replace(temp_path, target)
