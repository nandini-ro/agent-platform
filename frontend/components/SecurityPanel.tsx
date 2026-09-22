"use client";

import { useEffect, useRef, useState } from "react";

import { Panel, Section } from "@/components/ToolsPanel";
import {
  getSecurityRun,
  listProbePresets,
  listSecurityRuns,
  startSecurityRun,
} from "@/services/api";
import type { Agent, ProbePreset, SecurityRun, SecuritySummary } from "@/types";

const TERMINAL = new Set(["completed", "failed"]);

export function SecurityPanel({
  agents,
  selectedAgentId,
  onClose,
}: {
  agents: Agent[];
  selectedAgentId: string | null;
  onClose: () => void;
}) {
  const [agentId, setAgentId] = useState(selectedAgentId ?? agents[0]?.id ?? "");
  const [presets, setPresets] = useState<ProbePreset[]>([]);
  const [probes, setProbes] = useState("");
  const [runs, setRuns] = useState<SecurityRun[]>([]);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    listProbePresets()
      .then((p) => {
        setPresets(p);
        setProbes((current) => current || p[0]?.id || "");
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  useEffect(() => {
    let cancelled = false;
    listSecurityRuns()
      .then((r) => !cancelled && setRuns(r))
      .catch((e) => !cancelled && setError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, []);

  // Poll only while something is in flight, then stop.
  useEffect(() => {
    const active = runs.filter((r) => !TERMINAL.has(r.status));
    if (active.length === 0) return;

    pollTimer.current = setTimeout(async () => {
      const updated = await Promise.all(
        active.map((r) => getSecurityRun(r.id).catch(() => r)),
      );
      setRuns((current) =>
        current.map((r) => updated.find((u) => u.id === r.id) ?? r),
      );
    }, 2000);

    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [runs]);

  async function start() {
    if (!agentId) return;
    setStarting(true);
    setError(null);
    try {
      const run = await startSecurityRun({ agent_id: agentId, probes });
      setRuns((current) => [run, ...current]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  }

  const activePreset = presets.find((p) => p.id === probes);

  return (
    <Panel title="Security testing (Garak)" onClose={onClose}>
      <Section
        title="New run"
        subtitle="Garak runs as a subprocess and attacks the agent over its HTTP endpoint - the same path any external client takes."
      >
        <label className="block">
          <span className="mb-0.5 block text-xs font-semibold text-slate-600">
            Agent
          </span>
          <select
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-slate-500"
          >
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="mb-0.5 block text-xs font-semibold text-slate-600">
            Probes
          </span>
          <select
            value={presets.some((p) => p.id === probes) ? probes : "custom"}
            onChange={(e) => setProbes(e.target.value)}
            className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-slate-500"
          >
            {presets.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
            <option value="custom">Custom spec...</option>
          </select>
          {activePreset && (
            <span className="mt-1 block text-xs text-slate-400">
              {activePreset.description}
            </span>
          )}
        </label>

        <input
          value={probes}
          onChange={(e) => setProbes(e.target.value)}
          placeholder="probes.dan.Dan_11_0"
          className="w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-sm outline-none focus:border-slate-500"
        />

        <button
          type="button"
          onClick={start}
          disabled={starting || !agentId || !probes.trim()}
          className="w-full rounded bg-slate-800 px-3 py-2 text-sm font-medium text-white disabled:bg-slate-300"
        >
          {starting ? "Starting..." : "Start Garak run"}
        </button>

        {error && (
          <div className="rounded border border-red-200 bg-red-50 px-2 py-1.5 text-xs text-red-800">
            {error}
          </div>
        )}
      </Section>

      <Section title="Runs">
        {runs.length === 0 && (
          <p className="text-xs text-slate-400">No runs yet.</p>
        )}
        {runs.map((run) => (
          <RunCard
            key={run.id}
            run={run}
            agentName={agents.find((a) => a.id === run.agent_id)?.name ?? "—"}
          />
        ))}
      </Section>
    </Panel>
  );
}

function RunCard({ run, agentName }: { run: SecurityRun; agentName: string }) {
  const [open, setOpen] = useState(false);
  const summary = run.summary as SecuritySummary;
  const hasSummary = Boolean(summary?.results?.length);

  return (
    <div className="rounded-lg border border-slate-200">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <StatusDot status={run.status} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-medium text-slate-800">
            {agentName}
          </div>
          <div className="truncate font-mono text-xs text-slate-500">
            {run.probes}
          </div>
        </div>
        {hasSummary && (
          <PassRate rate={summary.pass_rate} />
        )}
        <span className="text-xs text-slate-400">{open ? "▾" : "▸"}</span>
      </button>

      {open && (
        <div className="space-y-2 border-t border-slate-200 px-3 py-2">
          {run.status === "running" && (
            <p className="text-xs text-slate-500">
              Garak is running. Results appear when the scan finishes.
            </p>
          )}

          {run.error && (
            <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-red-50 p-2 text-xs text-red-800">
              {run.error}
            </pre>
          )}

          {hasSummary && (
            <>
              <div className="text-xs text-slate-600">
                {summary.total_passed} passed / {summary.total_failed} failed
                across {summary.total_prompts} prompt(s)
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-slate-400">
                    <th className="pb-1 font-semibold">Probe</th>
                    <th className="pb-1 font-semibold">Detector</th>
                    <th className="pb-1 text-right font-semibold">Passed</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {summary.results.map((r, i) => (
                    <tr key={i} className="border-t border-slate-100">
                      <td className="py-1 pr-2">{r.probe}</td>
                      <td className="py-1 pr-2">{r.detector}</td>
                      <td
                        className={`py-1 text-right font-semibold ${
                          r.failed > 0 ? "text-red-600" : "text-emerald-600"
                        }`}
                      >
                        {r.passed}/{r.total}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {summary.hits?.length > 0 && (
                <div className="space-y-1.5">
                  <div className="text-2xs font-semibold uppercase tracking-[0.08em] text-red-600">
                    Example failures
                  </div>
                  {summary.hits.slice(0, 3).map((hit, i) => (
                    <div
                      key={i}
                      className="rounded border border-red-200 bg-red-50 p-2 text-xs"
                    >
                      <div className="font-semibold text-red-800">
                        {hit.detector}
                      </div>
                      <div className="mt-1 text-slate-600">
                        <span className="font-semibold">prompt:</span>{" "}
                        {hit.prompt.slice(0, 180)}…
                      </div>
                      <div className="mt-0.5 text-slate-600">
                        <span className="font-semibold">output:</span>{" "}
                        {hit.output.slice(0, 180)}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {run.report_path && (
            <div className="truncate font-mono text-xs text-slate-400">
              {run.report_path}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function StatusDot({ status }: { status: SecurityRun["status"] }) {
  const color =
    status === "completed"
      ? "bg-emerald-500"
      : status === "failed"
        ? "bg-red-500"
        : "bg-amber-400 animate-pulse";
  return <span className={`h-2 w-2 shrink-0 rounded-full ${color}`} />;
}

function PassRate({ rate }: { rate: number | null }) {
  if (rate === null) return null;
  const pct = Math.round(rate * 100);
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
        pct === 100
          ? "bg-emerald-100 text-emerald-700"
          : pct >= 50
            ? "bg-amber-100 text-amber-700"
            : "bg-red-100 text-red-700"
      }`}
    >
      {pct}% pass
    </span>
  );
}
