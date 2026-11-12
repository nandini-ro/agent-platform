"use client";

import { useEffect, useState } from "react";

import * as api from "@/services/api";
import type { Agent } from "@/types";

interface AgentAccessProps {
  agent: Agent | null; // null = creating
  publicBaseUrl: string;
  /** Creating only: the endpoint key entered above, which becomes the access key. */
  draftKey: string;
}

/** The backend's rule for a key it did not generate (chosen_key_problem). */
export function isUsableAccessKey(key: string): boolean {
  return /^[A-Za-z0-9_.~-]{24,200}$/.test(key);
}

/**
 * The agent's public URL and the key that opens it, read-only.
 *
 * The access key is always the custom endpoint's own key: it is set from the
 * key entered above whenever the agent is saved, never edited here.
 */
export function AgentAccess({ agent, publicBaseUrl, draftKey }: AgentAccessProps) {
  // The saved agent's current key, fetched when the panel opens.
  const [issuedKey, setIssuedKey] = useState<string | null>(null);
  // Why a key that exists cannot be shown (one issued before keys were kept).
  const [keyNote, setKeyNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const agentId = agent?.id;
  const hasKey = agent?.has_access_key ?? false;
  useEffect(() => {
    if (!agentId || !hasKey) return;
    let cancelled = false;
    api
      .revealAccessKey(agentId)
      .then((shown) => !cancelled && setIssuedKey(shown.access_key))
      .catch((e) => !cancelled && setKeyNote((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [agentId, hasKey]);

  const url = `${publicBaseUrl}/v1/agents/${agent?.slug ?? "<from-agent-name>"}/chat`;
  const draft = draftKey.trim();
  const shownKey = agent ? issuedKey : isUsableAccessKey(draft) ? draft : null;
  // Values for an external scanner's "E2E Application Testing" form. $INPUT
  // is where the scanner puts each test prompt; $.response is the reply text
  // in this endpoint's JSON (see AgentChatResponse in the backend).
  const e2e = [
    { label: "Application Endpoint URL", value: url },
    {
      label: "Auth Headers (JSON)",
      value: JSON.stringify(
        {
          Authorization: `Bearer ${shownKey ?? "<access-key>"}`,
          "Content-Type": "application/json",
        },
        null,
        2,
      ),
    },
    {
      label: "Request Template (JSON)",
      value: JSON.stringify({ message: "$INPUT" }, null, 2),
    },
    { label: "Response JSON Path", value: "$.response" },
  ];

  const keyStatus = shownKey
    ? null
    : agent && hasKey
      ? keyNote
      : draft && !isUsableAccessKey(draft)
        ? "The endpoint key is too short to use as an access key (24+ letters, digits, - _ . ~)."
        : "Enter the endpoint key above and save the agent to set its access key.";

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
          below. The access key is the custom endpoint key entered above.
        </p>
      </div>

      <ReadOnly
        label="Endpoint"
        value={`POST ${url}`}
        copied={copied === "url"}
        onCopy={() => copy("url", url)}
      />

      {shownKey ? (
        <ReadOnly
          label="Access key"
          value={shownKey}
          copied={copied === "key"}
          onCopy={() => copy("key", shownKey)}
        />
      ) : (
        keyStatus && (
          <p className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            {keyStatus}
          </p>
        )
      )}

      <div className="space-y-2 border-t border-slate-200 pt-3">
        <div>
          <span className="text-xs font-semibold text-slate-700">
            E2E security testing
          </span>
          <p className="text-xs text-slate-500">
            Paste these into a scanner&apos;s E2E application testing settings
            to test this agent through its public URL.
          </p>
        </div>
        {e2e.map((field) => (
          <div key={field.label}>
            <div className="mb-1 flex items-center">
              <span className="text-xs text-slate-600">{field.label}</span>
              <button
                type="button"
                onClick={() => copy(field.label, field.value)}
                className="ml-auto text-xs text-slate-500 hover:text-slate-800"
              >
                {copied === field.label ? "Copied" : "Copy"}
              </button>
            </div>
            <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-xs text-slate-800">
              {field.value}
            </pre>
          </div>
        ))}
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
          {error}
        </div>
      )}
    </section>
  );
}

function ReadOnly({
  label,
  value,
  copied,
  onCopy,
}: {
  label: string;
  value: string;
  copied: boolean;
  onCopy: () => void;
}) {
  return (
    <div>
      <span className="mb-1 block text-xs font-semibold text-slate-700">
        {label}
      </span>
      <div className="flex gap-2">
        <code className="min-w-0 flex-1 truncate rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-xs text-slate-800">
          {value}
        </code>
        <button
          type="button"
          onClick={onCopy}
          className="shrink-0 rounded-lg border border-slate-300 bg-white px-3 text-xs text-slate-600 hover:bg-slate-50"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}
