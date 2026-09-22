"use client";

import { useEffect, useRef } from "react";

import { MessageBubble } from "@/components/Message";
import { MessageInput } from "@/components/MessageInput";
import type { Agent, Message, PendingMessage } from "@/types";

interface ChatWindowProps {
  agent: Agent;
  messages: Message[];
  pending: PendingMessage | null;
  sending: boolean;
  error: string | null;
  onSend: (content: string) => void;
  onDismissError: () => void;
  onEditAgent: () => void;
}

export function ChatWindow({
  agent,
  messages,
  pending,
  sending,
  error,
  onSend,
  onDismissError,
  onEditAgent,
}: ChatWindowProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending]);

  const toolCount = agent.tools.length + agent.mcp_server_ids.length;

  return (
    <div className="flex h-full flex-col bg-slate-50">
      <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-6 py-3">
        <div className="min-w-0">
          <h1 className="truncate text-base font-semibold tracking-tight text-slate-900">
            {agent.name}
          </h1>
          <p className="truncate text-xs text-slate-500">
            {agent.provider} · {agent.model}
            {toolCount > 0 && ` · ${toolCount} tool source(s)`}
          </p>
        </div>
        <button
          type="button"
          onClick={onEditAgent}
          className="ml-auto rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50"
        >
          Configure
        </button>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-3xl space-y-5">
          {messages.length === 0 && !pending && (
            <EmptyState agent={agent} />
          )}

          {messages.map((message) => (
            <MessageBubble
              key={message.id}
              role={message.role}
              content={message.content}
              toolCalls={message.meta?.tool_calls}
              meta={message.meta}
            />
          ))}

          {pending && (
            <MessageBubble
              role="assistant"
              content={pending.content}
              toolCalls={pending.toolCalls}
              streaming
            />
          )}

          {error && (
            <div className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
              <div className="flex-1">{error}</div>
              <button
                type="button"
                onClick={onDismissError}
                className="text-xs font-medium underline"
              >
                dismiss
              </button>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      <MessageInput
        onSend={onSend}
        disabled={sending}
        placeholder={sending ? "Waiting for a response..." : "Send a message..."}
      />
    </div>
  );
}

function EmptyState({ agent }: { agent: Agent }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-10 text-center">
      <h2 className="text-base font-semibold tracking-tight text-slate-800">{agent.name}</h2>
      {agent.description && (
        <p className="mt-1 text-sm text-slate-500">{agent.description}</p>
      )}
      {agent.system_prompt && (
        <p className="mx-auto mt-4 max-w-md text-left text-xs leading-relaxed text-slate-400">
          <span className="text-2xs font-semibold uppercase tracking-[0.08em]">
            System instructions
          </span>
          <br />
          {agent.system_prompt}
        </p>
      )}
    </div>
  );
}
