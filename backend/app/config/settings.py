"""Application settings.

Every secret and deployment-specific value is read from the environment (or a
local .env file). Nothing sensitive is ever hardcoded or persisted to the DB.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Agent Platform"

    # SQLite for dev. Swap for postgresql+psycopg://... without code changes.
    database_url: str = "sqlite:///./chatbot.db"

    # --- LLM providers -----------------------------------------------------
    # Absent keys are tolerated: the provider reports itself unavailable and the
    # API returns a clear 4xx rather than crashing at import time.
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    default_provider: str = "mock"
    default_model: str = "gpt-5.6-sol"

    # --- Agent API / Garak -------------------------------------------------
    # Shared secret for POST /api/agents/{id}/chat. When unset the endpoint is
    # open, which is fine for a local POC but should be set for any shared host.
    agent_api_key: str | None = None

    # --- Limits (adversarial input makes these load-bearing) ---------------
    tool_timeout_seconds: float = 10.0
    mcp_timeout_seconds: float = 30.0
    llm_timeout_seconds: float = 120.0
    max_tool_iterations: int = 12
    max_history_messages: int = 40

    # --- User-defined HTTP tools ------------------------------------------
    http_tool_timeout_seconds: float = 10.0
    http_tool_max_response_chars: int = 8000
    # Empty means "any public host". Set to lock tools to known services.
    http_tool_allowed_hosts: str = ""
    # Off by default: allowing private ranges lets a UI-defined tool reach
    # anything on the host's network, including cloud metadata services.
    http_tool_allow_private_networks: bool = False

    # --- Garak -------------------------------------------------------------
    # Garak lives in its own virtualenv so its dependency tree never collides
    # with the backend's. Points at that interpreter.
    garak_python: str = ".venv-garak/bin/python"
    garak_report_dir: str = "garak_runs"
    # Public base URL garak should call back on to reach this API.
    self_base_url: str = "http://127.0.0.1:8000"

    # Both spellings of localhost: developers reach the UI by either.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def http_tool_allowed_host_list(self) -> list[str]:
        return [h.strip().lower() for h in self.http_tool_allowed_hosts.split(",") if h.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
