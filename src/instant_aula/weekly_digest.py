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

DIGEST_SYSTEM_PROMPT = """Du skriver et kort, venligt ugebrev til en forælder \
ud fra deres barns skoledata (Aula/Meebook), givet som JSON (kalenderbegivenheder \
og en dag-for-dag ugeplan med opgaver). Opsummer hvad der sker dag for dag \
denne uge, lektier eller ting der skal med, og datoer der er værd at huske. \
Spring stille tomme dage over. Hold det kortfattet og i almindeligt sprog -- \
dette erstatter at læse snesevis af rå notifikationer. Skriv KUN selve \
ugebrevet: ingen indledning som "Her er ugebrevet", ingen JSON, ingen \
overskrifter, ingen kommentarer om kildedataen -- bare teksten selv, som korte \
afsnit eller en simpel dag-for-dag liste, på dansk."""


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
