"""Fetch this week's Aula/Meebook plan and email it as a reader-friendly
table. Run once a week (e.g. via cron).

Deliberately does NOT involve the local LLM: the source data (calendar
events, Meebook weekplan notes) is already clean, well-formatted Danish
text -- including the teacher's own "___" section breaks between agenda
items -- so grouping it by date and splitting it into bullets is a plain
formatting job. Doing that in Python instead of asking a model to also
correlate two different date formats (ISO timestamps vs. Danish day labels
like "mandag 17. aug.") in one big pass is instant, never drops content, and
preserves the teacher's exact original wording.
"""

from __future__ import annotations

import datetime
import html
import re
import sys
from collections import defaultdict

from .aula_cli import run_aula
from .config import load_settings
from .emailer import send
from .notify_failure import notify_failure

_WEEKDAYS_DA = ("Mandag", "Tirsdag", "Onsdag", "Torsdag", "Fredag", "Lørdag", "Søndag")

_DA_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
}
_MEEBOOK_DATE_RE = re.compile(r"(\d{1,2})\.\s*([a-zæøå]+)", re.IGNORECASE)
_SEPARATOR_RE = re.compile(r"_{3,}")


def _weekday_da(date_str: str) -> str:
    try:
        date = datetime.date.fromisoformat(date_str)
    except ValueError:
        return date_str
    return f"{_WEEKDAYS_DA[date.weekday()]} d. {date.day}/{date.month}"


def _parse_meebook_date(date_label: str, year: int) -> str | None:
    """Meebook day labels look like 'mandag 17. aug.' -- turn that into an ISO date."""
    match = _MEEBOOK_DATE_RE.search(date_label or "")
    if not match:
        return None
    month = _DA_MONTHS.get(match.group(2).lower()[:3])
    if month is None:
        return None
    return f"{year:04d}-{month:02d}-{int(match.group(1)):02d}"


def _split_note_lines(content: str) -> list[str]:
    lines = []
    for block in _SEPARATOR_RE.split(content or ""):
        for line in block.splitlines():
            line = line.strip()
            if line:
                lines.append(line)
    return lines


def _group_events(events: list[dict]) -> dict[str, list[str]]:
    parsed = []
    for event in events:
        try:
            start = datetime.datetime.fromisoformat(event["start_datetime"])
            end = datetime.datetime.fromisoformat(event["end_datetime"])
        except (KeyError, ValueError):
            continue
        parsed.append((start, end, event))

    grouped: dict[str, list[str]] = defaultdict(list)
    for start, end, event in sorted(parsed, key=lambda p: p[0]):
        line = f"Kl. {start:%H.%M}-{end:%H.%M}: {event.get('title') or '?'}"
        if event.get("teacher_name"):
            line += f" ({event['teacher_name']})"
        if event.get("has_substitute") and event.get("substitute_name"):
            line += f" -- vikar: {event['substitute_name']}"
        if event.get("location"):
            line += f" [{event['location']}]"
        grouped[start.date().isoformat()].append(line)
    return grouped


def _group_notes(students: list[dict], year: int) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for student in students:
        for day in student.get("week_plan", []):
            date = _parse_meebook_date(day.get("date", ""), year)
            if date is None:
                continue
            for task in day.get("tasks", []):
                pill = task.get("pill")
                prefix = f"[{pill}] " if pill else ""
                grouped[date].extend(prefix + line for line in _split_note_lines(task.get("content")))
    return grouped


def _cell_list_html(items: list[str]) -> str:
    if not items:
        return "&mdash;"
    return '<ul style="margin:0;padding-left:18px;">' + "".join(
        f"<li>{html.escape(item)}</li>" for item in items
    ) + "</ul>"


def _render_html(dates: list[str], events: dict[str, list[str]], notes: dict[str, list[str]]) -> str:
    td = 'style="padding:8px 12px;border:1px solid #ddd;vertical-align:top;"'
    rows = []
    for date in dates:
        rows.append(
            "<tr>"
            f'<td {td} white-space:nowrap;font-weight:bold;">{html.escape(_weekday_da(date))}</td>'
            f"<td {td}>{_cell_list_html(events.get(date, []))}</td>"
            f"<td {td}>{_cell_list_html(notes.get(date, []))}</td>"
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


def _render_plain_text(dates: list[str], events: dict[str, list[str]], notes: dict[str, list[str]]) -> str:
    lines = []
    for date in dates:
        lines.append(_weekday_da(date) + ":")
        if events.get(date):
            lines.append("  Skema:")
            lines.extend(f"  - {item}" for item in events[date])
        if notes.get(date):
            lines.append("  Noter/lektier:")
            lines.extend(f"  - {item}" for item in notes[date])
        lines.append("")
    return "\n".join(lines).strip() or "Ingen planlagte aktiviteter fundet for denne uge."


def _next_iso_week() -> str:
    """ISO week for 7 days from now -- lands in "next week" regardless of
    which day of the current week this runs on (e.g. run on a Saturday to
    get a look-ahead digest for the upcoming Mon-Sun week)."""
    target = datetime.date.today() + datetime.timedelta(days=7)
    iso_year, iso_week, _ = target.isocalendar()
    return f"{iso_year}-W{iso_week}"


def main() -> int:
    settings = load_settings()

    week = _next_iso_week()
    summary = run_aula(settings, "weekly-summary", "--provider", "meebook", "--week", week)
    year = int(summary.get("week", "").split("-W")[0] or datetime.date.today().year)

    raw_task_count = sum(
        len(day.get("tasks", []))
        for student in summary.get("meebook_weekplan", [])
        for day in student.get("week_plan", [])
    )
    events = _group_events(summary.get("calendar_events", []))
    notes = _group_notes(summary.get("meebook_weekplan", []), year)
    dates = sorted(set(events) | set(notes))

    # Diagnostic trail for the "notes came back empty" issue seen once so
    # far -- pins down whether a recurrence is missing data from Aula's own
    # API, or a bug in the grouping step, without needing to reproduce it live.
    print(
        f"Fetched week {summary.get('week')}: requested={week}, "
        f"raw_meebook_tasks={raw_task_count}, days_with_events={len(events)}, "
        f"days_with_notes={len(notes)}, total_note_items={sum(len(v) for v in notes.values())}"
    )

    send(
        settings,
        subject=f"Aula ugebrev - uge {summary.get('week', '')}",
        body=_render_plain_text(dates, events, notes),
        html=_render_html(dates, events, notes),
    )
    print("Weekly digest sent.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        notify_failure("weekly_digest", exc)
        raise
