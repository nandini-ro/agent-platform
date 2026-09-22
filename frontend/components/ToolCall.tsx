"use client";

import { useState } from "react";

import type { ToolCall } from "@/types";

/** Collapsed record of one tool or MCP invocation, expandable for detail. */
export function ToolCallBadge({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false);

  const tone = call.ok
    ? "border-emerald-200 bg-emerald-50 text-emerald-900"
    : "border-red-200 bg-red-50 text-red-900";

  return (
    <div className={`rounded-lg border text-sm ${tone}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span className="font-mono text-2xs uppercase tracking-[0.08em] opacity-70">
          {call.source}
        </span>
        <span className="font-medium">{call.name}</span>
        {!call.ok && <span className="font-semibold">· refused</span>}
        <span className="ml-auto opacity-60">
          {call.duration_ms}ms {open ? "▾" : "▸"}
        </span>
      </button>

      {open && (
        <div className="space-y-2 border-t border-current/10 px-3 py-2">
          <Field label="Arguments">
            {JSON.stringify(call.arguments, null, 2)}
          </Field>
          <Field label={call.ok ? "Result" : "Error"}>
            {call.ok ? call.result : (call.error ?? call.result)}
          </Field>
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 font-semibold opacity-70">{label}</div>
      <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-white/60 p-2 font-mono text-xs leading-relaxed">
        {children}
      </pre>
    </div>
  );
}
