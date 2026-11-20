"""Publish Aula's school events as an iCalendar feed for Home Assistant.

Writes `skole.ics` -- special school events (trips, days off, school photos,
...), important dates, the parent meetings you have booked, and one condensed
line per school day from the Meebook weekplan (see day_summary) -- for
`ics_server` to serve on the add-on's internal network, where Home Assistant's
Remote calendar integration reads it. Nothing is written into Home Assistant
itself, so there is nothing to keep in sync: every run regenerates the whole
file from Aula, and an event Aula drops simply isn't in the next file.

What is deliberately left out:

- Regular lessons (`event_type == "lesson"`). Roughly 40 a week; on a family
  calendar they bury everything else.
- Meeting events from the calendar endpoint (`MEETING_TYPES`). Those describe
  the school's whole booking window, not your slot; the `meetings` command
  returns only the time you actually booked, so meetings come from there.
- Birthdays. Not wanted.

Deterministic, like the rest of instant-aula: event titles are Aula's own; the
day lines come from fixed rules in day_summary, with the teacher's full text in
each event's description.

After writing the file, asks Home Assistant to refresh the calendar entity
named in HA_CALENDAR_ENTITY, because Remote calendar on its own only fetches
once a day. Leave it empty to skip that step.
"""

from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from . import day_summary
from .aula_cli import run_aula
from .config import PROJECT_ROOT, load_settings
from .ha_notify import call_service
from .notify_failure import notify_failure

TZ = ZoneInfo("Europe/Copenhagen")
LOOKAHEAD_DAYS = 56  # eight weeks of school events
CHUNK_DAYS = 14  # Aula's calendar endpoint is asked for at most two weeks at a time
EXCLUDED_TYPES = frozenset({"lesson", "birthday", "school_home_meeting", "parental_meeting"})
ICS_FILENAME = "skole.ics"
CALENDAR_NAME = "Skole"


def ics_dir() -> Path:
    return Path(os.environ.get("ICS_DIR", str(PROJECT_ROOT / "state" / "www")))


# --------------------------------------------------------------------------
# Aula -> plain event dicts
# --------------------------------------------------------------------------


def _parse_dt(value) -> datetime.datetime | None:
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).replace(" ", "T", 1))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


def _looks_all_day(start: datetime.datetime, end: datetime.datetime) -> bool:
    """Aula's all-day flag is not in the CLI's JSON, so infer it.

    All-day events come back as local midnight to (nearly) the next midnight,
    or to 23:59 on the last day of a multi-day event.
    """
    s, e = start.astimezone(TZ), end.astimezone(TZ)
    starts_at_midnight = (s.hour, s.minute) == (0, 0)
    ends_at_day_edge = (e.hour, e.minute) in {(0, 0), (23, 59)}
    return starts_at_midnight and ends_at_day_edge and (e - s) >= datetime.timedelta(hours=23)


def _event(uid: str, title: str, start, end, location=None, description=None) -> dict | None:
    start_dt, end_dt = _parse_dt(start), _parse_dt(end)
    if start_dt is None:
        return None
    if end_dt is None or end_dt <= start_dt:
        end_dt = start_dt + datetime.timedelta(hours=1)
    return {
        "uid": uid,
        "title": (title or "").strip() or "Skolebegivenhed",
        "start": start_dt,
        "end": end_dt,
        "all_day": _looks_all_day(start_dt, end_dt),
        "location": location or None,
        "description": description or None,
    }


def school_events(calendar_rows: list[dict], important_ids: set) -> list[dict]:
    """Non-lesson calendar events, deduplicated by Aula id.

    Aula returns an event shared by several children (or several staff) more
    than once; the id is the same, so one copy is kept.
    """
    events: dict[str, dict] = {}
    for row in calendar_rows:
        if (row.get("event_type") or "").lower() in EXCLUDED_TYPES:
            continue
        event_id = row.get("id")
        if event_id is None:
            continue
        title = row.get("title") or ""
        if event_id in important_ids:
            title = f"❗ {title}"
        ev = _event(f"aula-event-{event_id}@instant-aula", title,
                    row.get("start_datetime"), row.get("end_datetime"), row.get("location"))
        if ev:
            events.setdefault(ev["uid"], ev)
    return list(events.values())


def important_date_events(rows: list[dict], known_uids: set[str]) -> list[dict]:
    """Important dates that the calendar window didn't already return.

    These are Aula's raw records (camelCase), not the CLI's dataclasses.
    """
    out = []
    for row in rows:
        event_id = row.get("id")
        if event_id is None or (row.get("type") or "").lower() in EXCLUDED_TYPES:
            continue
        uid = f"aula-event-{event_id}@instant-aula"
        if uid in known_uids:
            continue
        ev = _event(uid, f"❗ {row.get('title') or ''}".strip(),
                    row.get("startDateTime") or row.get("startDate"),
                    row.get("endDateTime") or row.get("endDate"))
        if ev:
            if row.get("allDay") is True:
                ev["all_day"] = True
            out.append(ev)
    return out


def meeting_events(rows: list[dict], child_count: int) -> list[dict]:
    """Booked parent meetings, at the time actually booked."""
    out = []
    for row in rows:
        meeting_id = row.get("meeting_id")
        if meeting_id is None:
            continue
        title = row.get("title") or "Samtale"
        first_name = (row.get("child_name") or "").split(" ")[0]
        if child_count > 1 and first_name:
            title = f"{first_name}: {title}"
        ev = _event(f"aula-meeting-{meeting_id}-{row.get('child_id')}@instant-aula", title,
                    row.get("start_datetime"), row.get("end_datetime"), row.get("location"))
        if ev:
            ev["all_day"] = False  # a booked slot is never all-day
            out.append(ev)
    return out


# --------------------------------------------------------------------------
# iCalendar rendering (RFC 5545), no dependency needed for this subset
# --------------------------------------------------------------------------


def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """Fold to 75 octets per line, never splitting a UTF-8 character."""
    out, current, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        limit = 75 if not out else 74  # continuation lines start with a space
        if size + n > limit:
            out.append(current)
            current, size = "", 0
        current += ch
        size += n
    out.append(current)
    return "\r\n ".join(out)


def _utc(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def render_ics(events: list[dict], now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//instant-aula//calendar_sync//DA",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{CALENDAR_NAME}",
        "X-WR-TIMEZONE:Europe/Copenhagen",
    ]
    for ev in sorted(events, key=lambda e: (e["start"], e["uid"])):
        lines += ["BEGIN:VEVENT", f"UID:{ev['uid']}", f"DTSTAMP:{_utc(now)}"]
        if ev["all_day"]:
            first = ev["start"].astimezone(TZ).date()
            last = (ev["end"] - datetime.timedelta(seconds=1)).astimezone(TZ).date()
            lines += [f"DTSTART;VALUE=DATE:{first:%Y%m%d}",
                      f"DTEND;VALUE=DATE:{last + datetime.timedelta(days=1):%Y%m%d}"]
        else:
            lines += [f"DTSTART:{_utc(ev['start'])}", f"DTEND:{_utc(ev['end'])}"]
        lines.append(f"SUMMARY:{_escape(ev['title'])}")
        if ev.get("location"):
            lines.append(f"LOCATION:{_escape(ev['location'])}")
        if ev.get("description"):
            lines.append(f"DESCRIPTION:{_escape(ev['description'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def write_atomically(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="")
    tmp.replace(path)  # the server never sees a half-written file


# --------------------------------------------------------------------------
# Job
# --------------------------------------------------------------------------


def _fetch_calendar(settings, today: datetime.date) -> list[dict]:
    rows: list[dict] = []
    start = today
    end_of_window = today + datetime.timedelta(days=LOOKAHEAD_DAYS)
    while start < end_of_window:
        end = min(start + datetime.timedelta(days=CHUNK_DAYS - 1), end_of_window)
        rows += run_aula(settings, "calendar", "--start-date", f"{start:%Y-%m-%d}",
                         "--end-date", f"{end:%Y-%m-%d}") or []
        start = end + datetime.timedelta(days=1)
    return rows


def day_events(settings, today: datetime.date) -> list[dict]:
    """One condensed all-day line per school day, this week and next.

    Next week mostly yields only timetable gear (Idrætstøj): teachers write the
    Meebook plan during the week itself. A failure here is logged and skipped
    rather than raised, so the school events above still get published.
    """
    out = []
    for offset in (0, 7):
        iso_year, iso_week, _ = (today + datetime.timedelta(days=offset)).isocalendar()
        week = f"{iso_year}-W{iso_week}"
        try:
            summary = run_aula(settings, "weekly-summary", "--provider", "meebook", "--week", week)
        except Exception as exc:
            print(f"calendar_sync: day lines for {week} skipped -- weekly-summary failed: {exc}")
            continue
        for day in day_summary.days_from_weekly_summary(summary):
            if day.date < today:
                continue
            s = day_summary.summarise(day)
            if s.title is None:
                continue
            print(f"calendar_sync: {day.date} {s.title!r} -- {s.note_count} notes, "
                  f"{len(s.unmatched)} without a match"
                  + (f", timetable gear cancelled by notes: {', '.join(s.cancelled)}" if s.cancelled else ""))
            out.append({
                "uid": f"aula-day-{day.date:%Y%m%d}@instant-aula",
                "title": s.title,
                "start": datetime.datetime.combine(day.date, datetime.time(), TZ),
                "end": datetime.datetime.combine(day.date + datetime.timedelta(days=1), datetime.time(), TZ),
                "all_day": True,
                "location": None,
                "description": s.description or None,
            })
    return out


def main() -> int:
    settings = load_settings()
    today = datetime.datetime.now(TZ).date()

    calendar_rows = _fetch_calendar(settings, today)
    important_rows = run_aula(settings, "important-dates", "--limit", "50") or []
    meeting_rows = run_aula(settings, "meetings") or []

    important_ids = {row.get("id") for row in important_rows if row.get("id") is not None}
    events = school_events(calendar_rows, important_ids)
    events += important_date_events(important_rows, {ev["uid"] for ev in events})
    child_count = len({row.get("child_id") for row in meeting_rows if row.get("child_id")})
    events += meeting_events(meeting_rows, child_count)
    events += day_events(settings, today)

    skipped = sum(1 for r in calendar_rows if (r.get("event_type") or "").lower() in EXCLUDED_TYPES)
    types = sorted({str(r.get("event_type")) for r in calendar_rows
                    if (r.get("event_type") or "").lower() not in EXCLUDED_TYPES})
    print(f"calendar_sync: {len(calendar_rows)} calendar rows ({skipped} lessons/meetings/birthdays "
          f"skipped, kept types: {', '.join(types) or 'none'}), {len(important_rows)} important dates, "
          f"{len(meeting_rows)} booked meetings -> {len(events)} events")

    path = ics_dir() / ICS_FILENAME
    write_atomically(path, render_ics(events))
    print(f"calendar_sync: wrote {path}")

    entity = os.environ.get("HA_CALENDAR_ENTITY", "").strip()
    if entity:
        try:
            call_service("homeassistant", "update_entity", {"entity_id": entity})
            print(f"calendar_sync: asked Home Assistant to refresh {entity}")
        except Exception as exc:  # the file is written; a missed refresh only delays it
            print(f"calendar_sync: could not refresh {entity}: {exc}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        notify_failure("calendar_sync", exc)
        raise
