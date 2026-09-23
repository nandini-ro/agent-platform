"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import agents, chat, conversations, mcp, security, tools
from app.config.settings import get_settings
from app.db.session import init_db

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    lifespan=lifespan,
    title=settings.app_name,
    description="Configurable multi-agent chatbot platform with MCP tools and "
    "Garak security testing.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Methods that can change stored configuration. Every dangerous operation this
# API offers is one of them: defining an MCP server (whose command this process
# then executes), defining an HTTP tool (whose URL this process then fetches),
# and starting a Garak run.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@app.middleware("http")
async def require_admin_key(request: Request, call_next):
    """Require AGENT_API_KEY on any state-changing /api request.

    With the key unset this is a no-op, so a local checkout stays frictionless.
    Once set it is the line between "reads the demo" and "runs a command on
    this host", which is why it is enforced here rather than route by route: a
    new endpoint is covered the moment it is written, instead of whenever
    someone remembers to add the dependency.

    Reads stay open. They expose configuration - system prompts, tool
    definitions - but they cannot spawn a process or make the server issue a
    request. Put the whole app behind a proxy or a private network if the
    configuration itself is sensitive.
    """
    expected = settings.agent_api_key
    if (
        expected
        and request.method in _MUTATING_METHODS
        and request.url.path.startswith("/api/")
    ):
        supplied = request.headers.get("X-API-Key")
        if not supplied or not secrets.compare_digest(supplied, expected):
            logger.warning(
                "rejected unauthenticated %s %s", request.method, request.url.path
            )
            return JSONResponse(
                {"detail": "Invalid or missing API key"}, status_code=401
            )
    return await call_next(request)

app.include_router(agents.router)
app.include_router(conversations.router)
app.include_router(chat.router)
app.include_router(tools.router)
app.include_router(mcp.router)
app.include_router(security.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}
