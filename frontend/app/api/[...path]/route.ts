/**
 * Same-origin proxy to the FastAPI backend.
 *
 * It puts the API on the page's own origin, so there is no cross-origin
 * request, no preflight, and no CORS_ORIGINS list to keep in step with the
 * deployment's hostname. It also means the backend needs no published port -
 * it is reachable only from this container.
 *
 * It does NOT add credentials. `X-API-Key` is forwarded exactly as the browser
 * sent it. Injecting a key from the server's environment here would look like
 * authentication and be nothing of the sort: this proxy answers anyone who can
 * load the page, so a server-held key would authenticate every anonymous
 * visitor as readily as an administrator. The key belongs to the browser -
 * see services/adminKey.ts.
 *
 * Only the container build routes through here. `next dev` sets
 * NEXT_PUBLIC_API_BASE_URL to http://127.0.0.1:8000 and the browser talks to
 * the backend directly, exactly as before - this handler is never reached.
 */

// The backend publishes no port; it is reachable only on the compose network.
const BACKEND = process.env.BACKEND_INTERNAL_URL ?? "http://backend:8000";

// No route segment config is needed here. Route Handlers are uncached by
// default in this version of Next (caching is opt-in via `dynamic`), and the
// `runtime` export is deprecated - nodejs is the default and the only runtime
// that can stream a proxied body.

/** Headers that describe the browser->Next hop and must not be forwarded. */
const HOP_BY_HOP = [
  "host",
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "content-length",
  // Let undici negotiate its own compression with the backend rather than
  // promising whatever the browser asked for.
  "accept-encoding",
];

async function proxy(request: Request, path: string[]): Promise<Response> {
  const { search } = new URL(request.url);
  const target = `${BACKEND}/api/${path.join("/")}${search}`;

  // X-API-Key passes through untouched; see the note at the top of the file.
  const headers = new Headers(request.headers);
  for (const name of HOP_BY_HOP) headers.delete(name);

  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      // Required by undici whenever a streaming body is forwarded.
      ...(hasBody ? { duplex: "half" } : {}),
      // The backend pins follow_redirects off for its own outbound calls; do
      // the same here rather than chasing a Location into somewhere unvetted.
      redirect: "manual",
      cache: "no-store",
    } as RequestInit & { duplex?: "half" });
  } catch {
    return Response.json(
      { detail: "The backend is not reachable." },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers(upstream.headers);
  // The body is re-framed by this hop, so the upstream's framing headers would
  // describe a response that no longer exists.
  for (const name of ["content-encoding", "content-length", "transfer-encoding"]) {
    responseHeaders.delete(name);
  }

  // Passing `upstream.body` through un-awaited is what keeps SSE incremental:
  // tool-call events reach the browser as the agent produces them.
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

type Context = { params: Promise<{ path: string[] }> };

const handler = async (request: Request, ctx: Context) =>
  proxy(request, (await ctx.params).path);

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
export const HEAD = handler;
