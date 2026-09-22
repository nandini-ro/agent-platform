"""Test fixtures.

The database URL is set at *module* level, not in a fixture. pytest imports
conftest before it collects test modules, and several of those import app
modules at import time - which builds the SQLAlchemy engine. A fixture would
run too late and the suite would quietly write to the development database.
"""

import atexit
import os
import tempfile

import pytest

_fd, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="agent-platform-test-")
os.close(_fd)

# Environment variables take precedence over .env in pydantic-settings, so this
# wins over whatever the developer has configured locally.
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["DEFAULT_PROVIDER"] = "mock"

# Blank out every credential. These are *set to empty*, not deleted: Settings
# also reads backend/.env, so popping the variable just lets the file's value
# through. An environment variable outranks the file, and "" is falsy, so this
# is what actually stops a developer's real keys from reaching the suite -
# building live clients, spending money, and making results depend on whose
# machine the tests run on. test_providers.py asserts this holds.
for _key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "AGENT_API_KEY"):
    os.environ[_key] = ""

# User-defined HTTP tools must not be able to reach the host's own network
# during a test run.
os.environ["HTTP_TOOL_ALLOW_PRIVATE_NETWORKS"] = "false"


@atexit.register
def _cleanup() -> None:
    try:
        os.unlink(_DB_PATH)
    except OSError:
        pass


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Create the tables once, before anything runs.

    Without this the suite only passes in file order: a test that touches a
    table is fine if some earlier test happened to have built the schema via
    the `client` fixture, and fails when run on its own.
    """
    from app.db.session import init_db

    init_db()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.db.session import engine, init_db
    from app.main import app

    # Guard against the failure this module exists to prevent.
    assert str(engine.url).endswith(_DB_PATH), (
        f"tests are pointed at {engine.url}, not the temporary database"
    )

    init_db()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def agent_payload():
    return {
        "name": "Test Agent",
        "description": "An agent for tests",
        "system_prompt": "You are a terse test assistant.",
        "provider": "mock",
        "model": "mock-1",
        "temperature": 0.5,
        "max_tokens": 512,
        "tools": [],
        "mcp_server_ids": [],
    }
