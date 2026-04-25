"""Tests for the sectioned markdown-backed persistent memory store."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

import memory_file


def _set_today(monkeypatch, iso: str) -> None:
    monkeypatch.setattr(memory_file, "_today_iso", lambda: iso)


def test_read_missing_file_returns_empty(tmp_path) -> None:
    assert memory_file.read_raw(tmp_path / "missing.md") == ""
    assert memory_file.read_for_prompt(tmp_path / "missing.md") == ""
    assert memory_file.list_notes(tmp_path / "missing.md") == []


def test_set_key_creates_file_with_current_section(tmp_path, monkeypatch) -> None:
    _set_today(monkeypatch, "2026-04-25")
    target = tmp_path / "memory.md"

    memory_file.set_key("name", "Aanya", target)

    assert memory_file.read_raw(target) == (
        "# Things to remember about the child\n\n"
        "## Current\n"
        "- name: Aanya _(2026-04-25)_"
    )


def test_set_key_supersedes_previous_value_into_history(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.md"
    _set_today(monkeypatch, "2026-04-24")
    memory_file.set_key("name", "Abi", target)
    _set_today(monkeypatch, "2026-04-25")
    memory_file.set_key("name", "Myra", target)

    raw = memory_file.read_raw(target)
    assert "## Current" in raw
    assert "- name: Myra _(2026-04-25)_" in raw
    assert "## History" in raw
    assert "- name: Abi _(2026-04-24 → 2026-04-25)_" in raw


def test_set_key_idempotent_when_value_unchanged(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.md"
    _set_today(monkeypatch, "2026-04-24")
    memory_file.set_key("name", "Aanya", target)
    _set_today(monkeypatch, "2026-04-25")
    memory_file.set_key("name", "Aanya", target)

    raw = memory_file.read_raw(target)
    assert raw.count("- name: Aanya") == 1
    assert "## History" not in raw
    # Original date preserved
    assert "_(2026-04-24)_" in raw


def test_set_key_rejects_unknown_key(tmp_path) -> None:
    target = tmp_path / "memory.md"
    with pytest.raises(memory_file.InvalidKeyError):
        memory_file.set_key("ssn", "123-45-6789", target)


def test_set_key_normalizes_whitespace_in_value(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.md"
    _set_today(monkeypatch, "2026-04-25")
    memory_file.set_key("name", "  Aanya  \n ", target)
    assert "- name: Aanya _(2026-04-25)_" in memory_file.read_raw(target)


def test_append_note_creates_notes_section(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.md"
    _set_today(monkeypatch, "2026-04-25")
    memory_file.append_note("She loves dinosaurs", target)
    raw = memory_file.read_raw(target)
    assert "## Notes" in raw
    assert "- She loves dinosaurs _(2026-04-25)_" in raw


def test_append_note_skips_exact_duplicate(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.md"
    _set_today(monkeypatch, "2026-04-25")
    memory_file.append_note("She loves dinosaurs", target)
    memory_file.append_note("  she LOVES dinosaurs ", target)
    assert memory_file.read_raw(target).count("dinosaurs") == 1


def test_list_notes_returns_only_current_notes(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.md"
    _set_today(monkeypatch, "2026-04-25")
    memory_file.append_note("She loves dinosaurs", target)
    memory_file.append_note("She is afraid of dogs", target)
    assert memory_file.list_notes(target) == [
        "She loves dinosaurs",
        "She is afraid of dogs",
    ]


def test_replace_notes_moves_targets_to_history_and_appends_merged(
    tmp_path, monkeypatch
) -> None:
    target = tmp_path / "memory.md"
    _set_today(monkeypatch, "2026-04-24")
    memory_file.append_note("She loves dinosaurs", target)
    memory_file.append_note("She loves T-rexes specifically", target)
    memory_file.append_note("She is afraid of dogs", target)

    _set_today(monkeypatch, "2026-04-25")
    memory_file.replace_notes(
        removed_indices=[0, 1],
        new_text="She loves dinosaurs, especially T-rexes",
        path=target,
    )

    notes = memory_file.list_notes(target)
    assert "She loves dinosaurs, especially T-rexes" in notes
    assert "She loves dinosaurs" not in notes
    assert "She loves T-rexes specifically" not in notes
    assert "She is afraid of dogs" in notes

    raw = memory_file.read_raw(target)
    assert "## History" in raw
    assert "- She loves dinosaurs _(2026-04-24 → 2026-04-25)_" in raw
    assert "- She loves T-rexes specifically _(2026-04-24 → 2026-04-25)_" in raw


def test_replace_notes_with_no_new_text_just_drops(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.md"
    _set_today(monkeypatch, "2026-04-24")
    memory_file.append_note("She loves dinosaurs", target)
    memory_file.append_note("She is afraid of dogs", target)

    _set_today(monkeypatch, "2026-04-25")
    memory_file.replace_notes(removed_indices=[0], new_text=None, path=target)

    notes = memory_file.list_notes(target)
    assert notes == ["She is afraid of dogs"]


def test_read_for_prompt_excludes_history(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.md"
    _set_today(monkeypatch, "2026-04-24")
    memory_file.set_key("name", "Abi", target)
    memory_file.append_note("She loves dinosaurs", target)
    _set_today(monkeypatch, "2026-04-25")
    memory_file.set_key("name", "Myra", target)

    prompt_text = memory_file.read_for_prompt(target)
    assert "## Current" in prompt_text
    assert "- name: Myra" in prompt_text
    assert "## Notes" in prompt_text
    assert "She loves dinosaurs" in prompt_text
    # History MUST NOT appear in the prompt — that's the whole point
    assert "## History" not in prompt_text
    assert "Abi" not in prompt_text


def test_read_for_prompt_empty_when_only_history(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.md"
    # Manually craft a file with only history
    target.write_text(
        "# Things to remember about the child\n\n"
        "## History\n"
        "- name: Abi _(2026-04-24 → 2026-04-25)_\n"
    )
    assert memory_file.read_for_prompt(target) == ""


def test_concurrent_set_keys_serialize_safely(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.md"
    _set_today(monkeypatch, "2026-04-25")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(memory_file.set_key, "name", "Aanya", target),
            pool.submit(memory_file.set_key, "favourite_colour", "blue", target),
        ]
        for f in futures:
            f.result()
    raw = memory_file.read_raw(target)
    assert "name: Aanya" in raw
    assert "favourite_colour: blue" in raw


def test_parse_round_trips_through_serialize(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.md"
    _set_today(monkeypatch, "2026-04-24")
    memory_file.set_key("name", "Abi", target)
    memory_file.append_note("She loves dinosaurs", target)
    _set_today(monkeypatch, "2026-04-25")
    memory_file.set_key("name", "Myra", target)
    memory_file.replace_notes(
        removed_indices=[0],
        new_text="She loves all kinds of dinosaurs",
        path=target,
    )

    raw_first = memory_file.read_raw(target)
    # A no-op write (idempotent set_key) should not change the file content
    memory_file.set_key("name", "Myra", target)
    raw_second = memory_file.read_raw(target)
    assert raw_first == raw_second
