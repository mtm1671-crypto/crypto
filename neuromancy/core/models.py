"""Pydantic v2 data models for the Neuromancy streaming AI agent system."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Role(str, Enum):
    user = "user"
    assistant = "assistant"
    system = "system"
    tool = "tool"


class ToolCallStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    denied = "denied"
    running = "running"
    completed = "completed"
    failed = "failed"


class StreamEventType(str, Enum):
    message_start = "message_start"
    text_delta = "text_delta"
    tool_call_start = "tool_call_start"
    tool_call_delta = "tool_call_delta"
    tool_result = "tool_result"
    approval_request = "approval_request"
    error = "error"
    done = "done"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class MemoryCategory(str, Enum):
    tool_preference = "tool_preference"
    strategy = "strategy"
    user_correction = "user_correction"
    tool_failure = "tool_failure"


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------

class ToolCall(BaseModel):
    model_config = {"frozen": False, "populate_by_name": True}

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    arguments: dict = Field(default_factory=dict)
    status: ToolCallStatus = ToolCallStatus.pending


class ToolResult(BaseModel):
    model_config = {"frozen": False, "populate_by_name": True}

    tool_call_id: str
    output: str
    error: Optional[str] = None
    duration_ms: Optional[float] = None


class Message(BaseModel):
    model_config = {"frozen": False, "populate_by_name": True}

    role: Role
    content: str
    tool_calls: Optional[list[ToolCall]] = None
    tool_call_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=_utcnow)


class StreamEvent(BaseModel):
    model_config = {"frozen": False, "populate_by_name": True}

    type: StreamEventType
    data: dict = Field(default_factory=dict)
    session_id: str
    timestamp: datetime = Field(default_factory=_utcnow)

    def to_ndjson(self) -> str:
        """Serialize to a newline-delimited JSON string."""
        return self.model_dump_json() + "\n"


class ApprovalRequest(BaseModel):
    model_config = {"frozen": False, "populate_by_name": True}

    tool_call: ToolCall
    reason: str
    risk_level: RiskLevel = RiskLevel.low


class MemoryEntry(BaseModel):
    model_config = {"frozen": False, "populate_by_name": True}

    id: str = Field(default_factory=lambda: str(uuid4()))
    category: MemoryCategory
    content: str
    source_session: Optional[str] = None
    confidence: float = 0.5
    times_reinforced: int = 0
    created_at: datetime = Field(default_factory=_utcnow)
    last_used: Optional[datetime] = None


class Session(BaseModel):
    model_config = {"frozen": False, "populate_by_name": True}

    id: str = Field(default_factory=lambda: str(uuid4()))
    messages: list[Message] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    model: str
    title: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _set_updated_at(cls, values: dict) -> dict:
        """Ensure updated_at defaults to created_at when not explicitly set."""
        if isinstance(values, dict):
            if "updated_at" not in values and "created_at" in values:
                values["updated_at"] = values["created_at"]
        return values
