"""Publish Aula's school events as an iCalendar feed for Home Assistant.

Writes one feed per child (`<first name>.ics`, e.g. `anna.ics`, `ben.ics`) --
special school events (trips, days off, parties, ...), important dates, the
parent meetings you have booked, and one condensed line per school day from the
Meebook weekplan (see day_summary) -- for `ics_server` to serve on the add-on's
internal network, where Home Assistant's Remote calendar integration reads each
one as its own calendar. Nothing is written into Home Assistant itself, so there
is nothing to keep in sync: every run regenerates the whole files from Aula, and
an event Aula drops simply isn't in the next file.

Whose event is whose: Aula answers for itself. The calendar is fetched once per
child (`aula calendar --institution-profile-id <child>`), so an event comes back
for the child it belongs to; an event shared by both children comes back for
both and appears in both feeds.

Events Aula addresses to a parent (the guardian profile of the Aula login this app uses), and to no child (a party "for the whole
family", a parents' meeting) are the third feed, `parent.ics`: the parents' calendar.
Nothing is guessed about which child they concern. Instead each one gets a
description with what Aula says about it -- institution (and which of your
children goes there), invited groups, invitees, who created it, whether a reply is
due, and Aula's own text -- so you can see who is invited.

`skole.ics` is the original combined feed, kept so a calendar set up before the
per-child feeds keeps working: with SCHOOL_FEED_CHILD set (part of a child's
name) it is that child's feed; left empty it holds everything, each entry
prefixed with the child's name (or "Forældre") unless it belongs to all children.

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
import re
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from . import day_summary
from .aula_cli import run_aula
from .html_utils import strip_html
from .config import PROJECT_ROOT, load_settings
from .ha_notify import call_service
from .notify_failure import notify_failure

TZ = ZoneInfo("Europe/Copenhagen")
LOOKAHEAD_DAYS = 56  # eight weeks of school events
CHUNK_DAYS = 14  # Aula's calendar endpoint is asked for at most two weeks at a time
EXCLUDED_TYPES = frozenset({"lesson", "birthday", "school_home_meeting", "parental_meeting"})
ICS_FILENAME = "skole.ics"
PARENT_FILENAME = "parent.ics"
PARENT_CALENDAR_NAME = "Forældre"
MAX_NAMES = 12  # invited people/groups listed in a description before "og N flere"
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


def render_ics(events: list[dict], now: datetime.datetime | None = None,
               calendar_name: str = CALENDAR_NAME) -> str:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//instant-aula//calendar_sync//DA",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_escape(calendar_name)}",
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
# Children
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Child:
    id: int      # Aula's institution profile id -- what `aula calendar` filters on
    name: str
    first: str
    slug: str    # feed file name: anna -> anna.ics
    institution_code: str | None = None
    institution_name: str | None = None


ALL = Child(0, "", "", "")  # stands in for "everyone" when the profile lists no children


def slugify(first_name: str) -> str:
    s = first_name.lower()
    for a, b in (("æ", "ae"), ("ø", "oe"), ("å", "aa")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "", s) or "child"


def parse_children(profile: dict) -> list[Child]:
    children, seen = [], set()
    for c in (profile or {}).get("children") or []:
        if c.get("id") is None or not c.get("name"):
            continue
        first = c["name"].split(" ")[0]
        slug = slugify(first)
        if slug in seen:  # two children with the same first name
            slug = f"{slug}-{c['id']}"
        seen.add(slug)
        children.append(Child(int(c["id"]), c["name"], first, slug,
                              str(c["institution_code"]) if c.get("institution_code") else None,
                              c.get("institution_name") or None))
    return children


def load_children(settings) -> list[Child]:
    try:
        return parse_children(run_aula(settings, "profile"))
    except Exception as exc:  # fall back to one combined feed rather than publish nothing
        print(f"calendar_sync: could not read the children from the profile ({exc}); "
              "publishing one combined feed")
        return []


# --------------------------------------------------------------------------
# Job
# --------------------------------------------------------------------------


def _fetch_calendar(settings, today: datetime.date, child_ids=()) -> list[dict]:
    """Calendar rows for the next LOOKAHEAD_DAYS; for the given children, or for
    everything the profile can see when no ids are given."""
    who = [arg for cid in child_ids for arg in ("--institution-profile-id", str(cid))]
    rows: list[dict] = []
    start = today
    end_of_window = today + datetime.timedelta(days=LOOKAHEAD_DAYS)
    while start < end_of_window:
        end = min(start + datetime.timedelta(days=CHUNK_DAYS - 1), end_of_window)
        rows += run_aula(settings, "calendar", *who, "--start-date", f"{start:%Y-%m-%d}",
                         "--end-date", f"{end:%Y-%m-%d}") or []
        start = end + datetime.timedelta(days=1)
    return rows


def day_events(settings, today: datetime.date, children: list[Child]) -> dict[int, list[dict]]:
    """One condensed all-day line per school day and child, this week and next.

    Next week mostly yields only timetable gear (Idrætstøj): teachers write the
    Meebook plan during the week itself. A failure here is logged and skipped
    rather than raised, so the school events still get published. The weekly
    summary covers every child; each child's lines are cut out of it by the
    child's profile id (lessons) and first name (Meebook notes).
    """
    out: dict[int, list[dict]] = {c.id: [] for c in children}
    for offset in (0, 7):
        iso_year, iso_week, _ = (today + datetime.timedelta(days=offset)).isocalendar()
        week = f"{iso_year}-W{iso_week}"
        try:
            summary = run_aula(settings, "weekly-summary", "--provider", "meebook", "--week", week)
        except Exception as exc:
            print(f"calendar_sync: day lines for {week} skipped -- weekly-summary failed: {exc}")
            continue
        for c in children:
            for day in day_summary.days_from_weekly_summary(summary, c.id or None, c.first or None):
                if day.date < today:
                    continue
                s = day_summary.summarise(day)
                if s.title is None:
                    continue
                print(f"calendar_sync: {c.first or 'all'} {day.date} {s.title!r} -- {s.note_count} notes, "
                      f"{len(s.unmatched)} without a match"
                      + (f", timetable gear cancelled by notes: {', '.join(s.cancelled)}" if s.cancelled else ""))
                stem = f"{c.slug}-" if c.slug else ""
                out[c.id].append({
                    "uid": f"aula-day-{stem}{day.date:%Y%m%d}@instant-aula",
                    "title": s.title,
                    "start": datetime.datetime.combine(day.date, datetime.time(), TZ),
                    "end": datetime.datetime.combine(day.date + datetime.timedelta(days=1),
                                                     datetime.time(), TZ),
                    "all_day": True,
                    "location": None,
                    "description": s.description or None,
                })
    return out


def _plain(value) -> str:
    """Aula text fields are a string or {"html": ...}/{"text": ...}; return plain text."""
    if isinstance(value, dict):
        value = value.get("html") or value.get("text") or ""
    return strip_html(value) if isinstance(value, str) else ""


def _names(items, *keys: str) -> list[str]:
    """Names out of a list of strings or dicts (first of `keys` present), in order, no repeats."""
    out: list[str] = []
    for item in items or []:
        name = item if isinstance(item, str) else next(
            (item[k] for k in keys if isinstance(item, dict) and item.get(k)), "")
        if isinstance(name, str) and name.strip() and name.strip() not in out:
            out.append(name.strip())
    return out


def _listed(names: list[str]) -> str:
    return ", ".join(names[:MAX_NAMES]) + (f" og {len(names) - MAX_NAMES} flere" if len(names) > MAX_NAMES else "")


def describe_parent_event(raw: dict | None, children: list[Child], event_type: str | None = None,
                          location: str | None = None, important: bool = False) -> str:
    """What Aula says about an event addressed to the parent, so you can see who is invited.

    `raw` is Aula's own record of the event (`aula calendar --event-id`); None when it
    couldn't be fetched, in which case only what the calendar row itself says is used.
    Every field is optional: Aula's records differ by institution.
    """
    raw = raw if isinstance(raw, dict) else {}
    lines = ["Sendt til forældre i Aula – ikke til et af børnene."]

    groups = [g for g in raw.get("invitedGroups") or [] if isinstance(g, dict)]
    inst_name = raw.get("institutionName") or next((g.get("institutionName") for g in groups
                                                    if g.get("institutionName")), None)
    inst_code = raw.get("institutionCode") or next((g.get("institutionCode") for g in groups
                                                    if g.get("institutionCode")), None)
    if inst_name or inst_code:
        same = [c.first for c in children
                if (inst_code and c.institution_code and str(inst_code) == c.institution_code)
                or (inst_name and c.institution_name and inst_name == c.institution_name)]
        lines.append(f"Institution: {inst_name or inst_code}"
                     + (f" (samme institution som {' og '.join(same)})" if same else ""))

    if group_names := _names(groups, "name", "shortName", "groupName"):
        lines.append(f"Inviterede grupper: {_listed(group_names)}")
    invitees = _names(raw.get("invitees") or raw.get("invitedProfiles") or raw.get("participants"),
                      "displayName", "name", "fullName")
    if invitees:
        lines.append(f"Inviterede: {_listed(invitees)}")
    if creator := raw.get("creatorName") or raw.get("creatorAulaName"):
        lines.append(f"Oprettet af: {creator}")
    place = location or (raw.get("primaryResource") or {}).get("name") or raw.get("primaryResourceText")
    if place:
        lines.append(f"Sted: {place}")
    if raw.get("responseRequired"):
        deadline = raw.get("responseDeadline")
        lines.append("Svar kræves" + (f" senest {str(deadline)[:10]}" if deadline else "")
                     + (f" (status: {raw['responseStatus']})" if raw.get("responseStatus") else ""))
    if raw.get("hasAttachments"):
        lines.append("Har vedhæftede filer – se Aula.")
    if important:
        lines.append("Står på Aulas liste over vigtige datoer.")
    if event_type and event_type != "event":
        lines.append(f"Type: {event_type}")
    if text := _plain(raw.get("description")):
        lines += ["", text]
    return "\n".join(lines)


def _event_id(uid: str) -> int | None:
    m = re.fullmatch(r"aula-event-(\d+)@instant-aula", uid)
    return int(m.group(1)) if m else None


def event_details(settings, event_id: int | None) -> dict | None:
    """Aula's own record of one event, or None (logged) if it can't be fetched."""
    if event_id is None:
        return None
    try:
        raw = run_aula(settings, "calendar", "--event-id", str(event_id))
    except Exception as exc:
        print(f"calendar_sync: details for event {event_id} unavailable ({exc})")
        return None
    if isinstance(raw, dict):
        print(f"calendar_sync: event {event_id} details from Aula, fields: {', '.join(sorted(raw))}")
        return raw
    return None


def parent_feed_events(settings, rows: list[dict], extra: list[dict], important_ids: set,
                       children: list[Child]) -> list[dict]:
    """Events addressed to the parent only, each described with what Aula says about it."""
    out = []
    for r in rows:
        for ev in school_events([r], important_ids):
            raw = event_details(settings, r["id"])
            ev["description"] = describe_parent_event(raw, children, r.get("event_type"), r.get("location"),
                                                      important=r["id"] in important_ids)
            print(f"calendar_sync: parent calendar: {ev['title']!r} (type {r.get('event_type')}, "
                  f"profiles {r.get('belongs_to_profiles')})")
            out.append(ev)
    for ev in extra:  # important dates that are in no calendar window at all
        raw = event_details(settings, _event_id(ev["uid"]))
        out.append({**ev, "description": describe_parent_event(raw, children, important=True)})
        print(f"calendar_sync: parent calendar: important date {ev['title']!r}")
    return out


def merge_for_everyone(children: list[Child], by_child: dict[int, list[dict]],
                       parent: list[dict] | None = None) -> list[dict]:
    """The combined feed: every child's events once, titled with the child's name
    unless the event belongs to all of them; the parent's events titled "Forældre"."""
    owners: dict[str, list[str]] = {}
    first_seen: dict[str, dict] = {}
    for c in children:
        for ev in by_child[c.id]:
            owners.setdefault(ev["uid"], []).append(c.first)
            first_seen.setdefault(ev["uid"], ev)
    merged = []
    for uid, ev in first_seen.items():
        names = owners[uid]
        if len(names) < len(children):
            ev = {**ev, "title": f"{' + '.join(names)}: {ev['title']}"}
        merged.append(ev)
    merged += [{**ev, "title": f"{PARENT_CALENDAR_NAME}: {ev['title']}"} for ev in parent or []]
    return merged


def build_feeds(settings, today: datetime.date, school_child: str = "") -> dict[str, tuple[str, list[dict]]]:
    """{file name: (calendar name, events)} for every feed to publish."""
    children = load_children(settings)
    combined_rows = _fetch_calendar(settings, today)
    important_rows = run_aula(settings, "important-dates", "--limit", "50") or []
    meeting_rows = run_aula(settings, "meetings") or []
    important_ids = {row.get("id") for row in important_rows if row.get("id") is not None}
    days = day_events(settings, today, children or [ALL])

    skipped = sum(1 for r in combined_rows if (r.get("event_type") or "").lower() in EXCLUDED_TYPES)
    types = sorted({str(r.get("event_type")) for r in combined_rows
                    if (r.get("event_type") or "").lower() not in EXCLUDED_TYPES})
    print(f"calendar_sync: {len(combined_rows)} calendar rows ({skipped} lessons/meetings/birthdays "
          f"skipped, kept types: {', '.join(types) or 'none'}), {len(important_rows)} important dates, "
          f"{len(meeting_rows)} booked meetings, {len(children)} children")

    if not children:  # one combined feed, as before the per-child feeds existed
        events = school_events(combined_rows, important_ids)
        events += important_date_events(important_rows, {ev["uid"] for ev in events})
        events += meeting_events(meeting_rows, len({r.get("child_id") for r in meeting_rows if r.get("child_id")}))
        events += days[ALL.id]
        return {ICS_FILENAME: (CALENDAR_NAME, events)}

    per_child_rows = {c.id: _fetch_calendar(settings, today, [c.id]) for c in children}
    attributed = {r["id"] for rows in per_child_rows.values() for r in rows if r.get("id") is not None}
    parent_rows, seen = [], set()   # in the combined fetch but in no child's: addressed to the parent
    for r in combined_rows:
        rid = r.get("id")
        if rid is None or rid in attributed or rid in seen or (r.get("event_type") or "").lower() in EXCLUDED_TYPES:
            continue
        seen.add(rid)
        parent_rows.append(r)
    school = {c.id: school_events(per_child_rows[c.id], important_ids) for c in children}
    known = {ev["uid"] for evs in school.values() for ev in evs}
    known |= {f"aula-event-{r['id']}@instant-aula" for r in parent_rows}
    parent = parent_feed_events(settings, parent_rows, important_date_events(important_rows, known),
                                important_ids, children)

    by_child: dict[int, list[dict]] = {}
    feeds: dict[str, tuple[str, list[dict]]] = {}
    for c in children:
        meetings = meeting_events([m for m in meeting_rows if m.get("child_id") == c.id], 1)
        by_child[c.id] = school[c.id] + meetings + days[c.id]
        feeds[f"{c.slug}.ics"] = (c.first, by_child[c.id])
        shared = sum(1 for ev in school[c.id] if all(ev in school[o.id] for o in children))
        print(f"calendar_sync: {c.first}: {len(by_child[c.id])} events ({len(school[c.id])} school "
              f"events of which {shared} shared with every child, {len(meetings)} meetings, "
              f"{len(days[c.id])} day lines)")
    feeds[PARENT_FILENAME] = (PARENT_CALENDAR_NAME, parent)
    print(f"calendar_sync: parent: {len(parent)} events addressed to you, not to a child")

    chosen = [c for c in children if school_child and school_child.lower() in c.name.lower()]
    if len(chosen) == 1:
        feeds[ICS_FILENAME] = (chosen[0].first, by_child[chosen[0].id])
    else:
        if school_child:
            print(f"calendar_sync: SCHOOL_FEED_CHILD={school_child!r} matches {len(chosen)} children; "
                  f"{ICS_FILENAME} holds everyone's events")
        feeds[ICS_FILENAME] = (CALENDAR_NAME, merge_for_everyone(children, by_child, parent))
    return feeds

def main() -> int:
    settings = load_settings()
    today = datetime.datetime.now(TZ).date()
    feeds = build_feeds(settings, today, os.environ.get("SCHOOL_FEED_CHILD", "").strip())

    host, port = socket.gethostname(), os.environ.get("ICS_PORT", "8099")
    for filename, (name, events) in feeds.items():
        path = ics_dir() / filename
        write_atomically(path, render_ics(events, calendar_name=name))
        print(f"calendar_sync: wrote {path} ({len(events)} events) -> http://{host}:{port}/{filename}")

    entity = os.environ.get("HA_CALENDAR_ENTITY", "").strip()
    if entity:
        try:
            call_service("homeassistant", "update_entity", {"entity_id": entity})
            print(f"calendar_sync: asked Home Assistant to refresh {entity}")
        except Exception as exc:  # the files are written; a missed refresh only delays it
            print(f"calendar_sync: could not refresh {entity}: {exc}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        notify_failure("calendar_sync", exc)
        raise
