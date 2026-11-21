"""One feed per child plus a parent feed: attribution, details, fallbacks.

Aula is faked at the `run_aula` boundary. Names and ids are made up. The key
properties: an event comes back from `aula calendar --institution-profile-id
<child>` only for the child it belongs to, and what Aula addresses to the parent
alone goes to the parent calendar with a description of who is invited.
"""

import datetime

import pytest

from instant_aula import calendar_sync, day_summary
from instant_aula.calendar_sync import (Child, build_feeds, describe_parent_event, parse_children,
                                        slugify)
from instant_aula.ics_server import FEED_PATH

TODAY = datetime.date(2026, 11, 18)
ANNA, BEN = 11, 22
KINDERGARTEN, SCHOOL = "KG-1", "SK-1"


def ev(id, title, start="2026-12-03T14:30:00+01:00", end="2026-12-03T17:00:00+01:00", kind="event"):
    return {"id": id, "title": title, "event_type": kind, "start_datetime": start,
            "end_datetime": end, "location": None}


LESSON = ev(1, "IDR", "2026-11-19T09:00:00+01:00", "2026-11-19T09:45:00+01:00", "lesson")
OUTING = ev(100, "Børnehavens udflugt")                     # Anna's kindergarten, Aula says it is hers
PHOTO = ev(200, "Skolefoto", "2026-12-01T09:00:00+01:00", "2026-12-01T10:00:00+01:00")  # Ben's school
TRIP = ev(300, "Fælles tur", "2026-12-04T09:00:00+01:00", "2026-12-04T12:00:00+01:00")  # both children
PARENTS = ev(400, "Forældremøde", "2026-12-08T19:00:00+01:00", "2026-12-08T21:00:00+01:00")  # parent only
PARTY = ev(600, "Juletræsfest")                            # parent only, and an important date

ROWS = {ANNA: [OUTING, TRIP], BEN: [LESSON, PHOTO, TRIP]}
COMBINED = [LESSON, OUTING, PHOTO, TRIP, PARENTS, PARTY]    # PARENTS, PARTY: addressed to the guardian only

PARTY_RAW = {                                              # Aula's own record of the party
    "id": 600, "title": "Juletræsfest", "institutionName": "Børnehaven Stjernen",
    "institutionCode": KINDERGARTEN, "creatorName": "Pædagog Testesen",
    "invitedGroups": [{"name": "Stuen", "institutionName": "Børnehaven Stjernen"},
                      {"name": "Kattegat", "institutionName": "Børnehaven Stjernen"}],
    "responseRequired": True, "responseDeadline": "2026-11-27T12:00:00+01:00", "responseStatus": None,
    "description": {"html": "<p>Vi holder juletræsfest med forældre og søskende.</p><p>Medbring en kage.</p>"},
}

SUMMARY = {
    "week": "2026-W47",
    "calendar_events": [{"title": "IDR", "event_type": "lesson", "belongs_to_profiles": [BEN],
                         "start_datetime": "2026-11-19T09:00:00+01:00",
                         "end_datetime": "2026-11-19T10:00:00+01:00"}],
    "meebook_weekplan": [{"name": "Ben Testesen", "week_plan": [
        {"date": "torsdag 19. nov.", "tasks": [{"pill": "Dansk", "content": "HUSK madpakke"}]}]}],
}

PROFILE = {"children": [
    {"id": ANNA, "name": "Anna Testesen", "institution_code": KINDERGARTEN, "institution_name": "Børnehaven Stjernen"},
    {"id": BEN, "name": "Ben Testesen", "institution_code": SCHOOL, "institution_name": "Testskolen"}]}


def make_fake(profile=None, fail_profile=False, fail_details=False, rows=None):
    calls = []
    per_child = rows or ROWS

    def fake(settings, *args):
        calls.append(args)
        cmd = args[0]
        if cmd == "profile":
            if fail_profile:
                raise RuntimeError("profile down")
            return profile or PROFILE
        if cmd == "calendar" and "--event-id" in args:
            if fail_details:
                raise RuntimeError("aula down")
            return PARTY_RAW if args[args.index("--event-id") + 1] == "600" else {"id": 400, "title": "Forældremøde"}
        if cmd == "calendar":
            if args[args.index("--start-date") + 1] != f"{TODAY:%Y-%m-%d}":
                return []                              # only the first 14-day chunk has events
            ids = [int(args[i + 1]) for i, a in enumerate(args) if a == "--institution-profile-id"]
            return COMBINED if not ids else [r for i in ids for r in per_child[i]]
        if cmd == "important-dates":
            return [{"id": 600, "title": "Juletræsfest"},
                    {"id": 500, "title": "Pædagogisk dag", "startDateTime": "2026-12-10T00:00:00+01:00",
                     "endDateTime": "2026-12-10T23:59:00+01:00"}]
        if cmd == "meetings":
            return [{"meeting_id": 7, "child_id": BEN, "child_name": "Ben Testesen",
                     "title": "Skole-hjem-samtale", "location": None,
                     "start_datetime": "2026-12-02T15:20:00+01:00",
                     "end_datetime": "2026-12-02T15:40:00+01:00"}]
        if cmd == "weekly-summary":
            return SUMMARY if args[-1] == "2026-W47" else {"week": args[-1]}
        raise AssertionError(f"unexpected aula call {args}")

    fake.calls = calls
    return fake


def titles(feeds, name):
    return sorted(e["title"] for e in feeds[name][1])


def event(feeds, name, title):
    return next(e for e in feeds[name][1] if e["title"] == title)


def test_each_childs_events_end_up_in_their_own_feed(monkeypatch):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="ben")
    assert sorted(feeds) == ["anna.ics", "ben.ics", "parent.ics", "skole.ics"]
    assert titles(feeds, "anna.ics") == sorted(["Børnehavens udflugt", "Fælles tur"])
    assert titles(feeds, "ben.ics") == sorted(["Fælles tur", "Skole-hjem-samtale", "Skolefoto",
                                               "🎒 Husk madpakke · Idrætstøj"])
    assert feeds["anna.ics"][0] == "Anna" and feeds["ben.ics"][0] == "Ben"


def test_what_aula_addresses_to_the_parent_goes_to_the_parent_calendar_only(monkeypatch):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="ben")
    assert titles(feeds, "parent.ics") == sorted(["Forældremøde", "❗ Juletræsfest", "❗ Pædagogisk dag"])
    assert feeds["parent.ics"][0] == "Forældre"
    for child_feed in ("anna.ics", "ben.ics", "skole.ics"):          # skole.ics = Ben's feed
        assert not {"Forældremøde", "❗ Juletræsfest", "❗ Pædagogisk dag"} & set(titles(feeds, child_feed))


def test_the_parent_event_says_who_is_invited(monkeypatch):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="ben")
    text = event(feeds, "parent.ics", "❗ Juletræsfest")["description"]
    assert text.startswith("Sendt til forældre i Aula – ikke til et af børnene.")
    assert "Institution: Børnehaven Stjernen (samme institution som Anna)" in text   # points at the child
    assert "Inviterede grupper: Stuen, Kattegat" in text
    assert "Oprettet af: Pædagog Testesen" in text
    assert "Svar kræves senest 2026-11-27" in text
    assert "Står på Aulas liste over vigtige datoer." in text
    assert text.endswith("Vi holder juletræsfest med forældre og søskende.\nMedbring en kage.")


def test_details_come_from_one_lookup_per_parent_event(monkeypatch):
    fake = make_fake()
    monkeypatch.setattr(calendar_sync, "run_aula", fake)
    build_feeds(None, TODAY, school_child="ben")
    looked_up = sorted(c[c.index("--event-id") + 1] for c in fake.calls if "--event-id" in c)
    assert looked_up == ["400", "500", "600"]            # only the parent-only events, not the children's


def test_a_failed_detail_lookup_still_publishes_the_event(monkeypatch, capsys):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake(fail_details=True))
    feeds = build_feeds(None, TODAY, school_child="ben")
    assert "❗ Juletræsfest" in titles(feeds, "parent.ics")
    assert event(feeds, "parent.ics", "❗ Juletræsfest")["description"].startswith("Sendt til forældre i Aula")
    assert "details for event 600 unavailable" in capsys.readouterr().out


def test_day_lines_are_cut_per_child(monkeypatch):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="ben")
    assert not [t for t in titles(feeds, "anna.ics") if t.startswith("🎒")]
    assert "aula-day-ben-20261119@instant-aula" in {e["uid"] for e in feeds["ben.ics"][1]}


def test_combined_feed_without_a_school_child_labels_whose_event_it_is(monkeypatch):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="")
    combined = titles(feeds, "skole.ics")
    assert "Anna: Børnehavens udflugt" in combined and "Ben: Skolefoto" in combined
    assert "Fælles tur" in combined                                 # belongs to both: no prefix
    assert "Forældre: ❗ Juletræsfest" in combined and "Forældre: Forældremøde" in combined


def test_school_child_that_matches_nobody_falls_back_to_the_combined_feed(monkeypatch, capsys):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="zoe")
    assert "Anna: Børnehavens udflugt" in titles(feeds, "skole.ics")
    assert "matches 0 children" in capsys.readouterr().out


def test_unreadable_profile_falls_back_to_one_combined_feed(monkeypatch, capsys):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake(fail_profile=True))
    feeds = build_feeds(None, TODAY, school_child="ben")
    assert list(feeds) == ["skole.ics"]
    assert "could not read the children" in capsys.readouterr().out
    assert "Børnehavens udflugt" in titles(feeds, "skole.ics") and "❗ Juletræsfest" in titles(feeds, "skole.ics")


def test_each_child_is_fetched_with_their_own_profile_id(monkeypatch):
    fake = make_fake()
    monkeypatch.setattr(calendar_sync, "run_aula", fake)
    build_feeds(None, TODAY, school_child="ben")
    per_child = [c for c in fake.calls if c[0] == "calendar" and "--institution-profile-id" in c]
    assert {c[c.index("--institution-profile-id") + 1] for c in per_child} == {str(ANNA), str(BEN)}


def test_the_parent_feed_exists_even_when_nothing_is_addressed_to_the_parent(monkeypatch):
    only_children = [LESSON, OUTING, PHOTO, TRIP]
    fake = make_fake()

    def run(settings, *args):
        if args[0] == "important-dates":
            return []
        out = fake(settings, *args)
        return only_children if (args[0] == "calendar" and "--institution-profile-id" not in args
                                 and "--event-id" not in args and out) else out

    monkeypatch.setattr(calendar_sync, "run_aula", run)
    feeds = build_feeds(None, TODAY, school_child="ben")
    assert feeds["parent.ics"] == ("Forældre", [])        # an empty calendar can still be added in HA


# --- describing an event ------------------------------------------------------

KIDS = [Child(ANNA, "Anna Testesen", "Anna", "anna", KINDERGARTEN, "Børnehaven Stjernen"),
        Child(BEN, "Ben Testesen", "Ben", "ben", SCHOOL, "Testskolen")]


def test_description_works_with_nothing_but_the_calendar_row():
    assert describe_parent_event(None, KIDS) == "Sendt til forældre i Aula – ikke til et af børnene."
    assert describe_parent_event({}, KIDS, event_type="parental_meeting", location="Lokale 4").splitlines() == [
        "Sendt til forældre i Aula – ikke til et af børnene.", "Sted: Lokale 4", "Type: parental_meeting"]


def test_description_matches_the_institution_by_code_or_by_name():
    by_code = describe_parent_event({"institutionCode": SCHOOL, "institutionName": "Anden skole"}, KIDS)
    assert "(samme institution som Ben)" in by_code
    by_name = describe_parent_event({"institutionName": "Børnehaven Stjernen"}, KIDS)
    assert "(samme institution som Anna)" in by_name
    elsewhere = describe_parent_event({"institutionName": "Idrætsforeningen"}, KIDS)
    assert "Institution: Idrætsforeningen" in elsewhere and "samme institution" not in elsewhere


def test_description_reads_the_institution_from_the_invited_groups_when_the_event_has_none():
    raw = {"invitedGroups": [{"name": "Stuen", "institutionName": "Børnehaven Stjernen", "institutionCode": KINDERGARTEN}]}
    assert "Institution: Børnehaven Stjernen (samme institution som Anna)" in describe_parent_event(raw, KIDS)


def test_description_text_may_be_a_plain_string_or_html_and_long_invitee_lists_are_cut():
    plain = describe_parent_event({"description": "Kom og se!"}, KIDS)
    assert plain.endswith("\n\nKom og se!")
    many = describe_parent_event({"invitees": [{"displayName": f"Barn {i}"} for i in range(15)]}, KIDS)
    assert "Barn 0" in many and "Barn 11" in many and "Barn 12" not in many and "og 3 flere" in many


# --- helpers ------------------------------------------------------------------


def test_slugify_and_children():
    assert slugify("Ærø") == "aeroe" and slugify("Åse-Marie") == "aasemarie" and slugify("!!!") == "child"
    kids = parse_children({"children": [
        {"id": 1, "name": "Ella Testesen", "institution_code": 4711, "institution_name": "Børnehaven Stjernen"},
        {"id": 2, "name": "Ella Andersen"}, {"id": None, "name": "Ghost"}, {"id": 3}]})
    assert [(k.slug, k.first) for k in kids] == [("ella", "Ella"), ("ella-2", "Ella")]
    assert kids[0].institution_code == "4711" and kids[0].institution_name == "Børnehaven Stjernen"
    assert kids[1].institution_code is None
    assert parse_children({}) == []


def test_days_from_weekly_summary_filters_by_child():
    both = {"week": "2026-W47",
            "calendar_events": [
                {"title": "IDR", "event_type": "lesson", "belongs_to_profiles": [BEN],
                 "start_datetime": "2026-11-19T09:00:00+01:00", "end_datetime": "2026-11-19T09:45:00+01:00"},
                {"title": "MAT", "event_type": "lesson", "belongs_to_profiles": [ANNA],
                 "start_datetime": "2026-11-19T10:00:00+01:00", "end_datetime": "2026-11-19T10:45:00+01:00"}],
            "meebook_weekplan": [
                {"name": "Anna Testesen", "week_plan": [{"date": "torsdag 19. nov.", "tasks": [
                    {"pill": "Matematik", "content": "Side 3-4 skal laves"}]}]},
                {"name": "Ben Testesen", "week_plan": [{"date": "torsdag 19. nov.", "tasks": [
                    {"pill": "Dansk", "content": "HUSK madpakke"}]}]}]}
    anna = day_summary.days_from_weekly_summary(both, ANNA, "Anna")[0]
    ben = day_summary.days_from_weekly_summary(both, BEN, "Ben")[0]
    assert anna.timetable == ["MAT"] and list(anna.notes) == ["Matematik"]   # no name prefix: only her notes
    assert ben.timetable == ["IDR"] and list(ben.notes) == ["Dansk"]
    merged = day_summary.days_from_weekly_summary(both)[0]                  # unfiltered: both, with names
    assert sorted(merged.notes) == ["Anna - Matematik", "Ben - Dansk"]


@pytest.mark.parametrize("path,ok", [("/anna.ics", True), ("/skole.ics", True), ("/parent.ics", True),
                                     ("/ben-2.ics", True), ("/state.json", False), ("/../state.json", False),
                                     ("/a/b.ics", False), ("/ANNA.ics", False), ("/anna.ics.tmp", False),
                                     ("/", False), ("/.ics", False)])
def test_feed_server_only_serves_ics_files_in_the_feed_directory(path, ok):
    assert bool(FEED_PATH.fullmatch(path)) is ok
