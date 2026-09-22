"use client";

import type { Agent } from "@/types";

interface AgentSelectorProps {
  agents: Agent[];
  selectedId: string | null;
  onSelect: (agent: Agent) => void;
  onEdit: (agent: Agent) => void;
  onDelete: (agent: Agent) => void;
}

export function AgentSelector({
  agents,
  selectedId,
  onSelect,
  onEdit,
  onDelete,
}: AgentSelectorProps) {
  if (agents.length === 0) {
    return (
      <p className="px-3 py-2 text-xs text-slate-500">
        No agents yet. Create one to start chatting.
      </p>
    );
  }

  return (
    <ul className="space-y-0.5">
      {agents.map((agent) => {
        const selected = agent.id === selectedId;
        return (
          <li key={agent.id} className="group relative">
            <button
              type="button"
              onClick={() => onSelect(agent)}
              className={`w-full rounded-lg px-3 py-2 pr-16 text-left transition ${
                selected
                  ? "bg-slate-700 text-white"
                  : "text-slate-300 hover:bg-slate-800"
              }`}
            >
              <div className="truncate text-sm font-medium">{agent.name}</div>
              <div
                className={`truncate text-xs ${
                  selected ? "text-slate-300" : "text-slate-500"
                }`}
              >
                {agent.model}
              </div>
            </button>

            <div className="absolute right-2 top-2 hidden gap-1 group-hover:flex">
              <IconButton label="Edit" onClick={() => onEdit(agent)}>
                ✎
              </IconButton>
              <IconButton label="Delete" onClick={() => onDelete(agent)}>
                ✕
              </IconButton>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function IconButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className="rounded px-1.5 py-0.5 text-xs text-slate-400 hover:bg-slate-600 hover:text-white"
    >
      {children}
    </button>
  );
}
