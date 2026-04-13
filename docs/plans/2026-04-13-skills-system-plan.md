# Skills System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship the v1 skills system for neuromancy — procedure templates with conditional branching, user-reviewed creation from reflection, test capture per run, and compile scaffolding for future evolution to Python. Also migrate `MemoryStore` from JSON to SQLite+FTS5 as a prerequisite.

**Architecture:** Skills are directories under `~/.neuromancy/skills/active/` containing `manifest.json` + `template.yaml` + `tests/` + `versions/`. Templates support sequential `tool_call`/`llm_call` steps plus `when`/`then`/`else` blocks and `terminate_if` early-exit. A `SkillRunner` executes them, captures test recordings, and updates stats. A `run_skill` meta-tool exposes skills to the LLM via a lightweight catalog in the system prompt (cached separately from preamble and memory). Post-turn reflection drafts candidates into `skills/pending/` for mandatory user review before promotion.

**Tech Stack:** Python 3.11+, Pydantic v2, PyYAML, jsonschema, aiosqlite (with FTS5), FastAPI, pytest + pytest-asyncio. Frontend: Next.js + React + TypeScript.

**Reference design:** `docs/plans/2026-04-13-tier1-architecture-design.md` is the authoritative design doc. When a task says "per the design," read that doc's relevant section. Do NOT re-derive architectural decisions; they are load-bearing.

---

## Prerequisites

Before starting Phase 1, do these once:

### Prework Task A: Verify dependencies

Check `pyproject.toml` contains:
- `pyyaml>=6.0`
- `jsonschema>=4.0`
- `aiosqlite>=0.20.0` (already present per existing `pyproject.toml`)
- `pytest` and `pytest-asyncio` in `[project.optional-dependencies].dev`

If `pyyaml` or `jsonschema` are missing, add them:

```toml
dependencies = [
    ...existing...,
    "pyyaml>=6.0",
    "jsonschema>=4.0",
]
```

Run: `pip install -e ".[dev]"`

Expected: clean install, no conflicts.

### Prework Task B: Create a feature branch

```bash
cd ~/Documents/GitHub/neuromancy
git checkout -b feat/skills-system
```

### Prework Task C: Baseline test run

Run: `pytest tests/ -q`

Expected: record the current pass/fail count as the baseline. Any new test failures during implementation should be attributable to the current phase's work, not preexisting breakage.

---

## Phase 1 — Plugin base prep

**Goal:** Add `PluginRegistry.filtered(allowlist)` so skills (and future cron jobs) can scope tool access. ~30 minutes.

**Files:**
- Modify: `neuromancy/plugins/base.py`
- Modify or create: `tests/test_plugin_registry.py`

### Task 1.1: Write failing tests for `filtered()`

**Step 1:** Open or create `tests/test_plugin_registry.py`. Add:

```python
import pytest
from neuromancy.plugins.base import BasePlugin, PluginRegistry, PluginManifest, ToolSchema


class _FakePlugin(BasePlugin):
    def __init__(self, name: str, tool_names: list[str]):
        self._name = name
        self._tool_names = tool_names

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            name=self._name,
            version="1.0",
            description="fake",
            tools=[ToolSchema(name=t, description="", parameters={}) for t in self._tool_names],
        )

    async def execute(self, tool_name: str, arguments: dict) -> str:
        return f"{self._name}:{tool_name}"


def test_filtered_allows_listed_tools():
    r = PluginRegistry()
    r.register(_FakePlugin("A", ["tool_a1", "tool_a2"]))
    r.register(_FakePlugin("B", ["tool_b1"]))

    restricted = r.filtered({"tool_a1", "tool_b1"})
    schemas = restricted.get_all_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {"tool_a1", "tool_b1"}


def test_filtered_none_allowlist_returns_full_registry():
    r = PluginRegistry()
    r.register(_FakePlugin("A", ["tool_a1"]))
    restricted = r.filtered(None)
    assert len(restricted.get_all_tool_schemas()) == 1


def test_filtered_lookup_raises_for_excluded_tool():
    r = PluginRegistry()
    r.register(_FakePlugin("A", ["tool_a1", "tool_a2"]))
    restricted = r.filtered({"tool_a1"})
    with pytest.raises(KeyError):
        restricted.find_plugin_for_tool("tool_a2")
```

**Step 2:** Run: `pytest tests/test_plugin_registry.py -v`
Expected: FAIL — `PluginRegistry` has no `filtered` method.

### Task 1.2: Implement `filtered()`

**Step 3:** Open `neuromancy/plugins/base.py`. Add to `PluginRegistry`:

```python
def filtered(self, allowlist: set[str] | None) -> "PluginRegistry":
    """Return a new registry containing only tools in the allowlist.

    Plugins whose tools are all excluded are not registered. A None allowlist
    returns a new registry with all plugins (useful for 'no restriction').
    """
    new = PluginRegistry()
    for plugin in self._plugins.values():
        if allowlist is None:
            new.register(plugin)
            continue
        allowed_tools = [t for t in plugin.manifest.tools if t.name in allowlist]
        if not allowed_tools:
            continue
        # Register a view of the plugin whose manifest lists only allowed tools.
        new.register(_PluginToolView(plugin, allowed_tools))
    return new
```

Add the helper class above `PluginRegistry`:

```python
class _PluginToolView(BasePlugin):
    """Wraps an existing plugin but exposes a subset of its tools."""

    def __init__(self, wrapped: BasePlugin, tools: list[ToolSchema]):
        self._wrapped = wrapped
        self._tools = tools
        self._manifest = PluginManifest(
            name=wrapped.manifest.name,
            version=wrapped.manifest.version,
            description=wrapped.manifest.description,
            tools=tools,
            credentials_required=wrapped.manifest.credentials_required,
        )

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    async def execute(self, tool_name: str, arguments: dict) -> str:
        allowed_names = {t.name for t in self._tools}
        if tool_name not in allowed_names:
            raise KeyError(f"Tool '{tool_name}' is not in the restricted allowlist")
        return await self._wrapped.execute(tool_name, arguments)
```

**Step 4:** Run: `pytest tests/test_plugin_registry.py -v`
Expected: PASS on all three tests.

**Step 5:** Run the full plugin test suite to make sure no regressions:
`pytest tests/ -k plugin -v`
Expected: all existing plugin tests still pass.

### Task 1.3: Commit Phase 1

```bash
git add neuromancy/plugins/base.py tests/test_plugin_registry.py
git commit -m "feat(plugins): add PluginRegistry.filtered() for scoped tool access"
```

---

## Phase 2 — Memory SQLite+FTS5 migration

**Goal:** Replace `memory.json` word-overlap store with SQLite + FTS5 while preserving the `MemoryStore` public API. ~5 hours.

**Files:**
- Modify: `neuromancy/core/memory.py` (rewrite internals)
- Create: `tests/test_memory_sqlite.py`
- Modify: `tests/test_query_loop_memory.py` if needed for compat

### Task 2.1: Write failing test for schema creation

**Step 1:** Create `tests/test_memory_sqlite.py`:

```python
import pytest
from pathlib import Path
from neuromancy.core.memory import MemoryStore
from neuromancy.core.models import MemoryEntry, MemoryCategory


@pytest.fixture
def temp_db(tmp_path):
    return tmp_path / "memory.db"


def test_new_db_creates_schema(temp_db):
    store = MemoryStore(path=temp_db)
    # Opening the store should create the db file with tables
    assert temp_db.exists()
    # Should be able to query without error
    assert store.get_all() == []
```

**Step 2:** Run: `pytest tests/test_memory_sqlite.py::test_new_db_creates_schema -v`
Expected: FAIL — current `MemoryStore` uses JSON, `temp_db` won't be created as SQLite.

### Task 2.2: Rewrite `MemoryStore` with SQLite schema

**Step 3:** Replace `neuromancy/core/memory.py` contents:

```python
"""SQLite-backed memory store with FTS5 for relevance search."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import MemoryEntry, MemoryCategory

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    source_session TEXT,
    confidence REAL NOT NULL DEFAULT 0.5,
    times_reinforced INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_used TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.rowid, old.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.rowid, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;
"""


class MemoryStore:
    """Persists MemoryEntry objects to a SQLite database with FTS5 indexing."""

    MIN_RELEVANCE_SCORE = 0.3

    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / ".neuromancy" / "memory.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._maybe_migrate_from_json()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _maybe_migrate_from_json(self) -> None:
        legacy = self.path.parent / "memory.json"
        if not legacy.exists():
            return
        try:
            with self._connect() as conn:
                cur = conn.execute("SELECT COUNT(*) FROM memories")
                if cur.fetchone()[0] > 0:
                    return  # Already migrated
            raw = json.loads(legacy.read_text(encoding="utf-8"))
            for e in raw:
                entry = MemoryEntry.model_validate(e)
                self.add(entry)
            legacy.rename(legacy.with_suffix(".json.pre-sqlite.bak"))
            logger.info("Migrated %d memories from memory.json to SQLite", len(raw))
        except Exception:
            logger.exception("Failed to migrate memory.json; leaving it in place")

    def add(self, entry: MemoryEntry) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memories
                (id, category, content, source_session, confidence, times_reinforced, created_at, last_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.category.value,
                    entry.content,
                    entry.source_session,
                    entry.confidence,
                    entry.times_reinforced,
                    entry.created_at.isoformat(),
                    entry.last_used.isoformat() if entry.last_used else None,
                ),
            )

    def reinforce(self, entry_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memories
                SET confidence = MIN(1.0, confidence + 0.1),
                    times_reinforced = times_reinforced + 1
                WHERE id = ?
                """,
                (entry_id,),
            )

    def get_relevant(self, context: str, limit: int = 10) -> list[MemoryEntry]:
        query = self._fts_query_from_context(context)
        if not query:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT m.*, bm25(memories_fts) AS rank_score
                FROM memories m
                JOIN memories_fts ON memories_fts.rowid = m.rowid
                WHERE memories_fts MATCH ?
                ORDER BY rank_score * m.confidence ASC
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
            results = [self._row_to_entry(r) for r in rows]
            # Update last_used for returned entries
            now_iso = datetime.now(timezone.utc).isoformat()
            ids = [r["id"] for r in rows]
            if ids:
                placeholders = ",".join("?" * len(ids))
                conn.execute(
                    f"UPDATE memories SET last_used = ? WHERE id IN ({placeholders})",
                    (now_iso, *ids),
                )
            return results

    def find_similar(self, text: str) -> Optional[MemoryEntry]:
        query = self._fts_query_from_context(text)
        if not query:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT m.*
                FROM memories m
                JOIN memories_fts ON memories_fts.rowid = m.rowid
                WHERE memories_fts MATCH ?
                ORDER BY bm25(memories_fts) ASC
                LIMIT 1
                """,
                (query,),
            ).fetchone()
            return self._row_to_entry(row) if row else None

    def get_all(self) -> list[MemoryEntry]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM memories ORDER BY created_at DESC").fetchall()
            return [self._row_to_entry(r) for r in rows]

    def remove(self, entry_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (entry_id,))

    def _row_to_entry(self, row) -> MemoryEntry:
        return MemoryEntry(
            id=row["id"],
            category=MemoryCategory(row["category"]),
            content=row["content"],
            source_session=row["source_session"],
            confidence=row["confidence"],
            times_reinforced=row["times_reinforced"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_used=datetime.fromisoformat(row["last_used"]) if row["last_used"] else None,
        )

    @staticmethod
    def _fts_query_from_context(text: str) -> str:
        """Build a safe FTS5 query from arbitrary text.

        Strip operators, split into words, quote each one to force literal match.
        Return empty string if no usable tokens remain.
        """
        import re
        tokens = re.findall(r"\w+", text.lower())
        tokens = [t for t in tokens if len(t) >= 3]
        if not tokens:
            return ""
        return " OR ".join(f'"{t}"' for t in tokens[:20])  # cap at 20 tokens
```

**Step 4:** Run: `pytest tests/test_memory_sqlite.py::test_new_db_creates_schema -v`
Expected: PASS.

### Task 2.3: Test CRUD round-trip

**Step 1:** Add to `tests/test_memory_sqlite.py`:

```python
def test_add_and_get_all(temp_db):
    store = MemoryStore(path=temp_db)
    e = MemoryEntry(category=MemoryCategory.strategy, content="prefer fetch_page for static sites")
    store.add(e)
    all_entries = store.get_all()
    assert len(all_entries) == 1
    assert all_entries[0].content == "prefer fetch_page for static sites"
    assert all_entries[0].category == MemoryCategory.strategy


def test_remove(temp_db):
    store = MemoryStore(path=temp_db)
    e = MemoryEntry(category=MemoryCategory.strategy, content="x")
    store.add(e)
    store.remove(e.id)
    assert store.get_all() == []


def test_reinforce_increments_confidence(temp_db):
    store = MemoryStore(path=temp_db)
    e = MemoryEntry(category=MemoryCategory.strategy, content="y", confidence=0.5, times_reinforced=0)
    store.add(e)
    store.reinforce(e.id)
    store.reinforce(e.id)
    refreshed = store.get_all()[0]
    assert refreshed.confidence == pytest.approx(0.7)
    assert refreshed.times_reinforced == 2
```

**Step 2:** Run: `pytest tests/test_memory_sqlite.py -v`
Expected: PASS on all three.

### Task 2.4: Test FTS5 relevance search

**Step 1:** Add:

```python
def test_get_relevant_matches_content(temp_db):
    store = MemoryStore(path=temp_db)
    store.add(MemoryEntry(category=MemoryCategory.strategy, content="The user prefers python for scripting"))
    store.add(MemoryEntry(category=MemoryCategory.strategy, content="Redis caches work best for session data"))
    store.add(MemoryEntry(category=MemoryCategory.strategy, content="Bash scripts break on Windows paths"))

    results = store.get_relevant("writing a python script")
    contents = [r.content for r in results]
    assert any("python" in c for c in contents)


def test_get_relevant_empty_query_returns_empty(temp_db):
    store = MemoryStore(path=temp_db)
    store.add(MemoryEntry(category=MemoryCategory.strategy, content="whatever"))
    assert store.get_relevant("") == []


def test_find_similar_returns_best_match(temp_db):
    store = MemoryStore(path=temp_db)
    store.add(MemoryEntry(category=MemoryCategory.strategy, content="user denied run_command with rm -rf"))
    store.add(MemoryEntry(category=MemoryCategory.strategy, content="user prefers markdown formatting"))

    match = store.find_similar("user denied run_command")
    assert match is not None
    assert "denied" in match.content
```

**Step 2:** Run: `pytest tests/test_memory_sqlite.py -v`
Expected: PASS on all.

### Task 2.5: Test JSON → SQLite migration

**Step 1:** Add:

```python
import json as _json

def test_migrates_from_json(tmp_path):
    db = tmp_path / "memory.db"
    legacy = tmp_path / "memory.json"
    legacy.write_text(_json.dumps([
        {
            "id": "abc",
            "category": "strategy",
            "content": "imported from json",
            "source_session": None,
            "confidence": 0.7,
            "times_reinforced": 2,
            "created_at": "2026-04-01T00:00:00+00:00",
            "last_used": None,
        }
    ]))
    store = MemoryStore(path=db)
    entries = store.get_all()
    assert len(entries) == 1
    assert entries[0].content == "imported from json"
    assert entries[0].confidence == 0.7
    # Legacy file should be renamed
    assert not legacy.exists()
    assert (tmp_path / "memory.json.pre-sqlite.bak").exists()
```

**Step 2:** Run: `pytest tests/test_memory_sqlite.py -v`
Expected: PASS.

### Task 2.6: Run existing memory tests for compat

**Step 1:** Run: `pytest tests/test_query_loop_memory.py -v` (or whatever test file references the old memory store)
Expected: PASS, or fix any tests that hardcode the JSON path. Specifically: if tests write `memory.json` directly, they now need to create the store via the class.

### Task 2.7: Commit Phase 2

```bash
git add neuromancy/core/memory.py tests/test_memory_sqlite.py tests/test_query_loop_memory.py
git commit -m "feat(memory): migrate MemoryStore to SQLite with FTS5 indexing

Replaces the JSON file store with a SQLite database using FTS5 virtual
tables for relevance search. Legacy memory.json files are automatically
migrated on first startup and backed up as memory.json.pre-sqlite.bak.
Public API of MemoryStore is unchanged."
```

---

## Phase 3 — Skill data models

**Goal:** Pydantic models for skills, their templates, and run results. ~3 hours.

**Files:**
- Create: `neuromancy/core/skill_models.py`
- Create: `tests/test_skill_models.py`

### Task 3.1: Write failing tests for manifest loading

**Step 1:** Create `tests/test_skill_models.py`:

```python
import json
import pytest
from pathlib import Path
from neuromancy.core.skill_models import (
    SkillManifest,
    SkillTemplate,
    SkillStep,
    SkillWhenBlock,
    StepAction,
)


def test_load_minimal_manifest(tmp_path):
    data = {
        "name": "hello",
        "version": 1,
        "description": "say hi",
        "created_at": "2026-04-13T00:00:00+00:00",
        "updated_at": "2026-04-13T00:00:00+00:00",
        "input_schema": {"type": "object", "properties": {}},
        "run_count": 0,
        "success_rate": 0.0,
        "has_compiled": False,
        "tags": [],
    }
    manifest = SkillManifest.model_validate(data)
    assert manifest.name == "hello"
    assert manifest.version == 1


def test_template_parses_sequential_steps():
    yaml_text = """
name: hello
version: 1
description: say hi
inputs:
  who:
    type: string
    required: true
steps:
  - id: greet
    action: llm_call
    model: anthropic/claude-haiku-4
    prompt: "Say hi to {{ inputs.who }}"
output: "{{ steps.greet.output }}"
"""
    template = SkillTemplate.from_yaml(yaml_text)
    assert template.name == "hello"
    assert len(template.steps) == 1
    assert template.steps[0].action == StepAction.llm_call
    assert template.output == "{{ steps.greet.output }}"


def test_template_parses_when_block():
    yaml_text = """
name: branching
version: 1
description: demo when block
inputs: {}
steps:
  - id: step_a
    action: tool_call
    tool: web_search
    arguments: {query: "x"}
  - when: "steps.step_a.output.results.length == 0"
    then:
      - id: fallback
        action: llm_call
        prompt: "fallback"
    else:
      - id: happy
        action: llm_call
        prompt: "happy"
output: "{{ steps.happy.output }}"
"""
    template = SkillTemplate.from_yaml(yaml_text)
    assert len(template.steps) == 2
    assert isinstance(template.steps[1], SkillWhenBlock)
    assert template.steps[1].condition == "steps.step_a.output.results.length == 0"
    assert len(template.steps[1].then_branch) == 1
    assert len(template.steps[1].else_branch) == 1


def test_template_rejects_unknown_action():
    yaml_text = """
name: bad
version: 1
description: bad
inputs: {}
steps:
  - id: step
    action: teleport
output: ""
"""
    with pytest.raises(ValueError):
        SkillTemplate.from_yaml(yaml_text)
```

**Step 2:** Run: `pytest tests/test_skill_models.py -v`
Expected: FAIL — module doesn't exist.

### Task 3.2: Implement skill data models

**Step 3:** Create `neuromancy/core/skill_models.py`:

```python
"""Pydantic data models for skills."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Union

import yaml
from pydantic import BaseModel, Field, model_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StepAction(str, Enum):
    tool_call = "tool_call"
    llm_call = "llm_call"


class SkillStep(BaseModel):
    """A regular step: either a tool call or an LLM call."""

    id: str
    action: StepAction
    terminate_if: Optional[str] = None

    # tool_call fields
    tool: Optional[str] = None
    arguments: dict = Field(default_factory=dict)

    # llm_call fields
    model: Optional[str] = None
    prompt: Optional[str] = None

    @model_validator(mode="after")
    def _validate_action_fields(self) -> "SkillStep":
        if self.action == StepAction.tool_call and not self.tool:
            raise ValueError(f"step {self.id}: tool_call requires a 'tool' field")
        if self.action == StepAction.llm_call and not self.prompt:
            raise ValueError(f"step {self.id}: llm_call requires a 'prompt' field")
        return self


class SkillWhenBlock(BaseModel):
    """Conditional step block: when/then/else."""

    condition: str
    then_branch: list["SkillStepUnion"] = Field(default_factory=list)
    else_branch: list["SkillStepUnion"] = Field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "SkillWhenBlock":
        if "when" not in data:
            raise ValueError("when block must have a 'when' key")
        return cls(
            condition=data["when"],
            then_branch=[_parse_step(s) for s in data.get("then", [])],
            else_branch=[_parse_step(s) for s in data.get("else", [])],
        )


SkillStepUnion = Union[SkillStep, SkillWhenBlock]
SkillWhenBlock.model_rebuild()


def _parse_step(raw: dict) -> SkillStepUnion:
    if "when" in raw:
        return SkillWhenBlock.from_dict(raw)
    # Validate action up front to produce a clear error before Pydantic
    action_str = raw.get("action")
    if action_str not in {a.value for a in StepAction}:
        raise ValueError(f"unknown step action: {action_str!r}")
    return SkillStep.model_validate(raw)


class SkillInputSpec(BaseModel):
    type: str
    description: Optional[str] = None
    required: bool = False
    enum: Optional[list[Any]] = None
    default: Optional[Any] = None


class SkillTemplate(BaseModel):
    name: str
    version: int = 1
    description: str
    inputs: dict[str, SkillInputSpec] = Field(default_factory=dict)
    steps: list[SkillStepUnion] = Field(default_factory=list)
    output: str = ""

    @classmethod
    def from_yaml(cls, yaml_text: str) -> "SkillTemplate":
        raw = yaml.safe_load(yaml_text)
        if not isinstance(raw, dict):
            raise ValueError("template root must be a mapping")
        steps = [_parse_step(s) for s in raw.get("steps", [])]
        inputs_raw = raw.get("inputs") or {}
        return cls(
            name=raw["name"],
            version=raw.get("version", 1),
            description=raw.get("description", ""),
            inputs={k: SkillInputSpec.model_validate(v) for k, v in inputs_raw.items()},
            steps=steps,
            output=raw.get("output", ""),
        )


class SkillManifest(BaseModel):
    name: str
    version: int
    description: str
    created_at: datetime
    updated_at: datetime
    source_session: Optional[str] = None
    input_schema: dict = Field(default_factory=dict)
    run_count: int = 0
    success_rate: float = 0.0
    has_compiled: bool = False
    trust_this_skill: bool = False
    tags: list[str] = Field(default_factory=list)


class SkillStepResult(BaseModel):
    step_id: str
    output: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    tokens_used: int = 0


class SkillRunResult(BaseModel):
    skill_name: str
    skill_version: int
    inputs: dict
    steps: list[SkillStepResult] = Field(default_factory=list)
    branch_path: list[str] = Field(default_factory=list)
    final_output: str = ""
    succeeded: bool = False
    error: Optional[str] = None
    duration_ms: float = 0.0
    cost_usd: float = 0.0
    started_at: datetime = Field(default_factory=_utcnow)


class SkillTestCase(BaseModel):
    """A captured execution used for empirical grounding of future refinements."""

    timestamp: datetime = Field(default_factory=_utcnow)
    inputs: dict
    branch_path: list[str]
    final_output: str
    succeeded: bool
    duration_ms: float
    cost_usd: float
```

**Step 4:** Run: `pytest tests/test_skill_models.py -v`
Expected: PASS on all four tests.

### Task 3.3: Commit Phase 3

```bash
git add neuromancy/core/skill_models.py tests/test_skill_models.py
git commit -m "feat(skills): add Pydantic data models for skill manifests, templates, and runs"
```

---

## Phase 4 — Condition DSL

**Goal:** A strict recursive-descent parser and evaluator for `when`/`terminate_if` conditions. ~4 hours.

**Files:**
- Create: `neuromancy/core/skill_condition.py`
- Create: `tests/test_skill_condition.py`

### Task 4.1: Write failing parser tests

**Step 1:** Create `tests/test_skill_condition.py`:

```python
import pytest
from neuromancy.core.skill_condition import evaluate, parse, ConditionError


@pytest.fixture
def ctx():
    return {
        "inputs": {"topic": "python", "depth": "deep"},
        "steps": {
            "search": {"output": {"results": [1, 2, 3]}, "error": None},
            "empty": {"output": {"results": []}, "error": None},
            "failed": {"output": None, "error": "timeout"},
        },
    }


def test_literal_comparison(ctx):
    assert evaluate('inputs.topic == "python"', ctx) is True
    assert evaluate('inputs.topic == "ruby"', ctx) is False


def test_length_helper(ctx):
    assert evaluate("steps.search.output.results.length == 3", ctx) is True
    assert evaluate("steps.empty.output.results.length == 0", ctx) is True


def test_boolean_and(ctx):
    assert evaluate('inputs.topic == "python" and inputs.depth == "deep"', ctx) is True
    assert evaluate('inputs.topic == "python" and inputs.depth == "shallow"', ctx) is False


def test_boolean_or(ctx):
    assert evaluate('inputs.topic == "ruby" or inputs.depth == "deep"', ctx) is True


def test_not(ctx):
    assert evaluate("not (steps.search.output.results.length == 0)", ctx) is True


def test_null_comparison(ctx):
    assert evaluate("steps.failed.error != null", ctx) is True
    assert evaluate("steps.search.error == null", ctx) is True


def test_missing_reference_raises(ctx):
    with pytest.raises(ConditionError):
        evaluate("steps.nonexistent.output == 1", ctx)


def test_unsupported_syntax_raises():
    with pytest.raises(ConditionError):
        parse("1 + 1 == 2")  # arithmetic not allowed


def test_numeric_comparison(ctx):
    assert evaluate("steps.search.output.results.length > 1", ctx) is True
    assert evaluate("steps.search.output.results.length <= 3", ctx) is True
```

**Step 2:** Run: `pytest tests/test_skill_condition.py -v`
Expected: FAIL — module missing.

### Task 4.2: Implement parser + evaluator

**Step 3:** Create `neuromancy/core/skill_condition.py`. This is the longest file in the phase.

The grammar (EBNF-ish):

```
expr       := or_expr
or_expr    := and_expr ("or" and_expr)*
and_expr   := not_expr ("and" not_expr)*
not_expr   := "not" not_expr | comparison
comparison := primary (COMP_OP primary)?
primary    := "(" expr ")" | literal | reference
literal    := STRING | NUMBER | "true" | "false" | "null"
reference  := IDENT ("." IDENT)* (".length")?
COMP_OP    := "==" | "!=" | "<" | "<=" | ">" | ">="
```

Implementation outline (write the full module):

```python
"""Tiny condition DSL for skill templates.

Grammar supports literals, dotted references against a context dict,
comparisons, boolean and/or/not, and a .length helper on strings and
arrays. No arithmetic, no method calls, no string operations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Union


class ConditionError(Exception):
    """Raised when a condition is malformed or cannot be evaluated."""


# ---- AST node types ----

@dataclass
class Literal:
    value: Any

@dataclass
class Reference:
    path: list[str]
    length: bool = False

@dataclass
class Comparison:
    op: str
    lhs: "Node"
    rhs: "Node"

@dataclass
class And:
    lhs: "Node"
    rhs: "Node"

@dataclass
class Or:
    lhs: "Node"
    rhs: "Node"

@dataclass
class Not:
    operand: "Node"


Node = Union[Literal, Reference, Comparison, And, Or, Not]


# ---- Tokenizer ----

_TOKEN_RE = re.compile(
    r'\s*(?:'
    r'(?P<STRING>"[^"]*")'
    r'|(?P<NUMBER>-?\d+(?:\.\d+)?)'
    r'|(?P<OP>==|!=|<=|>=|<|>)'
    r'|(?P<LPAREN>\()'
    r'|(?P<RPAREN>\))'
    r'|(?P<DOT>\.)'
    r'|(?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)'
    r')'
)


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            if text[pos].isspace():
                pos += 1
                continue
            raise ConditionError(f"unexpected character at position {pos}: {text[pos]!r}")
        kind = m.lastgroup
        value = m.group(kind)
        tokens.append((kind, value))
        pos = m.end()
    tokens.append(("EOF", ""))
    return tokens


# ---- Parser ----

class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> tuple[str, str]:
        return self.tokens[self.pos]

    def consume(self, kind: str | None = None) -> tuple[str, str]:
        tok = self.tokens[self.pos]
        if kind and tok[0] != kind:
            raise ConditionError(f"expected {kind}, got {tok[0]}:{tok[1]!r}")
        self.pos += 1
        return tok

    def parse(self) -> Node:
        node = self._or()
        if self.peek()[0] != "EOF":
            raise ConditionError(f"trailing tokens after expression: {self.peek()}")
        return node

    def _or(self) -> Node:
        left = self._and()
        while self.peek() == ("IDENT", "or"):
            self.consume()
            right = self._and()
            left = Or(lhs=left, rhs=right)
        return left

    def _and(self) -> Node:
        left = self._not()
        while self.peek() == ("IDENT", "and"):
            self.consume()
            right = self._not()
            left = And(lhs=left, rhs=right)
        return left

    def _not(self) -> Node:
        if self.peek() == ("IDENT", "not"):
            self.consume()
            return Not(operand=self._not())
        return self._comparison()

    def _comparison(self) -> Node:
        lhs = self._primary()
        if self.peek()[0] == "OP":
            op = self.consume("OP")[1]
            rhs = self._primary()
            return Comparison(op=op, lhs=lhs, rhs=rhs)
        return lhs

    def _primary(self) -> Node:
        tok = self.peek()
        if tok[0] == "LPAREN":
            self.consume()
            node = self._or()
            self.consume("RPAREN")
            return node
        if tok[0] == "STRING":
            self.consume()
            return Literal(value=tok[1][1:-1])  # strip quotes
        if tok[0] == "NUMBER":
            self.consume()
            value = float(tok[1]) if "." in tok[1] else int(tok[1])
            return Literal(value=value)
        if tok[0] == "IDENT":
            # May be a keyword literal (true/false/null) or a reference
            if tok[1] == "true":
                self.consume()
                return Literal(value=True)
            if tok[1] == "false":
                self.consume()
                return Literal(value=False)
            if tok[1] == "null":
                self.consume()
                return Literal(value=None)
            return self._reference()
        raise ConditionError(f"unexpected token: {tok}")

    def _reference(self) -> Reference:
        parts: list[str] = []
        tok = self.consume("IDENT")
        parts.append(tok[1])
        while self.peek()[0] == "DOT":
            self.consume()
            ident = self.consume("IDENT")
            parts.append(ident[1])
        length = False
        if parts and parts[-1] == "length":
            length = True
            parts.pop()
        return Reference(path=parts, length=length)


def parse(text: str) -> Node:
    tokens = _tokenize(text)
    return _Parser(tokens).parse()


# ---- Evaluator ----

def evaluate(condition: str, context: dict) -> bool:
    ast = parse(condition)
    return bool(_eval(ast, context))


def _eval(node: Node, context: dict) -> Any:
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, Reference):
        current: Any = context
        for part in node.path:
            if isinstance(current, dict):
                if part not in current:
                    raise ConditionError(f"reference not found: {'.'.join(node.path)}")
                current = current[part]
            else:
                raise ConditionError(f"cannot traverse non-dict at part {part!r}")
        if node.length:
            try:
                return len(current)
            except TypeError:
                raise ConditionError("cannot apply .length to non-array/string")
        return current
    if isinstance(node, Comparison):
        lv = _eval(node.lhs, context)
        rv = _eval(node.rhs, context)
        return _compare(node.op, lv, rv)
    if isinstance(node, And):
        return _eval(node.lhs, context) and _eval(node.rhs, context)
    if isinstance(node, Or):
        return _eval(node.lhs, context) or _eval(node.rhs, context)
    if isinstance(node, Not):
        return not _eval(node.operand, context)
    raise ConditionError(f"unknown AST node: {type(node).__name__}")


def _compare(op: str, lhs: Any, rhs: Any) -> bool:
    if op == "==":
        return lhs == rhs
    if op == "!=":
        return lhs != rhs
    if op == "<":
        return lhs < rhs
    if op == "<=":
        return lhs <= rhs
    if op == ">":
        return lhs > rhs
    if op == ">=":
        return lhs >= rhs
    raise ConditionError(f"unknown comparison operator: {op}")
```

**Step 4:** Run: `pytest tests/test_skill_condition.py -v`
Expected: PASS on all nine tests. If any fail, iterate.

### Task 4.3: Commit Phase 4

```bash
git add neuromancy/core/skill_condition.py tests/test_skill_condition.py
git commit -m "feat(skills): add condition DSL parser and evaluator for when/terminate_if"
```

---

## Phase 5 — Templating (Jinja subset)

**Goal:** Render `{{ inputs.X }}` and `{{ steps.X.output }}` expressions in skill step arguments and prompts. ~2 hours.

**Files:**
- Create: `neuromancy/core/skill_templating.py`
- Create: `tests/test_skill_templating.py`

### Task 5.1: Write failing tests

**Step 1:** Create `tests/test_skill_templating.py`:

```python
import pytest
from neuromancy.core.skill_templating import render, render_dict, TemplateError


@pytest.fixture
def ctx():
    return {
        "inputs": {"topic": "python", "depth": "deep"},
        "steps": {"search": {"output": {"query": "python"}, "error": None}},
    }


def test_render_simple_ref(ctx):
    assert render("Topic: {{ inputs.topic }}", ctx) == "Topic: python"


def test_render_nested_ref(ctx):
    assert render("Query: {{ steps.search.output.query }}", ctx) == "Query: python"


def test_render_multiple(ctx):
    result = render("{{ inputs.topic }}/{{ inputs.depth }}", ctx)
    assert result == "python/deep"


def test_render_missing_var_raises(ctx):
    with pytest.raises(TemplateError):
        render("{{ inputs.missing }}", ctx)


def test_render_dict_recurses(ctx):
    d = {"query": "{{ inputs.topic }}", "limit": 5, "nested": {"field": "{{ inputs.depth }}"}}
    rendered = render_dict(d, ctx)
    assert rendered == {"query": "python", "limit": 5, "nested": {"field": "deep"}}


def test_render_dict_handles_lists(ctx):
    d = {"items": ["{{ inputs.topic }}", "static"]}
    rendered = render_dict(d, ctx)
    assert rendered == {"items": ["python", "static"]}
```

**Step 2:** Run: `pytest tests/test_skill_templating.py -v`
Expected: FAIL.

### Task 5.2: Implement templating

**Step 3:** Create `neuromancy/core/skill_templating.py`:

```python
"""Minimal Jinja-subset renderer for skill templates.

Supports {{ references }} against a context dict. No filters except
tojson. No control flow (that's the step format's job).
"""

from __future__ import annotations

import json
import re
from typing import Any

_EXPR_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


class TemplateError(Exception):
    """Raised when a template can't be rendered."""


def render(template: str, context: dict) -> str:
    """Render a template string using the context dict.

    Raises TemplateError on missing references or malformed expressions.
    """
    def _sub(match: re.Match) -> str:
        expr = match.group(1).strip()
        return _stringify(_resolve(expr, context))

    return _EXPR_RE.sub(_sub, template)


def render_dict(value: Any, context: dict) -> Any:
    """Recursively render all string values in a dict/list structure."""
    if isinstance(value, str):
        return render(value, context)
    if isinstance(value, dict):
        return {k: render_dict(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render_dict(v, context) for v in value]
    return value


def _resolve(expr: str, context: dict) -> Any:
    # Support a single pipe filter: `path | tojson`
    filter_name: str | None = None
    if "|" in expr:
        path_part, filter_part = (p.strip() for p in expr.split("|", 1))
        filter_name = filter_part
        expr = path_part

    parts = expr.split(".")
    current: Any = context
    for part in parts:
        if isinstance(current, dict):
            if part not in current:
                raise TemplateError(f"template reference not found: {expr}")
            current = current[part]
        elif isinstance(current, list):
            try:
                idx = int(part)
                current = current[idx]
            except (ValueError, IndexError):
                raise TemplateError(f"invalid list index in template: {part}")
        else:
            raise TemplateError(f"cannot traverse non-dict/list for {expr} at {part!r}")

    if filter_name == "tojson":
        return json.dumps(current)
    if filter_name:
        raise TemplateError(f"unknown template filter: {filter_name}")
    return current


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)
```

**Step 4:** Run: `pytest tests/test_skill_templating.py -v`
Expected: PASS.

### Task 5.3: Commit Phase 5

```bash
git add neuromancy/core/skill_templating.py tests/test_skill_templating.py
git commit -m "feat(skills): add Jinja-subset templating renderer for skill steps"
```

---

## Phase 6 — SkillRegistry

**Goal:** Load active + pending skills from disk on startup, expose lookup + catalog generation, quarantine malformed skills. ~3 hours.

**Files:**
- Create: `neuromancy/core/skill_registry.py`
- Create: `tests/test_skill_registry.py`

### Task 6.1: Write failing tests

**Step 1:** Create `tests/test_skill_registry.py`:

```python
import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from neuromancy.core.skill_registry import SkillRegistry
from neuromancy.core.skill_models import SkillManifest


def _write_skill(root: Path, name: str, *, valid: bool = True) -> None:
    skill_dir = root / "active" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": 1,
        "description": f"{name} description",
        "created_at": "2026-04-13T00:00:00+00:00",
        "updated_at": "2026-04-13T00:00:00+00:00",
        "input_schema": {"type": "object", "properties": {}},
        "run_count": 0,
        "success_rate": 0.0,
        "has_compiled": False,
        "tags": [],
    }
    (skill_dir / "manifest.json").write_text(json.dumps(manifest))
    template = "name: " + name + "\nversion: 1\ndescription: x\ninputs: {}\nsteps: []\noutput: ''\n"
    if not valid:
        template = "this is not: valid: yaml: :"
    (skill_dir / "template.yaml").write_text(template)


def test_registry_loads_active_skills(tmp_path):
    _write_skill(tmp_path, "research_topic")
    _write_skill(tmp_path, "draft_email")

    registry = SkillRegistry(tmp_path)
    assert set(registry.list_names()) == {"research_topic", "draft_email"}


def test_registry_quarantines_malformed_templates(tmp_path):
    _write_skill(tmp_path, "good_one")
    _write_skill(tmp_path, "bad_one", valid=False)

    registry = SkillRegistry(tmp_path)
    assert registry.list_names() == ["good_one"]
    assert (tmp_path / "quarantine" / "bad_one").exists()


def test_registry_catalog_string(tmp_path):
    _write_skill(tmp_path, "research_topic")
    registry = SkillRegistry(tmp_path)
    catalog = registry.catalog_string()
    assert "research_topic" in catalog
    assert "research_topic description" in catalog


def test_registry_get_by_name(tmp_path):
    _write_skill(tmp_path, "research_topic")
    registry = SkillRegistry(tmp_path)
    skill = registry.get("research_topic")
    assert skill is not None
    assert skill.manifest.name == "research_topic"


def test_registry_get_missing_returns_none(tmp_path):
    registry = SkillRegistry(tmp_path)
    assert registry.get("nothing_here") is None
```

**Step 2:** Run: `pytest tests/test_skill_registry.py -v`
Expected: FAIL.

### Task 6.2: Implement registry

**Step 3:** Create `neuromancy/core/skill_registry.py`:

```python
"""SkillRegistry — loads and indexes active skills from disk."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .skill_models import SkillManifest, SkillTemplate

logger = logging.getLogger(__name__)


@dataclass
class LoadedSkill:
    manifest: SkillManifest
    template: SkillTemplate
    dir: Path


class SkillRegistry:
    """Loads skills from ~/.neuromancy/skills/active/ on construction.

    Malformed skills are moved to ~/.neuromancy/skills/quarantine/ and logged.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.active_dir = self.root / "active"
        self.pending_dir = self.root / "pending"
        self.quarantine_dir = self.root / "quarantine"
        self.active_dir.mkdir(parents=True, exist_ok=True)
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

        self._skills: dict[str, LoadedSkill] = {}
        self._load_active()

    def _load_active(self) -> None:
        for skill_dir in sorted(self.active_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            try:
                manifest = self._load_manifest(skill_dir)
                template = self._load_template(skill_dir)
                self._skills[manifest.name] = LoadedSkill(
                    manifest=manifest, template=template, dir=skill_dir
                )
            except Exception as e:
                logger.warning("Quarantining skill %s: %s", skill_dir.name, e)
                self._quarantine(skill_dir, reason=str(e))

    def _load_manifest(self, skill_dir: Path) -> SkillManifest:
        data = json.loads((skill_dir / "manifest.json").read_text(encoding="utf-8"))
        return SkillManifest.model_validate(data)

    def _load_template(self, skill_dir: Path) -> SkillTemplate:
        text = (skill_dir / "template.yaml").read_text(encoding="utf-8")
        return SkillTemplate.from_yaml(text)

    def _quarantine(self, skill_dir: Path, *, reason: str) -> None:
        target = self.quarantine_dir / skill_dir.name
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(skill_dir), str(target))
        (target / "_quarantine_reason.txt").write_text(reason)

    def list_names(self) -> list[str]:
        return sorted(self._skills.keys())

    def get(self, name: str) -> Optional[LoadedSkill]:
        return self._skills.get(name)

    def catalog_string(self) -> str:
        """Build the lightweight catalog for the system prompt.

        Format: `- {name}: {description}. Inputs: {keys}`
        """
        if not self._skills:
            return ""
        lines = ["Available skills:"]
        for name in self.list_names():
            skill = self._skills[name]
            input_keys = list(skill.template.inputs.keys())
            input_part = ", ".join(input_keys) if input_keys else "(none)"
            lines.append(f"- {name}: {skill.manifest.description}. Inputs: {input_part}")
        return "\n".join(lines)

    def reload(self) -> None:
        self._skills.clear()
        self._load_active()
```

**Step 4:** Run: `pytest tests/test_skill_registry.py -v`
Expected: PASS.

### Task 6.3: Commit Phase 6

```bash
git add neuromancy/core/skill_registry.py tests/test_skill_registry.py
git commit -m "feat(skills): add SkillRegistry with disk loading, quarantine, and catalog generation"
```

---

## Phase 7 — SkillRunner (longest phase)

**Goal:** Execute a skill template with sequential steps, `when`/`then`/`else` branching, `terminate_if` early-exit, test capture, and manifest stat updates. ~6 hours.

**Files:**
- Create: `neuromancy/core/skill_runner.py`
- Create: `tests/test_skill_runner.py`

### Task 7.1: Write failing tests — happy path

**Step 1:** Create `tests/test_skill_runner.py`:

```python
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from neuromancy.core.skill_runner import SkillRunner
from neuromancy.core.skill_registry import LoadedSkill
from neuromancy.core.skill_models import SkillManifest, SkillTemplate


def _make_skill(tmp_path: Path, template_yaml: str, name: str = "test_skill") -> LoadedSkill:
    skill_dir = tmp_path / "active" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "tests").mkdir(exist_ok=True)
    manifest = SkillManifest(
        name=name, version=1, description="t",
        created_at="2026-04-13T00:00:00+00:00",
        updated_at="2026-04-13T00:00:00+00:00",
        input_schema={"type": "object", "properties": {}},
    )
    (skill_dir / "manifest.json").write_text(json.dumps(manifest.model_dump(mode="json")))
    (skill_dir / "template.yaml").write_text(template_yaml)
    template = SkillTemplate.from_yaml(template_yaml)
    return LoadedSkill(manifest=manifest, template=template, dir=skill_dir)


@pytest.fixture
def mock_registry():
    r = MagicMock()
    # Mock plugin that returns a canned value for any tool call
    mock_plugin = MagicMock()
    mock_plugin.execute = AsyncMock(return_value='{"results": ["a", "b"]}')
    r.find_plugin_for_tool = MagicMock(return_value=(mock_plugin, MagicMock(requires_approval=False)))
    return r


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.model = "anthropic/claude-haiku-4"
    async def _complete(messages, *args, **kwargs):
        return {"choices": [{"message": {"content": "LLM response"}}], "usage": {"total_tokens": 100}}
    llm.complete = _complete
    llm.last_usage = None
    return llm


@pytest.mark.asyncio
async def test_runner_executes_sequential_steps(tmp_path, mock_registry, mock_llm):
    yaml_text = """
name: test_skill
version: 1
description: test
inputs:
  topic:
    type: string
    required: true
steps:
  - id: search
    action: tool_call
    tool: web_search
    arguments:
      query: "{{ inputs.topic }}"
  - id: summarize
    action: llm_call
    prompt: "Summarize: {{ steps.search.output }}"
output: "{{ steps.summarize.output }}"
"""
    skill = _make_skill(tmp_path, yaml_text)
    runner = SkillRunner(registry=mock_registry, llm=mock_llm)
    result = await runner.run(skill, inputs={"topic": "python"})

    assert result.succeeded is True
    assert result.final_output == "LLM response"
    assert result.branch_path == ["search", "summarize"]
    # Test capture should be written
    captures = list((skill.dir / "tests").glob("*.json"))
    assert len(captures) == 1
```

**Step 2:** Run: `pytest tests/test_skill_runner.py::test_runner_executes_sequential_steps -v`
Expected: FAIL.

### Task 7.2: Implement basic SkillRunner

**Step 3:** Create `neuromancy/core/skill_runner.py`:

```python
"""Execute skill templates. Capture tests. Update stats."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from .skill_condition import evaluate as eval_condition, ConditionError
from .skill_models import (
    LoadedSkill,  # re-exported via skill_registry; import from there if preferred
    SkillManifest,
    SkillRunResult,
    SkillStep,
    SkillStepResult,
    SkillStepUnion,
    SkillTemplate,
    SkillTestCase,
    SkillWhenBlock,
    StepAction,
)
from .skill_templating import render, render_dict, TemplateError

logger = logging.getLogger(__name__)


# NOTE: avoid a circular import — LoadedSkill actually lives in skill_registry.
from .skill_registry import LoadedSkill  # noqa: E402


EventCallback = Optional[Callable[[dict], Awaitable[None]]]


class SkillRunner:
    """Executes a loaded skill against a plugin registry and an LLM provider.

    The runner is stateless across runs; create one per daemon, reuse for all runs.
    """

    def __init__(self, registry, llm):
        self.registry = registry
        self.llm = llm

    async def run(
        self,
        skill: LoadedSkill,
        inputs: dict,
        on_event: EventCallback = None,
    ) -> SkillRunResult:
        started_at = datetime.now(timezone.utc)
        start_perf = time.perf_counter()

        context: dict[str, Any] = {"inputs": dict(inputs), "steps": {}}
        branch_path: list[str] = []
        step_results: list[SkillStepResult] = []
        succeeded = True
        error: str | None = None

        try:
            await self._run_steps(skill.template.steps, context, branch_path, step_results, on_event)
            final_output = self._render_output(skill.template.output, context)
        except _SkillAborted as e:
            succeeded = False
            error = str(e)
            final_output = ""
        except Exception as e:
            logger.exception("Skill %s crashed unexpectedly", skill.manifest.name)
            succeeded = False
            error = f"internal error: {e}"
            final_output = ""

        duration_ms = (time.perf_counter() - start_perf) * 1000
        result = SkillRunResult(
            skill_name=skill.manifest.name,
            skill_version=skill.manifest.version,
            inputs=inputs,
            steps=step_results,
            branch_path=branch_path,
            final_output=final_output,
            succeeded=succeeded,
            error=error,
            duration_ms=duration_ms,
            started_at=started_at,
        )

        self._capture_test(skill, result)
        self._update_manifest_stats(skill, result)
        return result

    async def _run_steps(
        self,
        steps: list[SkillStepUnion],
        context: dict,
        branch_path: list[str],
        step_results: list[SkillStepResult],
        on_event: EventCallback,
    ) -> None:
        for step in steps:
            if isinstance(step, SkillWhenBlock):
                try:
                    if eval_condition(step.condition, context):
                        await self._run_steps(step.then_branch, context, branch_path, step_results, on_event)
                    else:
                        await self._run_steps(step.else_branch, context, branch_path, step_results, on_event)
                except ConditionError as e:
                    raise _SkillAborted(f"when condition failed: {e}")
                continue

            # Regular step
            if step.terminate_if:
                try:
                    if eval_condition(step.terminate_if, context):
                        return
                except ConditionError as e:
                    raise _SkillAborted(f"terminate_if failed on step {step.id}: {e}")

            branch_path.append(step.id)
            started = time.perf_counter()
            step_error: str | None = None
            output: Any = None

            try:
                if step.action == StepAction.tool_call:
                    output = await self._execute_tool(step, context)
                elif step.action == StepAction.llm_call:
                    output = await self._execute_llm(step, context)
                else:
                    raise _SkillAborted(f"unknown step action: {step.action}")
            except Exception as e:
                step_error = str(e)
                step_results.append(SkillStepResult(
                    step_id=step.id,
                    output=None,
                    error=step_error,
                    duration_ms=(time.perf_counter() - started) * 1000,
                ))
                context["steps"][step.id] = {"output": None, "error": step_error}
                raise _SkillAborted(f"step {step.id} failed: {step_error}")

            step_duration = (time.perf_counter() - started) * 1000
            step_results.append(SkillStepResult(
                step_id=step.id,
                output=output,
                duration_ms=step_duration,
            ))
            context["steps"][step.id] = {"output": output, "error": None}

    async def _execute_tool(self, step: SkillStep, context: dict) -> Any:
        try:
            rendered_args = render_dict(step.arguments, context)
        except TemplateError as e:
            raise _SkillAborted(f"tool args render failed: {e}")
        plugin, _ = self.registry.find_plugin_for_tool(step.tool)
        raw_output = await plugin.execute(step.tool, rendered_args)
        # Tool outputs are strings; try parsing as JSON for structured access
        try:
            return json.loads(raw_output)
        except (json.JSONDecodeError, TypeError):
            return raw_output

    async def _execute_llm(self, step: SkillStep, context: dict) -> str:
        try:
            rendered_prompt = render(step.prompt or "", context)
        except TemplateError as e:
            raise _SkillAborted(f"llm prompt render failed: {e}")
        original_model = self.llm.model
        if step.model:
            self.llm.model = step.model
        try:
            resp = await self.llm.complete([{"role": "user", "content": rendered_prompt}])
        finally:
            if step.model:
                self.llm.model = original_model
        choices = resp.get("choices", [])
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content", "")

    def _render_output(self, output_template: str, context: dict) -> str:
        if not output_template:
            return ""
        try:
            return render(output_template, context)
        except TemplateError as e:
            raise _SkillAborted(f"output render failed: {e}")

    def _capture_test(self, skill: LoadedSkill, result: SkillRunResult) -> None:
        tests_dir = skill.dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        test = SkillTestCase(
            inputs=result.inputs,
            branch_path=result.branch_path,
            final_output=result.final_output,
            succeeded=result.succeeded,
            duration_ms=result.duration_ms,
            cost_usd=result.cost_usd,
        )
        timestamp = test.timestamp.isoformat().replace(":", "-")
        path = tests_dir / f"{timestamp}.json"
        path.write_text(json.dumps(test.model_dump(mode="json"), indent=2))

    def _update_manifest_stats(self, skill: LoadedSkill, result: SkillRunResult) -> None:
        manifest = skill.manifest
        manifest.run_count += 1
        alpha = 0.1
        target = 1.0 if result.succeeded else 0.0
        manifest.success_rate = (1 - alpha) * manifest.success_rate + alpha * target
        manifest.updated_at = datetime.now(timezone.utc)

        # Atomic write
        path = skill.dir / "manifest.json"
        fd, tmp_path = tempfile.mkstemp(dir=str(skill.dir), suffix=".tmp")
        try:
            os.write(fd, json.dumps(manifest.model_dump(mode="json"), indent=2).encode("utf-8"))
            os.close(fd)
            Path(tmp_path).replace(path)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise


class _SkillAborted(Exception):
    """Internal control-flow exception used to signal a failed skill run."""
```

Note: the import of `LoadedSkill` is split — edit `skill_models.py` or `skill_registry.py` so `LoadedSkill` can be imported from a single canonical place. The simplest fix: define `LoadedSkill` in `skill_models.py` and re-export it.

**Step 4:** Run: `pytest tests/test_skill_runner.py::test_runner_executes_sequential_steps -v`
Expected: PASS. If imports fail due to circular dep, move `LoadedSkill` into `skill_models.py`.

### Task 7.3: Add tests for branching, termination, failures

**Step 1:** Add to `tests/test_skill_runner.py`:

```python
@pytest.mark.asyncio
async def test_runner_when_block_takes_then_branch(tmp_path, mock_registry, mock_llm):
    yaml_text = """
name: test_when
version: 1
description: test
inputs:
  topic:
    type: string
    required: true
steps:
  - when: 'inputs.topic == "python"'
    then:
      - id: py_step
        action: llm_call
        prompt: "python path"
    else:
      - id: other_step
        action: llm_call
        prompt: "other path"
output: "{{ steps.py_step.output }}"
"""
    skill = _make_skill(tmp_path, yaml_text, name="test_when")
    runner = SkillRunner(registry=mock_registry, llm=mock_llm)
    result = await runner.run(skill, inputs={"topic": "python"})
    assert result.branch_path == ["py_step"]


@pytest.mark.asyncio
async def test_runner_terminate_if_stops_execution(tmp_path, mock_registry, mock_llm):
    yaml_text = """
name: test_term
version: 1
description: test
inputs:
  topic:
    type: string
    required: true
steps:
  - id: first
    action: llm_call
    prompt: "first step"
  - id: second
    terminate_if: 'inputs.topic == "python"'
    action: llm_call
    prompt: "should be skipped"
  - id: third
    action: llm_call
    prompt: "should also be skipped"
output: "{{ steps.first.output }}"
"""
    skill = _make_skill(tmp_path, yaml_text, name="test_term")
    runner = SkillRunner(registry=mock_registry, llm=mock_llm)
    result = await runner.run(skill, inputs={"topic": "python"})
    assert result.branch_path == ["first"]


@pytest.mark.asyncio
async def test_runner_captures_failure(tmp_path, mock_llm):
    # Use a registry that raises on tool call
    bad_registry = MagicMock()
    bad_plugin = MagicMock()
    bad_plugin.execute = AsyncMock(side_effect=RuntimeError("tool failed"))
    bad_registry.find_plugin_for_tool = MagicMock(return_value=(bad_plugin, MagicMock()))

    yaml_text = """
name: test_fail
version: 1
description: test
inputs: {}
steps:
  - id: will_fail
    action: tool_call
    tool: web_search
    arguments: {query: "x"}
output: "{{ steps.will_fail.output }}"
"""
    skill = _make_skill(tmp_path, yaml_text, name="test_fail")
    runner = SkillRunner(registry=bad_registry, llm=mock_llm)
    result = await runner.run(skill, inputs={})
    assert result.succeeded is False
    assert "tool failed" in (result.error or "")
    # Test capture should still be written, marked as failed
    captures = list((skill.dir / "tests").glob("*.json"))
    assert len(captures) == 1
    capture_data = json.loads(captures[0].read_text())
    assert capture_data["succeeded"] is False
```

**Step 2:** Run: `pytest tests/test_skill_runner.py -v`
Expected: PASS on all four tests.

### Task 7.4: Commit Phase 7

```bash
git add neuromancy/core/skill_runner.py neuromancy/core/skill_models.py tests/test_skill_runner.py
git commit -m "feat(skills): add SkillRunner with sequential/when/terminate_if execution and test capture"
```

---

## Phase 8 — `run_skill` meta-tool

**Goal:** Wrap `SkillRunner` as a tool the LLM can call. ~2 hours.

**Files:**
- Create: `neuromancy/core/skill_meta_tool.py`
- Create: `tests/test_skill_meta_tool.py`

### Task 8.1: Write failing tests

**Step 1:** Create `tests/test_skill_meta_tool.py` with tests covering:
- `run_skill` tool schema generation
- successful dispatch to `SkillRunner`
- error when skill doesn't exist
- error propagation from a failing skill

**Step 2:** Run tests, expect FAIL.

### Task 8.2: Implement meta-tool

**Step 3:** Create `neuromancy/core/skill_meta_tool.py`:

```python
"""run_skill meta-tool — exposes SkillRegistry skills to the LLM."""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from .skill_registry import SkillRegistry
from .skill_runner import SkillRunner

logger = logging.getLogger(__name__)


RUN_SKILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_skill",
        "description": (
            "Execute a previously-learned skill by name. Available skills "
            "and their inputs are listed in the 'Available skills' section "
            "of the system prompt."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Name of the skill to run"},
                "inputs": {"type": "object", "description": "Input values matching the skill's input schema"},
            },
            "required": ["skill_name", "inputs"],
        },
    },
}


class SkillMetaTool:
    """Dispatches run_skill invocations to the SkillRunner."""

    TOOL_NAME = "run_skill"

    def __init__(self, registry: SkillRegistry, runner: SkillRunner):
        self._registry = registry
        self._runner = runner

    def tool_schema(self) -> dict:
        return RUN_SKILL_SCHEMA

    async def execute(self, arguments: dict, on_event: Callable[[dict], Awaitable[None]] | None = None) -> str:
        skill_name = arguments.get("skill_name", "")
        inputs = arguments.get("inputs", {})
        if not skill_name:
            return "Error: run_skill requires a 'skill_name' argument."
        skill = self._registry.get(skill_name)
        if skill is None:
            available = ", ".join(self._registry.list_names()) or "(none)"
            return f"Error: skill {skill_name!r} not found. Available: {available}"
        try:
            result = await self._runner.run(skill, inputs, on_event=on_event)
        except Exception as e:
            logger.exception("run_skill dispatch crashed")
            return f"Error: skill execution crashed: {e}"
        if not result.succeeded:
            return f"Skill {skill_name} failed: {result.error}"
        return result.final_output or "(skill completed with no output)"
```

**Step 4:** Run the tests, expect PASS.

### Task 8.3: Commit Phase 8

```bash
git add neuromancy/core/skill_meta_tool.py tests/test_skill_meta_tool.py
git commit -m "feat(skills): add run_skill meta-tool dispatching to SkillRunner"
```

---

## Phase 9 — QueryLoop integration

**Goal:** Wire `SkillRegistry` and `SkillMetaTool` into `QueryLoop` so skills appear in the LLM's tool list and system prompt. ~3 hours.

**Files:**
- Modify: `neuromancy/core/query_loop.py`
- Create: `tests/test_query_loop_skills.py`

### Task 9.1: Add `skill_registry` parameter to QueryLoop

**Step 1:** In `neuromancy/core/query_loop.py`, update the `__init__` signature:

```python
def __init__(
    self,
    llm: OpenRouterProvider,
    registry: PluginRegistry,
    memory_store: MemoryStore | None = None,
    pricing_cache: PricingCache | None = None,
    context_manager: ContextManager | None = None,
    model_router: ModelRouter | None = None,
    skill_registry: "SkillRegistry | None" = None,
):
    ...
    self.skill_registry = skill_registry
    if skill_registry is not None:
        from .skill_runner import SkillRunner
        from .skill_meta_tool import SkillMetaTool
        self._skill_runner = SkillRunner(registry=registry, llm=llm)
        self._skill_meta_tool = SkillMetaTool(registry=skill_registry, runner=self._skill_runner)
    else:
        self._skill_runner = None
        self._skill_meta_tool = None
```

### Task 9.2: Inject skill catalog and run_skill schema

**Step 2:** In `QueryLoop.run()`, after `tools = self.registry.get_all_tool_schemas()`:

```python
# Add run_skill meta-tool
if self._skill_meta_tool:
    tools = list(tools) + [self._skill_meta_tool.tool_schema()]
```

And after `system_prefix` is built from memories, extend it with the skill catalog:

```python
if self.skill_registry:
    catalog = self.skill_registry.catalog_string()
    if catalog:
        if system_prefix:
            system_prefix = system_prefix + "\n\n" + catalog
        else:
            system_prefix = catalog
            messages.insert(0, {"role": "system", "content": system_prefix})
```

### Task 9.3: Dispatch run_skill tool calls

**Step 3:** In the tool dispatch loop, add a branch before the plugin dispatch:

```python
if tool_call.name == "run_skill" and self._skill_meta_tool:
    try:
        output = await self._skill_meta_tool.execute(tool_call.arguments)
        tool_call.status = ToolCallStatus.completed
        result = ToolResult(tool_call_id=tool_call.id, output=output, duration_ms=0.0)
    except Exception as e:
        tool_call.status = ToolCallStatus.failed
        result = ToolResult(tool_call_id=tool_call.id, output="", error=str(e))
    yield StreamEvent(...)  # use existing tool_result event pattern
    continue
```

### Task 9.4: Write integration test

**Step 4:** Create `tests/test_query_loop_skills.py` that runs a full `QueryLoop` turn with a mock LLM that returns a `run_skill` tool call, verifies the skill executes, and verifies the result flows back.

### Task 9.5: Run and commit

**Step 5:** Run: `pytest tests/test_query_loop_skills.py tests/test_query_loop_memory.py -v`
Expected: PASS, no regressions.

**Step 6:**

```bash
git add neuromancy/core/query_loop.py tests/test_query_loop_skills.py
git commit -m "feat(skills): integrate SkillRegistry and run_skill meta-tool into QueryLoop"
```

---

## Phase 10 — Prompt cache segmentation

**Goal:** Place the skill catalog in its own cache-control block so skill changes don't invalidate the rest of the system prompt. ~1 hour.

**Files:**
- Modify: `neuromancy/core/prompt_cache.py`
- Create or modify: `tests/test_prompt_cache.py`

### Task 10.1: Review current cache behavior

**Step 1:** Open `neuromancy/core/prompt_cache.py`. Understand where markers are currently placed (likely on the last system message).

### Task 10.2: Split into three segments

**Step 2:** Refactor `apply_cache_markers` so that when the system prompt contains multiple segments (separated by the sentinel `\n---CATALOG---\n` or similar), each segment gets its own cache_control block. Alternatively, accept the segments as a list and build the messages with explicit markers.

### Task 10.3: Update QueryLoop to use segmented injection

**Step 3:** In `QueryLoop.run()`, change how memory + skill catalog are injected so they become structurally separate messages with independent cache markers.

### Task 10.4: Test and commit

**Step 4:** Add tests for marker placement across segments.

**Step 5:**

```bash
git add neuromancy/core/prompt_cache.py neuromancy/core/query_loop.py tests/test_prompt_cache.py
git commit -m "feat(prompt_cache): segment cache markers for preamble/skills/memory independence"
```

---

## Phase 11 — Reflection-based skill extraction

**Goal:** Extend post-turn reflection to draft skill candidates from procedural turns. ~4 hours.

**Files:**
- Create: `neuromancy/core/skill_reflection.py`
- Modify: `neuromancy/core/query_loop.py` (the `_reflect` method)
- Create: `tests/test_skill_reflection.py`

### Task 11.1: Define procedural-turn heuristic

**Step 1:** In `skill_reflection.py`, write:

```python
def is_procedural_turn(messages: list) -> bool:
    """A turn is procedural if it had >=3 tool calls in a coherent sequence."""
    tool_calls = [m for m in messages if ...]  # count assistant tool calls
    return len(tool_calls) >= 3
```

### Task 11.2: Write the reflection prompt

**Step 2:** The prompt tells the LLM: "Here's a turn transcript. If it was procedural and would generalize well, draft a skill template matching this schema: {schema}. Otherwise return null. Return JSON."

Include the condition DSL grammar in the prompt so the LLM knows what's allowed.

### Task 11.3: Implement candidate writing

**Step 3:** `write_candidate(candidate_data: dict, pending_dir: Path)` creates a new directory under `pending/`, writes manifest.json and template.yaml, returns the path.

### Task 11.4: Hook into `_reflect`

**Step 4:** In `QueryLoop._reflect`, after the existing memory extraction, call `_reflect_skill_candidate()`. Fire-and-forget, errors swallowed.

### Task 11.5: Tests

**Step 5:** Test with a canned multi-tool transcript and a mocked LLM that returns a canned candidate. Verify a directory appears in `pending/`.

### Task 11.6: Commit

```bash
git add neuromancy/core/skill_reflection.py neuromancy/core/query_loop.py tests/test_skill_reflection.py
git commit -m "feat(skills): add reflection-based skill candidate extraction to pending queue"
```

---

## Phase 12 — HTTP endpoints

**Goal:** Expose skill management via FastAPI endpoints. ~3 hours.

**Files:**
- Modify: `neuromancy/server/app.py`
- Create: `tests/test_skills_endpoints.py`

### Task 12.1: Add endpoints

**Step 1:** In `create_app()`, add:

- `GET /skills` — list active skills with manifests
- `GET /skills/pending` — list pending candidates
- `POST /skills/{name}/approve` — move from pending to active, reload registry
- `POST /skills/{name}/reject` — delete from pending
- `POST /skills/{name}/edit` — accept YAML body, validate, overwrite, reload
- `DELETE /skills/{name}` — delete from active, reload
- `POST /skills/{name}/compile` — call compile function, return result
- `GET /skills/{name}/tests` — list captured test file names
- `GET /skills/{name}/history` — list version files

### Task 12.2: Test with FastAPI TestClient

**Step 2:** Use `fastapi.testclient.TestClient` to hit each endpoint against a temp skills directory.

### Task 12.3: Commit

```bash
git add neuromancy/server/app.py tests/test_skills_endpoints.py
git commit -m "feat(skills): add HTTP endpoints for listing, approving, editing, compiling skills"
```

---

## Phase 13 — Compile scaffolding

**Goal:** Ship the `compile_skill` function with a stub implementation that's not auto-triggered. ~3 hours.

**Files:**
- Create: `neuromancy/core/skill_compiler.py`
- Create: `tests/test_skill_compiler.py`

### Task 13.1: Define `CompileResult`

**Step 1:** Dataclass with `success: bool`, `compiled_path: Path | None`, `reason: str`.

### Task 13.2: Implement `compile_skill`

**Step 2:** Load the template, load all test cases. If fewer than 5 successful cases, return `insufficient_data`. Otherwise, prompt the LLM with a carefully-structured message asking for a Python async function. Validate the returned Python parses. Do **not** execute it in v1 (execution against test cases is a subprocess-isolated subagent feature that ships with subagents in a future session).

### Task 13.3: Write one compile test

**Step 3:** Feed a mock LLM that returns a trivial valid Python function. Verify `compiled.py` is written and parses.

### Task 13.4: Commit

```bash
git add neuromancy/core/skill_compiler.py tests/test_skill_compiler.py
git commit -m "feat(skills): add compile_skill scaffolding (exposed via endpoint, not auto-triggered)"
```

---

## Phase 14 — Frontend Skills panel

**Goal:** Review pending, manage active, show run history. ~6 hours.

**Files:**
- Create: `frontend/src/components/SkillsPanel.tsx`
- Modify: `frontend/src/components/SidebarTabs.tsx`
- Modify: `frontend/src/app/page.tsx` if needed
- Potentially create: `frontend/src/hooks/useSkills.ts`

### Task 14.1: Hook to fetch skills

**Step 1:** Create `useSkills()` hook that fetches `/skills` and `/skills/pending` from the backend. Polls or refetches on pending-event.

### Task 14.2: Panel layout

**Step 2:** Two tabs inside the panel: "Active" and "Pending". Active lists name, description, run count, success rate. Pending shows review cards.

### Task 14.3: Review card

**Step 3:** Shows name, description, YAML template (syntax-highlighted, use a simple `<pre>` with CSS for v1), source session link, three buttons: Approve, Edit & Approve, Reject.

### Task 14.4: Edit flow

**Step 4:** Clicking "Edit & Approve" opens a textarea pre-filled with the YAML. Save calls `POST /skills/{name}/edit`. Validate the YAML on the server (Phase 12 endpoint handles it).

### Task 14.5: Wire into sidebar

**Step 5:** Add a "Skills" tab to `SidebarTabs.tsx`.

### Task 14.6: Commit

```bash
git add frontend/src/components/SkillsPanel.tsx frontend/src/components/SidebarTabs.tsx frontend/src/hooks/useSkills.ts
git commit -m "feat(frontend): add SkillsPanel for reviewing pending and managing active skills"
```

---

## Phase 15 — Directory bootstrap & final verification

**Goal:** Ensure runtime directories exist on startup; run the manual smoke test. ~1 hour.

### Task 15.1: Bootstrap code

**Step 1:** In `create_app()` or `MemoryStore.__init__` (whichever is earlier in the lifecycle), ensure:

```python
skills_root = Path.home() / ".neuromancy" / "skills"
(skills_root / "active").mkdir(parents=True, exist_ok=True)
(skills_root / "pending").mkdir(parents=True, exist_ok=True)
(skills_root / "quarantine").mkdir(parents=True, exist_ok=True)
```

### Task 15.2: Full test suite

**Step 2:** Run: `pytest tests/ -v`
Expected: all tests pass, including the baseline from Prework Task C plus all new tests.

### Task 15.3: Manual smoke test

Per the design doc's "manual smoke test before declaring complete":

1. Boot Electron (`cd frontend && npm run dev` + `python -m neuromancy.server.app`)
2. Open a new session with a valid API key
3. Type a multi-step prompt: "Search the web for 'python asyncio patterns', fetch the top result, and summarize it."
4. Observe the agent runs `web_search`, `fetch_page`, then synthesizes
5. Check the Skills panel — a candidate should appear in the Pending tab
6. Click Approve on the candidate
7. Start a new session
8. Type "Use the research_topic skill to explore 'WebSocket vs SSE'"
9. Observe the LLM calls `run_skill`, the skill executes, the result returns
10. Check `~/.neuromancy/skills/active/research_topic/tests/` — a capture file is present
11. Check `~/.neuromancy/memory.db` persists across daemon restart

All ten steps passing = skills system is shippable.

### Task 15.4: Final commit

```bash
git add neuromancy/server/app.py
git commit -m "feat(skills): bootstrap skills directories on daemon startup"
```

### Task 15.5: Merge to main

Once the smoke test passes and all automated tests are green:

```bash
git checkout main
git merge --no-ff feat/skills-system
```

Or open a PR if you prefer to review the full diff before merging.

---

## Post-implementation checklist

- [ ] All 15 phases committed
- [ ] Baseline tests still green (no regressions)
- [ ] New tests all green
- [ ] Manual smoke test passed (all 10 steps)
- [ ] `memory.json.pre-sqlite.bak` exists in `~/.neuromancy/` confirming migration ran
- [ ] At least one skill is visible in the Skills panel
- [ ] At least one skill test capture file exists in `~/.neuromancy/skills/active/*/tests/`
- [ ] The skill catalog appears in the system prompt (check via a debug log or breakpoint)
- [ ] Prompt cache markers segment the preamble/skills/memory independently (verify via test or trace)
- [ ] No TODOs left without a follow-up issue

## What's explicitly NOT done in this plan

Per the design doc, these are deferred to later sessions:

- `gateways/` directory and all gateway implementations (contract, CLI client, Telegram gateway)
- `runtime/` directory and daemon process model
- Cron scheduler
- Subagent manager and meta-tools
- Electron tray integration (Electron-side change)
- Daemon startup / PID lock / tray menu
- Automatic compile-skill triggering (endpoint exists; scheduler does not)
- v2 skill evolution features (version A/B comparison, automatic refinement)
