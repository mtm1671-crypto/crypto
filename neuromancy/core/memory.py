"""File-backed memory store for agent learnings."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import MemoryEntry

logger = logging.getLogger(__name__)


class MemoryStore:
    """Persists MemoryEntry objects to a JSON file on disk."""

    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / ".neuromancy" / "memory.json"
        self.entries: list[MemoryEntry] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = [MemoryEntry.model_validate(e) for e in raw]
        except Exception:
            logger.warning("Failed to load memory file at %s, starting fresh", self.path)
            self.entries = []

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = [e.model_dump(mode="json") for e in self.entries]
            content = json.dumps(data, indent=2)
            # Atomic write: write to temp file, then replace
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.path.parent), suffix=".tmp"
            )
            closed = False
            try:
                os.write(fd, content.encode("utf-8"))
                os.close(fd)
                closed = True
                Path(tmp_path).replace(self.path)
            except BaseException:
                if not closed:
                    os.close(fd)
                Path(tmp_path).unlink(missing_ok=True)
                raise
        except Exception:
            logger.warning("Failed to save memory file at %s", self.path, exc_info=True)

    def add(self, entry: MemoryEntry) -> None:
        self.entries.append(entry)
        self._save()

    def reinforce(self, entry_id: str) -> None:
        for entry in self.entries:
            if entry.id == entry_id:
                entry.confidence = min(1.0, entry.confidence + 0.1)
                entry.times_reinforced += 1
                self._save()
                return

    def get_relevant(self, context: str, limit: int = 10) -> list[MemoryEntry]:
        """Return memories relevant to the context string, sorted by confidence."""
        context_lower = context.lower()
        context_words = set(context_lower.split())

        scored: list[tuple[float, MemoryEntry]] = []
        for entry in self.entries:
            content_words = set(entry.content.lower().split())
            overlap = len(context_words & content_words)
            if overlap > 0:
                score = overlap * entry.confidence
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        now = datetime.now(timezone.utc)
        for _, entry in scored[:limit]:
            entry.last_used = now
        if scored:
            self._save()
        return [entry for _, entry in scored[:limit]]

    def find_similar(self, text: str) -> Optional[MemoryEntry]:
        """Find an existing memory with similar content (simple word overlap)."""
        text_lower = text.lower()
        text_words = set(text_lower.split())
        best: Optional[MemoryEntry] = None
        best_ratio = 0.0
        for entry in self.entries:
            entry_words = set(entry.content.lower().split())
            if not entry_words:
                continue
            overlap = len(text_words & entry_words)
            ratio = overlap / max(len(text_words), len(entry_words))
            if ratio > 0.5 and ratio > best_ratio:
                best = entry
                best_ratio = ratio
        return best

    def get_all(self) -> list[MemoryEntry]:
        return list(self.entries)

    def remove(self, entry_id: str) -> None:
        self.entries = [e for e in self.entries if e.id != entry_id]
        self._save()
