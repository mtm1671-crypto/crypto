"""SQLite-backed memory store with FTS5 for relevance search.

Replaces the previous JSON file store. The public API is unchanged — any
caller using `add`, `reinforce`, `get_relevant`, `find_similar`, `get_all`,
and `remove` continues to work. On first open, if a legacy `memory.json`
exists next to the database path, its entries are imported and the JSON
file is backed up as `memory.json.pre-sqlite.bak`.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import MemoryCategory, MemoryEntry

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


# Tokens under this length are dropped when building FTS queries. FTS5 will
# happily match on single-character tokens but they produce noisy results and
# inflate the query size for no benefit.
_MIN_TOKEN_LENGTH = 3

# Cap on how many tokens are OR'd together in a single FTS5 MATCH. Real-world
# queries rarely need more, and FTS5 parser performance degrades as the
# expression grows.
_MAX_TOKENS_PER_QUERY = 20


class MemoryStore:
    """Persists :class:`MemoryEntry` objects to a SQLite database with FTS5.

    The store is single-writer single-thread. All methods are synchronous
    because every production caller is already calling them synchronously
    from an asyncio context. If a future caller needs concurrent writes
    from multiple threads, move to `aiosqlite` — but the API surface will
    stay identical.
    """

    def __init__(self, path: Path | None = None):
        resolved = Path(path) if path is not None else Path.home() / ".neuromancy" / "memory.db"
        self.path = resolved
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._maybe_migrate_from_json()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a fresh connection for a single operation.

        Each call opens its own connection and relies on the caller to use
        ``with`` for transaction scoping. This keeps threading concerns
        contained — no shared cursors, no `check_same_thread=False` risk.
        """
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        """Create schema; if the file exists but isn't a valid SQLite db,
        back it up as ``{path}.corrupt`` and start fresh.

        SQLite doesn't validate the file format on ``connect()`` — the first
        query is what fails. The retry-after-backup pattern means a corrupt
        on-disk state (garbage bytes, interrupted write, or a user pointing
        the store at the wrong file) is recoverable without human action.

        Note on connection lifecycle: we use explicit ``close()`` rather than
        ``with`` because sqlite3.Connection's context manager commits but
        does NOT close the connection. On Windows the open handle would lock
        the file and prevent the backup rename.
        """
        conn: sqlite3.Connection | None = None
        try:
            conn = self._connect()
            conn.executescript(_SCHEMA)
            conn.commit()
            return
        except sqlite3.DatabaseError as e:
            if conn is not None:
                conn.close()
                conn = None
            logger.warning(
                "Existing file at %s is not a valid SQLite database: %s — "
                "backing up and starting fresh",
                self.path, e,
            )
            if self.path.exists():
                backup = self.path.with_suffix(self.path.suffix + ".corrupt")
                try:
                    if backup.exists():
                        backup.unlink()
                    self.path.rename(backup)
                except OSError:
                    # If we can't rename, delete — we never want to be stuck
                    # in a state where a corrupt file blocks a fresh store.
                    self.path.unlink(missing_ok=True)
            conn = self._connect()
            try:
                conn.executescript(_SCHEMA)
                conn.commit()
            finally:
                conn.close()
        finally:
            if conn is not None:
                conn.close()

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------

    def _maybe_migrate_from_json(self) -> None:
        """Import entries from a legacy ``memory.json`` next to the db, once.

        The backup side-effect is what provides idempotency: once migrated,
        the legacy file no longer exists, so subsequent opens are no-ops.
        Corrupt legacy files are left in place and logged — we never delete
        user data to hide a parse error.
        """
        legacy = self.path.parent / "memory.json"
        if not legacy.exists():
            return
        # If the caller happened to name their db "memory.json", the legacy
        # path resolves to the db file itself. Don't try to read a SQLite
        # binary as JSON — there's no migration to do.
        try:
            if legacy.resolve() == self.path.resolve():
                return
        except OSError:
            return
        try:
            raw = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(
                "Failed to read legacy %s for migration: %s — leaving file in place",
                legacy, e,
            )
            return
        if not isinstance(raw, list):
            logger.warning("Legacy %s is not a JSON list — skipping migration", legacy)
            return

        imported = 0
        for row in raw:
            try:
                entry = MemoryEntry.model_validate(row)
            except Exception as e:
                logger.debug("Skipping malformed legacy entry: %s", e)
                continue
            self.add(entry)
            imported += 1

        try:
            legacy.rename(legacy.with_suffix(".json.pre-sqlite.bak"))
        except OSError as e:
            logger.warning(
                "Migration imported %d entries but could not rename %s: %s",
                imported, legacy, e,
            )
            return
        logger.info("Migrated %d memories from %s to SQLite", imported, legacy)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, entry: MemoryEntry) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memories
                    (id, category, content, source_session, confidence,
                     times_reinforced, created_at, last_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.category.value,
                    entry.content,
                    entry.source_session,
                    float(entry.confidence),
                    int(entry.times_reinforced),
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

    def remove(self, entry_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (entry_id,))

    def get_all(self) -> list[MemoryEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memories ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    # ------------------------------------------------------------------
    # FTS5 relevance search
    # ------------------------------------------------------------------

    def get_relevant(self, context: str, limit: int = 10) -> list[MemoryEntry]:
        """Return memories relevant to ``context``, confidence-weighted.

        Uses FTS5 ``MATCH`` with a sanitized token query and ranks results
        by ``bm25 * confidence``. Because bm25 scores are negative (lower is
        a stronger match), multiplying by confidence amplifies strong-match
        + high-confidence entries and keeps them at the top of an ASC sort.
        """
        query = self._fts_query_from_text(context)
        if not query:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT m.*, bm25(memories_fts) * m.confidence AS rank_score
                FROM memories m
                JOIN memories_fts ON memories_fts.rowid = m.rowid
                WHERE memories_fts MATCH ?
                ORDER BY rank_score ASC
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()

            if not rows:
                return []

            # Update last_used for returned entries in the same connection
            now_iso = datetime.now(timezone.utc).isoformat()
            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE memories SET last_used = ? WHERE id IN ({placeholders})",
                (now_iso, *ids),
            )
        return [self._row_to_entry(r) for r in rows]

    def find_similar(self, text: str) -> Optional[MemoryEntry]:
        """Return the single best FTS5 match for ``text``, or None."""
        query = self._fts_query_from_text(text)
        if not query:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT m.*, bm25(memories_fts) AS rank_score
                FROM memories m
                JOIN memories_fts ON memories_fts.rowid = m.rowid
                WHERE memories_fts MATCH ?
                ORDER BY rank_score ASC
                LIMIT 1
                """,
                (query,),
            ).fetchone()
        return self._row_to_entry(row) if row else None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
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
    def _fts_query_from_text(text: str) -> str:
        """Build a safe FTS5 MATCH query from arbitrary text.

        FTS5's query syntax treats many characters as operators, so we can't
        hand it user input raw. Strategy: extract word tokens via regex,
        lowercase, drop very short ones, cap the count, and wrap each in
        double quotes to force literal matching. The tokens are OR-joined
        so any overlap produces a match.
        """
        if not text:
            return ""
        tokens = re.findall(r"\w+", text.lower())
        tokens = [t for t in tokens if len(t) >= _MIN_TOKEN_LENGTH]
        if not tokens:
            return ""
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return " OR ".join(f'"{t}"' for t in unique[:_MAX_TOKENS_PER_QUERY])
