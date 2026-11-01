"""Central configuration. Every tunable lives here; env vars use the ACT_AGENT_ prefix."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ACT_AGENT_",
        extra="ignore",
    )

    app_name: str = "act-agent"
    debug: bool = False
    log_level: str = "INFO"

    # ---- LLM ----
    llm_provider: str = "openai"  # openai | anthropic
    llm_model: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "ACT_AGENT_LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"
        ),
    )
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096

    # ---- Auth ----
    jwt_secret: str = "dev-only-secret-change-me-0123456789abcdef"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "admin123"
    bootstrap_tenant: str = "default"

    # ---- Storage ----
    database_url: str = "sqlite+aiosqlite:///./data/act_agent.db"
    checkpoint_dsn: str = ""
    lock_backend: str = "memory"  # pg | memory

    # ---- Skills ----
    skills_dir: str = str(REPO_ROOT.parent / "act-skills")

    # ---- MCP ----
    mcp_config_path: str = "config/mcp.json"
    mcp_tool_timeout_seconds: float = 60.0
    mcp_connect_timeout_seconds: float = 10.0

    # ---- Context compression ----
    max_context_tokens: int = 64_000
    compress_ratio: float = 0.6
    keep_recent_messages: int = 20
    summary_max_tokens: int = 1200

    # ---- Sessions ----
    max_concurrent_sessions_per_tenant: int = 50
    session_lock_ttl_seconds: int = 300

    # ---- Server ----
    host: str = "0.0.0.0"
    port: int = 8088

    @property
    def compression_threshold(self) -> int:
        return int(self.max_context_tokens * self.compress_ratio)

    @property
    def is_default_jwt_secret(self) -> bool:
        return self.jwt_secret.startswith("dev-only-secret")

    @property
    def resolved_skills_dir(self) -> Path:
        p = Path(self.skills_dir)
        return p if p.is_absolute() else (REPO_ROOT / p).resolve()

    @property
    def resolved_mcp_config(self) -> Path:
        p = Path(self.mcp_config_path)
        return p if p.is_absolute() else (REPO_ROOT / p).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
