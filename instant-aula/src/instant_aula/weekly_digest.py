"""Fetch this week's Aula/Meebook plan and push it as a Home Assistant
notification. Run once a week (e.g. via cron).

Grouping and formatting deliberately does NOT involve the local LLM: the
source data (calendar events, Meebook weekplan notes) is already clean,
well-formatted Danish text -- including the teacher's own "___" section
breaks between agenda items -- so grouping it by date and splitting it
into bullets is a plain formatting job. Doing that in Python instead of
asking a model to also correlate two different date formats (ISO
timestamps vs. Danish day labels like "mandag 17. aug.") in one big pass
is instant, never drops content, and preserves the teacher's exact
original wording.

A separate, optional pass (see highlights.py) *does* use a local Ollama
model, to pull out the handful of parent-actionable reminders (bring gym
clothes, bring the "læsemappe", homework due, etc.) buried in that same
text -- a genuine judgment call, unlike the deterministic grouping above.
"""

from __future__ import annotations

import datetime
import re
import sys
from collections import defaultdict

from .aula_cli import run_aula
from .config import load_settings
from .ha_notify import notify
from .highlights import extract_highlights
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


def _render_highlights_plain(highlights: list[tuple[str, str]] | None) -> str:
    if not highlights:
        return ""
    lines = ["Husk:"]
    lines.extend(f"- {_weekday_da(date)}: {text}" for date, text in sorted(highlights))
    return "\n".join(lines) + "\n\n"


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
    highlights = extract_highlights(settings, dates, events, notes)

    # Diagnostic trail for the "notes came back empty" issue seen once so
    # far -- pins down whether a recurrence is missing data from Aula's own
    # API, or a bug in the grouping step, without needing to reproduce it live.
    print(
        f"Fetched week {summary.get('week')}: requested={week}, "
        f"raw_meebook_tasks={raw_task_count}, days_with_events={len(events)}, "
        f"days_with_notes={len(notes)}, total_note_items={sum(len(v) for v in notes.values())}, "
        f"highlights={len(highlights) if highlights else 0}"
    )

    notify(
        settings,
        title=f"Aula ugebrev - uge {summary.get('week', '')}",
        message=_render_highlights_plain(highlights) + _render_plain_text(dates, events, notes),
    )
    print("Weekly digest sent.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        notify_failure("weekly_digest", exc)
        raise
