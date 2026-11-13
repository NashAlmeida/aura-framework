"""Application settings loaded from environment / .env."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bridge_host: str = "127.0.0.1"
    bridge_port: int = 8000
    mark_lvii_path: Path = Path("./mark_lvii")
    log_level: str = "INFO"

    # Phase 2 — LLM agent (optional; demo planner is used when unset)
    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"


settings = Settings()
