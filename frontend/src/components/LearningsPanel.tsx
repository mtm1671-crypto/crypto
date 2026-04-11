"use client";

import { useCallback, useEffect, useState } from "react";
import type { MemoryEntry, MemoryCategory } from "@/lib/types";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL?.replace(/^ws/, "http") ||
  "http://localhost:8000";

const CATEGORY_LABELS: Record<MemoryCategory, string> = {
  tool_preference: "Tool Pref",
  strategy: "Strategy",
  user_correction: "Correction",
  tool_failure: "Failure",
};

const CATEGORY_COLORS: Record<MemoryCategory, string> = {
  tool_preference: "bg-amber-500/20 text-amber-300 border-amber-500/30",
  strategy: "bg-blue-500/20 text-blue-300 border-blue-500/30",
  user_correction: "bg-purple-500/20 text-purple-300 border-purple-500/30",
  tool_failure: "bg-red-500/20 text-red-300 border-red-500/30",
};

interface Props {
  refreshKey?: number;
}

export function LearningsPanel({ refreshKey }: Props) {
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const fetchMemories = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const resp = await fetch(`${BACKEND_URL}/memories`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setMemories(data.memories || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMemories();
  }, [fetchMemories, refreshKey]);

  const handleDelete = useCallback(
    async (id: string) => {
      setDeleteError(null);
      // Optimistic removal
      setMemories((prev) => prev.filter((m) => m.id !== id));
      try {
        const resp = await fetch(`${BACKEND_URL}/memories/${id}`, { method: "DELETE" });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      } catch (e) {
        // Revert by re-fetching
        setDeleteError(
          e instanceof Error ? e.message : "Failed to delete memory"
        );
        fetchMemories();
      }
    },
    [fetchMemories]
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center h-32 text-[var(--text-faint)] text-xs">
        Loading...
      </div>
    );
  }

  if (error) {
    return (
      <div className="px-4 py-4">
        <div className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
        <button
          onClick={fetchMemories}
          className="mt-2 text-xs text-[var(--accent)] hover:underline"
        >
          Retry
        </button>
      </div>
    );
  }

  if (memories.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-48 px-6 text-center">
        <svg
          width="32"
          height="32"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          className="text-[var(--text-faint)] mb-3"
        >
          <path d="M12 2a7 7 0 0 1 7 7c0 2.38-1.19 4.47-3 5.74V17a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 0 1 7-7z" />
          <path d="M9 21h6M10 17v4M14 17v4" />
        </svg>
        <p className="text-xs text-[var(--text-faint)]">
          No learnings yet — the agent will learn as you interact.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {deleteError && (
        <div className="mx-3 mt-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300 flex items-center justify-between">
          <span>{deleteError}</span>
          <button
            onClick={() => setDeleteError(null)}
            className="text-red-400 hover:text-red-300 ml-2"
          >
            &times;
          </button>
        </div>
      )}
      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
        {memories.map((m) => (
          <div
            key={m.id}
            className="rounded-md border border-[var(--border)] bg-[var(--void)] p-3 group"
          >
            {/* Header: category badge + delete */}
            <div className="flex items-center justify-between mb-1.5">
              <span
                className={`text-[10px] font-medium px-1.5 py-0.5 rounded border ${
                  CATEGORY_COLORS[m.category]
                }`}
              >
                {CATEGORY_LABELS[m.category]}
              </span>
              <button
                onClick={() => handleDelete(m.id)}
                className="opacity-0 group-hover:opacity-100 transition-opacity text-[var(--text-faint)] hover:text-red-400 text-xs"
                title="Delete memory"
              >
                &times;
              </button>
            </div>

            {/* Content */}
            <p className="text-xs text-[var(--text)] leading-relaxed">
              {m.content}
            </p>

            {/* Footer: confidence bar + reinforcements */}
            <div className="flex items-center gap-3 mt-2">
              <div className="flex-1 h-1 rounded-full bg-[var(--border)] overflow-hidden">
                <div
                  className="h-full rounded-full bg-[var(--accent)]"
                  style={{ width: `${m.confidence * 100}%` }}
                />
              </div>
              <span className="text-[10px] text-[var(--text-faint)] whitespace-nowrap">
                {m.times_reinforced > 0
                  ? `${m.times_reinforced}x reinforced`
                  : "new"}
              </span>
            </div>
          </div>
        ))}
      </div>
      <div className="border-t border-[var(--border)] px-3 py-2">
        <button
          onClick={fetchMemories}
          className="text-[10px] text-[var(--accent)] hover:underline uppercase tracking-wider"
        >
          Refresh
        </button>
      </div>
    </div>
  );
}
