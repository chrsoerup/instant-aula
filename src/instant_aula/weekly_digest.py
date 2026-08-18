"""Fetch this week's Aula/Meebook plan, ask a local LLM to structure it into
day-by-day items, and email it as a reader-friendly table. Run once a week
(e.g. via cron).
"""

from __future__ import annotations

import datetime
import html
import json
import sys

from .aula_cli import run_aula
from .config import load_settings
from .emailer import send
from .ollama_client import chat

DIGEST_SYSTEM_PROMPT = """Du strukturerer et barns skoledata (Aula/Meebook), \
givet som JSON (kalenderbegivenheder og en dag-for-dag ugeplan med opgaver), \
til et ugebrev til en forælder.

Slå kalenderbegivenheder og ugeplan-opgaver sammen, så alt der hører til \
samme dato står under den dato. Spring dage uden indhold over. Oversæt \
IKKE og opfind ikke indhold -- brug teksten som den står (dansk).

Svar KUN med et JSON-objekt på denne form, sorteret efter dato:
{"days": [{"date": "YYYY-MM-DD", "items": ["kort punkt 1", "kort punkt 2"]}]}

Hvert punkt i "items" skal være kort (én linje), fx "Kl. 8.00-8.45: Dansk med \
Mette Bondesen" eller "HUSK LÆSEBOGEN!". Ingen andre nøgler, ingen forklarende \
tekst uden for JSON'en."""

_WEEKDAYS_DA = ("Mandag", "Tirsdag", "Onsdag", "Torsdag", "Fredag", "Lørdag", "Søndag")


def _weekday_da(date_str: str) -> str:
    try:
        date = datetime.date.fromisoformat(date_str)
    except ValueError:
        return date_str
    return f"{_WEEKDAYS_DA[date.weekday()]} d. {date.day}/{date.month}"


def _render_html(days: list[dict]) -> str:
    rows = []
    for day in days:
        items = day.get("items") or []
        if not items:
            continue
        items_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in items)
        rows.append(
            "<tr>"
            f'<td style="padding:8px 12px;border:1px solid #ddd;vertical-align:top;'
            f'white-space:nowrap;font-weight:bold;">{html.escape(_weekday_da(day.get("date", "")))}</td>'
            f'<td style="padding:8px 12px;border:1px solid #ddd;">'
            f'<ul style="margin:0;padding-left:18px;">{items_html}</ul></td>'
            "</tr>"
        )
    if not rows:
        return "<p>Ingen planlagte aktiviteter fundet for denne uge.</p>"

    return (
        '<table style="border-collapse:collapse;font-family:sans-serif;font-size:14px;">'
        "<tr>"
        '<th style="padding:8px 12px;border:1px solid #ddd;text-align:left;background:#f2f2f2;">Dag</th>'
        '<th style="padding:8px 12px;border:1px solid #ddd;text-align:left;background:#f2f2f2;">Program</th>'
        "</tr>" + "".join(rows) + "</table>"
    )


def _render_plain_text(days: list[dict]) -> str:
    lines = []
    for day in days:
        items = day.get("items") or []
        if not items:
            continue
        lines.append(_weekday_da(day.get("date", "")) + ":")
        lines.extend(f"- {item}" for item in items)
        lines.append("")
    return "\n".join(lines).strip() or "Ingen planlagte aktiviteter fundet for denne uge."


def main() -> int:
    settings = load_settings()

    summary = run_aula(settings, "weekly-summary", "--provider", "meebook")

    reply = chat(
        settings.ollama_host,
        settings.ollama_model,
        DIGEST_SYSTEM_PROMPT,
        json.dumps(summary, ensure_ascii=False),
        json_mode=True,
    )

    try:
        days = json.loads(reply)["days"]
        plain_body = _render_plain_text(days)
        html_body = _render_html(days)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"Could not parse structured digest ({exc}); falling back to raw model reply.")
        plain_body = reply
        html_body = None

    send(
        settings,
        subject=f"Aula ugebrev - uge {summary.get('week', '')}",
        body=plain_body,
        html=html_body,
    )
    print("Weekly digest sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
