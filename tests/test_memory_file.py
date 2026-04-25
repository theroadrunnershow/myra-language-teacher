"""Tests for the tiny markdown-backed persistent memory store."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import memory_file


def test_read_missing_file_returns_empty(tmp_path) -> None:
    assert memory_file.read(tmp_path / "missing.md") == ""


def test_append_creates_memory_file_with_heading_and_dated_bullet(
    tmp_path,
    monkeypatch,
) -> None:
    target = tmp_path / "memory.md"
    monkeypatch.setattr(memory_file, "_today_iso", lambda: "2026-04-24")

    memory_file.append("Her favourite colour is blue", target)

    assert memory_file.read(target) == (
        "# Things to remember about the child\n\n"
        "- Her favourite colour is blue _(2026-04-24)_"
    )


def test_append_skips_duplicate_fact_case_insensitively(
    tmp_path,
    monkeypatch,
) -> None:
    target = tmp_path / "memory.md"
    monkeypatch.setattr(memory_file, "_today_iso", lambda: "2026-04-24")

    memory_file.append("Her name is Aanya", target)
    memory_file.append("  her NAME is aanya  ", target)

    assert memory_file.read(target).count("Her name is Aanya") == 1


def test_remove_deletes_matching_bullet_and_keeps_other_lines(
    tmp_path,
    monkeypatch,
) -> None:
    target = tmp_path / "memory.md"
    monkeypatch.setattr(memory_file, "_today_iso", lambda: "2026-04-24")

    memory_file.append("Her name is Aanya", target)
    memory_file.append("Her brother is Rohan", target)

    removed = memory_file.remove("Her brother is Rohan", target)

    assert removed is True
    text = memory_file.read(target)
    assert "Her name is Aanya" in text
    assert "Her brother is Rohan" not in text


def test_concurrent_appends_keep_both_facts(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.md"
    monkeypatch.setattr(memory_file, "_today_iso", lambda: "2026-04-24")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(memory_file.append, "She loves tigers", target),
            pool.submit(memory_file.append, "Her favourite colour is blue", target),
        ]
        for future in futures:
            future.result()

    text = memory_file.read(target)
    assert "She loves tigers" in text
    assert "Her favourite colour is blue" in text


def test_remove_requires_exact_normalized_fact_match(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.md"
    monkeypatch.setattr(memory_file, "_today_iso", lambda: "2026-04-24")

    memory_file.append("Her brother is Rohan", target)

    removed = memory_file.remove("rohan", target)

    assert removed is False
    assert "Her brother is Rohan" in memory_file.read(target)
