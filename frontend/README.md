# Frontend — Agent Platform

Next.js UI for the agent platform. Setup, architecture and usage live in the
[root README](../README.md).

```bash
npm install
cp .env.example .env.local
npm run dev        # http://localhost:3000
```

Use `localhost`, not `127.0.0.1` — Next.js 16 blocks dev resources across origin
spellings, so the page would load but never hydrate.

The backend must be running on the URL in `NEXT_PUBLIC_API_BASE_URL`
(default `http://127.0.0.1:8000`).

| Path | Contents |
|---|---|
| `app/page.tsx` | Application shell and state |
| `components/` | Sidebar, ChatWindow, Message, MessageInput, AgentForm, AgentSelector, ToolCall, ToolsPanel, SecurityPanel |
| `services/api.ts` | REST client and the SSE stream reader |
| `types/` | Shapes mirroring the backend schemas |
