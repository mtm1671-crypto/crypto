"use client";

import { useEffect, useRef } from "react";
import type { ActivityEvent } from "@/lib/types";

const typeIcons: Record<string, string> = {
  message_start: "\u25B6",
  text_delta: "\u270E",
  tool_call_start: "\u2699",
  tool_call_delta: "\u22EF",
  tool_result: "\u2713",
  approval_request: "\u26A0",
  error: "\u2717",
  done: "\u25CF",
};

const typeColors: Record<string, string> = {
  message_start: "text-[var(--accent)]",
  text_delta: "text-[var(--text-dim)]",
  tool_call_start: "text-yellow-400",
  tool_call_delta: "text-yellow-400",
  tool_result: "text-green-400",
  approval_request: "text-orange-400",
  error: "text-red-400",
  done: "text-[var(--accent)]",
};

interface Props {
  events: ActivityEvent[];
}

export function ActivityStream({ events }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-3 border-b border-[var(--border)]">
        <h2
          className="text-xs font-semibold tracking-widest uppercase"
          style={{ color: "var(--text-dim)" }}
        >
          Activity
        </h2>
      </div>
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-1">
        {events.length === 0 && (
          <p className="text-xs text-[var(--text-faint)] text-center mt-8">
            Events will appear here...
          </p>
        )}
        {events.map((ev, i) => (
          <div
            key={ev.id}
            className="animate-slide-in flex items-start gap-2 py-1.5 text-xs"
            style={{ animationDelay: `${i * 20}ms` }}
          >
            <span className={`mt-0.5 ${typeColors[ev.type] || "text-[var(--text-dim)]"}`}>
              {typeIcons[ev.type] || "\u25CB"}
            </span>
            <div className="flex-1 min-w-0">
              <span className="text-[var(--text)]">{ev.summary}</span>
              <span className="text-[var(--text-faint)] ml-2">
                {ev.timestamp.toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                })}
              </span>
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
