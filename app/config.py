"""Application settings loaded from environment / .env.

``load_dotenv()`` runs first so Langfuse (which reads ``os.environ`` itself)
sees LANGFUSE_* before any Langfuse client is imported.
"""

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


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
