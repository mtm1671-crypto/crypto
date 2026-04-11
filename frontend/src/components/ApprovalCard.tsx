"use client";

import { useState } from "react";
import type { ApprovalRequest, RiskLevel } from "@/lib/types";

/* ── Danger pattern detection ── */

const DANGER_PATTERNS: { pattern: RegExp; warning: string }[] = [
  { pattern: /rm\s+-[^\s]*r[^\s]*f|rm\s+-[^\s]*f[^\s]*r/g, warning: "Recursive force-delete can destroy files irreversibly" },
  { pattern: /rm\s+-rf\s+[\/~]/g, warning: "Deleting from root or home directory" },
  { pattern: /\bsudo\b/g, warning: "Runs with elevated privileges" },
  { pattern: /curl\s[^|]*\|\s*(ba)?sh/g, warning: "Piping remote code directly into shell" },
  { pattern: /wget\s[^|]*\|\s*(ba)?sh/g, warning: "Piping remote code directly into shell" },
  { pattern: />\s*\/dev\//g, warning: "Writing to device file" },
  { pattern: /\bchmod\s+777\b/g, warning: "Makes file world-writable" },
  { pattern: /\bdd\s+/g, warning: "Low-level disk write — can overwrite partitions" },
  { pattern: /\bmkfs\b/g, warning: "Formats a filesystem" },
  { pattern: /:(){ :\|:& };:/g, warning: "Fork bomb — will crash the system" },
  { pattern: /--no-preserve-root/g, warning: "Bypasses root directory safety check" },
];

function detectDangers(command: string): { highlights: Set<string>; warnings: string[] } {
  const highlights = new Set<string>();
  const warnings: string[] = [];
  for (const { pattern, warning } of DANGER_PATTERNS) {
    const re = new RegExp(pattern.source, pattern.flags);
    let match;
    while ((match = re.exec(command)) !== null) {
      highlights.add(match[0]);
      if (!warnings.includes(warning)) warnings.push(warning);
    }
  }
  return { highlights, warnings };
}

/** Render command text with dangerous tokens highlighted red */
function HighlightedCommand({ command, highlights }: { command: string; highlights: Set<string> }) {
  if (highlights.size === 0) {
    return <span>{command}</span>;
  }
  // Build a combined regex from all matched tokens
  const escaped = Array.from(highlights).map((s) =>
    s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  );
  const combinedRe = new RegExp(`(${escaped.join("|")})`, "g");
  const parts = command.split(combinedRe);

  return (
    <>
      {parts.map((part, i) =>
        highlights.has(part) ? (
          <span key={i} className="text-red-400 bg-red-400/15 rounded px-0.5">
            {part}
          </span>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </>
  );
}

/* ── Tool descriptions ── */

const TOOL_DESCRIPTIONS: Record<string, string> = {
  run_command: "Executes a shell command on the host machine",
  write_file: "Creates or overwrites a file on disk",
  read_file: "Reads the contents of a file",
  list_directory: "Lists files and folders in a directory",
  web_search: "Searches the web and returns results",
};

/* ── Subcomponents ── */

function ToolNameWithTooltip({ name }: { name: string }) {
  const [showTooltip, setShowTooltip] = useState(false);
  const description = TOOL_DESCRIPTIONS[name];

  return (
    <span
      className="relative text-sm font-medium text-[var(--accent-bright)] font-[Fira_Code] cursor-default"
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      {name}
      {description && showTooltip && (
        <span className="absolute left-0 top-full mt-1 z-20 px-2.5 py-1.5 rounded-md text-[11px] font-[Outfit] font-normal text-[var(--text)] bg-[var(--void-lighter)] border border-[var(--border)] shadow-lg whitespace-nowrap">
          {description}
        </span>
      )}
    </span>
  );
}

function TerminalBlock({ command, highlights }: { command: string; highlights: Set<string> }) {
  return (
    <div className="rounded-md bg-[#0c0b09] border border-[var(--border)] p-3 font-[Fira_Code] text-xs overflow-x-auto">
      <div className="flex items-start gap-2">
        <span className="text-[var(--text-faint)] select-none shrink-0">$</span>
        <span className="text-[var(--text)] whitespace-pre-wrap break-all">
          <HighlightedCommand command={command} highlights={highlights} />
        </span>
      </div>
    </div>
  );
}

function FormattedFields({ args }: { args: Record<string, unknown> }) {
  return (
    <div className="space-y-2">
      {Object.entries(args).map(([key, value]) => (
        <div key={key}>
          <div className="text-[10px] text-[var(--text-faint)] uppercase tracking-wider mb-0.5">
            {key.replace(/_/g, " ")}
          </div>
          <div className="text-xs text-[var(--text)] font-[Fira_Code] bg-[var(--void)] rounded px-2.5 py-1.5 border border-[var(--border)] break-all whitespace-pre-wrap">
            {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
          </div>
        </div>
      ))}
    </div>
  );
}

function DangerBanner({ warnings }: { warnings: string[] }) {
  return (
    <div className="rounded-md border border-red-400/30 bg-red-400/8 px-3 py-2 mb-3">
      <div className="flex items-center gap-1.5 text-red-400 text-[11px] font-medium uppercase tracking-wider mb-1">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
        Warning
      </div>
      {warnings.map((w, i) => (
        <div key={i} className="text-[11px] text-red-300/80 leading-snug">
          {w}
        </div>
      ))}
    </div>
  );
}

/* ── Risk styling ── */

const riskColors: Record<RiskLevel, string> = {
  low: "text-green-400 bg-green-400/10 border-green-400/20",
  medium: "text-yellow-400 bg-yellow-400/10 border-yellow-400/20",
  high: "text-red-400 bg-red-400/10 border-red-400/20",
};

/* ── Collapsed summary ── */

export type ApprovalOutcome = "approved" | "denied";

export function CollapsedApproval({
  toolName,
  outcome,
}: {
  toolName: string;
  outcome: ApprovalOutcome;
}) {
  const isApproved = outcome === "approved";
  return (
    <div className="my-2 flex items-center gap-2 text-xs px-1">
      <span className={isApproved ? "text-green-400" : "text-red-400"}>
        {isApproved ? "\u2713" : "\u2717"}
      </span>
      <span className="text-[var(--text-dim)]">
        {isApproved ? "Approved" : "Denied"}:
      </span>
      <span className="font-[Fira_Code] text-[var(--text)]">{toolName}</span>
    </div>
  );
}

/* ── Main ApprovalCard ── */

interface Props {
  approval: ApprovalRequest;
  onApprove: (toolCallId: string) => void;
  onDeny: (toolCallId: string) => void;
}

export function ApprovalCard({ approval, onApprove, onDeny }: Props) {
  const { tool_call, reason, risk_level } = approval;
  const isShell = tool_call.name === "run_command";
  const command = isShell
    ? String(tool_call.arguments.command || tool_call.arguments.cmd || "")
    : "";
  const { highlights, warnings } = isShell ? detectDangers(command) : { highlights: new Set<string>(), warnings: [] };

  return (
    <div className="my-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4 glow-border-active">
      {/* Header */}
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xs font-medium tracking-wider uppercase text-[var(--text-dim)]">
          Approval Required
        </span>
        <span
          className={`text-[10px] px-2 py-0.5 rounded-full border font-medium uppercase tracking-wider ${riskColors[risk_level]}`}
        >
          {risk_level} risk
        </span>
      </div>

      {/* Tool name with tooltip */}
      <div className="mb-3">
        <ToolNameWithTooltip name={tool_call.name} />
      </div>

      {/* Danger warnings */}
      {warnings.length > 0 && <DangerBanner warnings={warnings} />}

      {/* Arguments display */}
      <div className="mb-3">
        {isShell && command ? (
          <TerminalBlock command={command} highlights={highlights} />
        ) : (
          <FormattedFields args={tool_call.arguments} />
        )}
      </div>

      {/* Reason */}
      <p className="text-[11px] text-[var(--text-faint)] mb-3">{reason}</p>

      {/* Buttons — equal weight */}
      <div className="flex gap-2">
        <button
          onClick={() => onApprove(String(tool_call.id))}
          className="px-4 py-1.5 text-xs font-medium rounded-md border border-green-500/40 text-green-400
                     hover:bg-green-400/10 transition-colors"
        >
          Approve
        </button>
        <button
          onClick={() => onDeny(String(tool_call.id))}
          className="px-4 py-1.5 text-xs font-medium rounded-md border border-red-500/40 text-red-400
                     hover:bg-red-400/10 transition-colors"
        >
          Deny
        </button>
      </div>
    </div>
  );
}
