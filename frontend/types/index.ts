/** Shapes mirroring the FastAPI schemas in backend/app/schemas. */

export interface Agent {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
  provider: string;
  model: string;
  temperature: number;
  max_tokens: number;
  tools: string[];
  mcp_server_ids: string[];
  created_at: string;
  updated_at: string;
}

export type AgentDraft = Omit<Agent, "id" | "created_at" | "updated_at">;

export interface ProviderInfo {
  name: string;
  available: boolean;
  supports_tools: boolean;
  models: string[];
}

export interface ToolDefinition {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  source: "builtin" | "http";
}

/** A tool defined through the UI, backed by an HTTP request. */
export interface HTTPTool {
  id: string;
  name: string;
  description: string;
  parameters: JSONSchema;
  method: string;
  url_template: string;
  headers: Record<string, string>;
  query_template: Record<string, string>;
  body_template: string | null;
  response_path: string | null;
  enabled: boolean;
  timeout_seconds: number;
  created_at: string;
  updated_at: string;
}

export interface JSONSchema {
  type: "object";
  properties: Record<string, { type: string; description?: string }>;
  required?: string[];
  additionalProperties?: boolean;
}

export type HTTPToolDraft = Omit<
  HTTPTool,
  "id" | "created_at" | "updated_at"
>;

/** One row of the parameter builder, before it becomes a JSON Schema. */
export interface ParamRow {
  name: string;
  type: string;
  description: string;
  required: boolean;
}

export interface HTTPToolTestResult {
  ok: boolean;
  result: string;
  error: string | null;
  duration_ms: number;
}

export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
  result: string;
  source: "local" | "mcp";
  ok: boolean;
  error: string | null;
  duration_ms: number;
}

export interface Conversation {
  id: string;
  agent_id: string;
  title: string;
  created_at: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  meta: {
    tool_calls?: ToolCall[];
    provider?: string;
    model?: string;
    usage?: Record<string, number>;
    iterations?: number;
  };
  created_at: string;
}

export type MCPTransport = "stdio" | "http";

export interface MCPServer {
  id: string;
  name: string;
  slug: string;
  description: string;
  transport: MCPTransport;
  /** stdio: the local subprocess to launch. */
  command: string;
  args: string[];
  cwd: string | null;
  env_keys: string[];
  /** http: the remote streamable-HTTP endpoint. */
  url: string;
  /** Values may reference a secret as {{env:NAME}}. */
  headers: Record<string, string>;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

/** What the Add-server form submits; the transport decides which half matters. */
export interface MCPServerDraft {
  name: string;
  description?: string;
  transport: MCPTransport;
  command?: string;
  args?: string[];
  cwd?: string | null;
  url?: string;
  headers?: Record<string, string>;
}

export interface MCPTool {
  server_id: string;
  server_slug: string;
  tool_name: string;
  qualified_name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface MCPDiscoverResult {
  server_id: string;
  connected: boolean;
  tools: MCPTool[];
  error: string | null;
}

export interface ProbePreset {
  id: string;
  label: string;
  description: string;
}

export interface SecurityResult {
  probe: string;
  detector: string;
  passed: number;
  failed: number;
  total: number;
  pass_rate: number | null;
}

export interface SecurityHit {
  probe: string;
  detector: string;
  score: number | null;
  prompt: string;
  output: string;
}

export interface SecuritySummary {
  total_probes: number;
  total_prompts: number;
  total_passed: number;
  total_failed: number;
  pass_rate: number | null;
  results: SecurityResult[];
  hits: SecurityHit[];
}

export interface SecurityRun {
  id: string;
  agent_id: string;
  status: "queued" | "running" | "completed" | "failed";
  probes: string;
  generations: number;
  report_path: string | null;
  summary: SecuritySummary | Record<string, never>;
  error: string | null;
  log_tail: string | null;
  created_at: string;
  finished_at: string | null;
}

/** An assistant turn still being streamed, not yet persisted. */
export interface PendingMessage {
  content: string;
  toolCalls: ToolCall[];
}
