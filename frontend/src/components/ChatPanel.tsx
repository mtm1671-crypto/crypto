"use client";

import { useEffect, useRef, useState } from "react";
import type { ChatMessage, ToolCall, ToolResult, ApprovalRequest } from "@/lib/types";
import { ApprovalCard, CollapsedApproval } from "./ApprovalCard";
import type { ApprovalOutcome } from "./ApprovalCard";
import { HeroLogo, type EyeStatus } from "./HeroLogo";

interface Props {
  messages: ChatMessage[];
  pendingApprovals: ApprovalRequest[];
  isStreaming: boolean;
  eyeStatus: EyeStatus;
  onSend: (text: string) => void;
  onApprove: (toolCallId: string) => void;
  onDeny: (toolCallId: string) => void;
}

export function ChatPanel({
  messages,
  pendingApprovals,
  isStreaming,
  eyeStatus,
  onSend,
  onApprove,
  onDeny,
}: Props) {
  const [input, setInput] = useState("");
  const [resolvedApprovals, setResolvedApprovals] = useState<
    Map<string, { toolName: string; outcome: ApprovalOutcome }>
  >(new Map());
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pendingApprovals]);

  const handleApproveWrapped = (toolCallId: string) => {
    const approval = pendingApprovals.find((a) => String(a.tool_call.id) === toolCallId);
    if (approval) {
      setResolvedApprovals((prev) => {
        const next = new Map(prev);
        next.set(toolCallId, { toolName: approval.tool_call.name, outcome: "approved" });
        return next;
      });
    }
    onApprove(toolCallId);
  };

  const handleDenyWrapped = (toolCallId: string) => {
    const approval = pendingApprovals.find((a) => String(a.tool_call.id) === toolCallId);
    if (approval) {
      setResolvedApprovals((prev) => {
        const next = new Map(prev);
        next.set(toolCallId, { toolName: approval.tool_call.name, outcome: "denied" });
        return next;
      });
    }
    onDeny(toolCallId);
  };

  const handleSubmit = () => {
    const text = input.trim();
    if (!text || isStreaming) return;
    onSend(text);
    setInput("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center pt-[8vh] h-full text-center">
            <HeroLogo size={320} status={eyeStatus} />
            <h2
              className="text-xl font-semibold text-[var(--text-dim)] mb-2 mt-2"
              style={{ fontFamily: "Cinzel, serif" }}
            >
              Neuromancy
            </h2>
            <p className="text-sm text-[var(--text-faint)] max-w-sm">
              Send a message to begin. The agent can run commands, read files,
              and search the web.
            </p>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`animate-fade-in mb-4 ${
              msg.role === "user" ? "flex justify-end" : ""
            }`}
          >
            {msg.role === "user" ? (
              <div className="max-w-[75%] rounded-2xl rounded-br-sm bg-[var(--accent)] px-4 py-2.5 text-sm text-white">
                {msg.content}
              </div>
            ) : (
              <div className="max-w-[85%]">
                <div className="text-[10px] text-[var(--text-faint)] uppercase tracking-wider mb-1 font-medium">
                  Assistant
                </div>
                <div className="text-sm text-[var(--text)] whitespace-pre-wrap leading-relaxed">
                  {msg.content}
                  {msg.isStreaming && (
                    <span
                      className="inline-block w-2 h-4 bg-[var(--accent)] ml-0.5 rounded-sm"
                      style={{ animation: "pulse-glow 1s ease-in-out infinite" }}
                    />
                  )}
                </div>

                {/* Tool calls */}
                {msg.toolCalls?.map((tc) => (
                  <ToolCallDisplay key={tc.id} toolCall={tc} />
                ))}

                {/* Tool results */}
                {msg.toolResults?.map((tr) => (
                  <ToolResultDisplay key={tr.tool_call_id} result={tr} />
                ))}
              </div>
            )}
          </div>
        ))}

        {/* Resolved approvals (collapsed) */}
        {Array.from(resolvedApprovals.entries()).map(([id, { toolName, outcome }]) => (
          <CollapsedApproval key={id} toolName={toolName} outcome={outcome} />
        ))}

        {/* Pending approvals */}
        {pendingApprovals.map((a) => (
          <ApprovalCard
            key={a.tool_call.id}
            approval={a}
            onApprove={handleApproveWrapped}
            onDeny={handleDenyWrapped}
          />
        ))}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="border-t border-[var(--border)] px-4 py-3 bg-[var(--void-light)]">
        <div className="flex items-end gap-2 max-w-3xl mx-auto">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={isStreaming ? "Agent is thinking..." : "Send a message..."}
            disabled={isStreaming}
            rows={1}
            className="flex-1 resize-none rounded-xl border border-[var(--border)] bg-[var(--void)]
                       px-4 py-2.5 text-sm text-[var(--text)] placeholder-[var(--text-faint)]
                       focus:border-[var(--accent-dim)] focus:outline-none focus:ring-1
                       focus:ring-[var(--accent-dim)] disabled:opacity-50
                       max-h-32 overflow-y-auto"
            style={{ minHeight: "42px" }}
          />
          <button
            onClick={handleSubmit}
            disabled={isStreaming || !input.trim()}
            className="shrink-0 rounded-xl bg-[var(--accent)] p-2.5 text-white
                       hover:bg-[var(--accent-bright)] disabled:opacity-30
                       disabled:cursor-not-allowed transition-colors"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M22 2L11 13" />
              <path d="M22 2L15 22L11 13L2 9L22 2Z" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}

function ToolCallDisplay({ toolCall }: { toolCall: ToolCall }) {
  const statusColors: Record<string, string> = {
    pending: "text-yellow-400",
    approved: "text-green-400",
    denied: "text-red-400",
    running: "text-blue-400",
    completed: "text-green-400",
    failed: "text-red-400",
  };

  return (
    <div className="mt-2 rounded-lg border border-[var(--border)] bg-[var(--void)] p-3 text-xs">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[var(--accent-bright)] font-[Fira_Code] font-medium">
          {toolCall.name}
        </span>
        <span className={`text-[10px] uppercase tracking-wider ${statusColors[toolCall.status]}`}>
          {toolCall.status}
        </span>
      </div>
      <pre className="text-[var(--text-dim)] overflow-x-auto">
        {JSON.stringify(toolCall.arguments, null, 2)}
      </pre>
    </div>
  );
}

function ToolResultDisplay({ result }: { result: ToolResult }) {
  return (
    <div className="mt-2 rounded-lg border border-[var(--border)] bg-[var(--void)] p-3 text-xs">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-green-400 font-medium">Result</span>
        {result.duration_ms != null && (
          <span className="text-[var(--text-faint)]">
            {result.duration_ms.toFixed(0)}ms
          </span>
        )}
      </div>
      {result.error ? (
        <pre className="text-red-400 overflow-x-auto">{result.error}</pre>
      ) : (
        <pre className="text-[var(--text-dim)] overflow-x-auto whitespace-pre-wrap">
          {result.output}
        </pre>
      )}
    </div>
  );
}
