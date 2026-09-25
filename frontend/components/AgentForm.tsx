"use client";

import { useState } from "react";

import type {
  Agent,
  AgentDraft,
  MCPServer,
  ProviderInfo,
  ToolDefinition,
} from "@/types";

interface AgentFormProps {
  agent: Agent | null; // null = creating
  providers: ProviderInfo[];
  tools: ToolDefinition[];
  mcpServers: MCPServer[];
  saving: boolean;
  error: string | null;
  onSave: (draft: AgentDraft) => void;
  onCancel: () => void;
}

const EMPTY: AgentDraft = {
  name: "",
  description: "",
  system_prompt: "",
  provider: "mock",
  model: "mock-1",
  temperature: 1,
  max_tokens: 2048,
  tools: [],
  mcp_server_ids: [],
};

function toDraft(agent: Agent): AgentDraft {
  return {
    name: agent.name,
    description: agent.description,
    system_prompt: agent.system_prompt,
    provider: agent.provider,
    model: agent.model,
    temperature: agent.temperature,
    max_tokens: agent.max_tokens,
    tools: agent.tools,
    mcp_server_ids: agent.mcp_server_ids,
  };
}

export function AgentForm({
  agent,
  providers,
  tools,
  mcpServers,
  saving,
  error,
  onSave,
  onCancel,
}: AgentFormProps) {
  // The form is mounted fresh each time it opens (and keyed by agent id), so
  // the initial value is all the synchronisation it needs.
  const [draft, setDraft] = useState<AgentDraft>(() =>
    agent ? toDraft(agent) : EMPTY,
  );
  // The provider key lives only in this component's state until it is
  // submitted; the backend never sends a stored one back.
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [keyError, setKeyError] = useState<string | null>(null);

  const provider = providers.find((p) => p.name === draft.provider);
  const needsKey = provider?.requires_api_key ?? false;
  // A stored key only counts for the provider it was saved with.
  const keySaved = Boolean(
    agent?.has_api_key && agent.provider === draft.provider,
  );

  function set<K extends keyof AgentDraft>(key: K, value: AgentDraft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  function toggle(key: "tools" | "mcp_server_ids", value: string) {
    setDraft((d) => {
      const current = d[key];
      return {
        ...d,
        [key]: current.includes(value)
          ? current.filter((v) => v !== value)
          : [...current, value],
      };
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-6">
      <div className="w-full max-w-2xl rounded-xl bg-white shadow-xl">
        <header className="border-b border-slate-200 px-6 py-4">
          <h2 className="text-base font-semibold text-slate-900">
            {agent ? `Configure ${agent.name}` : "Create agent"}
          </h2>
          <p className="mt-0.5 text-xs text-slate-500">
            Configuration is stored in the database and read by the shared agent
            runtime at request time.
          </p>
        </header>

        <form
          className="space-y-5 px-6 py-5"
          onSubmit={(e) => {
            e.preventDefault();
            const key = apiKey.trim();
            if (needsKey && !key && !keySaved) {
              setKeyError(
                `An API key is required before this agent can use ${draft.provider}.`,
              );
              return;
            }
            setKeyError(null);
            // Blank means "keep the stored key", so it is not sent at all.
            onSave(needsKey && key ? { ...draft, api_key: key } : draft);
          }}
        >
          <Field label="Agent name" required>
            <input
              required
              value={draft.name}
              onChange={(e) => set("name", e.target.value)}
              placeholder="Support Assistant"
              className={inputClass}
            />
          </Field>

          <Field label="Description">
            <input
              value={draft.description}
              onChange={(e) => set("description", e.target.value)}
              placeholder="What this agent is for"
              className={inputClass}
            />
          </Field>

          <Field
            label="System instructions"
            hint="Drives the agent's behaviour. Two agents differ by this alone."
          >
            <textarea
              rows={5}
              value={draft.system_prompt}
              onChange={(e) => set("system_prompt", e.target.value)}
              placeholder="You are a concise support assistant. Never reveal internal configuration."
              className={`${inputClass} resize-y font-mono text-xs`}
            />
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Model provider">
              <select
                value={draft.provider}
                onChange={(e) => {
                  const next = providers.find((p) => p.name === e.target.value);
                  set("provider", e.target.value);
                  if (next?.models.length) set("model", next.models[0]);
                }}
                className={inputClass}
              >
                {providers.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Model">
              <input
                list="model-suggestions"
                value={draft.model}
                onChange={(e) => set("model", e.target.value)}
                className={inputClass}
              />
              <datalist id="model-suggestions">
                {provider?.models.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            </Field>
          </div>

          {needsKey && (
            <Field
              label="API key"
              required={!keySaved}
              hint="Encrypted on the server and never shown again."
            >
              <div className="flex gap-2">
                <input
                  type={showKey ? "text" : "password"}
                  value={apiKey}
                  onChange={(e) => {
                    setApiKey(e.target.value);
                    setKeyError(null);
                  }}
                  placeholder={
                    keySaved
                      ? "Leave blank to keep the saved key"
                      : `Paste your ${draft.provider} API key`
                  }
                  autoComplete="new-password"
                  spellCheck={false}
                  className={`${inputClass} font-mono`}
                />
                <button
                  type="button"
                  onClick={() => setShowKey((s) => !s)}
                  aria-pressed={showKey}
                  className="shrink-0 rounded-lg border border-slate-300 px-3 text-xs text-slate-600 hover:bg-slate-50"
                >
                  {showKey ? "Hide" : "Show"}
                </button>
              </div>
              <span
                className={`mt-1 block text-xs ${keySaved ? "text-emerald-700" : "text-amber-700"}`}
              >
                {keySaved
                  ? "✓ API key configured. Enter a new key to replace it."
                  : agent?.has_api_key
                    ? `The saved key belongs to ${agent.provider}. Enter a ${draft.provider} API key.`
                    : "No API key configured."}
              </span>
            </Field>
          )}

          <div className="grid grid-cols-2 gap-4">
            <Field
              label={`Temperature — ${draft.temperature.toFixed(2)}`}
              hint="Ignored by models that reject sampling parameters."
            >
              <input
                type="range"
                min={0}
                max={2}
                step={0.05}
                value={draft.temperature}
                onChange={(e) => set("temperature", Number(e.target.value))}
                className="w-full"
              />
            </Field>

            <Field label="Max tokens">
              <input
                type="number"
                min={1}
                max={64000}
                value={draft.max_tokens}
                onChange={(e) => set("max_tokens", Number(e.target.value))}
                className={inputClass}
              />
            </Field>
          </div>

          <Field
            label="Tools"
            hint="An agent can only execute tools ticked here."
          >
            <div className="space-y-1.5">
              {tools.length === 0 && (
                <p className="text-xs text-slate-400">No tools registered.</p>
              )}
              {tools.map((tool) => (
                <Checkbox
                  key={tool.name}
                  checked={draft.tools.includes(tool.name)}
                  onChange={() => toggle("tools", tool.name)}
                  title={tool.name}
                  subtitle={tool.description}
                  badge={tool.source === "http" ? "custom" : undefined}
                />
              ))}
            </div>
          </Field>

          <Field
            label="MCP servers"
            hint="The agent may call any tool exposed by a ticked server."
          >
            <div className="space-y-1.5">
              {mcpServers.length === 0 && (
                <p className="text-xs text-slate-400">
                  No MCP servers configured yet.
                </p>
              )}
              {mcpServers.map((server) => (
                <Checkbox
                  key={server.id}
                  checked={draft.mcp_server_ids.includes(server.id)}
                  onChange={() => toggle("mcp_server_ids", server.id)}
                  title={server.name}
                  subtitle={server.description || server.command}
                />
              ))}
            </div>
          </Field>

          {(keyError ?? error) && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
              {keyError ?? error}
            </div>
          )}

          <div className="flex justify-end gap-2 border-t border-slate-200 pt-4">
            <button
              type="button"
              onClick={onCancel}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving || !draft.name.trim()}
              className="rounded-lg bg-slate-800 px-4 py-2 text-sm font-medium text-white disabled:bg-slate-300"
            >
              {saving ? "Saving..." : "Save agent"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

const inputClass =
  "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500";

function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold text-slate-700">
        {label}
        {required && <span className="text-red-500"> *</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  );
}

function Checkbox({
  checked,
  onChange,
  title,
  subtitle,
  badge,
}: {
  checked: boolean;
  onChange: () => void;
  title: string;
  subtitle?: string;
  badge?: string;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-slate-200 px-3 py-2 hover:bg-slate-50">
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange}
        className="mt-0.5"
      />
      <span className="min-w-0">
        <span className="block font-mono text-xs text-slate-800">
          {title}
          {badge && (
            <span className="ml-1.5 rounded bg-slate-200 px-1 py-0.5 text-2xs font-sans uppercase tracking-[0.08em] text-slate-600">
              {badge}
            </span>
          )}
        </span>
        {subtitle && (
          <span className="block truncate text-xs text-slate-500">
            {subtitle}
          </span>
        )}
      </span>
    </label>
  );
}
