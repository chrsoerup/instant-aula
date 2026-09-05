"""Configuration loaded from environment variables / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    aula_username: str
    aula_auth_method: str
    aula_mitid_password: str | None
    ha_notify_service: str
    state_file: Path
    ollama_host: str
    ollama_model: str
    ollama_timeout: float


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_settings() -> Settings:
    state_dir = Path(os.environ.get("STATE_DIR", str(PROJECT_ROOT / "state")))
    state_dir.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "logs").mkdir(exist_ok=True)

    return Settings(
        aula_username=_require("AULA_MITID_USERNAME"),
        aula_auth_method=os.environ.get("AULA_AUTH_METHOD", "app"),
        aula_mitid_password=os.environ.get("AULA_MITID_PASSWORD") or None,
        ha_notify_service=_require("HA_NOTIFY_SERVICE"),
        state_file=state_dir / "state.json",
        ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "llama3.1:8b"),
        ollama_timeout=float(os.environ.get("OLLAMA_TIMEOUT", "300")),
    )
