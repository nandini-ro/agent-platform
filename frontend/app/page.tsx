"use client";

import { useEffect, useState } from "react";

import { AgentForm } from "@/components/AgentForm";
import { ChatWindow } from "@/components/ChatWindow";
import { SecurityPanel } from "@/components/SecurityPanel";
import { Sidebar } from "@/components/Sidebar";
import { ToolsPanel } from "@/components/ToolsPanel";
import * as api from "@/services/api";
import type {
  Agent,
  AgentDraft,
  Conversation,
  MCPServer,
  Message,
  PendingMessage,
  ProviderInfo,
  ToolDefinition,
} from "@/types";

type Overlay = "none" | "agent-form" | "tools" | "security";

// Ids for optimistic messages that exist only until the server's `done` event
// supplies the real one. A counter rather than a timestamp: two messages in the
// same millisecond would otherwise collide as React keys.
let optimisticCounter = 0;
const nextOptimisticId = () => `local-${(optimisticCounter += 1)}`;

export default function Home() {
  // Catalogue
  const [agents, setAgents] = useState<Agent[]>([]);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [tools, setTools] = useState<ToolDefinition[]>([]);
  const [mcpServers, setMcpServers] = useState<MCPServer[]>([]);

  // Selection.
  //
  // Conversations belong to an agent and messages to a conversation, so each
  // is stored together with the id it was loaded for and derived at render
  // time. Scoping them this way means switching agents cannot briefly show the
  // previous agent's threads, and no effect has to reset anything.
  const [agent, setAgent] = useState<Agent | null>(null);
  const [conversationCache, setConversationCache] = useState<{
    agentId: string;
    items: Conversation[];
  } | null>(null);
  const [selection, setSelection] = useState<{
    agentId: string;
    conversationId: string | null;
  } | null>(null);
  const [messageCache, setMessageCache] = useState<{
    conversationId: string;
    items: Message[];
  } | null>(null);

  const conversations =
    conversationCache !== null && conversationCache.agentId === agent?.id
      ? conversationCache.items
      : [];
  const conversationId =
    selection !== null && selection.agentId === agent?.id
      ? selection.conversationId
      : null;
  const messages =
    conversationId && messageCache?.conversationId === conversationId
      ? messageCache.items
      : [];

  // Transient
  const [pending, setPending] = useState<PendingMessage | null>(null);
  const [sending, setSending] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Overlays
  const [overlay, setOverlay] = useState<Overlay>("none");
  const [editing, setEditing] = useState<Agent | null>(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // -- bootstrap ------------------------------------------------------------

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.listAgents(),
      api.listProviders(),
      api.listTools(),
      api.listMCPServers(),
    ])
      .then(([agentList, providerList, toolList, serverList]) => {
        if (cancelled) return;
        setAgents(agentList);
        setProviders(providerList);
        setTools(toolList);
        setMcpServers(serverList);
        setAgent((current) => current ?? agentList[0] ?? null);
      })
      .catch((e) => !cancelled && setBootError((e as Error).message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const agentId = agent?.id ?? null;

  useEffect(() => {
    if (!agentId) return;
    let cancelled = false;
    api
      .listConversations(agentId)
      .then((items) => {
        if (cancelled) return;
        setConversationCache({ agentId, items });
        setSelection({ agentId, conversationId: items[0]?.id ?? null });
      })
      .catch((e) => !cancelled && setChatError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    api
      .listMessages(conversationId)
      .then((items) => !cancelled && setMessageCache({ conversationId, items }))
      .catch((e) => !cancelled && setChatError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  // -- chat -----------------------------------------------------------------

  async function send(content: string) {
    if (!agent) return;
    setChatError(null);
    setSending(true);

    // A conversation is created lazily on the first message.
    let targetId = conversationId;
    try {
      if (!targetId) {
        const conversation = await api.createConversation(agent.id);
        setConversationCache((cache) => ({
          agentId: agent.id,
          items: [conversation, ...(cache?.items ?? [])],
        }));
        setSelection({ agentId: agent.id, conversationId: conversation.id });
        targetId = conversation.id;
      }
    } catch (e) {
      setChatError((e as Error).message);
      setSending(false);
      return;
    }

    const optimisticUser: Message = {
      id: nextOptimisticId(),
      conversation_id: targetId,
      role: "user",
      content,
      meta: {},
      created_at: new Date().toISOString(),
    };
    appendMessage(targetId, optimisticUser);
    setPending({ content: "", toolCalls: [] });

    await api.streamMessage(targetId, content, {
      onToken: (text) =>
        setPending((p) => (p ? { ...p, content: p.content + text } : p)),
      onToolCall: (call) =>
        setPending((p) => (p ? { ...p, toolCalls: [...p.toolCalls, call] } : p)),
      onDone: (payload) => {
        setPending(null);
        appendMessage(targetId, {
          id: payload.message_id,
          conversation_id: targetId,
          role: "assistant",
          content: payload.content,
          meta: { tool_calls: payload.tool_calls, ...payload.metadata },
          created_at: new Date().toISOString(),
        });
        // The backend titles a conversation from its first message.
        void api
          .listConversations(agent.id)
          .then((items) => setConversationCache({ agentId: agent.id, items }))
          .catch(() => undefined);
      },
      onError: (message) => {
        setPending(null);
        setChatError(message);
      },
    });

    // The stream can also end without a terminal event (dropped connection).
    // Clearing here guarantees the streaming placeholder never outlives it.
    setPending(null);
    setSending(false);
  }

  // -- agents ---------------------------------------------------------------

  async function saveAgent(draft: AgentDraft) {
    setSaving(true);
    setFormError(null);
    try {
      const saved = editing
        ? await api.updateAgent(editing.id, draft)
        : await api.createAgent(draft);
      const agentList = await api.listAgents();
      setAgents(agentList);
      setAgent(saved);
      setOverlay("none");
      setEditing(null);
    } catch (e) {
      setFormError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function removeAgent(target: Agent) {
    if (
      !window.confirm(
        `Delete "${target.name}"? Its conversations are deleted too.`,
      )
    ) {
      return;
    }
    try {
      await api.deleteAgent(target.id);
      const agentList = await api.listAgents();
      setAgents(agentList);
      if (agent?.id === target.id) setAgent(agentList[0] ?? null);
    } catch (e) {
      setChatError((e as Error).message);
    }
  }

  function startNewChat() {
    if (!agent) return;
    setSelection({ agentId: agent.id, conversationId: null });
    setPending(null);
    setChatError(null);
  }

  /** Append to the cache only if it still holds the conversation we sent to. */
  function appendMessage(targetId: string, message: Message) {
    setMessageCache((cache) =>
      cache?.conversationId === targetId
        ? { conversationId: targetId, items: [...cache.items, message] }
        : { conversationId: targetId, items: [message] },
    );
  }

  // -- render ---------------------------------------------------------------

  if (loading) {
    return <Centered>Loading…</Centered>;
  }

  if (bootError) {
    return (
      <Centered>
        <div className="max-w-md space-y-2 text-center">
          <p className="font-medium text-red-700">Cannot reach the backend</p>
          <p className="text-sm text-slate-600">{bootError}</p>
          <p className="text-xs text-slate-400">
            Start it with{" "}
            <code className="font-mono">
              uvicorn app.main:app --reload
            </code>{" "}
            in <code className="font-mono">backend/</code>.
          </p>
        </div>
      </Centered>
    );
  }

  return (
    <div className="flex h-screen">
      <Sidebar
        agents={agents}
        conversations={conversations}
        selectedAgent={agent}
        selectedConversationId={conversationId}
        onCreateAgent={() => {
          setEditing(null);
          setFormError(null);
          setOverlay("agent-form");
        }}
        onSelectAgent={setAgent}
        onEditAgent={(a) => {
          setEditing(a);
          setFormError(null);
          setOverlay("agent-form");
        }}
        onDeleteAgent={removeAgent}
        onNewChat={startNewChat}
        onSelectConversation={(c) =>
          setSelection({ agentId: c.agent_id, conversationId: c.id })
        }
        onOpenTools={() => setOverlay("tools")}
        onOpenSecurity={() => setOverlay("security")}
      />

      <main className="min-w-0 flex-1">
        {agent ? (
          <ChatWindow
            agent={agent}
            messages={messages}
            pending={pending}
            sending={sending}
            error={chatError}
            onSend={send}
            onDismissError={() => setChatError(null)}
            onEditAgent={() => {
              setEditing(agent);
              setFormError(null);
              setOverlay("agent-form");
            }}
          />
        ) : (
          <Centered>
            <div className="text-center">
              <p className="text-sm font-medium text-slate-700">
                No agents yet
              </p>
              <p className="mt-1 text-sm text-slate-500">
                Create one from the sidebar to start chatting.
              </p>
            </div>
          </Centered>
        )}
      </main>

      {overlay === "agent-form" && (
        <AgentForm
          key={editing?.id ?? "new"}
          agent={editing}
          providers={providers}
          tools={tools}
          mcpServers={mcpServers}
          saving={saving}
          error={formError}
          onSave={saveAgent}
          onCancel={() => {
            setOverlay("none");
            setEditing(null);
          }}
        />
      )}

      {overlay === "tools" && (
        <ToolsPanel
          tools={tools}
          servers={mcpServers}
          onClose={() => setOverlay("none")}
          onDiscover={api.discoverMCPServer}
          onAddServer={async (payload) => {
            await api.createMCPServer(payload);
            setMcpServers(await api.listMCPServers());
          }}
          onDeleteServer={async (id) => {
            await api.deleteMCPServer(id);
            setMcpServers(await api.listMCPServers());
          }}
          onToolsChanged={async () => setTools(await api.listTools())}
        />
      )}

      {overlay === "security" && (
        <SecurityPanel
          agents={agents}
          selectedAgentId={agent?.id ?? null}
          onClose={() => setOverlay("none")}
        />
      )}
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen items-center justify-center bg-slate-50 p-6 text-sm text-slate-500">
      {children}
    </div>
  );
}
