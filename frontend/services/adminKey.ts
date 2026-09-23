/**
 * The admin key, as held by the browser.
 *
 * Every write to /api must carry it as `X-API-Key`. It lives here, in the
 * browser, rather than in the server's environment on purpose: the /api proxy
 * forwards whatever the browser sends and adds nothing of its own, because a
 * key injected server-side would authenticate an anonymous visitor exactly as
 * readily as it authenticates you - the proxy cannot tell the two apart.
 *
 * localStorage is per-browser and per-origin, so this is a convenience for
 * whoever administers a deployment, not a security boundary. Anyone holding
 * the key can make any change the API allows.
 */

const STORAGE_KEY = "agent-platform.admin-key";

// Components read the key through useSyncExternalStore, so a write here has to
// tell them. Without this the sidebar would keep saying "Read-only" until
// something else happened to re-render it.
const listeners = new Set<() => void>();

/** Subscribe to key changes, including ones made in another tab. */
export function subscribeAdminKey(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

/** There is no localStorage during a server render, so it is never set. */
export function getServerAdminKey(): string {
  return "";
}

/** The stored key, or "" when there is none. Never throws. */
export function getAdminKey(): string {
  // Called from the API client, which can be imported into a server render.
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    // Private windows and blocked site data make every accessor throw.
    return "";
  }
}

/** Store a key, or clear it when given an empty string. */
export function setAdminKey(key: string): void {
  if (typeof window === "undefined") return;
  try {
    if (key) window.localStorage.setItem(STORAGE_KEY, key);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing to do: the header goes unsent and writes fail with a message
    // that says to add the key, which is the same prompt the user would get.
  }
  for (const listener of listeners) listener();
}

/** The header to merge into a request, empty when no key is stored. */
export function adminKeyHeader(): Record<string, string> {
  const key = getAdminKey();
  return key ? { "X-API-Key": key } : {};
}
