"""Core query loop -- plan -> route -> approve -> execute -> stream."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator, TYPE_CHECKING

from .models import (
    Message, Role, Session, StreamEvent, StreamEventType,
    ToolCall, ToolCallStatus, ToolResult, ApprovalRequest, RiskLevel,
    MemoryEntry, MemoryCategory,
)
from .llm import OpenRouterProvider, LLMConnectionError
from ..plugins.base import PluginRegistry

if TYPE_CHECKING:
    from .memory import MemoryStore

logger = logging.getLogger(__name__)


class QueryLoop:
    def __init__(self, llm: OpenRouterProvider, registry: PluginRegistry,
                 memory_store: MemoryStore | None = None):
        self.llm = llm
        self.registry = registry
        self.memory_store = memory_store
        self._pending_approvals: dict[str, asyncio.Future] = {}

    async def run(self, session: Session, user_message: str) -> AsyncIterator[StreamEvent]:
        """Run one turn of the agent loop. Yields StreamEvents."""
        session.messages.append(Message(role=Role.user, content=user_message))

        # Convert messages to OpenAI format
        messages = [{"role": m.role.value, "content": m.content} for m in session.messages]
        tools = self.registry.get_all_tool_schemas()

        # Inject relevant memories as a system message
        if self.memory_store:
            relevant = self.memory_store.get_relevant(user_message, limit=10)
            if relevant:
                memory_lines = [f"- {m.content}" for m in relevant]
                memory_msg = (
                    "You have learned the following from past interactions:\n"
                    + "\n".join(memory_lines)
                    + "\nApply these learnings where relevant."
                )
                messages.insert(0, {"role": "system", "content": memory_msg})

        yield StreamEvent(
            type=StreamEventType.message_start,
            data={"role": "assistant"},
            session_id=session.id,
        )

        # Stream LLM response
        full_content = ""
        tool_calls_raw: list[dict] = []

        try:
            async for chunk in self.llm.stream_complete(messages, tools or None):
                choices = chunk.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})

                # Text content
                if text := delta.get("content"):
                    full_content += text
                    yield StreamEvent(
                        type=StreamEventType.text_delta,
                        data={"text": text},
                        session_id=session.id,
                    )

                # Tool calls
                if tc_deltas := delta.get("tool_calls"):
                    for tc in tc_deltas:
                        idx = tc.get("index", 0)
                        while len(tool_calls_raw) <= idx:
                            tool_calls_raw.append({"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            tool_calls_raw[idx]["id"] = tc["id"]
                        if func := tc.get("function"):
                            if func.get("name"):
                                tool_calls_raw[idx]["name"] = func["name"]
                            if func.get("arguments"):
                                tool_calls_raw[idx]["arguments"] += func["arguments"]
        except LLMConnectionError as e:
            logger.warning("LLM connection error during streaming: %s", e)
            yield StreamEvent(
                type=StreamEventType.error,
                data={"message": str(e)},
                session_id=session.id,
            )
            yield StreamEvent(type=StreamEventType.done, data={}, session_id=session.id)
            return

        # Process tool calls if any
        if tool_calls_raw:
            for tc_raw in tool_calls_raw:
                try:
                    args = json.loads(tc_raw["arguments"]) if tc_raw["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}

                tool_call = ToolCall(
                    name=tc_raw["name"],
                    arguments=args,
                )

                yield StreamEvent(
                    type=StreamEventType.tool_call_start,
                    data={"tool_call": tool_call.model_dump(mode="json")},
                    session_id=session.id,
                )

                # Check if approval needed
                needs_approval = self.registry.requires_approval(tool_call.name)
                if needs_approval:
                    tool_call.status = ToolCallStatus.pending
                    risk = RiskLevel.high if tool_call.name == "run_command" else RiskLevel.medium
                    approval = ApprovalRequest(
                        tool_call=tool_call,
                        reason=f"Tool '{tool_call.name}' requires your approval",
                        risk_level=risk,
                    )
                    yield StreamEvent(
                        type=StreamEventType.approval_request,
                        data={"approval": approval.model_dump(mode="json")},
                        session_id=session.id,
                    )

                    # Wait for approval
                    future: asyncio.Future = asyncio.get_running_loop().create_future()
                    self._pending_approvals[str(tool_call.id)] = future
                    try:
                        approved = await asyncio.wait_for(future, timeout=300)
                    except asyncio.TimeoutError:
                        approved = False
                        yield StreamEvent(
                            type=StreamEventType.error,
                            data={"message": "Approval timed out after 5 minutes — tool call denied"},
                            session_id=session.id,
                        )
                    finally:
                        self._pending_approvals.pop(str(tool_call.id), None)

                    if not approved:
                        tool_call.status = ToolCallStatus.denied
                        # Record the denial as a learning
                        if self.memory_store:
                            args_summary = json.dumps(tool_call.arguments)[:100]
                            existing = self.memory_store.find_similar(
                                f"User denied {tool_call.name}"
                            )
                            if existing:
                                self.memory_store.reinforce(existing.id)
                            else:
                                self.memory_store.add(MemoryEntry(
                                    category=MemoryCategory.tool_preference,
                                    content=f"User denied '{tool_call.name}' with args: {args_summary}",
                                    source_session=session.id,
                                ))
                        result = ToolResult(
                            tool_call_id=tool_call.id,
                            output="Tool call was denied by user.",
                        )
                        yield StreamEvent(
                            type=StreamEventType.tool_result,
                            data={"result": result.model_dump(mode="json")},
                            session_id=session.id,
                        )
                        continue

                # Execute tool
                tool_call.status = ToolCallStatus.running
                try:
                    plugin, _ = self.registry.find_plugin_for_tool(tool_call.name)
                    start = datetime.now(timezone.utc)
                    output = await plugin.execute(tool_call.name, tool_call.arguments)
                    duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000

                    tool_call.status = ToolCallStatus.completed
                    result = ToolResult(
                        tool_call_id=tool_call.id,
                        output=output,
                        duration_ms=duration,
                    )
                except KeyError:
                    tool_call.status = ToolCallStatus.failed
                    result = ToolResult(
                        tool_call_id=tool_call.id,
                        output="",
                        error=f"Unknown tool: '{tool_call.name}'",
                    )
                except Exception as e:
                    tool_call.status = ToolCallStatus.failed
                    result = ToolResult(
                        tool_call_id=tool_call.id,
                        output="",
                        error=str(e),
                    )

                yield StreamEvent(
                    type=StreamEventType.tool_result,
                    data={"result": result.model_dump(mode="json")},
                    session_id=session.id,
                )

        # Save assistant message
        session.messages.append(Message(role=Role.assistant, content=full_content))

        yield StreamEvent(
            type=StreamEventType.done,
            data={},
            session_id=session.id,
        )

        # Post-turn reflection — extract learnings asynchronously
        if self.memory_store:
            try:
                await self._reflect(session)
            except Exception:
                logger.debug("Reflection step failed", exc_info=True)

    async def _reflect(self, session: Session) -> None:
        """Ask the LLM to extract learnings from the last turn."""
        last_messages = session.messages[-4:]
        if len(last_messages) < 2:
            return

        reflection_prompt = [
            {"role": "system", "content": (
                "You are a reflection agent. Review the conversation below and extract "
                "0-2 brief, actionable learnings. Categories: tool_preference, strategy, "
                "user_correction, tool_failure. Return a JSON array of objects with "
                "\"category\" and \"content\" keys, or an empty array if nothing notable. "
                "Be selective — only real insights. Return ONLY the JSON array, no other text."
            )},
            *[{"role": m.role.value, "content": m.content} for m in last_messages if m.content],
        ]

        try:
            resp = await self.llm.complete(reflection_prompt)
            choices = resp.get("choices", [])
            if not choices:
                return
            text = choices[0].get("message", {}).get("content", "").strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            entries = json.loads(text)
            if not isinstance(entries, list):
                return
            for item in entries[:2]:
                category = item.get("category", "")
                content = item.get("content", "")
                if not content or category not in [c.value for c in MemoryCategory]:
                    continue
                existing = self.memory_store.find_similar(content)
                if existing:
                    self.memory_store.reinforce(existing.id)
                else:
                    self.memory_store.add(MemoryEntry(
                        category=MemoryCategory(category),
                        content=content,
                        source_session=session.id,
                    ))
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    def resolve_approval(self, tool_call_id: str, approved: bool) -> None:
        """Resolve a pending approval request."""
        if future := self._pending_approvals.get(tool_call_id):
            if not future.done():
                future.set_result(approved)
