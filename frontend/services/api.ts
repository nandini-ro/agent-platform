/**
 * Backend client.
 *
 * One place that knows the API base URL and response shapes, so components
 * deal in typed values rather than fetch calls.
 */

import type {
  Agent,
  AgentDraft,
  Conversation,
  HTTPTool,
  HTTPToolDraft,
  HTTPToolTestResult,
  MCPDiscoverResult,
  MCPServer,
  MCPServerDraft,
  Message,
  ProbePreset,
  ProviderInfo,
  SecurityRun,
  ToolCall,
  ToolDefinition,
} from "@/types";

const BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(
      `Cannot reach the backend at ${BASE_URL}. Is it running?`,
      0,
    );
  }

  if (!response.ok) {
    throw new ApiError(await describeError(response), response.status);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/** FastAPI returns `detail` as a string or as a list of validation errors. */
async function describeError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d: { loc?: string[]; msg?: string }) =>
          [d.loc?.slice(1).join("."), d.msg].filter(Boolean).join(": "),
        )
        .join("; ");
    }
  } catch {
    /* fall through to the status text */
  }
  return `${response.status} ${response.statusText}`;
}

// -- agents -----------------------------------------------------------------

export const listAgents = () => request<Agent[]>("/api/agents");

export const getAgent = (id: string) => request<Agent>(`/api/agents/${id}`);

export const createAgent = (draft: AgentDraft) =>
  request<Agent>("/api/agents", {
    method: "POST",
    body: JSON.stringify(draft),
  });

export const updateAgent = (id: string, patch: Partial<AgentDraft>) =>
  request<Agent>(`/api/agents/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const deleteAgent = (id: string) =>
  request<void>(`/api/agents/${id}`, { method: "DELETE" });

export const listProviders = () =>
  request<ProviderInfo[]>("/api/agents/providers");

/** Stateless single-turn call - the same endpoint Garak targets. */
export const callAgent = (id: string, message: string) =>
  request<{
    agent_id: string;
    response: string;
    tool_calls: ToolCall[];
    metadata: Record<string, unknown>;
  }>(`/api/agents/${id}/chat`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });

// -- conversations ----------------------------------------------------------

export const listConversations = (agentId?: string) =>
  request<Conversation[]>(
    `/api/conversations${agentId ? `?agent_id=${agentId}` : ""}`,
  );

export const createConversation = (agentId: string) =>
  request<Conversation>("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ agent_id: agentId }),
  });

export const deleteConversation = (id: string) =>
  request<void>(`/api/conversations/${id}`, { method: "DELETE" });

export const listMessages = (conversationId: string) =>
  request<Message[]>(`/api/conversations/${conversationId}/messages`);

// -- tools / MCP ------------------------------------------------------------

export const listTools = () => request<ToolDefinition[]>("/api/tools");

// -- user-defined HTTP tools ------------------------------------------------

export const listHTTPTools = () => request<HTTPTool[]>("/api/tools/http");

export const createHTTPTool = (draft: HTTPToolDraft) =>
  request<HTTPTool>("/api/tools/http", {
    method: "POST",
    body: JSON.stringify(draft),
  });

export const updateHTTPTool = (id: string, patch: Partial<HTTPToolDraft>) =>
  request<HTTPTool>(`/api/tools/http/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const deleteHTTPTool = (id: string) =>
  request<void>(`/api/tools/http/${id}`, { method: "DELETE" });

/** Run a tool once with sample arguments, before granting it to an agent. */
export const testHTTPTool = (id: string, args: Record<string, unknown>) =>
  request<HTTPToolTestResult>(`/api/tools/http/${id}/test`, {
    method: "POST",
    body: JSON.stringify({ arguments: args }),
  });

export const listMCPServers = () => request<MCPServer[]>("/api/mcp/servers");

export const createMCPServer = (payload: MCPServerDraft) =>
  request<MCPServer>("/api/mcp/servers", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const deleteMCPServer = (id: string) =>
  request<void>(`/api/mcp/servers/${id}`, { method: "DELETE" });

export const discoverMCPServer = (id: string) =>
  request<MCPDiscoverResult>(`/api/mcp/servers/${id}/discover`, {
    method: "POST",
  });

// -- security ---------------------------------------------------------------

export const listProbePresets = () =>
  request<ProbePreset[]>("/api/security/probes");

export const listSecurityRuns = (agentId?: string) =>
  request<SecurityRun[]>(
    `/api/security/runs${agentId ? `?agent_id=${agentId}` : ""}`,
  );

export const getSecurityRun = (id: string) =>
  request<SecurityRun>(`/api/security/runs/${id}`);

export const startSecurityRun = (payload: {
  agent_id: string;
  probes?: string;
  generations?: number;
}) =>
  request<SecurityRun>("/api/security/runs", {
    method: "POST",
    body: JSON.stringify(payload),
  });

// -- streaming chat ---------------------------------------------------------

export interface StreamHandlers {
  onToken: (text: string) => void;
  onToolCall: (call: ToolCall) => void;
  onDone: (payload: {
    message_id: string;
    content: string;
    tool_calls: ToolCall[];
    metadata: Record<string, unknown>;
  }) => void;
  onError: (message: string) => void;
}

/**
 * POST a message and consume the SSE response.
 *
 * EventSource cannot issue a POST, so the stream is read off the fetch body and
 * the small amount of SSE framing we need is parsed by hand.
 */
export async function streamMessage(
  conversationId: string,
  content: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(
      `${BASE_URL}/api/conversations/${conversationId}/messages`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
        signal,
      },
    );
  } catch (error) {
    if ((error as Error).name === "AbortError") return;
    handlers.onError(`Cannot reach the backend at ${BASE_URL}.`);
    return;
  }

  if (!response.ok || !response.body) {
    handlers.onError(await describeError(response));
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // An SSE event ends at a blank line, and the spec allows CRLF, LF or CR
      // as the terminator. sse-starlette emits CRLF, so matching only "\n\n"
      // finds no boundary at all and every event is silently dropped.
      let match: RegExpExecArray | null;
      while ((match = FRAME_BOUNDARY.exec(buffer)) !== null) {
        const frame = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        dispatchFrame(frame, handlers);
      }
    }
  } catch (error) {
    if ((error as Error).name !== "AbortError") {
      handlers.onError((error as Error).message);
    }
  }
}

/** Blank-line frame separator: CRLF, LF or CR pairs. */
const FRAME_BOUNDARY = /\r\n\r\n|\n\n|\r\r/;

function dispatchFrame(frame: string, handlers: StreamHandlers): void {
  let event = "message";
  const dataLines: string[] = [];

  for (const line of frame.split(/\r\n|\n|\r/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return;

  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return;
  }

  switch (event) {
    case "token":
      handlers.onToken((payload as { text: string }).text);
      break;
    case "tool":
      handlers.onToolCall(payload as ToolCall);
      break;
    case "done":
      handlers.onDone(payload as Parameters<StreamHandlers["onDone"]>[0]);
      break;
    case "error":
      handlers.onError((payload as { message: string }).message);
      break;
  }
}
