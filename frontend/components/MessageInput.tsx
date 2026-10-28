"use client";

import { useEffect, useRef, useState } from "react";

import * as api from "@/services/api";
import type { Attachment } from "@/types";

interface MessageInputProps {
  onSend: (content: string, attachments: Attachment[]) => void;
  disabled?: boolean;
  placeholder?: string;
}

// Mirrors the backend's limits (app/services/attachments.py), so a file that
// would be refused is caught before it is uploaded.
const MAX_FILES = 5;
const ACCEPT =
  "image/png,image/jpeg,image/webp,image/gif,.pdf,.txt,.md,.csv,.tsv,.json,.xml,.yaml,.yml,.html,.log,.py,.js,.ts,.sql";

/** A file in the composer: uploading, uploaded, or refused. */
interface Pending {
  key: string;
  name: string;
  preview: string | null; // object URL for images
  status: "uploading" | "ready" | "error";
  attachment?: Attachment;
  error?: string;
}

let pendingCounter = 0;

export function MessageInput({
  onSend,
  disabled = false,
  placeholder = "Send a message...",
}: MessageInputProps) {
  const [value, setValue] = useState("");
  const [files, setFiles] = useState<Pending[]>([]);
  const [dragging, setDragging] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Grow with content, up to a cap.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  const ready = files.filter((f) => f.status === "ready" && f.attachment);
  const uploading = files.some((f) => f.status === "uploading");
  const canSend =
    !disabled && !uploading && (value.trim().length > 0 || ready.length > 0);

  function update(key: string, patch: Partial<Pending>) {
    setFiles((list) => list.map((f) => (f.key === key ? { ...f, ...patch } : f)));
  }

  function add(selected: FileList | File[]) {
    const incoming = Array.from(selected);
    if (incoming.length === 0) return;
    const room = MAX_FILES - files.length;
    const accepted = incoming.slice(0, Math.max(room, 0));
    const refused = incoming.slice(accepted.length).map<Pending>((file) => ({
      key: `file-${(pendingCounter += 1)}`,
      name: file.name,
      preview: null,
      status: "error",
      error: `At most ${MAX_FILES} files per message.`,
    }));

    const added = accepted.map<Pending>((file) => ({
      key: `file-${(pendingCounter += 1)}`,
      name: file.name || "pasted-image.png",
      preview: file.type.startsWith("image/") ? URL.createObjectURL(file) : null,
      status: "uploading",
    }));
    setFiles((list) => [...list, ...added, ...refused]);

    added.forEach((entry, i) => {
      api
        .uploadAttachment(accepted[i])
        .then((attachment) => update(entry.key, { status: "ready", attachment }))
        .catch((e) =>
          update(entry.key, { status: "error", error: (e as Error).message }),
        );
    });
  }

  function remove(key: string) {
    setFiles((list) => {
      const gone = list.find((f) => f.key === key);
      if (gone?.preview) URL.revokeObjectURL(gone.preview);
      return list.filter((f) => f.key !== key);
    });
  }

  function submit() {
    if (!canSend) return;
    onSend(
      value.trim(),
      ready.map((f) => f.attachment as Attachment),
    );
    files.forEach((f) => f.preview && URL.revokeObjectURL(f.preview));
    setValue("");
    setFiles([]);
  }

  return (
    <div
      className="border-t border-slate-200 bg-white px-6 py-4"
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        if (!e.dataTransfer.files.length) return;
        e.preventDefault();
        setDragging(false);
        add(e.dataTransfer.files);
      }}
    >
      <div className="mx-auto max-w-3xl">
        {files.length > 0 && (
          <ul className="mb-2 flex flex-wrap gap-2">
            {files.map((f) => (
              <li
                key={f.key}
                title={f.error ?? f.name}
                className={`flex max-w-[16rem] items-center gap-2 rounded-lg border px-2 py-1.5 text-xs ${
                  f.status === "error"
                    ? "border-red-200 bg-red-50 text-red-800"
                    : "border-slate-200 bg-slate-50 text-slate-700"
                }`}
              >
                {f.preview ? (
                  // eslint-disable-next-line @next/next/no-img-element -- a local object URL, not an optimisable asset
                  <img src={f.preview} alt="" className="h-8 w-8 shrink-0 rounded object-cover" />
                ) : (
                  <FileIcon />
                )}
                <span className="min-w-0">
                  <span className="block truncate font-medium">{f.name}</span>
                  <span className="block truncate text-2xs text-slate-500">
                    {f.status === "uploading"
                      ? "Uploading…"
                      : f.status === "error"
                        ? f.error
                        : f.attachment?.kind === "image"
                          ? `Image · ${formatSize(f.attachment.size)}`
                          : `Document · ${formatSize(f.attachment?.size ?? 0)}`}
                  </span>
                </span>
                <button
                  type="button"
                  onClick={() => remove(f.key)}
                  aria-label={`Remove ${f.name}`}
                  className="ml-1 shrink-0 rounded px-1 text-slate-400 hover:bg-slate-200 hover:text-slate-700"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}

        <div
          className={`flex items-end gap-2 rounded-xl ${dragging ? "ring-2 ring-slate-400 ring-offset-2" : ""}`}
        >
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => {
              if (e.target.files) add(e.target.files);
              e.target.value = ""; // allow picking the same file again
            }}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled || files.length >= MAX_FILES}
            aria-label="Attach images or files"
            title="Attach images or files"
            className="rounded-xl border border-slate-300 p-2.5 text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <PaperclipIcon />
          </button>
          <textarea
            ref={textareaRef}
            rows={1}
            value={value}
            disabled={disabled}
            placeholder={placeholder}
            onChange={(e) => setValue(e.target.value)}
            onPaste={(e) => {
              // Pasted screenshots arrive as files on the clipboard.
              if (e.clipboardData.files.length) {
                e.preventDefault();
                add(e.clipboardData.files);
              }
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            className="flex-1 resize-none rounded-xl border border-slate-300 px-4 py-2.5 text-base outline-none placeholder:text-slate-400 focus:border-slate-500 disabled:bg-slate-50"
          />
          <button
            type="button"
            onClick={submit}
            disabled={!canSend}
            className="rounded-xl bg-slate-800 px-4 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            Send
          </button>
        </div>
        <p className="mt-2 text-xs text-slate-400">
          Enter to send · Shift+Enter for a new line · Attach, paste or drop
          images and documents (PDF, TXT, MD, CSV, JSON)
        </p>
      </div>
    </div>
  );
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function PaperclipIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5" aria-hidden="true">
      <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
    </svg>
  );
}

export function FileIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="h-6 w-6 shrink-0 text-slate-400" aria-hidden="true">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  );
}
