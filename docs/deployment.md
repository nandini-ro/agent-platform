# Deploying to a single VM

Three containers on one private Docker network, with only Caddy published to
the host.

```
              :80 ─┐
                   ▼
              ┌─────────┐      ┌──────────┐      ┌─────────┐
  browser ───►│  caddy  ├─────►│ frontend ├─────►│ backend │
              └─────────┘      │ (Next)   │      │(FastAPI)│
                               └──────────┘      └────┬────┘
                                 adds X-API-Key       │ spawns
                                 to /api calls        ▼
                                                 stdio MCP
                                                 subprocesses
```

The backend publishes no port. Every request reaches it through the frontend's
`/api` proxy ([route.ts](../frontend/app/api/%5B...path%5D/route.ts)), which
forwards the request unchanged and adds no credentials of its own.

That last part is the important one. The proxy answers anyone who can load the
page, so a key held in the frontend container would authenticate an anonymous
visitor exactly as readily as it authenticates you - it would look like
authentication and protect nothing. The key lives in the administrator's
browser instead.

## Render, free tier

[`render.yaml`](../render.yaml) deploys the backend and the UI as two free
Render web services, with the database on an external PostgreSQL. Nothing here
costs money, at the price of the limits listed below.

1. **Create the database.** Sign up at [neon.tech](https://neon.tech), create a
   project, and copy its connection string (`postgresql://...?sslmode=require`).
2. **Push this repo to GitHub**, then in Render choose **New → Blueprint** and
   pick it. Render asks for two values:
   - `DATABASE_URL`: the Neon connection string.
   - `BACKEND_INTERNAL_URL`: the backend's public URL,
     `https://agent-platform-api.onrender.com`. If Render had to add a suffix
     because the name was taken, correct it under the UI service's
     **Environment** once the backend shows its real URL.
3. **Open the backend service → Environment**, copy `AGENT_API_KEY`, open the
   UI's URL, click **Add key** in the sidebar and paste it.

The tables are created on first start. Agents' public URLs are on the backend:
`https://agent-platform-api.onrender.com/v1/agents/<slug>/chat`.

Limits of the free tier:

- **Services sleep after 15 minutes without traffic** and take about a minute
  to wake. That includes agents' public URLs: a caller with a short timeout
  will fail on the first request after a quiet spell.
- **750 free instance hours a month**, shared by both services. Sleeping
  services do not use them; two services awake all month would need about
  1,460, after which Render suspends them until the month ends.
- **No persistent disk**, which is why the database is external. Do not point
  `DATABASE_URL` at SQLite on a free service: the file is wiped on every
  deploy and restart.

To remove the sleep, change both services to `plan: starter` in
`render.yaml`. The UI can then also reach the backend over Render's private
network: set `BACKEND_INTERNAL_URL` from `fromService` (`property: hostport`)
instead of the public URL.

## Sizing the VM

**2 GB RAM minimum, 2 vCPU comfortable.** The constraint is the Next.js build,
not the running app — `next build` on a 1 GB box gets OOM-killed. If you are
set on a 1 GB instance, build the images somewhere else and push them to a
registry rather than running `docker compose build` on the VM.

Disk: 10 GB is plenty. The images are roughly 400 MB (backend) and 250 MB
(frontend); everything else is the SQLite file and logs, which are capped at
30 MB per container.

## First deploy

On a fresh Ubuntu/Debian VM with Docker Engine and the Compose plugin
installed:

```bash
git clone <your-repo> chatbot
cd chatbot

cp .env.example .env
openssl rand -hex 32          # paste into AGENT_API_KEY
openssl rand -base64 32       # paste into CREDENTIAL_ENCRYPTION_KEY
$EDITOR .env                  # both are required; provider keys go in the UI

docker compose up -d --build
```

Compose refuses to start if `AGENT_API_KEY` is empty — that is deliberate, and
the error names the variable.

Then create the demo agents and the bundled MCP server **inside** the backend
container, so the stored paths are the container's:

```bash
docker compose exec backend python seed.py
```

Open `http://<vm-ip>/`. The sidebar shows **Read-only** at the bottom: click
**Add key**, paste the `AGENT_API_KEY` from `.env`, and save. It is kept in
that browser's localStorage, so you do it once per browser.

Until a key is entered the app lists agents and tools but cannot change
anything - and that includes sending a chat message, which is a `POST`. This
is the intended posture for a deployment with one administrator. Anyone you
share the URL with sees the configuration and nothing more.

## Bringing your existing database over

The agents, HTTP tools and MCP servers you built locally live in
`backend/chatbot.db`. `seed.py` does not know about them — it only creates the
three demo agents and the Demo Knowledge Base.

Two things to know before copying that file up:

**It contains secrets.** A user-defined HTTP tool stores its URL template
verbatim, and yours have an Alpha Vantage key in them. Treat the file like a
credential: `scp` it, do not commit it or put it in a bucket.

**Its stdio MCP paths are macOS paths.** A row for the Financial Intelligence
server points at `/Users/…/backend/.venv/bin/python`, which does not exist in
the container, so the server silently vanishes from the agent at discovery
time. Fix it after copying:

```bash
docker compose cp backend/chatbot.db backend:/data/chatbot.db
docker compose exec backend python relink_mcp.py
docker compose restart backend
```

`relink_mcp.py` rewrites the command, script path and working directory of
every stdio server whose script it can find in `mcp_servers/`. It reports
anything it could not resolve rather than guessing — those need fixing by hand
in the UI.

HTTP MCP servers need no fixing, with one exception: the "Remote Demo KB" row
points at `http://127.0.0.1:8765/mcp`, which inside the container means the
container itself. Delete it or repoint it.

## Adding TLS later

Point a DNS A record at the VM, then in [`Caddyfile`](../Caddyfile) replace:

```
:80 {
```

with your hostname:

```
chat.example.com {
```

and `docker compose restart caddy`. Caddy obtains and renews the certificate
on its own. Nothing else changes — no hostname is baked into either image, and
the frontend talks to the backend by service name regardless.

## Sharing an agent

Each agent's public URL is `https://<host>/v1/agents/<slug>/chat`. Caddy sends
`/v1/*` straight to the backend, so it works as soon as the stack is up. Open
the agent's **Configure** panel, generate a key under **API access**, and give
out the URL and that key. The key opens that one agent's chat and nothing
else; rotate or revoke it from the same place.

There is no rate limit on this route. Every call spends the agent's provider
credit, so put a limit in front of it (Caddy's `rate_limit` plugin, or
Cloudflare) before you hand a key to anyone you would not hand your card to.

## Updating

```bash
git pull
docker compose up -d --build
```

The database is on a named volume, so it survives rebuilds. Schema changes are
applied by `init_db()` at startup, which can add columns but not alter or drop
them; anything beyond that needs a real migration tool.

## Backups

One file matters:

```bash
docker compose exec backend \
  python -c "import sqlite3,sys; sqlite3.connect('/data/chatbot.db').backup(sqlite3.connect('/data/backup.db'))"
docker compose cp backend:/data/backup.db ./chatbot-$(date +%F).db
```

Using SQLite's backup API rather than `cp` means you get a consistent snapshot
even while the app is serving. Keep those files somewhere private — see the
note about secrets above.

## What is deliberately not here

**Garak.** It lives in its own virtualenv for development and is not installed
in the image; its dependency tree is large and it is only ever run as a
subprocess. `docker-compose.yml` sets `SECURITY_TESTING_ENABLED=false`, which
removes the Security Testing panel and the `/api/security` routes. To enable
it, add `requirements-garak.txt` to the backend image, set `GARAK_PYTHON` to
the interpreter you installed it with, and set the flag back to `true`.

**PostgreSQL.** SQLite on a volume is the right size for one VM. If you
outgrow it, add a `postgres` service and set
`DATABASE_URL=postgresql+psycopg://…` — the schema is already compatible and
no application code changes. You will also want Alembic at that point.

## What is still open

`AGENT_API_KEY` guards every `POST`/`PUT`/`PATCH`/`DELETE` under `/api`
when `ADMIN_KEY_ENABLED=true` (this compose file sets it; a deployment that
does not, such as the Render Blueprint, leaves writes open),
enforced in [`main.py`](../backend/app/main.py) as middleware rather than route
by route, so a new endpoint is covered the day it is written.

Reads are not guarded. Anyone who reaches the URL can list your agents and read
their system prompts and tool definitions. They cannot change anything, spawn a
process, or make the server fetch a URL. If the configuration itself is
sensitive - and a system prompt often is - put the whole thing behind
Cloudflare Access or a Tailscale-only interface rather than opening port 80 to
the internet.

The key is a single shared credential with no expiry, no per-user identity and
no audit trail. Rotating it means editing `.env`, running
`docker compose up -d`, and re-entering it in each browser. That is the right
size for one administrator and the wrong size for a team.

Two settings in `.env` are worth a second look before you expose this:

- `HTTP_TOOL_ALLOWED_HOSTS` — empty means a user-defined tool may call any
  public host. Pinning it to the services you actually use is the strongest
  single limit on what a prompt-injected agent can reach.
- `HTTP_TOOL_ALLOW_PRIVATE_NETWORKS` — leave it `false`. True lets a
  UI-defined tool reach the VM's own network and your cloud provider's
  metadata endpoint.
