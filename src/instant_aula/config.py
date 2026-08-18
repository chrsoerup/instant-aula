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
    ollama_host: str
    ollama_model: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_to: str
    state_file: Path


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_settings() -> Settings:
    state_dir = PROJECT_ROOT / "state"
    state_dir.mkdir(exist_ok=True)
    (PROJECT_ROOT / "logs").mkdir(exist_ok=True)

    return Settings(
        aula_username=_require("AULA_MITID_USERNAME"),
        aula_auth_method=os.environ.get("AULA_AUTH_METHOD", "app"),
        aula_mitid_password=os.environ.get("AULA_MITID_PASSWORD") or None,
        ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "llama3.1:8b"),
        smtp_host=_require("SMTP_HOST"),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_user=_require("SMTP_USER"),
        smtp_password=_require("SMTP_PASSWORD"),
        smtp_from=os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"],
        smtp_to=_require("SMTP_TO"),
        state_file=state_dir / "state.json",
    )
