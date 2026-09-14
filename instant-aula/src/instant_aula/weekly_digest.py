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

There was an optional Ollama pass (highlights.py) that prepended a "Husk"
list of parent-actionable reminders. It is no longer wired in: measured
against one real week on 2026-09-14, llama3.1:8b dropped the week's maths
homework on one run and on the next invented a "medbring madpakke" errand
lifted straight from its own prompt's worked example. A summary whose
misses and inventions are both invisible is worse than no summary. See
highlights.py if reviving it.
"""

from __future__ import annotations

import datetime
import re
import sys
from collections import defaultdict

from .aula_cli import run_aula
from .config import load_settings
from .ha_notify import notify
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


def _teacher_label(event: dict) -> str:
    """Teacher for one event, with the substitute noted where there is one."""
    teacher = event.get("teacher_name") or ""
    if event.get("has_substitute") and event.get("substitute_name"):
        substitute = f"vikar: {event['substitute_name']}"
        return f"{teacher} -- {substitute}" if teacher else substitute
    return teacher


def _render_slot(start, end, events: list[dict]) -> str:
    """One timetable line per time slot, however many staff Aula lists for it.

    Aula returns one event per *staff assignment*, not per lesson: a Danish
    lesson with a resource teacher present comes back as two events with
    different ids on the same slot (DAN/Mette + RES/Katrine), and a PE lesson
    with two teachers as two IDR events. Printed one per line that was 40 lines
    to describe 26 periods, and it read as though Monday 09.50 held two separate
    lessons. Measured on week 2026-W38: 12 of 26 slots carried more than one
    event, and all 11 RES events shadowed another lesson.

    Same-titled events collapse into one entry with the teachers listed
    together; different titles are joined with "+" so a supported lesson still
    shows both roles.
    """
    by_title: dict[str, list[str]] = {}
    locations: list[str] = []
    for event in events:
        title = event.get("title") or "?"
        teacher = _teacher_label(event)
        labels = by_title.setdefault(title, [])
        if teacher and teacher not in labels:
            labels.append(teacher)
        location = event.get("location")
        if location and location not in locations:
            locations.append(location)

    parts = [f"{title} ({', '.join(labels)})" if labels else title for title, labels in by_title.items()]
    line = f"Kl. {start:%H.%M}-{end:%H.%M}: " + " + ".join(parts)
    if locations:
        line += f" [{', '.join(locations)}]"
    return line


def _group_events(events: list[dict]) -> dict[str, list[str]]:
    parsed = []
    for event in events:
        try:
            start = datetime.datetime.fromisoformat(event["start_datetime"])
            end = datetime.datetime.fromisoformat(event["end_datetime"])
        except (KeyError, ValueError):
            continue
        parsed.append((start, end, event))

    slots: dict[tuple, list[dict]] = {}
    for start, end, event in sorted(parsed, key=lambda p: (p[0], p[1])):
        slots.setdefault((start, end), []).append(event)

    grouped: dict[str, list[str]] = defaultdict(list)
    for (start, end), slot_events in slots.items():
        grouped[start.date().isoformat()].append(_render_slot(start, end, slot_events))
    return grouped


def _group_notes(students: list[dict], year: int) -> dict[str, dict[str, list[str]]]:
    """Group notes by date, then by subject.

    Returns {date: {heading: [line, ...]}}. The subject ("pill") becomes a
    heading rendered once per group rather than a "[Dansk] " prefix repeated on
    every single line -- a week's notes are mostly one or two subjects per day,
    so the prefix was pure repetition.

    The heading carries the child's name only when more than one child has notes
    that week: with one child it is noise, with two, merging their subjects into
    a shared "Dansk" heading would silently attribute one child's homework to
    the other.
    """
    with_notes = {
        student.get("name")
        for student in students
        for day in student.get("week_plan", [])
        if day.get("tasks")
    }
    name_headings = len(with_notes) > 1

    grouped: dict[str, dict[str, list[str]]] = defaultdict(dict)
    for student in students:
        first_name = (student.get("name") or "").split(" ")[0]
        for day in student.get("week_plan", []):
            date = _parse_meebook_date(day.get("date", ""), year)
            if date is None:
                continue
            for task in day.get("tasks", []):
                lines = _split_note_lines(task.get("content"))
                if not lines:
                    continue
                heading = (task.get("pill") or "Noter").strip()
                if name_headings and first_name:
                    heading = f"{first_name} - {heading}"
                grouped[date].setdefault(heading, []).extend(lines)
    return grouped


def _note_item_count(notes: dict[str, dict[str, list[str]]]) -> int:
    return sum(len(lines) for groups in notes.values() for lines in groups.values())


def _render_plain_text(
    dates: list[str], events: dict[str, list[str]], notes: dict[str, dict[str, list[str]]]
) -> str:
    lines = []
    for date in dates:
        lines.append(_weekday_da(date) + ":")
        if events.get(date):
            lines.append("  Skema:")
            lines.extend(f"  - {item}" for item in events[date])
        for heading, items in notes.get(date, {}).items():
            lines.append(f"  {heading}:")
            lines.extend(f"  - {item}" for item in items)
        lines.append("")
    return "\n".join(lines).strip() or "Ingen planlagte aktiviteter fundet for denne uge."


def _current_iso_week() -> str:
    """ISO week containing today.

    This used to ask for *next* week, so a Saturday run doubled as a weekend
    look-ahead. In practice that reliably produced a timetable with no notes:
    Aula publishes the calendar a week ahead, but teachers write their Meebook
    weekplans for the week they are in -- often referring forward from it
    ("vi fortsætter i næste uge med...") rather than filling the next week out.
    Measured on 2026-09-14: current week 7 tasks, next week 0.

    Paired with the Monday 06:00 cron slot in run.sh, this delivers the week
    you are entering, with notes, before school starts.
    """
    iso_year, iso_week, _ = datetime.date.today().isocalendar()
    return f"{iso_year}-W{iso_week}"


def main() -> int:
    settings = load_settings()

    week = _current_iso_week()
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
        f"days_with_notes={len(notes)}, total_note_items={_note_item_count(notes)}"
    )

    notify(
        settings,
        title=f"Aula ugebrev - uge {summary.get('week', '')}",
        message=_render_plain_text(dates, events, notes),
    )
    print("Weekly digest sent.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        notify_failure("weekly_digest", exc)
        raise
