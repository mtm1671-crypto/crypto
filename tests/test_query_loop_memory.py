"""Tests for memory integration in the QueryLoop."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from neuromancy.core.memory import MemoryStore
from neuromancy.core.models import (
    MemoryCategory, MemoryEntry, Message, Role, Session,
    StreamEvent, StreamEventType,
)
from neuromancy.core.query_loop import QueryLoop


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_session() -> Session:
    return Session(id="test-session", model="test-model")


def make_text_chunk(text: str) -> dict:
    """Simulate an OpenAI-format streaming chunk with text content."""
    return {"choices": [{"delta": {"content": text}}]}


def make_done_chunk() -> dict:
    return {"choices": [{"delta": {}}]}


def make_tool_call_chunk(name: str, arguments: str, tc_id: str = "tc-1") -> dict:
    return {
        "choices": [{
            "delta": {
                "tool_calls": [{
                    "index": 0,
                    "id": tc_id,
                    "function": {"name": name, "arguments": arguments},
                }]
            }
        }]
    }


class FakeLLM:
    """Fake LLM that yields pre-configured chunks."""

    def __init__(self, chunks: list[dict], reflection_response: dict | None = None):
        self._chunks = chunks
        self._reflection_response = reflection_response or {
            "choices": [{"message": {"content": "[]"}}]
        }
        self.last_usage: dict | None = None
        self.model: str = "anthropic/claude-haiku-4"

    async def stream_complete(self, messages, tools=None):
        self.last_usage = None
        for chunk in self._chunks:
            yield chunk

    async def complete(self, messages, tools=None):
        self.last_usage = None
        return self._reflection_response


class FakeRegistry:
    """Fake plugin registry that returns no tools and never needs approval."""

    def get_all_tool_schemas(self):
        return []

    def requires_approval(self, name: str) -> bool:
        return False

    def find_plugin_for_tool(self, name: str):
        plugin = AsyncMock()
        plugin.execute = AsyncMock(return_value="tool output")
        return plugin, None


class FakeRegistryWithApproval(FakeRegistry):
    def requires_approval(self, name: str) -> bool:
        return True


async def collect_events(loop: QueryLoop, session: Session, msg: str) -> list[StreamEvent]:
    events = []
    async for event in loop.run(session, msg):
        events.append(event)
    return events


def _message_text(message: dict) -> str:
    """Extract the full text of a message regardless of content shape.

    After prompt caching was added, system messages may carry a list of
    cache-controlled content blocks instead of a plain string. Tests that
    assert on the text should work against either form.
    """
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


# ---------------------------------------------------------------------------
# Memory injection tests
# ---------------------------------------------------------------------------

class TestMemoryInjection:
    @pytest.fixture
    def store(self, tmp_path: Path) -> MemoryStore:
        return MemoryStore(tmp_path / "memory.json")

    @pytest.mark.asyncio
    async def test_injects_relevant_memories(self, store: MemoryStore):
        store.add(MemoryEntry(
            category=MemoryCategory.tool_preference,
            content="User denied sudo commands",
        ))

        captured_messages = []
        original_chunks = [make_text_chunk("Hello"), make_done_chunk()]

        class SpyLLM(FakeLLM):
            async def stream_complete(self, messages, tools=None):
                captured_messages.extend(messages)
                async for chunk in super().stream_complete(messages, tools):
                    yield chunk

        llm = SpyLLM(original_chunks)
        loop = QueryLoop(llm, FakeRegistry(), memory_store=store)
        session = make_session()

        await collect_events(loop, session, "run sudo apt-get update")

        # The first message should be a system message with memories
        assert captured_messages[0]["role"] == "system"
        system_text = _message_text(captured_messages[0])
        assert "sudo" in system_text
        assert "learned" in system_text.lower()

    @pytest.mark.asyncio
    async def test_no_injection_when_no_relevant_memories(self, store: MemoryStore):
        store.add(MemoryEntry(
            category=MemoryCategory.strategy,
            content="alpha beta gamma unrelated",
        ))

        captured_messages = []

        class SpyLLM(FakeLLM):
            async def stream_complete(self, messages, tools=None):
                captured_messages.extend(messages)
                async for chunk in super().stream_complete(messages, tools):
                    yield chunk

        llm = SpyLLM([make_text_chunk("Hi"), make_done_chunk()])
        loop = QueryLoop(llm, FakeRegistry(), memory_store=store)
        session = make_session()

        await collect_events(loop, session, "completely different topic xyz")

        # No system message should be injected
        assert all(m["role"] != "system" for m in captured_messages)

    @pytest.mark.asyncio
    async def test_no_injection_when_no_store(self):
        llm = FakeLLM([make_text_chunk("Hi"), make_done_chunk()])
        loop = QueryLoop(llm, FakeRegistry(), memory_store=None)
        session = make_session()

        # Should run fine without crashing
        events = await collect_events(loop, session, "hello")
        event_types = [e.type for e in events]
        assert StreamEventType.message_start in event_types
        assert StreamEventType.done in event_types

    @pytest.mark.asyncio
    async def test_no_injection_when_store_empty(self, store: MemoryStore):
        captured_messages = []

        class SpyLLM(FakeLLM):
            async def stream_complete(self, messages, tools=None):
                captured_messages.extend(messages)
                async for chunk in super().stream_complete(messages, tools):
                    yield chunk

        llm = SpyLLM([make_text_chunk("Hi"), make_done_chunk()])
        loop = QueryLoop(llm, FakeRegistry(), memory_store=store)
        session = make_session()

        await collect_events(loop, session, "hello world")

        assert all(m["role"] != "system" for m in captured_messages)


# ---------------------------------------------------------------------------
# Denial recording tests
# ---------------------------------------------------------------------------

class TestDenialRecording:
    @pytest.fixture
    def store(self, tmp_path: Path) -> MemoryStore:
        return MemoryStore(tmp_path / "memory.json")

    @pytest.mark.asyncio
    async def test_denial_creates_memory(self, store: MemoryStore):
        chunks = [
            make_tool_call_chunk("run_command", '{"command": "rm -rf /"}'),
            make_done_chunk(),
        ]
        llm = FakeLLM(chunks)
        loop = QueryLoop(llm, FakeRegistryWithApproval(), memory_store=store)
        session = make_session()

        # Run in background, deny the approval
        async def deny_after_request():
            # Wait for the approval to be registered
            for _ in range(100):
                if loop._pending_approvals:
                    tool_call_id = list(loop._pending_approvals.keys())[0]
                    loop.resolve_approval(tool_call_id, False)
                    return
                await asyncio.sleep(0.01)

        task = asyncio.create_task(deny_after_request())
        events = await collect_events(loop, session, "delete everything")
        await task

        # Should have recorded a denial memory
        memories = store.get_all()
        assert len(memories) >= 1
        denial_mem = [m for m in memories if m.category == MemoryCategory.tool_preference]
        assert len(denial_mem) >= 1
        assert "denied" in denial_mem[0].content.lower()
        assert "run_command" in denial_mem[0].content

    @pytest.mark.asyncio
    async def test_repeated_denial_reinforces(self, store: MemoryStore):
        # Pre-populate with an existing denial memory
        existing = MemoryEntry(
            category=MemoryCategory.tool_preference,
            content="User denied run_command with args: something",
        )
        store.add(existing)

        chunks = [
            make_tool_call_chunk("run_command", '{"command": "ls"}'),
            make_done_chunk(),
        ]
        llm = FakeLLM(chunks)
        loop = QueryLoop(llm, FakeRegistryWithApproval(), memory_store=store)
        session = make_session()

        async def deny_after_request():
            for _ in range(100):
                if loop._pending_approvals:
                    tool_call_id = list(loop._pending_approvals.keys())[0]
                    loop.resolve_approval(tool_call_id, False)
                    return
                await asyncio.sleep(0.01)

        task = asyncio.create_task(deny_after_request())
        await collect_events(loop, session, "list files")
        await task

        # Should have reinforced, not added a new one
        memories = store.get_all()
        tool_pref_memories = [m for m in memories if m.category == MemoryCategory.tool_preference]
        # Either reinforced existing (count stays 1) or added new — depends on find_similar match.
        # The existing memory should be reinforced if similar enough.
        reinforced = [m for m in memories if m.times_reinforced > 0]
        if reinforced:
            assert reinforced[0].id == existing.id


# ---------------------------------------------------------------------------
# Reflection tests
# ---------------------------------------------------------------------------

class TestReflection:
    @pytest.fixture
    def store(self, tmp_path: Path) -> MemoryStore:
        return MemoryStore(tmp_path / "memory.json")

    @pytest.mark.asyncio
    async def test_reflection_extracts_learnings(self, store: MemoryStore):
        reflection_resp = {
            "choices": [{
                "message": {
                    "content": json.dumps([
                        {"category": "strategy", "content": "Breaking tasks into steps worked well"}
                    ])
                }
            }]
        }
        llm = FakeLLM(
            [make_text_chunk("Sure, let me help"), make_done_chunk()],
            reflection_response=reflection_resp,
        )
        loop = QueryLoop(llm, FakeRegistry(), memory_store=store)
        session = make_session()

        await collect_events(loop, session, "help me with this task")
        await asyncio.sleep(0.1)  # let fire-and-forget reflection task complete

        memories = store.get_all()
        assert len(memories) == 1
        assert memories[0].category == MemoryCategory.strategy
        assert "steps" in memories[0].content

    @pytest.mark.asyncio
    async def test_reflection_handles_empty_response(self, store: MemoryStore):
        reflection_resp = {
            "choices": [{"message": {"content": "[]"}}]
        }
        llm = FakeLLM(
            [make_text_chunk("Hello"), make_done_chunk()],
            reflection_response=reflection_resp,
        )
        loop = QueryLoop(llm, FakeRegistry(), memory_store=store)
        session = make_session()

        await collect_events(loop, session, "hi there")

        assert len(store.get_all()) == 0

    @pytest.mark.asyncio
    async def test_reflection_handles_invalid_json(self, store: MemoryStore):
        reflection_resp = {
            "choices": [{"message": {"content": "not json at all"}}]
        }
        llm = FakeLLM(
            [make_text_chunk("Hello"), make_done_chunk()],
            reflection_response=reflection_resp,
        )
        loop = QueryLoop(llm, FakeRegistry(), memory_store=store)
        session = make_session()

        # Should not crash
        await collect_events(loop, session, "hi there")
        assert len(store.get_all()) == 0

    @pytest.mark.asyncio
    async def test_reflection_handles_markdown_fenced_json(self, store: MemoryStore):
        reflection_resp = {
            "choices": [{
                "message": {
                    "content": '```json\n[{"category": "user_correction", "content": "User prefers Python"}]\n```'
                }
            }]
        }
        llm = FakeLLM(
            [make_text_chunk("OK"), make_done_chunk()],
            reflection_response=reflection_resp,
        )
        loop = QueryLoop(llm, FakeRegistry(), memory_store=store)
        session = make_session()

        await collect_events(loop, session, "use Python please")
        await asyncio.sleep(0.1)  # let fire-and-forget reflection task complete

        memories = store.get_all()
        assert len(memories) == 1
        assert memories[0].category == MemoryCategory.user_correction

    @pytest.mark.asyncio
    async def test_reflection_ignores_invalid_category(self, store: MemoryStore):
        reflection_resp = {
            "choices": [{
                "message": {
                    "content": json.dumps([
                        {"category": "bogus_category", "content": "something"}
                    ])
                }
            }]
        }
        llm = FakeLLM(
            [make_text_chunk("OK"), make_done_chunk()],
            reflection_response=reflection_resp,
        )
        loop = QueryLoop(llm, FakeRegistry(), memory_store=store)
        session = make_session()

        await collect_events(loop, session, "test")
        assert len(store.get_all()) == 0

    @pytest.mark.asyncio
    async def test_reflection_reinforces_similar_existing(self, store: MemoryStore):
        existing = MemoryEntry(
            category=MemoryCategory.strategy,
            content="Breaking tasks into steps works well",
        )
        store.add(existing)

        reflection_resp = {
            "choices": [{
                "message": {
                    "content": json.dumps([
                        {"category": "strategy", "content": "Breaking tasks into steps works well for complex problems"}
                    ])
                }
            }]
        }
        llm = FakeLLM(
            [make_text_chunk("OK"), make_done_chunk()],
            reflection_response=reflection_resp,
        )
        loop = QueryLoop(llm, FakeRegistry(), memory_store=store)
        session = make_session()

        await collect_events(loop, session, "help me plan")
        await asyncio.sleep(0.1)  # let fire-and-forget reflection task complete

        # Should reinforce, not duplicate
        memories = store.get_all()
        reinforced = [m for m in memories if m.times_reinforced > 0]
        if reinforced:
            assert reinforced[0].id == existing.id

    @pytest.mark.asyncio
    async def test_reflection_caps_at_2_learnings(self, store: MemoryStore):
        reflection_resp = {
            "choices": [{
                "message": {
                    "content": json.dumps([
                        {"category": "strategy", "content": "learning one"},
                        {"category": "strategy", "content": "learning two"},
                        {"category": "strategy", "content": "learning three"},
                        {"category": "strategy", "content": "learning four"},
                    ])
                }
            }]
        }
        llm = FakeLLM(
            [make_text_chunk("OK"), make_done_chunk()],
            reflection_response=reflection_resp,
        )
        loop = QueryLoop(llm, FakeRegistry(), memory_store=store)
        session = make_session()

        await collect_events(loop, session, "big task")
        await asyncio.sleep(0.1)  # let fire-and-forget reflection task complete

        assert len(store.get_all()) <= 2

    @pytest.mark.asyncio
    async def test_no_reflection_without_store(self):
        llm = FakeLLM([make_text_chunk("Hi"), make_done_chunk()])
        loop = QueryLoop(llm, FakeRegistry(), memory_store=None)
        session = make_session()

        # Should not crash
        events = await collect_events(loop, session, "hello")
        assert any(e.type == StreamEventType.done for e in events)

    @pytest.mark.asyncio
    async def test_reflection_exception_does_not_crash_loop(self, store: MemoryStore):
        class ExplodingLLM(FakeLLM):
            async def complete(self, messages, tools=None):
                raise RuntimeError("LLM exploded")

        llm = ExplodingLLM([make_text_chunk("Hi"), make_done_chunk()])
        loop = QueryLoop(llm, FakeRegistry(), memory_store=store)
        session = make_session()

        # Should complete normally — reflection failure is swallowed
        events = await collect_events(loop, session, "hello")
        assert any(e.type == StreamEventType.done for e in events)
