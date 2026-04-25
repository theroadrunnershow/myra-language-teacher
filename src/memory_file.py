"""Tiny markdown-backed persistent memory store for kids-teacher mode."""

from __future__ import annotations

import datetime as _dt
import fcntl
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

MEMORY_FILE_ENV_VAR = "MYRA_MEMORY_FILE"
DEFAULT_MEMORY_FILE = Path("~/.myra/memory.md")
DEFAULT_MEMORY_HEADING = "# Things to remember about the child"
MAX_MEMORY_BYTES = 4096

_ENTRY_DATE_RE = re.compile(r"\s+_\(\d{4}-\d{2}-\d{2}\)_$")


def resolve_memory_file_path(path: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the configured memory path, honoring ``MYRA_MEMORY_FILE``."""
    if path is not None:
        return Path(path).expanduser()
    override = os.environ.get(MEMORY_FILE_ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_MEMORY_FILE.expanduser()


def read(path: str | os.PathLike[str] | None = None) -> str:
    """Return the raw markdown memory contents, or ``""`` when missing."""
    target = resolve_memory_file_path(path)
    if not target.exists():
        return ""
    with target.open("r", encoding="utf-8") as handle:
        return handle.read().strip()


def append(fact: str, path: str | os.PathLike[str] | None = None) -> None:
    """Append a dated memory bullet unless an equivalent fact already exists."""
    normalized = _normalize_fact(fact)
    if not normalized:
        return

    target = resolve_memory_file_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    with _locked(target):
        current = read(target)
        current_entries = {_entry_body(line).casefold() for line in current.splitlines()}
        current_entries.discard("")
        if normalized.casefold() in current_entries:
            logger.debug("[memory_file] duplicate fact skipped: %r", normalized)
            return

        entry = f"- {normalized} _({_today_iso()})_"
        if current:
            new_text = f"{current.rstrip()}\n{entry}\n"
        else:
            new_text = f"{DEFAULT_MEMORY_HEADING}\n\n{entry}\n"

        if len(new_text.encode("utf-8")) > MAX_MEMORY_BYTES:
            logger.warning(
                "[memory_file] memory file would exceed %d bytes; skipping append",
                MAX_MEMORY_BYTES,
            )
            return

        _atomic_write(target, new_text)


def remove(
    fact: str,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    """Remove a bullet whose normalized fact text exactly matches ``fact``."""
    query = _normalize_fact(fact).casefold()
    if not query:
        return False

    target = resolve_memory_file_path(path)
    if not target.exists():
        return False

    with _locked(target):
        current = read(target)
        if not current:
            return False

        kept_lines: list[str] = []
        removed = False
        for line in current.splitlines():
            body = _entry_body(line)
            if body and query == body.casefold():
                removed = True
                continue
            kept_lines.append(line)

        if not removed:
            return False

        new_text = "\n".join(kept_lines).strip()
        if new_text:
            new_text = f"{new_text}\n"
        _atomic_write(target, new_text)
        return True


def remove_lines_matching_substring(
    substring: str,
    path: str | os.PathLike[str] | None = None,
) -> int:
    """Delete bullet lines whose body contains ``substring``. Returns count removed.

    Used by ``forget_face`` to drop the relationship line for a name. Matches
    on the bullet body (date suffix stripped), case-insensitive.
    """
    needle = _normalize_fact(substring).casefold()
    if not needle:
        return 0
    target = resolve_memory_file_path(path)
    if not target.exists():
        return 0
    with _locked(target):
        current = read(target)
        if not current:
            return 0
        kept: list[str] = []
        removed = 0
        for line in current.splitlines():
            body = _entry_body(line)
            if body and needle in body.casefold():
                removed += 1
                continue
            kept.append(line)
        if not removed:
            return 0
        new_text = "\n".join(kept).strip()
        if new_text:
            new_text = f"{new_text}\n"
        _atomic_write(target, new_text)
        return removed


def _today_iso() -> str:
    return _dt.date.today().isoformat()


def _normalize_fact(text: str) -> str:
    return " ".join((text or "").strip().split())


def _entry_body(line: str) -> str:
    stripped = line.strip()
    if not stripped.startswith("- "):
        return ""
    body = stripped[2:].strip()
    body = _ENTRY_DATE_RE.sub("", body)
    return _normalize_fact(body)


@contextmanager
def _locked(target: Path) -> Iterator[None]:
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
