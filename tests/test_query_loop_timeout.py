"""Tests for tool execution timeout and tool_call_status event emission."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from neuromancy.core.models import (
    Message, Role, Session, StreamEvent, StreamEventType,
)
from neuromancy.core.query_loop import QueryLoop


# ---------------------------------------------------------------------------
# Helpers (mirrors test_query_loop_memory.py conventions)
# ---------------------------------------------------------------------------

def make_session() -> Session:
    return Session(id="test-session", model="test-model")


def make_text_chunk(text: str) -> dict:
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
    def __init__(self, chunks: list[dict]):
        self._chunks = chunks
        self.last_usage: dict | None = None
        self.model: str = "anthropic/claude-haiku-4"

    async def stream_complete(self, messages, tools=None):
        self.last_usage = None
        for chunk in self._chunks:
            yield chunk

    async def complete(self, messages, tools=None):
        self.last_usage = None
        return {"choices": [{"message": {"content": "[]"}}]}


class FakeRegistry:
    def __init__(self, plugin=None):
        self._plugin = plugin

    def get_all_tool_schemas(self):
        return []

    def requires_approval(self, name: str) -> bool:
        return False

    def find_plugin_for_tool(self, name: str):
        if self._plugin:
            return self._plugin, None
        plugin = AsyncMock()
        plugin.execute = AsyncMock(return_value="tool output")
        return plugin, None


async def collect_events(loop: QueryLoop, session: Session, msg: str) -> list[StreamEvent]:
    events = []
    async for event in loop.run(session, msg):
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestToolCallStatusEvent:
    """tool_call_status event is emitted before tool execution."""

    @pytest.mark.asyncio
    async def test_status_event_emitted_before_result(self):
        chunks = [
            make_tool_call_chunk("web_search", '{"query": "hello"}'),
            make_done_chunk(),
        ]
        llm = FakeLLM(chunks)
        loop = QueryLoop(llm, FakeRegistry(), memory_store=None)
        session = make_session()

        events = await collect_events(loop, session, "search for hello")
        types = [e.type for e in events]

        assert StreamEventType.tool_call_status in types
        assert StreamEventType.tool_result in types

        status_idx = types.index(StreamEventType.tool_call_status)
        result_idx = types.index(StreamEventType.tool_result)
        assert status_idx < result_idx

    @pytest.mark.asyncio
    async def test_status_event_contains_correct_data(self):
        chunks = [
            make_tool_call_chunk("web_search", '{"query": "test"}'),
            make_done_chunk(),
        ]
        llm = FakeLLM(chunks)
        loop = QueryLoop(llm, FakeRegistry(), memory_store=None)
        session = make_session()

        events = await collect_events(loop, session, "search")
        status_events = [e for e in events if e.type == StreamEventType.tool_call_status]

        assert len(status_events) == 1
        data = status_events[0].data
        assert data["status"] == "running"
        assert data["tool_name"] == "web_search"
        assert "started_at" in data
        assert "tool_call_id" in data


class TestToolTimeout:
    """Tool execution respects the TOOL_TIMEOUT cap."""

    @pytest.mark.asyncio
    async def test_tool_times_out(self):
        """A tool that takes longer than TOOL_TIMEOUT fails with a timeout error."""

        async def slow_execute(name, args):
            await asyncio.sleep(10)  # much longer than our test timeout
            return "should not reach here"

        plugin = AsyncMock()
        plugin.execute = slow_execute
        registry = FakeRegistry(plugin=plugin)

        chunks = [
            make_tool_call_chunk("slow_tool", "{}"),
            make_done_chunk(),
        ]
        llm = FakeLLM(chunks)
        loop = QueryLoop(llm, registry, memory_store=None)
        loop.TOOL_TIMEOUT = 0.1  # 100ms for fast test

        session = make_session()
        events = await collect_events(loop, session, "run slow tool")

        result_events = [e for e in events if e.type == StreamEventType.tool_result]
        assert len(result_events) == 1

        result_data = result_events[0].data["result"]
        assert result_data["error"] is not None
        assert "timed out" in result_data["error"]
        assert "slow_tool" in result_data["error"]

    @pytest.mark.asyncio
    async def test_fast_tool_completes_normally(self):
        """A tool that finishes within TOOL_TIMEOUT succeeds."""
        chunks = [
            make_tool_call_chunk("fast_tool", "{}"),
            make_done_chunk(),
        ]
        llm = FakeLLM(chunks)
        loop = QueryLoop(llm, FakeRegistry(), memory_store=None)
        loop.TOOL_TIMEOUT = 5  # generous timeout

        session = make_session()
        events = await collect_events(loop, session, "run fast tool")

        result_events = [e for e in events if e.type == StreamEventType.tool_result]
        assert len(result_events) == 1

        result_data = result_events[0].data["result"]
        assert result_data["error"] is None
        assert result_data["output"] == "tool output"
        assert result_data["duration_ms"] is not None

    @pytest.mark.asyncio
    async def test_timeout_error_message_includes_limit(self):
        """The timeout error message includes the configured timeout value."""

        async def slow_execute(name, args):
            await asyncio.sleep(10)
            return ""

        plugin = AsyncMock()
        plugin.execute = slow_execute
        registry = FakeRegistry(plugin=plugin)

        chunks = [
            make_tool_call_chunk("my_tool", "{}"),
            make_done_chunk(),
        ]
        llm = FakeLLM(chunks)
        loop = QueryLoop(llm, registry, memory_store=None)
        loop.TOOL_TIMEOUT = 0.05

        session = make_session()
        events = await collect_events(loop, session, "go")

        result_events = [e for e in events if e.type == StreamEventType.tool_result]
        error_msg = result_events[0].data["result"]["error"]
        assert "0.05" in error_msg
