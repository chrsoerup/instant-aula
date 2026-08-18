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
givet som JSON med to kilder: "calendar_events" (skema/kalender) og \
"meebook_weekplan" (ugeplanens noter og opgaver pr. dag), til et ugebrev til \
en forælder.

Gruppér begge kilder efter dato. Spring dage uden indhold i nogen af kilderne \
over. Oversæt IKKE og opfind ikke indhold -- brug teksten som den står \
(dansk). Medtag ALT indhold fra "meebook_weekplan" for hver dato, ikke kun et \
udpluk.

Svar KUN med et JSON-objekt på denne form, sorteret efter dato:
{"days": [{"date": "YYYY-MM-DD", \
"events": ["kort punkt fra calendar_events", ...], \
"notes": ["kort punkt fra meebook_weekplan", ...]}]}

Hvert punkt skal være kort (én linje), fx "Kl. 8.00-8.45: Dansk med Mette \
Bondesen" for et event, eller "HUSK LÆSEBOGEN!" for en note. Brug en tom \
liste [] hvis en kilde intet har for den dato -- udelad aldrig nøglerne. \
Ingen andre nøgler, ingen forklarende tekst uden for JSON'en."""

_WEEKDAYS_DA = ("Mandag", "Tirsdag", "Onsdag", "Torsdag", "Fredag", "Lørdag", "Søndag")


def _weekday_da(date_str: str) -> str:
    try:
        date = datetime.date.fromisoformat(date_str)
    except ValueError:
        return date_str
    return f"{_WEEKDAYS_DA[date.weekday()]} d. {date.day}/{date.month}"


def _cell_list_html(items: list) -> str:
    if not items:
        return "&mdash;"
    return '<ul style="margin:0;padding-left:18px;">' + "".join(
        f"<li>{html.escape(str(item))}</li>" for item in items
    ) + "</ul>"


def _render_html(days: list[dict]) -> str:
    td = 'style="padding:8px 12px;border:1px solid #ddd;vertical-align:top;"'
    rows = []
    for day in days:
        events, notes = day.get("events") or [], day.get("notes") or []
        if not events and not notes:
            continue
        rows.append(
            "<tr>"
            f'<td {td} white-space:nowrap;font-weight:bold;">{html.escape(_weekday_da(day.get("date", "")))}</td>'
            f"<td {td}>{_cell_list_html(events)}</td>"
            f"<td {td}>{_cell_list_html(notes)}</td>"
            "</tr>"
        )
    if not rows:
        return "<p>Ingen planlagte aktiviteter fundet for denne uge.</p>"

    th = 'style="padding:8px 12px;border:1px solid #ddd;text-align:left;background:#f2f2f2;"'
    return (
        '<table style="border-collapse:collapse;font-family:sans-serif;font-size:14px;">'
        f"<tr><th {th}>Dag</th><th {th}>Skema</th><th {th}>Noter/lektier</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def _render_plain_text(days: list[dict]) -> str:
    lines = []
    for day in days:
        events, notes = day.get("events") or [], day.get("notes") or []
        if not events and not notes:
            continue
        lines.append(_weekday_da(day.get("date", "")) + ":")
        if events:
            lines.append("  Skema:")
            lines.extend(f"  - {item}" for item in events)
        if notes:
            lines.append("  Noter/lektier:")
            lines.extend(f"  - {item}" for item in notes)
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
