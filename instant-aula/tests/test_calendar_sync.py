"""calendar_sync: filtering, deduplication and iCalendar output.

Fixtures mirror the `aula --output json` shapes (aula 1.22): `calendar` rows are
the CalendarEvent dataclass, `important-dates` rows are Aula's raw camelCase
records, `meetings` rows are MeetingBooking plus child_name. Names are made up.
"""

import datetime

from instant_aula.calendar_sync import (
    TZ,
    _fold,
    important_date_events,
    meeting_events,
    render_ics,
    school_events,
)


def cal(id, title, start, end, event_type="event", location=None):
    return {"id": id, "title": title, "start_datetime": start, "end_datetime": end,
            "event_type": event_type, "location": location, "teacher_name": None,
            "has_substitute": False, "substitute_name": None, "belongs_to": 1,
            "belongs_to_profiles": [1]}


ROWS = [
    cal(1, "DAN", "2026-11-23T08:00:00+01:00", "2026-11-23T08:45:00+01:00", "lesson"),
    cal(2, "Skolefoto", "2026-11-24T09:00:00+01:00", "2026-11-24T12:00:00+01:00", "event", "Gymnastiksal"),
    cal(2, "Skolefoto", "2026-11-24T09:00:00+01:00", "2026-11-24T12:00:00+01:00", "event"),  # 2nd child
    cal(3, "Juleferie", "2026-12-21T00:00:00+01:00", "2027-01-01T23:59:00+01:00", "holiday"),
    cal(4, "Skole-hjem-samtaler", "2026-11-25T14:00:00+01:00", "2026-11-25T18:00:00+01:00", "school_home_meeting"),
    cal(5, "Ole fylder 9", "2026-11-26T00:00:00+01:00", "2026-11-26T23:59:00+01:00", "birthday"),
    cal(6, "Motionsdag", "2026-11-27T00:00:00+01:00", "2026-11-28T00:00:00+01:00", "excursion"),
]


def test_lessons_meetings_birthdays_dropped_and_duplicates_merged():
    events = school_events(ROWS, important_ids=set())
    assert sorted(e["title"] for e in events) == ["Juleferie", "Motionsdag", "Skolefoto"]
    foto = next(e for e in events if e["title"] == "Skolefoto")
    assert foto["location"] == "Gymnastiksal" and not foto["all_day"]


def test_all_day_detection():
    by_title = {e["title"]: e for e in school_events(ROWS, set())}
    assert by_title["Juleferie"]["all_day"]   # midnight -> 23:59 on the last day
    assert by_title["Motionsdag"]["all_day"]  # midnight -> next midnight


def test_important_ids_are_marked_and_extra_important_dates_added():
    events = school_events(ROWS, important_ids={2})
    assert any(e["title"] == "❗ Skolefoto" for e in events)
    extra = important_date_events(
        [{"id": 2, "title": "Skolefoto", "startDateTime": "2026-11-24T09:00:00+01:00"},  # already known
         {"id": 9, "title": "Forældremøde 3.A", "type": "event",
          "startDateTime": "2027-02-02T19:00:00+01:00", "endDateTime": "2027-02-02T21:00:00+01:00"}],
        {e["uid"] for e in events},
    )
    assert [e["title"] for e in extra] == ["❗ Forældremøde 3.A"]


def test_meetings_get_child_name_only_with_several_children():
    row = {"meeting_id": 7, "child_id": 1, "child_name": "Anna Hansen", "title": "Skole-hjem-samtale",
           "meeting_type": "school_home_meeting", "location": "Lokale 12",
           "start_datetime": "2026-11-25T15:20:00+01:00", "end_datetime": "2026-11-25T15:40:00+01:00"}
    assert meeting_events([row], child_count=1)[0]["title"] == "Skole-hjem-samtale"
    assert meeting_events([row], child_count=2)[0]["title"] == "Anna: Skole-hjem-samtale"


def test_render_ics():
    now = datetime.datetime(2026, 11, 20, 12, tzinfo=datetime.timezone.utc)
    ics = render_ics(school_events(ROWS, {2}), now=now)
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert ics.count("BEGIN:VEVENT") == 3
    # timed event in UTC; all-day spans with an exclusive end date
    assert "DTSTART:20261124T080000Z" in ics and "DTEND:20261124T110000Z" in ics
    assert "DTSTART;VALUE=DATE:20261221\r\nDTEND;VALUE=DATE:20270102" in ics
    assert "DTSTART;VALUE=DATE:20261127\r\nDTEND;VALUE=DATE:20261128" in ics
    assert "SUMMARY:❗ Skolefoto" in ics


def test_escaping_and_folding():
    ev = {"uid": "x@y", "title": "Tur; madpakke, idrætstøj\nog regntøj", "all_day": False,
          "start": datetime.datetime(2026, 11, 24, 9, tzinfo=TZ),
          "end": datetime.datetime(2026, 11, 24, 10, tzinfo=TZ),
          "location": None, "description": "æøå " * 40}
    ics = render_ics([ev])
    assert r"SUMMARY:Tur\; madpakke\, idrætstøj\nog regntøj" in ics
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    assert _fold("æ" * 100).replace("\r\n ", "") == "æ" * 100


def test_day_lines_end_to_end(monkeypatch):
    """weekly-summary JSON -> day events -> iCalendar, with Aula mocked."""
    from instant_aula import calendar_sync
    from test_day_summary import W47

    def fake_run_aula(settings, *args):
        assert args[:3] == ("weekly-summary", "--provider", "meebook")
        return W47 if args[4] == "2026-W47" else {"week": args[4]}

    monkeypatch.setattr(calendar_sync, "run_aula", fake_run_aula)
    events = calendar_sync.day_events(None, datetime.date(2026, 11, 18))  # Wednesday
    assert [e["title"] for e in events] == [  # Mon/Tue are past and left out
        "📝 6 noter", "📝 6 noter", "🎒 Medbring ynglingsbamse + fysiske spil"]
    ics = render_ics(events)
    assert "DTSTART;VALUE=DATE:20261120\r\nDTEND;VALUE=DATE:20261121" in ics
    assert "UID:aula-day-20261120@instant-aula" in ics
    assert "DESCRIPTION:andet\\, Dansk\\, Temauge:\\n- Projekt kl 8.00 -13.00" in ics


def test_day_lines_failure_is_skipped(monkeypatch, capsys):
    from instant_aula import calendar_sync

    def broken(settings, *args):
        raise RuntimeError("Aula down")

    monkeypatch.setattr(calendar_sync, "run_aula", broken)
    assert calendar_sync.day_events(None, datetime.date(2026, 11, 18)) == []
    assert "weekly-summary failed: Aula down" in capsys.readouterr().out
