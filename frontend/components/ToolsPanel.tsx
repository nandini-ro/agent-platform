"use client";

import { useEffect, useState } from "react";

import { HTTPToolForm } from "@/components/HTTPToolForm";
import {
  createHTTPTool,
  deleteHTTPTool,
  listHTTPTools,
  testHTTPTool,
} from "@/services/api";
import type {
  HTTPTool,
  HTTPToolDraft,
  MCPDiscoverResult,
  MCPServer,
  MCPServerDraft,
  MCPTransport,
  ToolDefinition,
} from "@/types";

interface ToolsPanelProps {
  tools: ToolDefinition[];
  servers: MCPServer[];
  onClose: () => void;
  onDiscover: (id: string) => Promise<MCPDiscoverResult>;
  onAddServer: (payload: MCPServerDraft) => Promise<void>;
  onDeleteServer: (id: string) => Promise<void>;
  /** Refresh the agent form's tool list after a custom tool is added. */
  onToolsChanged: () => void;
}

export function ToolsPanel({
  tools,
  servers,
  onClose,
  onDiscover,
  onAddServer,
  onDeleteServer,
  onToolsChanged,
}: ToolsPanelProps) {
  const [discovered, setDiscovered] = useState<
    Record<string, MCPDiscoverResult>
  >({});
  const [busy, setBusy] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  // Defaults to the remote path: it needs no code and no process on this box.
  const [transport, setTransport] = useState<MCPTransport>("http");
  const [form, setForm] = useState({
    name: "",
    description: "",
    command: "",
    args: "",
    cwd: "",
    url: "",
    headers: "",
  });
  const [formError, setFormError] = useState<string | null>(null);

  // User-defined HTTP tools
  const [httpTools, setHttpTools] = useState<HTTPTool[]>([]);
  const [showToolForm, setShowToolForm] = useState(false);
  const [savingTool, setSavingTool] = useState(false);
  const [toolError, setToolError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listHTTPTools()
      .then((t) => !cancelled && setHttpTools(t))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  async function saveTool(draft: HTTPToolDraft) {
    setSavingTool(true);
    setToolError(null);
    try {
      await createHTTPTool(draft);
      setHttpTools(await listHTTPTools());
      setShowToolForm(false);
      onToolsChanged();
    } catch (e) {
      setToolError((e as Error).message);
    } finally {
      setSavingTool(false);
    }
  }

  async function removeTool(id: string) {
    await deleteHTTPTool(id);
    setHttpTools(await listHTTPTools());
    onToolsChanged();
  }

  async function discover(id: string) {
    setBusy(id);
    try {
      const result = await onDiscover(id);
      setDiscovered((d) => ({ ...d, [id]: result }));
    } finally {
      setBusy(null);
    }
  }

  async function addServer(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    setBusy("add");
    try {
      // Only the half the transport uses is sent; the backend rejects a
      // stdio server with no command and an http server with no URL.
      await onAddServer(
        transport === "stdio"
          ? {
              name: form.name,
              description: form.description,
              transport,
              command: form.command,
              // Whitespace-separated, which covers the interpreter + script case.
              args: form.args.trim() ? form.args.trim().split(/\s+/) : [],
              cwd: form.cwd.trim() || null,
            }
          : {
              name: form.name,
              description: form.description,
              transport,
              url: form.url.trim(),
              headers: parseHeaders(form.headers),
            },
      );
      setForm({
        name: "",
        description: "",
        command: "",
        args: "",
        cwd: "",
        url: "",
        headers: "",
      });
      setShowAdd(false);
    } catch (error) {
      setFormError((error as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Panel title="Tools & MCP" onClose={onClose}>
      <Section
        title="Built-in tools"
        subtitle="Defined in code in the backend tool registry. Assign them per agent."
      >
        {tools
          .filter((tool) => tool.source === "builtin")
          .map((tool) => (
          <div
            key={tool.name}
            className="rounded-lg border border-slate-200 px-3 py-2"
          >
            <div className="font-mono text-sm font-medium text-slate-800">
              {tool.name}
            </div>
            <div className="mt-0.5 text-xs text-slate-500">
              {tool.description}
            </div>
          </div>
          ))}
      </Section>

      <Section
        title="Custom HTTP tools"
        subtitle="Define a tool here and it becomes assignable to any agent. It can only make an outbound HTTP request - never run code."
        action={
          <button
            type="button"
            onClick={() => {
              setShowToolForm((v) => !v);
              setToolError(null);
            }}
            className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50"
          >
            {showToolForm ? "Cancel" : "+ New tool"}
          </button>
        }
      >
        {showToolForm && (
          <HTTPToolForm
            saving={savingTool}
            error={toolError}
            onSave={saveTool}
            onCancel={() => setShowToolForm(false)}
          />
        )}

        {httpTools.length === 0 && !showToolForm && (
          <p className="text-xs text-slate-400">
            No custom tools yet. They are defined here and granted per agent.
          </p>
        )}

        {httpTools.map((tool) => (
          <CustomToolRow
            key={tool.id}
            tool={tool}
            onDelete={() => removeTool(tool.id)}
          />
        ))}
      </Section>

      <Section
        title="MCP servers"
        subtitle="Remote servers are reached by URL; local ones are launched as subprocesses. Either way, their tools are granted per agent."
        action={
          <button
            type="button"
            onClick={() => setShowAdd((v) => !v)}
            className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50"
          >
            {showAdd ? "Cancel" : "+ Add server"}
          </button>
        }
      >
        {showAdd && (
          <form
            onSubmit={addServer}
            className="space-y-2 rounded-lg border border-slate-300 bg-slate-50 p-3"
          >
            <div className="flex gap-1 rounded-lg bg-slate-200 p-0.5">
              {(
                [
                  ["http", "Remote (HTTP)"],
                  ["stdio", "Local (stdio)"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setTransport(value)}
                  className={`flex-1 rounded-md px-2 py-1 text-xs font-medium transition ${
                    transport === value
                      ? "bg-white text-slate-800 shadow-sm"
                      : "text-slate-500 hover:text-slate-700"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            <p className="text-xs leading-relaxed text-slate-500">
              {transport === "http"
                ? "Connects to an MCP server someone else runs. No code and no process on this machine."
                : "Launches a program on this machine and talks to it over stdio. The command must already exist on the server."}
            </p>

            {(transport === "http"
              ? ([
                  ["name", "Name", "Remote Knowledge Base", true],
                  ["url", "URL", "https://example.com/mcp", true],
                  [
                    "headers",
                    "Headers (optional, one per line)",
                    "Authorization: Bearer {{env:MCP_TOKEN}}",
                    false,
                  ],
                  ["description", "Description (optional)", "Hosted KB", false],
                ] as const)
              : ([
                  ["name", "Name", "Demo Knowledge Base", true],
                  ["command", "Command", "/abs/path/to/.venv/bin/python", true],
                  [
                    "args",
                    "Arguments",
                    "/abs/path/to/mcp_servers/demo_server.py",
                    false,
                  ],
                  [
                    "cwd",
                    "Working directory (optional)",
                    "/abs/path/to/backend",
                    false,
                  ],
                  [
                    "description",
                    "Description (optional)",
                    "Toy KB over stdio",
                    false,
                  ],
                ] as const)
            ).map(([key, label, placeholder, required]) => (
              <label key={key} className="block">
                <span className="mb-0.5 block text-xs font-semibold text-slate-600">
                  {label}
                </span>
                {key === "headers" ? (
                  <textarea
                    rows={2}
                    value={form.headers}
                    placeholder={placeholder}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, headers: e.target.value }))
                    }
                    className="w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-sm outline-none focus:border-slate-500"
                  />
                ) : (
                  <input
                    required={required}
                    value={form[key]}
                    placeholder={placeholder}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, [key]: e.target.value }))
                    }
                    className="w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-sm outline-none focus:border-slate-500"
                  />
                )}
              </label>
            ))}

            {transport === "http" && (
              <p className="text-xs leading-relaxed text-slate-400">
                Write a secret as{" "}
                <code className="font-mono">{"{{env:NAME}}"}</code> — the name is
                stored, the value is read from the backend at connect time.
              </p>
            )}
            {formError && (
              <div className="rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-800">
                {formError}
              </div>
            )}
            <button
              type="submit"
              disabled={busy === "add"}
              className="w-full rounded bg-slate-800 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
            >
              {busy === "add" ? "Adding..." : "Add server"}
            </button>
          </form>
        )}

        {servers.length === 0 && !showAdd && (
          <p className="text-xs text-slate-400">
            No MCP servers configured. Two ready-made ones ship in{" "}
            <code className="font-mono">backend/mcp_servers/</code> — one stdio,
            one HTTP.
          </p>
        )}

        {servers.map((server) => {
          const result = discovered[server.id];
          return (
            <div
              key={server.id}
              className="space-y-2 rounded-lg border border-slate-200 px-3 py-2"
            >
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-slate-800">
                    {server.name}
                    <span className="ml-1.5 font-mono text-xs text-slate-400">
                      {server.slug}
                    </span>
                    <span className="ml-1.5 rounded bg-slate-100 px-1 py-0.5 text-2xs font-semibold uppercase tracking-[0.08em] text-slate-500">
                      {server.transport}
                    </span>
                  </div>
                  <div className="truncate font-mono text-xs text-slate-500">
                    {server.transport === "http"
                      ? server.url
                      : `${server.command} ${server.args.join(" ")}`}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => discover(server.id)}
                  disabled={busy === server.id}
                  className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50"
                >
                  {busy === server.id ? "..." : "Discover"}
                </button>
                <button
                  type="button"
                  onClick={() => onDeleteServer(server.id)}
                  className="rounded px-1.5 py-1 text-xs text-slate-400 hover:bg-red-50 hover:text-red-600"
                >
                  ✕
                </button>
              </div>

              {result && !result.connected && (
                <div className="rounded border border-red-200 bg-red-50 px-2 py-1.5 text-xs text-red-800">
                  {result.error}
                </div>
              )}

              {result?.connected && (
                <div className="space-y-1">
                  <div className="text-2xs font-semibold uppercase tracking-[0.08em] text-emerald-700">
                    {result.tools.length} tool(s) discovered
                  </div>
                  {result.tools.map((tool) => (
                    <div
                      key={tool.qualified_name}
                      className="rounded bg-slate-50 px-2 py-1"
                    >
                      <div className="font-mono text-xs text-slate-700">
                        {tool.qualified_name}
                      </div>
                      <div className="text-xs text-slate-500">
                        {tool.description.split("\n")[0]}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </Section>
    </Panel>
  );
}

/** "Name: value" per line. Blank lines and lines without a colon are ignored. */
function parseHeaders(text: string): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const at = line.indexOf(":");
    if (at <= 0) continue;
    const name = line.slice(0, at).trim();
    if (name) headers[name] = line.slice(at + 1).trim();
  }
  return headers;
}

function CustomToolRow({
  tool,
  onDelete,
}: {
  tool: HTTPTool;
  onDelete: () => void;
}) {
  const [args, setArgs] = useState("{}");
  const [result, setResult] = useState<string | null>(null);
  const [ok, setOk] = useState(true);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    try {
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(args || "{}");
      } catch {
        setOk(false);
        setResult("Arguments must be valid JSON.");
        return;
      }
      const r = await testHTTPTool(tool.id, parsed);
      setOk(r.ok);
      setResult(r.ok ? `${r.result}\n\n(${r.duration_ms}ms)` : r.error);
    } finally {
      setBusy(false);
    }
  }

  const params = Object.keys(tool.parameters?.properties ?? {});

  return (
    <div className="space-y-2 rounded-lg border border-slate-200 px-3 py-2">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="font-mono text-sm font-medium text-slate-800">
            {tool.name}
          </div>
          <div className="truncate text-xs text-slate-500">
            {tool.description}
          </div>
          <div className="truncate font-mono text-xs text-slate-400">
            {tool.method} {tool.url_template}
          </div>
          {params.length > 0 && (
            <div className="mt-0.5 font-mono text-xs text-slate-400">
              params: {params.join(", ")}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={onDelete}
          className="rounded px-1.5 py-1 text-xs text-slate-400 hover:bg-red-50 hover:text-red-600"
        >
          ✕
        </button>
      </div>

      <div className="flex gap-1.5">
        <input
          value={args}
          onChange={(e) => setArgs(e.target.value)}
          className="flex-1 rounded border border-slate-300 px-2 py-1 font-mono text-xs outline-none focus:border-slate-500"
        />
        <button
          type="button"
          onClick={run}
          disabled={busy}
          className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50"
        >
          {busy ? "..." : "Test"}
        </button>
      </div>

      {result && (
        <pre
          className={`overflow-x-auto whitespace-pre-wrap rounded p-2 text-xs ${
            ok ? "bg-emerald-50 text-emerald-900" : "bg-red-50 text-red-900"
          }`}
        >
          {result}
        </pre>
      )}
    </div>
  );
}

export function Panel({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-900/40">
      <div className="flex h-full w-full max-w-lg flex-col bg-white shadow-xl">
        <header className="flex items-center border-b border-slate-200 px-5 py-3.5">
          <h2 className="text-base font-semibold tracking-tight text-slate-900">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto rounded px-2 py-1 text-sm text-slate-400 hover:bg-slate-100"
          >
            ✕
          </button>
        </header>
        <div className="flex-1 space-y-6 overflow-y-auto px-5 py-4">
          {children}
        </div>
      </div>
    </div>
  );
}

export function Section({
  title,
  subtitle,
  action,
  children,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-2 flex items-start gap-2">
        <div className="flex-1">
          <h3 className="text-2xs font-semibold uppercase tracking-[0.08em] text-slate-500">
            {title}
          </h3>
          {subtitle && (
            <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>
          )}
        </div>
        {action}
      </div>
      <div className="space-y-2">{children}</div>
    </section>
  );
}
