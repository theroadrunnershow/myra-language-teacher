"""Tests for src/memory_reconciler.py."""

from __future__ import annotations

import json

import pytest

import memory_file
import memory_reconciler


def _set_today(monkeypatch, iso: str) -> None:
    monkeypatch.setattr(memory_file, "_today_iso", lambda: iso)


def test_add_note_appends_when_below_min_existing(tmp_path, monkeypatch) -> None:
    _set_today(monkeypatch, "2026-04-25")
    target = tmp_path / "memory.md"

    def boom(**kwargs) -> str:  # would crash if called
        raise AssertionError("LLM should not be called below the threshold")

    action = memory_reconciler.add_note(
        "She loves dinosaurs",
        path=target,
        completer=boom,
        min_existing_for_llm=3,
    )
    assert action == "appended_no_llm"
    assert "She loves dinosaurs" in memory_file.list_notes(target)


def test_add_note_calls_llm_when_at_threshold_and_skips_duplicate(
    tmp_path, monkeypatch
) -> None:
    _set_today(monkeypatch, "2026-04-25")
    target = tmp_path / "memory.md"
    for n in ("She loves dinosaurs", "She is afraid of dogs", "She loves mango"):
        memory_file.append_note(n, target)

    captured: dict = {}

    def fake_completer(**kwargs) -> str:
        captured.update(kwargs)
        # LLM says "skip" — new note is fully covered
        return json.dumps({"action": "skip", "remove": [], "text": ""})

    action = memory_reconciler.add_note(
        "She really loves dinosaurs",
        path=target,
        completer=fake_completer,
        min_existing_for_llm=3,
    )
    assert action == "skipped"
    assert captured["json_mode"] is True
    notes = memory_file.list_notes(target)
    assert "She really loves dinosaurs" not in notes
    assert len(notes) == 3


def test_add_note_merge_replaces_target_indices(tmp_path, monkeypatch) -> None:
    _set_today(monkeypatch, "2026-04-25")
    target = tmp_path / "memory.md"
    for n in (
        "She loves dinosaurs",
        "She loves T-rexes",
        "She is afraid of dogs",
    ):
        memory_file.append_note(n, target)

    def fake_completer(**kwargs) -> str:
        return json.dumps(
            {
                "action": "merge",
                "remove": [1, 2],  # 1-based, refers to relevant list
                "text": "She loves dinosaurs, especially T-rexes",
            }
        )

    action = memory_reconciler.add_note(
        "She loves all dinosaurs, especially T-rexes",
        path=target,
        completer=fake_completer,
        min_existing_for_llm=3,
    )
    assert action == "merge"
    notes = memory_file.list_notes(target)
    assert "She loves dinosaurs, especially T-rexes" in notes
    assert "She loves dinosaurs" not in notes
    assert "She loves T-rexes" not in notes
    assert "She is afraid of dogs" in notes


def test_add_note_replace_swaps_old_for_new(tmp_path, monkeypatch) -> None:
    _set_today(monkeypatch, "2026-04-25")
    target = tmp_path / "memory.md"
    for n in (
        "She is afraid of dogs",
        "She loves dinosaurs",
        "She loves mango",
    ):
        memory_file.append_note(n, target)

    def fake_completer(**kwargs) -> str:
        return json.dumps(
            {
                "action": "replace",
                "remove": [1],
                "text": "She used to be afraid of dogs but now likes them",
            }
        )

    action = memory_reconciler.add_note(
        "She isn't scared of dogs anymore",
        path=target,
        completer=fake_completer,
        min_existing_for_llm=3,
    )
    assert action == "replace"
    notes = memory_file.list_notes(target)
    assert "She is afraid of dogs" not in notes
    assert any("used to be afraid of dogs" in n for n in notes)


def test_add_note_falls_back_to_append_on_llm_exception(
    tmp_path, monkeypatch, caplog
) -> None:
    _set_today(monkeypatch, "2026-04-25")
    target = tmp_path / "memory.md"
    for n in ("a", "b", "c"):
        memory_file.append_note(n, target)

    def boom(**kwargs) -> str:
        raise RuntimeError("network dead")

    with caplog.at_level("WARNING"):
        action = memory_reconciler.add_note(
            "She loves dinosaurs",
            path=target,
            completer=boom,
            min_existing_for_llm=3,
        )
    assert action == "appended"
    assert "She loves dinosaurs" in memory_file.list_notes(target)
    assert any("LLM call failed" in r.message for r in caplog.records)


def test_add_note_falls_back_to_append_on_invalid_json(
    tmp_path, monkeypatch, caplog
) -> None:
    _set_today(monkeypatch, "2026-04-25")
    target = tmp_path / "memory.md"
    for n in ("a", "b", "c"):
        memory_file.append_note(n, target)

    def junk(**kwargs) -> str:
        return "not json at all"

    with caplog.at_level("WARNING"):
        action = memory_reconciler.add_note(
            "She loves dinosaurs",
            path=target,
            completer=junk,
            min_existing_for_llm=3,
        )
    assert action == "appended"
    assert "She loves dinosaurs" in memory_file.list_notes(target)
    assert any("invalid JSON" in r.message for r in caplog.records)


def test_find_relevant_notes_returns_all_when_few_existing() -> None:
    notes = ["one", "two"]
    relevant = memory_reconciler.find_relevant_notes("query", notes, k=5)
    assert relevant == [(0, "one"), (1, "two")]


def test_find_relevant_notes_filters_by_similarity() -> None:
    notes = [
        "She loves dinosaurs",
        "She is afraid of dogs",
        "She loves T-rexes",
        "Her favourite colour is blue",
        "She likes to count to ten",
    ]
    relevant = memory_reconciler.find_relevant_notes(
        "She loves all kinds of dinosaurs", notes, k=2
    )
    assert len(relevant) == 2
    texts = [t for _, t in relevant]
    # The two dinosaur-related notes should rank above the unrelated ones
    assert any("dinosaurs" in t for t in texts)


def test_add_note_skips_empty_text(tmp_path) -> None:
    target = tmp_path / "memory.md"
    action = memory_reconciler.add_note("   ", path=target)
    assert action == "skipped"


def test_system_prompt_carries_different_names_guardrail() -> None:
    """Regression: the prompt rule that prevents merge/replace across
    different proper-name subjects (so 'Sara is Myra's aunt' can't clobber
    'Priya is Myra's aunt') must remain in the reconciler's system prompt."""
    # Normalize line wraps so source-level hard wraps don't matter to substring
    # checks — the LLM sees the prompt as a continuous string anyway.
    prompt = " ".join(memory_reconciler._SYSTEM_PROMPT.split())
    assert "different proper-name subjects" in prompt
    assert "Never merge or replace across different named subjects" in prompt
