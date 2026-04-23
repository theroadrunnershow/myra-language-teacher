"""Shared dotenv bootstrap for local development.

The app primarily reads configuration from ``os.environ``. This helper loads
the repo-root ``.env`` file into the process environment so existing
``os.environ.get(...)`` calls work unchanged.

Real process environment variables still win over ``.env`` by default.
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from dotenv import load_dotenv

_LOAD_LOCK = Lock()
_LOADED_PATHS: set[str] = set()


def project_dotenv_path() -> Path:
    """Return the repository-root ``.env`` path."""
    return Path(__file__).resolve().parents[1] / ".env"


def load_project_dotenv(
    *,
    override: bool = False,
    dotenv_path: str | Path | None = None,
    force: bool = False,
) -> Path | None:
    """Load ``.env`` into ``os.environ`` once per path.

    Args:
        override: Whether values from ``.env`` should override existing
            process environment variables. Defaults to ``False`` so real env
            vars take precedence.
        dotenv_path: Optional explicit dotenv path, mainly for tests.
        force: Reload the file even if that path was loaded before.

    Returns:
        The resolved dotenv path when the file exists, else ``None``.
    """
    candidate = Path(dotenv_path) if dotenv_path is not None else project_dotenv_path()
    candidate = candidate.expanduser().resolve()
    if not candidate.is_file():
        return None

    cache_key = str(candidate)
    with _LOAD_LOCK:
        if not force and cache_key in _LOADED_PATHS:
            return candidate
        load_dotenv(dotenv_path=str(candidate), override=override)
        _LOADED_PATHS.add(cache_key)
    return candidate
