"""Tests for the /memories REST endpoints."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from neuromancy.core.memory import MemoryStore
from neuromancy.core.models import MemoryCategory, MemoryEntry
from neuromancy.server.app import create_app


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.json")


@pytest.fixture
def client(store: MemoryStore) -> TestClient:
    app = create_app()
    app.state.memory_store = store
    return TestClient(app)


class TestListMemories:
    def test_empty(self, client: TestClient):
        resp = client.get("/memories")
        assert resp.status_code == 200
        assert resp.json() == {"memories": []}

    def test_returns_all_memories(self, client: TestClient, store: MemoryStore):
        store.add(MemoryEntry(category=MemoryCategory.strategy, content="one"))
        store.add(MemoryEntry(category=MemoryCategory.tool_failure, content="two"))
        resp = client.get("/memories")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["memories"]) == 2
        contents = {m["content"] for m in data["memories"]}
        assert contents == {"one", "two"}

    def test_memory_fields(self, client: TestClient, store: MemoryStore):
        store.add(MemoryEntry(
            category=MemoryCategory.user_correction,
            content="prefers tabs",
            confidence=0.8,
        ))
        resp = client.get("/memories")
        mem = resp.json()["memories"][0]
        assert mem["category"] == "user_correction"
        assert mem["content"] == "prefers tabs"
        assert mem["confidence"] == 0.8
        assert "id" in mem
        assert "created_at" in mem


class TestDeleteMemory:
    def test_delete_existing(self, client: TestClient, store: MemoryStore):
        entry = MemoryEntry(category=MemoryCategory.strategy, content="to delete")
        store.add(entry)
        resp = client.delete(f"/memories/{entry.id}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        # Verify it's gone
        resp2 = client.get("/memories")
        assert len(resp2.json()["memories"]) == 0

    def test_delete_nonexistent(self, client: TestClient):
        resp = client.delete("/memories/nonexistent-id")
        assert resp.status_code == 200  # idempotent

    def test_delete_one_of_many(self, client: TestClient, store: MemoryStore):
        e1 = MemoryEntry(category=MemoryCategory.strategy, content="keep")
        e2 = MemoryEntry(category=MemoryCategory.strategy, content="remove")
        store.add(e1)
        store.add(e2)

        client.delete(f"/memories/{e2.id}")

        resp = client.get("/memories")
        remaining = resp.json()["memories"]
        assert len(remaining) == 1
        assert remaining[0]["content"] == "keep"
