"use client";

import { useState } from "react";

import type {
  HTTPToolDraft,
  HTTPToolTestResult,
  JSONSchema,
  ParamRow,
} from "@/types";

const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];

interface HTTPToolFormProps {
  saving: boolean;
  error: string | null;
  onSave: (draft: HTTPToolDraft) => Promise<void>;
  onCancel: () => void;
  /** Available only once the tool exists, so it can actually be called. */
  onTest?: (args: Record<string, unknown>) => Promise<HTTPToolTestResult>;
}

/** Builds a JSON Schema from the parameter rows, so nobody has to write one. */
function toSchema(rows: ParamRow[]): JSONSchema {
  const properties: JSONSchema["properties"] = {};
  const required: string[] = [];
  for (const row of rows) {
    const name = row.name.trim();
    if (!name) continue;
    properties[name] = { type: row.type };
    if (row.description.trim()) {
      properties[name].description = row.description.trim();
    }
    if (row.required) required.push(name);
  }
  return {
    type: "object",
    properties,
    ...(required.length ? { required } : {}),
    additionalProperties: false,
  };
}

export function HTTPToolForm({
  saving,
  error,
  onSave,
  onCancel,
  onTest,
}: HTTPToolFormProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [method, setMethod] = useState("GET");
  const [url, setUrl] = useState("");
  const [params, setParams] = useState<ParamRow[]>([
    { name: "", type: "string", description: "", required: true },
  ]);
  const [headers, setHeaders] = useState<{ key: string; value: string }[]>([]);
  const [body, setBody] = useState("");
  const [responsePath, setResponsePath] = useState("");

  const [testArgs, setTestArgs] = useState("{}");
  const [testResult, setTestResult] = useState<HTTPToolTestResult | null>(null);
  const [testing, setTesting] = useState(false);

  const schema = toSchema(params);
  const declared = Object.keys(schema.properties);
  const usedInUrl = [...url.matchAll(/\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g)].map(
    (m) => m[1],
  );
  const undeclared = usedInUrl.filter((p) => !declared.includes(p));

  function draft(): HTTPToolDraft {
    return {
      name: name.trim(),
      description: description.trim(),
      parameters: schema,
      method,
      url_template: url.trim(),
      headers: Object.fromEntries(
        headers.filter((h) => h.key.trim()).map((h) => [h.key.trim(), h.value]),
      ),
      query_template: {},
      body_template: body.trim() || null,
      response_path: responsePath.trim() || null,
      enabled: true,
      timeout_seconds: 10,
    };
  }

  async function runTest() {
    if (!onTest) return;
    setTesting(true);
    setTestResult(null);
    try {
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(testArgs || "{}");
      } catch {
        setTestResult({
          ok: false,
          result: "",
          error: "Test arguments must be valid JSON.",
          duration_ms: 0,
        });
        return;
      }
      setTestResult(await onTest(parsed));
    } finally {
      setTesting(false);
    }
  }

  return (
    <form
      className="space-y-3 rounded-lg border border-slate-300 bg-slate-50 p-3"
      onSubmit={async (e) => {
        e.preventDefault();
        await onSave(draft());
      }}
    >
      <Row label="Tool name" hint="Letters, digits, _ and - only.">
        <input
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="get_weather"
          className={input}
        />
      </Row>

      <Row
        label="Description"
        hint="The model chooses tools by this. Be specific about when to use it."
      >
        <input
          required
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Get the current weather for a city."
          className={input}
        />
      </Row>

      <div className="grid grid-cols-[90px_1fr] gap-2">
        <Row label="Method">
          <select
            value={method}
            onChange={(e) => setMethod(e.target.value)}
            className={input}
          >
            {METHODS.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </Row>
        <Row label="URL" hint="Use {param} to insert a parameter.">
          <input
            required
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://api.example.com/weather/{city}"
            className={input}
          />
        </Row>
      </div>

      {undeclared.length > 0 && (
        <Warning>
          URL uses {undeclared.map((u) => `{${u}}`).join(", ")}, which{" "}
          {undeclared.length === 1 ? "is" : "are"} not declared below. Add{" "}
          {undeclared.length === 1 ? "it" : "them"} as a parameter.
        </Warning>
      )}

      <Row label="Parameters" hint="What the model fills in when calling.">
        <div className="space-y-1.5">
          {params.map((row, i) => (
            <div key={i} className="grid grid-cols-[1fr_78px_1fr_auto] gap-1.5">
              <input
                value={row.name}
                placeholder="city"
                onChange={(e) =>
                  setParams((p) =>
                    p.map((r, j) => (j === i ? { ...r, name: e.target.value } : r)),
                  )
                }
                className={tiny}
              />
              <select
                value={row.type}
                onChange={(e) =>
                  setParams((p) =>
                    p.map((r, j) => (j === i ? { ...r, type: e.target.value } : r)),
                  )
                }
                className={tiny}
              >
                {["string", "number", "integer", "boolean"].map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
              <input
                value={row.description}
                placeholder="City name"
                onChange={(e) =>
                  setParams((p) =>
                    p.map((r, j) =>
                      j === i ? { ...r, description: e.target.value } : r,
                    ),
                  )
                }
                className={tiny}
              />
              <label className="flex items-center gap-1 text-xs text-slate-500">
                <input
                  type="checkbox"
                  checked={row.required}
                  onChange={(e) =>
                    setParams((p) =>
                      p.map((r, j) =>
                        j === i ? { ...r, required: e.target.checked } : r,
                      ),
                    )
                  }
                />
                req
              </label>
            </div>
          ))}
          <button
            type="button"
            onClick={() =>
              setParams((p) => [
                ...p,
                { name: "", type: "string", description: "", required: false },
              ])
            }
            className="text-xs text-slate-500 underline"
          >
            + parameter
          </button>
        </div>
      </Row>

      <Row
        label="Headers"
        hint="Reference a secret as {{env:MY_TOKEN}} — the value is read from the backend, never stored."
      >
        <div className="space-y-1.5">
          {headers.map((h, i) => (
            <div key={i} className="grid grid-cols-2 gap-1.5">
              <input
                value={h.key}
                placeholder="Authorization"
                onChange={(e) =>
                  setHeaders((p) =>
                    p.map((r, j) => (j === i ? { ...r, key: e.target.value } : r)),
                  )
                }
                className={tiny}
              />
              <input
                value={h.value}
                placeholder="Bearer {{env:MY_TOKEN}}"
                onChange={(e) =>
                  setHeaders((p) =>
                    p.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)),
                  )
                }
                className={tiny}
              />
            </div>
          ))}
          <button
            type="button"
            onClick={() => setHeaders((p) => [...p, { key: "", value: "" }])}
            className="text-xs text-slate-500 underline"
          >
            + header
          </button>
        </div>
      </Row>

      {method !== "GET" && (
        <Row label="Request body" hint="JSON, with {param} placeholders.">
          <textarea
            rows={3}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder='{"query": "{q}"}'
            className={`${input} font-mono`}
          />
        </Row>
      )}

      <Row
        label="Response path (optional)"
        hint="Pull one field out of a JSON response, e.g. data.items.0.title"
      >
        <input
          value={responsePath}
          onChange={(e) => setResponsePath(e.target.value)}
          placeholder="data.items.0.title"
          className={input}
        />
      </Row>

      {onTest && (
        <Row label="Try it" hint="Runs the real request with these arguments.">
          <div className="space-y-1.5">
            <textarea
              rows={2}
              value={testArgs}
              onChange={(e) => setTestArgs(e.target.value)}
              className={`${input} font-mono`}
            />
            <button
              type="button"
              onClick={runTest}
              disabled={testing}
              className="rounded border border-slate-300 bg-white px-2 py-1 text-xs disabled:opacity-50"
            >
              {testing ? "Running..." : "Run test"}
            </button>
            {testResult && (
              <pre
                className={`overflow-x-auto whitespace-pre-wrap rounded p-2 text-xs ${
                  testResult.ok
                    ? "bg-emerald-50 text-emerald-900"
                    : "bg-red-50 text-red-900"
                }`}
              >
                {testResult.ok
                  ? `${testResult.result}\n\n(${testResult.duration_ms}ms)`
                  : testResult.error}
              </pre>
            )}
          </div>
        </Row>
      )}

      {error && <Warning tone="error">{error}</Warning>}

      <div className="flex gap-2 pt-1">
        <button
          type="submit"
          disabled={saving || !name.trim() || !url.trim()}
          className="flex-1 rounded bg-slate-800 px-3 py-1.5 text-xs font-medium text-white disabled:bg-slate-300"
        >
          {saving ? "Saving..." : "Save tool"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded border border-slate-300 bg-white px-3 py-1.5 text-xs"
        >
          Cancel
        </button>
      </div>

      <p className="text-xs text-slate-400">
        Saving creates the tool. It is not usable until you tick it on an agent.
      </p>
    </form>
  );
}

const input =
  "w-full rounded border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-slate-500";
const tiny =
  "w-full rounded border border-slate-300 px-1.5 py-1 font-mono text-sm outline-none focus:border-slate-500";

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-0.5 block text-xs font-semibold text-slate-600">
        {label}
      </span>
      {children}
      {hint && <span className="mt-0.5 block text-xs text-slate-400">{hint}</span>}
    </label>
  );
}

function Warning({
  children,
  tone = "warn",
}: {
  children: React.ReactNode;
  tone?: "warn" | "error";
}) {
  const cls =
    tone === "error"
      ? "border-red-200 bg-red-50 text-red-800"
      : "border-amber-200 bg-amber-50 text-amber-900";
  return (
    <div className={`rounded border px-2 py-1.5 text-xs ${cls}`}>{children}</div>
  );
}
