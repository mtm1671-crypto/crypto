"use client";

import { useCallback, useState } from "react";
import { MODELS } from "@/lib/types";

const BACKEND_HTTP =
  (process.env.NEXT_PUBLIC_BACKEND_URL || "ws://localhost:8000").replace(
    /^ws/,
    "http"
  );

interface Props {
  apiKey: string;
  model: string;
  onApiKeyChange: (key: string) => void;
  onModelChange: (model: string) => void;
  onConnect: (apiKey: string, model: string) => void;
  connectionState: string;
}

type TestStatus = "idle" | "testing" | "success" | "error";

export function SettingsPanel({
  apiKey,
  model,
  onApiKeyChange,
  onModelChange,
  onConnect,
  connectionState,
}: Props) {
  const [localKey, setLocalKey] = useState(apiKey);
  const [localModel, setLocalModel] = useState(model);
  const [testStatus, setTestStatus] = useState<TestStatus>("idle");
  const [testMessage, setTestMessage] = useState("");

  const isPreset = MODELS.some((m) => m.id === localModel);

  const connDot =
    connectionState === "open"
      ? "bg-green-400"
      : connectionState === "connecting"
      ? "bg-yellow-400"
      : "bg-red-400";

  const handleConnect = useCallback(async () => {
    if (!localKey.trim()) {
      setTestStatus("error");
      setTestMessage("Enter an API key first.");
      return;
    }
    if (!localModel.trim()) {
      setTestStatus("error");
      setTestMessage("Enter a model ID first.");
      return;
    }

    setTestStatus("testing");
    setTestMessage("Testing connection...");

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);

    try {
      const resp = await fetch(`${BACKEND_HTTP}/test-connection`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: localKey, model: localModel }),
        signal: controller.signal,
      });
      const data = await resp.json();

      if (data.ok) {
        setTestStatus("success");
        setTestMessage(`Connected — ${data.model.split("/").pop()}`);
        // Apply the config
        onApiKeyChange(localKey);
        onModelChange(localModel);
        onConnect(localKey, localModel);
      } else {
        setTestStatus("error");
        setTestMessage(data.error || "Connection failed.");
      }
    } catch (e) {
      setTestStatus("error");
      if (e instanceof DOMException && e.name === "AbortError") {
        setTestMessage("Connection test timed out after 30s");
      } else {
        setTestMessage(
          e instanceof Error ? e.message : "Could not reach backend."
        );
      }
    } finally {
      clearTimeout(timeoutId);
    }
  }, [localKey, localModel, onApiKeyChange, onModelChange, onConnect]);

  const statusColor =
    testStatus === "success"
      ? "text-green-400"
      : testStatus === "error"
      ? "text-red-400"
      : testStatus === "testing"
      ? "text-yellow-400"
      : "text-[var(--text-faint)]";

  const buttonDisabled = testStatus === "testing";

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex-1 min-h-0 overflow-y-auto px-4 py-4 space-y-5">
        {/* API Key */}
        <label className="block">
          <span className="text-xs font-medium tracking-wider uppercase text-[var(--text-dim)]">
            OpenRouter API Key
          </span>
          <input
            type="password"
            value={localKey}
            onChange={(e) => {
              setLocalKey(e.target.value);
              setTestStatus("idle");
            }}
            placeholder="sk-or-..."
            className="mt-1.5 w-full rounded-md border border-[var(--border)] bg-[var(--void)]
                       px-3 py-2 text-sm text-[var(--text)] placeholder-[var(--text-faint)]
                       focus:border-[var(--accent-dim)] focus:outline-none focus:ring-1
                       focus:ring-[var(--accent-dim)] font-[Fira_Code]"
          />
          <span className="text-[10px] text-[var(--text-faint)] mt-1 block">
            Get one at openrouter.ai/keys
          </span>
        </label>

        {/* Model */}
        <div>
          <span className="text-xs font-medium tracking-wider uppercase text-[var(--text-dim)] block mb-1.5">
            Model
          </span>
          {/* Dropdown for presets */}
          <select
            value={isPreset ? localModel : "__custom__"}
            onChange={(e) => {
              if (e.target.value !== "__custom__") {
                setLocalModel(e.target.value);
                setTestStatus("idle");
              }
            }}
            className="w-full rounded-md border border-[var(--border)] bg-[var(--void)]
                       px-3 py-2 text-sm text-[var(--text)]
                       focus:border-[var(--accent-dim)] focus:outline-none focus:ring-1
                       focus:ring-[var(--accent-dim)]"
          >
            {MODELS.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
            <option value="__custom__">Custom model...</option>
          </select>
          {/* Text input for custom model ID */}
          <input
            type="text"
            value={localModel}
            onChange={(e) => {
              setLocalModel(e.target.value);
              setTestStatus("idle");
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleConnect();
            }}
            placeholder="e.g. mistralai/mistral-7b-instruct:free"
            className="mt-1.5 w-full rounded-md border border-[var(--border)] bg-[var(--void)]
                       px-3 py-2 text-sm text-[var(--text)] placeholder-[var(--text-faint)]
                       focus:border-[var(--accent-dim)] focus:outline-none focus:ring-1
                       focus:ring-[var(--accent-dim)] font-[Fira_Code]"
          />
          <span className="text-[10px] text-[var(--text-faint)] mt-1 block">
            Any OpenRouter model ID — type or pick from dropdown
          </span>
        </div>

        {/* Connection Status */}
        <div>
          <span className="text-xs font-medium tracking-wider uppercase text-[var(--text-dim)] block mb-2">
            Connection
          </span>
          <div className="flex items-center gap-2 rounded-md border border-[var(--border)] bg-[var(--void)] px-3 py-2">
            <div className={`w-2 h-2 rounded-full ${connDot}`} />
            <span className="text-xs text-[var(--text)]">{connectionState}</span>
          </div>
        </div>
      </div>

      {/* Bottom: test status + connect button — always visible */}
      <div className="shrink-0 border-t border-[var(--border)] px-4 py-3 space-y-2">
        {testMessage && (
          <div className={`text-xs ${statusColor} flex items-center gap-1.5`}>
            {testStatus === "testing" && (
              <span
                className="inline-block w-2 h-2 rounded-full bg-yellow-400"
                style={{ animation: "pulse-glow 1s ease-in-out infinite" }}
              />
            )}
            {testStatus === "success" && <span>&#10003;</span>}
            {testStatus === "error" && <span>&#10007;</span>}
            <span>{testMessage}</span>
          </div>
        )}
        <button
          onClick={handleConnect}
          disabled={buttonDisabled}
          className="w-full rounded-md py-2 text-sm font-medium transition-colors
                     bg-[var(--accent)] text-white hover:bg-[var(--accent-bright)]
                     disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {testStatus === "testing" ? "Testing..." : "Connect"}
        </button>
      </div>
    </div>
  );
}
