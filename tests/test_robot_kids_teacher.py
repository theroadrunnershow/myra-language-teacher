"""Tests for the thin CLI entry at src/robot_kids_teacher.py.

We verify two V1 guarantees:

1. Importing ``robot_kids_teacher`` does NOT drag in the ``openai`` SDK.
2. ``--help`` runs cleanly without touching any asyncio loop or backend.
"""

from __future__ import annotations

import sys

import pytest


def test_import_does_not_pull_in_openai():
    # Drop any cached imports so this is a genuine fresh import test.
    sys.modules.pop("robot_kids_teacher", None)
    sys.modules.pop("openai", None)
    import robot_kids_teacher  # noqa: F401
    assert "openai" not in sys.modules


def test_help_exits_cleanly():
    import robot_kids_teacher

    with pytest.raises(SystemExit) as excinfo:
        robot_kids_teacher.main(["--help"])
    # argparse raises SystemExit(0) on --help.
    assert excinfo.value.code == 0


def test_missing_openai_returns_exit_code_two(monkeypatch):
    import robot_kids_teacher

    # Simulate "openai not installed" by blocking the import.
    real_find_spec = None

    def _blocked_import(name, *args, **kwargs):
        if name == "openai":
            raise ImportError("openai not installed in test env")
        return original_import(name, *args, **kwargs)

    original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
    monkeypatch.setattr("builtins.__import__", _blocked_import)
    # Also pre-clear any cached openai module.
    sys.modules.pop("openai", None)

    exit_code = robot_kids_teacher.main(["--session-id", "test-session"])
    assert exit_code == 2
