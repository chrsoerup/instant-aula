"""Fetch this week's Aula/Meebook plan, ask a local LLM to turn it into one
friendly digest, and email it. Run once a week (e.g. via cron).
"""

from __future__ import annotations

import json
import sys

from .aula_cli import run_aula
from .config import load_settings
from .emailer import send
from .ollama_client import chat

DIGEST_SYSTEM_PROMPT = """You write a short, friendly weekly digest for a \
parent from their child's school data (Aula/Meebook), given as JSON \
(calendar events and a day-by-day weekly plan with tasks). Summarize what's \
happening day by day this week, any homework or things to bring, and any \
dates to remember. Skip empty days silently. Keep it concise and in plain \
language -- this replaces reading dozens of raw notifications. Output plain \
text only: no JSON, no markdown headers, just short paragraphs or a simple \
day-by-day list."""


def main() -> int:
    settings = load_settings()

    summary = run_aula(settings, "weekly-summary", "--provider", "meebook")

    digest = chat(
        settings.ollama_host,
        settings.ollama_model,
        DIGEST_SYSTEM_PROMPT,
        json.dumps(summary, ensure_ascii=False),
    )

    send(settings, subject=f"Aula weekly digest - week {summary.get('week', '')}", body=digest)
    print("Weekly digest sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
