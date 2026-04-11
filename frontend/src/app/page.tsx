"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Logo } from "@/components/Logo";
import type { EyeStatus } from "@/components/HeroLogo";
import { ChatPanel } from "@/components/ChatPanel";
import { ActivityStream } from "@/components/ActivityStream";
import { SidebarTabs, type Tab } from "@/components/SidebarTabs";
import { SettingsPanel } from "@/components/SettingsPanel";
import { LearningsPanel } from "@/components/LearningsPanel";
import { useWebSocket } from "@/hooks/useWebSocket";
import type {
  ChatMessage,
  ActivityEvent,
  ApprovalRequest,
  StreamEvent,
  ToolCall,
  ToolResult,
} from "@/lib/types";

function genId(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "ws://localhost:8000";

export default function Home() {
  const [sessionId] = useState(() => genId());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [pendingApprovals, setPendingApprovals] = useState<ApprovalRequest[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [activeTab, setActiveTab] = useState<Tab>("activity");
  const [learningsRefreshKey, setLearningsRefreshKey] = useState(0);
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("anthropic/claude-sonnet-4");
  const [eyeStatus, setEyeStatus] = useState<EyeStatus>("idle");
  const stoppedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(288); // 18rem = 288px
  const isResizing = useRef(false);

  // Load saved settings
  useEffect(() => {
    const saved = localStorage.getItem("neuromancy-settings");
    if (saved) {
      try {
        const s = JSON.parse(saved);
        if (s.apiKey) setApiKey(s.apiKey);
        if (s.model) setModel(s.model);
      } catch {}
    }
  }, []);

  const startResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isResizing.current = true;
    const onMouseMove = (ev: MouseEvent) => {
      if (!isResizing.current) return;
      const newWidth = Math.min(Math.max(ev.clientX, 200), 600);
      setSidebarWidth(newWidth);
    };
    const onMouseUp = () => {
      isResizing.current = false;
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  }, []);

  const addEvent = useCallback((type: string, summary: string, data?: Record<string, unknown>) => {
    setEvents((prev) => [
      ...prev,
      {
        id: genId(),
        type: type as ActivityEvent["type"],
        summary,
        timestamp: new Date(),
        data,
      },
    ]);
  }, []);

  const handleEvent = useCallback(
    (event: StreamEvent) => {
      switch (event.type) {
        case "message_start":
          setIsStreaming(true);
          setEyeStatus("active");
          if (stoppedTimerRef.current) {
            clearTimeout(stoppedTimerRef.current);
            stoppedTimerRef.current = null;
          }
          setMessages((prev) => [
            ...prev,
            {
              id: genId(),
              role: "assistant",
              content: "",
              toolCalls: [],
              toolResults: [],
              timestamp: new Date(),
              isStreaming: true,
            },
          ]);
          addEvent("message_start", "Agent started responding");
          break;

        case "text_delta": {
          const text = (event.data as { text?: string }).text || "";
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last?.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                content: last.content + text,
              };
            }
            return updated;
          });
          break;
        }

        case "tool_call_start": {
          const tc = (event.data as { tool_call?: ToolCall }).tool_call;
          if (tc) {
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last?.role === "assistant") {
                updated[updated.length - 1] = {
                  ...last,
                  toolCalls: [...(last.toolCalls || []), tc],
                };
              }
              return updated;
            });
            addEvent("tool_call_start", `Calling tool: ${tc.name}`);
          }
          break;
        }

        case "approval_request": {
          const approval = (event.data as { approval?: ApprovalRequest }).approval;
          if (approval) {
            setPendingApprovals((prev) => [...prev, approval]);
            addEvent(
              "approval_request",
              `Approval needed: ${approval.tool_call.name} (${approval.risk_level})`
            );
          }
          break;
        }

        case "tool_result": {
          const result = (event.data as { result?: ToolResult }).result;
          if (result) {
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last?.role === "assistant") {
                updated[updated.length - 1] = {
                  ...last,
                  toolResults: [...(last.toolResults || []), result],
                };
              }
              return updated;
            });
            const summary = result.error
              ? `Tool error: ${result.error.slice(0, 60)}`
              : `Tool completed${result.duration_ms ? ` (${result.duration_ms.toFixed(0)}ms)` : ""}`;
            addEvent("tool_result", summary);
          }
          break;
        }

        case "error": {
          const msg = (event.data as { message?: string }).message || "Unknown error";
          addEvent("error", msg);
          setIsStreaming(false);
          setEyeStatus("stopped");
          stoppedTimerRef.current = setTimeout(() => setEyeStatus("idle"), 2000);
          break;
        }

        case "done":
          setIsStreaming(false);
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last?.role === "assistant") {
              updated[updated.length - 1] = { ...last, isStreaming: false };
            }
            return updated;
          });
          addEvent("done", "Turn complete");
          setLearningsRefreshKey((k) => k + 1);
          setEyeStatus("stopped");
          stoppedTimerRef.current = setTimeout(() => setEyeStatus("idle"), 2000);
          break;
      }
    },
    [addEvent]
  );

  const { send, connectionState } = useWebSocket(sessionId, BACKEND_URL, handleEvent);

  const handleSend = useCallback(
    (text: string) => {
      setMessages((prev) => [
        ...prev,
        {
          id: genId(),
          role: "user",
          content: text,
          timestamp: new Date(),
        },
      ]);
      send({ type: "message", content: text });
    },
    [send]
  );

  const handleApprove = useCallback(
    (toolCallId: string) => {
      send({ type: "approval", tool_call_id: toolCallId, approved: true });
      setPendingApprovals((prev) =>
        prev.filter((a) => String(a.tool_call.id) !== toolCallId)
      );
      addEvent("tool_call_start", "Tool approved");
    },
    [send, addEvent]
  );

  const handleDeny = useCallback(
    (toolCallId: string) => {
      send({ type: "approval", tool_call_id: toolCallId, approved: false });
      setPendingApprovals((prev) =>
        prev.filter((a) => String(a.tool_call.id) !== toolCallId)
      );
      addEvent("error", "Tool denied");
    },
    [send, addEvent]
  );

  const handleApiKeyChange = useCallback(
    (key: string) => {
      setApiKey(key);
      localStorage.setItem(
        "neuromancy-settings",
        JSON.stringify({ apiKey: key, model })
      );
      send({ type: "config", api_key: key });
    },
    [model, send]
  );

  const handleModelChange = useCallback(
    (m: string) => {
      setModel(m);
      localStorage.setItem(
        "neuromancy-settings",
        JSON.stringify({ apiKey, model: m })
      );
      send({ type: "config", model: m });
    },
    [apiKey, send]
  );

  const handleConnect = useCallback(
    (key: string, m: string) => {
      // Send both key and model over WebSocket so the backend rebuilds the loop
      send({ type: "config", api_key: key, model: m });
    },
    [send]
  );

  const connDot =
    connectionState === "open"
      ? "bg-green-400"
      : connectionState === "connecting"
      ? "bg-yellow-400"
      : "bg-red-400";

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      {/* Sidebar */}
      <div
        className={`relative flex flex-col border-r border-[var(--border)] bg-[var(--void-light)] shrink-0 ${
          sidebarOpen ? "" : "w-0 overflow-hidden"
        }`}
        style={sidebarOpen ? { width: sidebarWidth, transition: isResizing.current ? "none" : "width 0.3s" } : undefined}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-4 border-b border-[var(--border)]">
          <Logo size={28} status={eyeStatus} />
          <h1
            className="text-base font-semibold tracking-wide glow-text"
            style={{ fontFamily: "Cinzel, serif" }}
          >
            Neuromancy
          </h1>
        </div>

        {/* Tabs */}
        <SidebarTabs activeTab={activeTab} onTabChange={setActiveTab} />

        {/* Tab content */}
        <div className="flex-1 min-h-0 overflow-hidden">
          {activeTab === "activity" ? (
            <ActivityStream events={events} />
          ) : activeTab === "learnings" ? (
            <LearningsPanel refreshKey={learningsRefreshKey} />
          ) : (
            <SettingsPanel
              apiKey={apiKey}
              model={model}
              onApiKeyChange={handleApiKeyChange}
              onModelChange={handleModelChange}
              onConnect={handleConnect}
              connectionState={connectionState}
            />
          )}
        </div>

        {/* Bottom bar */}
        <div className="border-t border-[var(--border)] px-4 py-3 flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${connDot}`} />
          <span className="text-[10px] text-[var(--text-faint)] uppercase tracking-wider">
            {connectionState}
          </span>
        </div>

        {/* Resize handle */}
        <div
          onMouseDown={startResize}
          className="absolute top-0 right-0 w-1 h-full cursor-col-resize hover:bg-[var(--accent)] transition-colors z-10"
        />
      </div>

      {/* Main area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <div className="flex items-center gap-2 px-4 py-2 border-b border-[var(--border)] bg-[var(--void-light)]">
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="text-[var(--text-dim)] hover:text-[var(--text)] transition-colors p-1"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 12h18M3 6h18M3 18h18" />
            </svg>
          </button>
          <span className="text-xs text-[var(--text-faint)] font-[Fira_Code]">
            {model.split("/").pop()}
          </span>
          {isStreaming && (
            <span
              className="ml-2 w-1.5 h-1.5 rounded-full bg-[var(--accent)]"
              style={{ animation: "pulse-glow 1s ease-in-out infinite" }}
            />
          )}
        </div>

        {/* Chat */}
        <ChatPanel
          messages={messages}
          pendingApprovals={pendingApprovals}
          isStreaming={isStreaming}
          eyeStatus={eyeStatus}
          onSend={handleSend}
          onApprove={handleApprove}
          onDeny={handleDeny}
        />
      </div>

    </div>
  );
}
