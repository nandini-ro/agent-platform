"use client";

import { useState, useSyncExternalStore } from "react";

import {
  getAdminKey,
  getServerAdminKey,
  setAdminKey,
  subscribeAdminKey,
} from "@/services/adminKey";

/**
 * Paste-in control for the admin key that authorises every write.
 *
 * Without a key the app is readable but inert: agents and tools are listed,
 * and creating, editing or even sending a message returns 401. A deployment
 * with AGENT_API_KEY unset needs nothing entered here.
 */
export function AdminKeyBar() {
  // localStorage is external state, and reading it during render would
  // disagree with what the server rendered. useSyncExternalStore is the
  // supported way to read it: the server snapshot is "", so the control
  // renders locked on the server and corrects itself on hydration.
  const stored = useSyncExternalStore(
    subscribeAdminKey,
    getAdminKey,
    getServerAdminKey,
  );
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  function save() {
    setAdminKey(draft.trim());
    setDraft("");
    setEditing(false);
  }

  if (editing) {
    return (
      <div className="space-y-1.5 px-3 py-2">
        <input
          type="password"
          autoFocus
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") save();
            if (event.key === "Escape") setEditing(false);
          }}
          placeholder="Paste admin key"
          className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-100 placeholder:text-slate-600 focus:border-slate-500 focus:outline-none"
        />
        <div className="flex gap-1.5">
          <button
            type="button"
            onClick={save}
            className="flex-1 rounded-lg bg-slate-700 px-2 py-1 text-xs font-medium text-white hover:bg-slate-600"
          >
            Save
          </button>
          <button
            type="button"
            onClick={() => setEditing(false)}
            className="rounded-lg px-2 py-1 text-xs text-slate-400 hover:text-white"
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 px-3 py-2">
      <span
        aria-hidden
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${
          stored ? "bg-emerald-400" : "bg-slate-600"
        }`}
      />
      <span className="text-xs text-slate-400">
        {stored ? "Admin key set" : "Read-only"}
      </span>
      <button
        type="button"
        onClick={() => (stored ? setAdminKey("") : setEditing(true))}
        className="ml-auto rounded px-1.5 py-0.5 text-xs text-slate-500 hover:bg-slate-800 hover:text-white"
      >
        {stored ? "Forget" : "Add key"}
      </button>
    </div>
  );
}
