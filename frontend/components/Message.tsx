"use client";

import { Markdown } from "@/components/Markdown";
import { FileIcon, formatSize } from "@/components/MessageInput";
import { ToolCallBadge } from "@/components/ToolCall";
import { attachmentUrl } from "@/services/api";
import type { Attachment, ToolCall } from "@/types";

interface MessageProps {
  role: "user" | "assistant";
  content: string;
  attachments?: Attachment[];
  toolCalls?: ToolCall[];
  meta?: { provider?: string; model?: string; iterations?: number };
  streaming?: boolean;
}

export function MessageBubble({
  role,
  content,
  attachments = [],
  toolCalls = [],
  meta,
  streaming = false,
}: MessageProps) {
  const isUser = role === "user";

  return (
    <div className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-slate-800 text-xs font-semibold text-white">
          AI
        </div>
      )}

      <div className={`max-w-2xl space-y-2 ${isUser ? "items-end" : ""}`}>
        {toolCalls.length > 0 && (
          <div className="space-y-1.5">
            {toolCalls.map((call, i) => (
              <ToolCallBadge key={`${call.name}-${i}`} call={call} />
            ))}
          </div>
        )}

        {attachments.length > 0 && (
          <div className={`flex flex-wrap gap-2 ${isUser ? "justify-end" : ""}`}>
            {attachments.map((a) =>
              a.kind === "image" ? (
                <a
                  key={a.id}
                  href={attachmentUrl(a.id)}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={a.filename}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element -- served by the API, not a static asset */}
                  <img
                    src={attachmentUrl(a.id)}
                    alt={a.filename}
                    className="max-h-48 max-w-xs rounded-xl border border-slate-200 object-cover"
                  />
                </a>
              ) : (
                <div
                  key={a.id}
                  title={a.filename}
                  className="flex max-w-[16rem] items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700"
                >
                  <FileIcon />
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{a.filename}</span>
                    <span className="block text-2xs text-slate-500">
                      {a.content_type === "application/pdf" ? "PDF" : "Text"} ·{" "}
                      {formatSize(a.size)}
                    </span>
                  </span>
                </div>
              ),
            )}
          </div>
        )}

        {(content || streaming) && (
          <div
            className={`break-words rounded-2xl px-4 py-2.5 text-base leading-relaxed ${
              isUser
                ? "whitespace-pre-wrap bg-slate-800 text-white"
                : "border border-slate-200 bg-white text-slate-800"
            }`}
          >
            {/* Only the model writes markdown; a user's message stays verbatim
                so their asterisks and pound signs survive as typed. */}
            {isUser ? content : <Markdown content={content} caret={streaming} />}
            {/* Before the first token arrives there is no block to hang the
                caret off, so the bubble shows a bare one. */}
            {streaming && !content && (
              <span className="inline-block h-4 w-1.5 animate-pulse bg-slate-400 align-text-bottom" />
            )}
          </div>
        )}

        {meta?.provider && !streaming && (
          <div className="px-1 text-xs text-slate-400">
            {meta.provider} · {meta.model || "endpoint default"}
            {meta.iterations && meta.iterations > 1
              ? ` · ${meta.iterations} turns`
              : ""}
          </div>
        )}
      </div>

      {isUser && (
        <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-slate-200 text-xs font-semibold text-slate-600">
          You
        </div>
      )}
    </div>
  );
}
