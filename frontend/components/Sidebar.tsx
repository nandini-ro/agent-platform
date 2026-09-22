"use client";

import { AgentSelector } from "@/components/AgentSelector";
import type { Agent, Conversation } from "@/types";

interface SidebarProps {
  agents: Agent[];
  conversations: Conversation[];
  selectedAgent: Agent | null;
  selectedConversationId: string | null;
  onCreateAgent: () => void;
  onSelectAgent: (agent: Agent) => void;
  onEditAgent: (agent: Agent) => void;
  onDeleteAgent: (agent: Agent) => void;
  onNewChat: () => void;
  onSelectConversation: (conversation: Conversation) => void;
  onOpenTools: () => void;
  onOpenSecurity: () => void;
}

export function Sidebar({
  agents,
  conversations,
  selectedAgent,
  selectedConversationId,
  onCreateAgent,
  onSelectAgent,
  onEditAgent,
  onDeleteAgent,
  onNewChat,
  onSelectConversation,
  onOpenTools,
  onOpenSecurity,
}: SidebarProps) {
  return (
    <aside className="flex h-full w-72 shrink-0 flex-col bg-slate-900 text-slate-100">
      <div className="px-4 py-4">
        <h1 className="text-base font-semibold tracking-tight">Agent Platform</h1>
        <p className="text-xs text-slate-500">
          Configurable agents · tools · MCP
        </p>
      </div>

      <div className="px-3">
        <button
          type="button"
          onClick={onCreateAgent}
          className="w-full rounded-lg border border-slate-700 px-3 py-2 text-sm font-medium text-slate-200 hover:bg-slate-800"
        >
          + Create Agent
        </button>
      </div>

      <div className="mt-5 min-h-0 flex-1 overflow-y-auto px-3">
        <SectionLabel>Agents</SectionLabel>
        <AgentSelector
          agents={agents}
          selectedId={selectedAgent?.id ?? null}
          onSelect={onSelectAgent}
          onEdit={onEditAgent}
          onDelete={onDeleteAgent}
        />

        {selectedAgent && (
          <>
            <div className="mt-5 flex items-center gap-2">
              <SectionLabel>Conversations</SectionLabel>
              <button
                type="button"
                onClick={onNewChat}
                className="ml-auto mb-1.5 rounded px-1.5 py-0.5 text-xs text-slate-400 hover:bg-slate-800 hover:text-white"
              >
                + New chat
              </button>
            </div>

            {conversations.length === 0 ? (
              <p className="px-3 text-xs text-slate-500">
                No conversations with this agent yet.
              </p>
            ) : (
              <ul className="space-y-0.5">
                {conversations.map((conversation) => (
                  <li key={conversation.id}>
                    <button
                      type="button"
                      onClick={() => onSelectConversation(conversation)}
                      className={`w-full truncate rounded-lg px-3 py-1.5 text-left text-sm ${
                        conversation.id === selectedConversationId
                          ? "bg-slate-700 text-white"
                          : "text-slate-400 hover:bg-slate-800"
                      }`}
                    >
                      {conversation.title}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>

      {/* Extra bottom padding keeps these clear of the Next.js dev indicator,
          which floats in the bottom-left corner during development. */}
      <div className="space-y-0.5 border-t border-slate-800 px-3 pb-10 pt-3">
        <NavButton onClick={onOpenTools}>Tools &amp; MCP</NavButton>
        <NavButton onClick={onOpenSecurity}>Security Testing</NavButton>
      </div>
    </aside>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-1.5 px-3 text-2xs font-semibold uppercase tracking-[0.08em] text-slate-500">
      {children}
    </div>
  );
}

function NavButton({
  onClick,
  children,
}: {
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full rounded-lg px-3 py-2 text-left text-sm text-slate-400 hover:bg-slate-800 hover:text-white"
    >
      {children}
    </button>
  );
}
