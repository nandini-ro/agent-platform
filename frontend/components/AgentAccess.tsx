"use client";

import { useState } from "react";

import * as api from "@/services/api";
import type { Agent } from "@/types";

interface AgentAccessProps {
  agent: Agent | null; // null = creating; keys need a saved agent
  slug: string;
  onSlugChange: (slug: string) => void;
  publicBaseUrl: string;
  /** A key was issued or revoked, so the parent's copy of the agent is stale. */
  onAccessChanged: () => void;
}

/**
 * The agent's public URL and the key that opens it.
 *
 * The URL name is part of the form and saved with it. The key is not: issuing
 * and revoking take effect immediately, because the plaintext key exists only
 * in the response to that one request and has to be shown right then.
 */
export function AgentAccess({
  agent,
  slug,
  onSlugChange,
  publicBaseUrl,
  onAccessChanged,
}: AgentAccessProps) {
  const [hasKey, setHasKey] = useState(agent?.has_access_key ?? false);
  const [hint, setHint] = useState(agent?.access_key_hint ?? null);
  // Held only until the form closes; it cannot be fetched again.
  const [issuedKey, setIssuedKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const shownSlug = slug.trim() || agent?.slug || "<from-agent-name>";
  const url = `${publicBaseUrl}/v1/agents/${shownSlug}/chat`;
  const slugPending = Boolean(agent && slug.trim() && slug.trim() !== agent.slug);
  const curl = [
    `curl -X POST ${url} \\`,
    `  -H "Authorization: Bearer ${issuedKey ?? "<access-key>"}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{"message": "Hello"}'`,
  ].join("\n");

  async function issue() {
    if (!agent) return;
    if (
      hasKey &&
      !window.confirm(
        "Generate a new key? The current key stops working immediately.",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const issued = await api.issueAccessKey(agent.id);
      setIssuedKey(issued.access_key);
      setHint(issued.access_key_hint);
      setHasKey(true);
      onAccessChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function revoke() {
    if (!agent) return;
    if (
      !window.confirm(
        "Revoke the key? The public URL refuses every call until a new key is generated.",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.revokeAccessKey(agent.id);
      setIssuedKey(null);
      setHint(null);
      setHasKey(false);
      onAccessChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function copy(label: string, text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      window.setTimeout(() => setCopied((c) => (c === label ? null : c)), 1500);
    } catch {
      setError("Could not copy - select the text and copy it by hand.");
    }
  }

  return (
    <section className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
      <div>
        <h3 className="text-xs font-semibold text-slate-700">API access</h3>
        <p className="mt-0.5 text-xs text-slate-500">
          Anyone with this agent&apos;s access key can chat with it at the URL
          below - and do nothing else. It is separate from the admin key.
        </p>
      </div>

      <label className="block">
        <span className="mb-1 block text-xs font-semibold text-slate-700">
          URL name
        </span>
        <input
          value={slug}
          onChange={(e) => onSlugChange(e.target.value.toLowerCase())}
          placeholder={agent?.slug ?? "Derived from the agent name"}
          spellCheck={false}
          className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm outline-none focus:border-slate-500"
        />
        <span className="mt-1 block text-xs text-slate-400">
          Lowercase letters, digits and hyphens.
          {slugPending && " Saved with the agent - the old URL then stops working."}
        </span>
      </label>

      <div>
        <span className="mb-1 block text-xs font-semibold text-slate-700">
          Endpoint
        </span>
        <div className="flex gap-2">
          <code className="min-w-0 flex-1 truncate rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-xs text-slate-800">
            POST {url}
          </code>
          <CopyButton
            label={copied === "url" ? "Copied" : "Copy"}
            onClick={() => copy("url", url)}
          />
        </div>
      </div>

      {agent ? (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span
              className={`text-xs ${hasKey ? "text-emerald-700" : "text-amber-700"}`}
            >
              {hasKey
                ? `✓ Access key active (${hint ?? "agk_"}…)`
                : "No access key - the URL refuses every call."}
            </span>
            <div className="ml-auto flex gap-2">
              {hasKey && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={revoke}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50"
                >
                  Revoke
                </button>
              )}
              <button
                type="button"
                disabled={busy}
                onClick={issue}
                className="rounded-lg bg-slate-800 px-3 py-1.5 text-xs font-medium text-white disabled:bg-slate-300"
              >
                {hasKey ? "Rotate key" : "Generate key"}
              </button>
            </div>
          </div>

          {issuedKey && (
            <div className="space-y-1 rounded-lg border border-amber-300 bg-amber-50 p-3">
              <p className="text-xs font-semibold text-amber-900">
                Copy this key now - it will not be shown again.
              </p>
              <div className="flex gap-2">
                <code className="min-w-0 flex-1 break-all rounded border border-amber-200 bg-white px-2 py-1.5 font-mono text-xs text-slate-800">
                  {issuedKey}
                </code>
                <CopyButton
                  label={copied === "key" ? "Copied" : "Copy"}
                  onClick={() => copy("key", issuedKey)}
                />
              </div>
            </div>
          )}

          <div>
            <div className="mb-1 flex items-center">
              <span className="text-xs font-semibold text-slate-700">
                Example
              </span>
              <button
                type="button"
                onClick={() => copy("curl", curl)}
                className="ml-auto text-xs text-slate-500 hover:text-slate-800"
              >
                {copied === "curl" ? "Copied" : "Copy"}
              </button>
            </div>
            <pre className="overflow-x-auto rounded-lg bg-slate-900 px-3 py-2 font-mono text-xs leading-relaxed text-slate-100">
              {curl}
            </pre>
          </div>
        </div>
      ) : (
        <p className="text-xs text-slate-500">
          Save the agent first, then generate its access key here.
        </p>
      )}

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
          {error}
        </div>
      )}
    </section>
  );
}

function CopyButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="shrink-0 rounded-lg border border-slate-300 bg-white px-3 text-xs text-slate-600 hover:bg-slate-50"
    >
      {label}
    </button>
  );
}
