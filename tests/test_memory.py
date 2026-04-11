"""Tests for the MemoryStore and MemoryEntry model."""

import json
from pathlib import Path

import pytest

from neuromancy.core.memory import MemoryStore
from neuromancy.core.models import MemoryCategory, MemoryEntry


@pytest.fixture
def tmp_memory_path(tmp_path: Path) -> Path:
    return tmp_path / "memory.json"


@pytest.fixture
def store(tmp_memory_path: Path) -> MemoryStore:
    return MemoryStore(tmp_memory_path)


# ---------------------------------------------------------------------------
# MemoryEntry model
# ---------------------------------------------------------------------------

class TestMemoryEntry:
    def test_defaults(self):
        entry = MemoryEntry(category=MemoryCategory.strategy, content="test")
        assert entry.id  # auto-generated
        assert entry.confidence == 0.5
        assert entry.times_reinforced == 0
        assert entry.created_at is not None
        assert entry.last_used is None
        assert entry.source_session is None

    def test_serialization_roundtrip(self):
        entry = MemoryEntry(
            category=MemoryCategory.tool_preference,
            content="User denied sudo",
            source_session="sess-1",
        )
        data = entry.model_dump(mode="json")
        restored = MemoryEntry.model_validate(data)
        assert restored.id == entry.id
        assert restored.category == entry.category
        assert restored.content == entry.content


# ---------------------------------------------------------------------------
# MemoryStore — basic CRUD
# ---------------------------------------------------------------------------

class TestMemoryStoreCRUD:
    def test_add_and_get_all(self, store: MemoryStore):
        assert store.get_all() == []
        entry = MemoryEntry(category=MemoryCategory.strategy, content="break tasks into steps")
        store.add(entry)
        assert len(store.get_all()) == 1
        assert store.get_all()[0].content == "break tasks into steps"

    def test_add_multiple(self, store: MemoryStore):
        store.add(MemoryEntry(category=MemoryCategory.strategy, content="one"))
        store.add(MemoryEntry(category=MemoryCategory.tool_failure, content="two"))
        assert len(store.get_all()) == 2

    def test_remove(self, store: MemoryStore):
        e1 = MemoryEntry(category=MemoryCategory.strategy, content="keep")
        e2 = MemoryEntry(category=MemoryCategory.strategy, content="remove me")
        store.add(e1)
        store.add(e2)
        store.remove(e2.id)
        remaining = store.get_all()
        assert len(remaining) == 1
        assert remaining[0].id == e1.id

    def test_remove_nonexistent_is_noop(self, store: MemoryStore):
        store.add(MemoryEntry(category=MemoryCategory.strategy, content="x"))
        store.remove("does-not-exist")
        assert len(store.get_all()) == 1


# ---------------------------------------------------------------------------
# MemoryStore — reinforce
# ---------------------------------------------------------------------------

class TestMemoryStoreReinforce:
    def test_reinforce_increments(self, store: MemoryStore):
        entry = MemoryEntry(category=MemoryCategory.strategy, content="test")
        store.add(entry)
        store.reinforce(entry.id)
        updated = store.get_all()[0]
        assert updated.times_reinforced == 1
        assert updated.confidence == pytest.approx(0.6)

    def test_reinforce_twice(self, store: MemoryStore):
        entry = MemoryEntry(category=MemoryCategory.strategy, content="test")
        store.add(entry)
        store.reinforce(entry.id)
        store.reinforce(entry.id)
        updated = store.get_all()[0]
        assert updated.times_reinforced == 2
        assert updated.confidence == pytest.approx(0.7)

    def test_reinforce_caps_at_1(self, store: MemoryStore):
        entry = MemoryEntry(category=MemoryCategory.strategy, content="test", confidence=0.95)
        store.add(entry)
        store.reinforce(entry.id)
        assert store.get_all()[0].confidence == 1.0
        store.reinforce(entry.id)
        assert store.get_all()[0].confidence == 1.0

    def test_reinforce_nonexistent_is_noop(self, store: MemoryStore):
        store.add(MemoryEntry(category=MemoryCategory.strategy, content="x"))
        store.reinforce("nonexistent")  # should not raise
        assert store.get_all()[0].times_reinforced == 0


# ---------------------------------------------------------------------------
# MemoryStore — get_relevant
# ---------------------------------------------------------------------------

class TestMemoryStoreRelevance:
    def test_returns_matching_memories(self, store: MemoryStore):
        store.add(MemoryEntry(category=MemoryCategory.tool_preference, content="User denied sudo commands"))
        store.add(MemoryEntry(category=MemoryCategory.strategy, content="Python works better than Node"))
        relevant = store.get_relevant("sudo permission denied")
        assert len(relevant) == 1
        assert "sudo" in relevant[0].content

    def test_returns_empty_for_no_match(self, store: MemoryStore):
        store.add(MemoryEntry(category=MemoryCategory.strategy, content="alpha beta gamma"))
        assert store.get_relevant("completely unrelated xyz") == []

    def test_respects_limit(self, store: MemoryStore):
        for i in range(20):
            store.add(MemoryEntry(category=MemoryCategory.strategy, content=f"strategy word number {i}"))
        relevant = store.get_relevant("strategy word", limit=5)
        assert len(relevant) == 5

    def test_sorted_by_confidence(self, store: MemoryStore):
        low = MemoryEntry(category=MemoryCategory.strategy, content="shared keyword here", confidence=0.2)
        high = MemoryEntry(category=MemoryCategory.strategy, content="shared keyword there", confidence=0.9)
        store.add(low)
        store.add(high)
        relevant = store.get_relevant("shared keyword")
        assert relevant[0].id == high.id

    def test_updates_last_used(self, store: MemoryStore):
        entry = MemoryEntry(category=MemoryCategory.strategy, content="unique findme token")
        store.add(entry)
        assert entry.last_used is None
        store.get_relevant("findme")
        updated = store.get_all()[0]
        assert updated.last_used is not None


# ---------------------------------------------------------------------------
# MemoryStore — find_similar
# ---------------------------------------------------------------------------

class TestMemoryStoreFindSimilar:
    def test_finds_similar(self, store: MemoryStore):
        store.add(MemoryEntry(category=MemoryCategory.tool_preference, content="User denied run_command"))
        found = store.find_similar("User denied run_command operations")
        assert found is not None
        assert "run_command" in found.content

    def test_returns_none_for_no_match(self, store: MemoryStore):
        store.add(MemoryEntry(category=MemoryCategory.strategy, content="alpha beta gamma"))
        assert store.find_similar("completely different unrelated text") is None

    def test_picks_best_match(self, store: MemoryStore):
        store.add(MemoryEntry(category=MemoryCategory.strategy, content="user prefers python"))
        store.add(MemoryEntry(category=MemoryCategory.strategy, content="user prefers python over node for scripting"))
        found = store.find_similar("user prefers python over node for scripting tasks")
        assert found is not None
        assert "node" in found.content  # should match the more specific one


# ---------------------------------------------------------------------------
# MemoryStore — persistence
# ---------------------------------------------------------------------------

class TestMemoryStorePersistence:
    def test_persists_to_disk(self, tmp_memory_path: Path):
        store1 = MemoryStore(tmp_memory_path)
        store1.add(MemoryEntry(category=MemoryCategory.strategy, content="persisted"))
        assert tmp_memory_path.exists()

        store2 = MemoryStore(tmp_memory_path)
        assert len(store2.get_all()) == 1
        assert store2.get_all()[0].content == "persisted"

    def test_survives_reinforce_and_reload(self, tmp_memory_path: Path):
        store1 = MemoryStore(tmp_memory_path)
        entry = MemoryEntry(category=MemoryCategory.strategy, content="reinforced")
        store1.add(entry)
        store1.reinforce(entry.id)

        store2 = MemoryStore(tmp_memory_path)
        assert store2.get_all()[0].times_reinforced == 1

    def test_survives_remove_and_reload(self, tmp_memory_path: Path):
        store1 = MemoryStore(tmp_memory_path)
        e1 = MemoryEntry(category=MemoryCategory.strategy, content="keep")
        e2 = MemoryEntry(category=MemoryCategory.strategy, content="delete")
        store1.add(e1)
        store1.add(e2)
        store1.remove(e2.id)

        store2 = MemoryStore(tmp_memory_path)
        assert len(store2.get_all()) == 1
        assert store2.get_all()[0].content == "keep"

    def test_handles_missing_file(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "does_not_exist.json")
        assert store.get_all() == []

    def test_handles_corrupt_file(self, tmp_memory_path: Path):
        tmp_memory_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_memory_path.write_text("not valid json {{{", encoding="utf-8")
        store = MemoryStore(tmp_memory_path)
        assert store.get_all() == []

    def test_creates_parent_directories(self, tmp_path: Path):
        deep_path = tmp_path / "a" / "b" / "c" / "memory.json"
        store = MemoryStore(deep_path)
        store.add(MemoryEntry(category=MemoryCategory.strategy, content="deep"))
        assert deep_path.exists()

    def test_file_is_valid_json(self, tmp_memory_path: Path):
        store = MemoryStore(tmp_memory_path)
        store.add(MemoryEntry(category=MemoryCategory.strategy, content="check json"))
        data = json.loads(tmp_memory_path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["content"] == "check json"
