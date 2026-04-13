"""Tests for the SQLite + FTS5 backed MemoryStore.

These tests assert the new storage contract while preserving the public
API the rest of the codebase depends on (add, get_all, remove, reinforce,
get_relevant, find_similar). The test_query_loop_memory.py tests cover
the integration with QueryLoop and should also remain green.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from neuromancy.core.memory import MemoryStore
from neuromancy.core.models import MemoryCategory, MemoryEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    return tmp_path / "memory.db"


def _entry(content: str, *, category: MemoryCategory = MemoryCategory.strategy,
           confidence: float = 0.5, times_reinforced: int = 0) -> MemoryEntry:
    return MemoryEntry(
        category=category,
        content=content,
        confidence=confidence,
        times_reinforced=times_reinforced,
    )


# ---------------------------------------------------------------------------
# Storage primitive — schema, CRUD
# ---------------------------------------------------------------------------


def test_opening_a_new_store_creates_the_db_file(temp_db: Path):
    assert not temp_db.exists()
    MemoryStore(path=temp_db)
    assert temp_db.exists()


def test_opening_a_new_store_creates_the_schema(temp_db: Path):
    MemoryStore(path=temp_db)
    with sqlite3.connect(temp_db) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert "memories" in tables
    assert "memories_fts" in tables


def test_empty_store_get_all_returns_empty_list(temp_db: Path):
    store = MemoryStore(path=temp_db)
    assert store.get_all() == []


def test_add_and_retrieve_single_entry(temp_db: Path):
    store = MemoryStore(path=temp_db)
    e = _entry("prefer fetch_page for static sites")
    store.add(e)
    all_entries = store.get_all()
    assert len(all_entries) == 1
    assert all_entries[0].content == "prefer fetch_page for static sites"
    assert all_entries[0].category == MemoryCategory.strategy


def test_add_multiple_entries_are_all_returned(temp_db: Path):
    store = MemoryStore(path=temp_db)
    store.add(_entry("one"))
    store.add(_entry("two"))
    store.add(_entry("three"))
    assert len(store.get_all()) == 3


def test_remove_deletes_a_specific_entry(temp_db: Path):
    store = MemoryStore(path=temp_db)
    keep = _entry("keep me")
    drop = _entry("drop me")
    store.add(keep)
    store.add(drop)
    store.remove(drop.id)
    remaining = store.get_all()
    assert len(remaining) == 1
    assert remaining[0].id == keep.id


def test_remove_nonexistent_id_is_a_noop(temp_db: Path):
    store = MemoryStore(path=temp_db)
    store.remove("nonexistent-uuid")  # should not raise
    assert store.get_all() == []


def test_entries_persist_across_store_instances(temp_db: Path):
    store1 = MemoryStore(path=temp_db)
    store1.add(_entry("persistent fact"))

    store2 = MemoryStore(path=temp_db)
    all_entries = store2.get_all()
    assert len(all_entries) == 1
    assert all_entries[0].content == "persistent fact"


def test_round_trip_preserves_all_fields(temp_db: Path):
    store = MemoryStore(path=temp_db)
    original = MemoryEntry(
        category=MemoryCategory.tool_preference,
        content="verbatim content",
        source_session="abc-123",
        confidence=0.87,
        times_reinforced=5,
    )
    store.add(original)
    loaded = store.get_all()[0]
    assert loaded.id == original.id
    assert loaded.category == original.category
    assert loaded.content == original.content
    assert loaded.source_session == original.source_session
    assert loaded.confidence == pytest.approx(0.87)
    assert loaded.times_reinforced == 5
    assert loaded.created_at is not None


# ---------------------------------------------------------------------------
# Reinforcement — updates confidence and counter
# ---------------------------------------------------------------------------


def test_reinforce_increments_confidence_by_point_one(temp_db: Path):
    store = MemoryStore(path=temp_db)
    e = _entry("memory to reinforce", confidence=0.5)
    store.add(e)
    store.reinforce(e.id)
    store.reinforce(e.id)
    refreshed = store.get_all()[0]
    assert refreshed.confidence == pytest.approx(0.7)
    assert refreshed.times_reinforced == 2


def test_reinforce_caps_confidence_at_one(temp_db: Path):
    store = MemoryStore(path=temp_db)
    e = _entry("almost full", confidence=0.95)
    store.add(e)
    store.reinforce(e.id)
    store.reinforce(e.id)  # would exceed 1.0 if uncapped
    refreshed = store.get_all()[0]
    assert refreshed.confidence == pytest.approx(1.0)


def test_reinforce_nonexistent_id_is_a_noop(temp_db: Path):
    store = MemoryStore(path=temp_db)
    store.reinforce("nothing-here")  # should not raise
    assert store.get_all() == []


# ---------------------------------------------------------------------------
# FTS5 relevance search — get_relevant and find_similar
# ---------------------------------------------------------------------------


def test_get_relevant_matches_on_shared_content_word(temp_db: Path):
    store = MemoryStore(path=temp_db)
    store.add(_entry("The user prefers python scripts"))
    store.add(_entry("Redis caches work well for session data"))
    store.add(_entry("Bash scripts break on Windows paths"))

    results = store.get_relevant("writing a python script")
    contents = [r.content for r in results]
    assert any("python" in c for c in contents)


def test_get_relevant_empty_context_returns_empty(temp_db: Path):
    store = MemoryStore(path=temp_db)
    store.add(_entry("anything"))
    assert store.get_relevant("") == []


def test_get_relevant_respects_limit(temp_db: Path):
    store = MemoryStore(path=temp_db)
    for i in range(10):
        store.add(_entry(f"python scripting tip number {i}"))
    results = store.get_relevant("python", limit=3)
    assert len(results) == 3


def test_get_relevant_prefers_higher_confidence(temp_db: Path):
    """Two equally-matching memories — higher confidence should rank first."""
    store = MemoryStore(path=temp_db)
    low = _entry("python is great for data work", confidence=0.3)
    high = _entry("python is great for everything", confidence=0.9)
    store.add(low)
    store.add(high)
    results = store.get_relevant("python", limit=2)
    assert len(results) == 2
    assert results[0].id == high.id


def test_get_relevant_returns_nothing_when_no_matches(temp_db: Path):
    store = MemoryStore(path=temp_db)
    store.add(_entry("redis caches sessions"))
    results = store.get_relevant("unrelated swahili proverb")
    assert results == []


def test_get_relevant_tolerates_punctuation_in_query(temp_db: Path):
    """FTS5 can choke on special chars — the store must sanitize queries."""
    store = MemoryStore(path=temp_db)
    store.add(_entry("user denied sudo commands"))
    results = store.get_relevant("run sudo: apt-get update (now)!")
    assert any("sudo" in r.content for r in results)


def test_get_relevant_updates_last_used_on_returned_entries(temp_db: Path):
    store = MemoryStore(path=temp_db)
    e = _entry("python practices")
    store.add(e)
    before = store.get_all()[0]
    assert before.last_used is None
    store.get_relevant("python")
    after = store.get_all()[0]
    assert after.last_used is not None


def test_find_similar_returns_best_match(temp_db: Path):
    store = MemoryStore(path=temp_db)
    store.add(_entry("user denied run_command with rm -rf root"))
    store.add(_entry("user prefers markdown for formatting"))
    match = store.find_similar("user denied run_command")
    assert match is not None
    assert "denied" in match.content


def test_find_similar_returns_none_when_no_match(temp_db: Path):
    store = MemoryStore(path=temp_db)
    store.add(_entry("totally unrelated fact about turtles"))
    match = store.find_similar("quantum chromodynamics")
    assert match is None


def test_find_similar_returns_none_on_empty_query(temp_db: Path):
    store = MemoryStore(path=temp_db)
    store.add(_entry("content"))
    assert store.find_similar("") is None


# ---------------------------------------------------------------------------
# Migration — legacy memory.json → SQLite
# ---------------------------------------------------------------------------


def test_migrates_from_legacy_json_file(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    legacy = tmp_path / "memory.json"
    legacy.write_text(
        json.dumps(
            [
                {
                    "id": "abc-1",
                    "category": "strategy",
                    "content": "imported strategy learning",
                    "source_session": None,
                    "confidence": 0.7,
                    "times_reinforced": 2,
                    "created_at": "2026-04-01T00:00:00+00:00",
                    "last_used": None,
                },
                {
                    "id": "def-2",
                    "category": "tool_preference",
                    "content": "imported tool preference",
                    "source_session": "sess-42",
                    "confidence": 0.9,
                    "times_reinforced": 4,
                    "created_at": "2026-04-02T00:00:00+00:00",
                    "last_used": "2026-04-03T00:00:00+00:00",
                },
            ]
        )
    )

    store = MemoryStore(path=db_path)
    entries = store.get_all()
    assert len(entries) == 2

    # Identity, confidence, reinforcement counts, and source session should all survive
    by_id = {e.id: e for e in entries}
    assert by_id["abc-1"].content == "imported strategy learning"
    assert by_id["abc-1"].confidence == pytest.approx(0.7)
    assert by_id["def-2"].source_session == "sess-42"
    assert by_id["def-2"].times_reinforced == 4

    # Legacy file is backed up, not deleted
    assert not legacy.exists()
    assert (tmp_path / "memory.json.pre-sqlite.bak").exists()


def test_migration_is_idempotent(tmp_path: Path):
    """If the db already has rows, don't re-import on subsequent startups.

    The legacy file gets backed up on first startup; any later open that
    finds the backup in place should be a pure no-op.
    """
    db_path = tmp_path / "memory.db"
    legacy = tmp_path / "memory.json"
    legacy.write_text(
        json.dumps(
            [
                {
                    "id": "abc-1",
                    "category": "strategy",
                    "content": "imported",
                    "source_session": None,
                    "confidence": 0.5,
                    "times_reinforced": 0,
                    "created_at": "2026-04-01T00:00:00+00:00",
                    "last_used": None,
                }
            ]
        )
    )
    MemoryStore(path=db_path)
    MemoryStore(path=db_path)  # re-open
    store = MemoryStore(path=db_path)
    entries = store.get_all()
    assert len(entries) == 1  # still exactly one, not duplicated


def test_migration_handles_missing_legacy_file(tmp_path: Path):
    """Opening a store when no legacy file exists should be a fresh start."""
    db_path = tmp_path / "memory.db"
    assert not (tmp_path / "memory.json").exists()
    store = MemoryStore(path=db_path)
    assert store.get_all() == []


def test_migration_handles_corrupted_legacy_file(tmp_path: Path):
    """Corrupted legacy JSON must not crash startup; the corrupt file is left
    in place so the user can inspect it."""
    db_path = tmp_path / "memory.db"
    legacy = tmp_path / "memory.json"
    legacy.write_text("this is { not valid json")
    # Should not raise
    store = MemoryStore(path=db_path)
    assert store.get_all() == []
    # Corrupt file should still be there (not backed up, not deleted)
    assert legacy.exists()


# ---------------------------------------------------------------------------
# API stability — every method used by production callers still works
# ---------------------------------------------------------------------------


def test_public_api_surface_is_preserved(temp_db: Path):
    """Guardrail: the methods the rest of the codebase relies on must exist
    with their expected signatures."""
    store = MemoryStore(path=temp_db)
    # Methods query_loop.py and server/app.py rely on
    assert callable(store.add)
    assert callable(store.reinforce)
    assert callable(store.get_relevant)
    assert callable(store.find_similar)
    assert callable(store.get_all)
    assert callable(store.remove)
