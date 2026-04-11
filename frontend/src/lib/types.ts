export type Role = "user" | "assistant" | "system" | "tool";

export type StreamEventType =
  | "message_start"
  | "text_delta"
  | "tool_call_start"
  | "tool_call_delta"
  | "tool_result"
  | "approval_request"
  | "error"
  | "done";

export type ToolCallStatus =
  | "pending"
  | "approved"
  | "denied"
  | "running"
  | "completed"
  | "failed";

export type RiskLevel = "low" | "medium" | "high";

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  status: ToolCallStatus;
}

export interface ToolResult {
  tool_call_id: string;
  output: string;
  error?: string;
  duration_ms?: number;
}

export interface ApprovalRequest {
  tool_call: ToolCall;
  reason: string;
  risk_level: RiskLevel;
}

export interface StreamEvent {
  type: StreamEventType;
  data: Record<string, unknown>;
  session_id: string;
  timestamp: string;
}

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  toolCalls?: ToolCall[];
  toolResults?: ToolResult[];
  timestamp: Date;
  isStreaming?: boolean;
}

export interface ActivityEvent {
  id: string;
  type: StreamEventType;
  summary: string;
  timestamp: Date;
  data?: Record<string, unknown>;
}

export type MemoryCategory =
  | "tool_preference"
  | "strategy"
  | "user_correction"
  | "tool_failure";

export interface MemoryEntry {
  id: string;
  category: MemoryCategory;
  content: string;
  source_session?: string;
  confidence: number;
  times_reinforced: number;
  created_at: string;
  last_used?: string;
}

export interface ModelOption {
  id: string;
  name: string;
}

export const MODELS: ModelOption[] = [
  { id: "anthropic/claude-sonnet-4", name: "Claude Sonnet 4" },
  { id: "anthropic/claude-haiku-4", name: "Claude Haiku 4" },
  { id: "openai/gpt-4o", name: "GPT-4o" },
  { id: "openai/gpt-4o-mini", name: "GPT-4o Mini" },
  { id: "meta-llama/llama-3.1-405b-instruct", name: "Llama 3.1 405B" },
  { id: "google/gemini-2.0-flash-001", name: "Gemini 2.0 Flash" },
];
