"""One feed per child: attribution, the combined feed, fallbacks.

Aula is faked at the `run_aula` boundary. Names and ids are made up. The
important property: an event comes back from `aula calendar --institution-
profile-id <child>` only for the child it belongs to, which is how a kindergarten
child's party stays out of the school child's calendar.
"""

import datetime

import pytest

from instant_aula import calendar_sync, day_summary
from instant_aula.calendar_sync import (Child, build_feeds, owners_of, parse_children, parse_owner_rules,
                                        rule_problems, slugify)
from instant_aula.ics_server import FEED_PATH

TODAY = datetime.date(2026, 11, 18)
ANNA, BEN = 11, 22


def ev(id, title, start="2026-12-03T14:30:00+01:00", end="2026-12-03T17:00:00+01:00", kind="event"):
    return {"id": id, "title": title, "event_type": kind, "start_datetime": start,
            "end_datetime": end, "location": None}


LESSON = ev(1, "IDR", "2026-11-19T09:00:00+01:00", "2026-11-19T09:45:00+01:00", "lesson")
PARTY = ev(100, "Juletræsfest")                      # Anna's kindergarten
PHOTO = ev(200, "Skolefoto", "2026-12-01T09:00:00+01:00", "2026-12-01T10:00:00+01:00")  # Ben's school
TRIP = ev(300, "Fælles tur", "2026-12-04T09:00:00+01:00", "2026-12-04T12:00:00+01:00")  # both
PARENTS = ev(400, "Forældremøde", "2026-12-08T19:00:00+01:00", "2026-12-08T21:00:00+01:00")

ROWS = {ANNA: [PARTY, TRIP], BEN: [LESSON, PHOTO, TRIP]}
COMBINED = [LESSON, PARTY, PHOTO, TRIP, PARENTS]    # PARENTS: only addressed to the guardian

SUMMARY = {
    "week": "2026-W47",
    "calendar_events": [{"title": "IDR", "event_type": "lesson", "belongs_to_profiles": [BEN],
                         "start_datetime": "2026-11-19T09:00:00+01:00",
                         "end_datetime": "2026-11-19T10:00:00+01:00"}],
    "meebook_weekplan": [{"name": "Ben Testesen", "week_plan": [
        {"date": "torsdag 19. nov.", "tasks": [{"pill": "Dansk", "content": "HUSK madpakke"}]}]}],
}


def make_fake(profile=None, fail_profile=False, rows=None):
    calls = []
    per_child = rows or ROWS

    def fake(settings, *args):
        calls.append(args)
        cmd = args[0]
        if cmd == "profile":
            if fail_profile:
                raise RuntimeError("profile down")
            return profile or {"children": [{"id": ANNA, "name": "Anna Testesen"},
                                            {"id": BEN, "name": "Ben Testesen"}]}
        if cmd == "calendar":
            if args[args.index("--start-date") + 1] != f"{TODAY:%Y-%m-%d}":
                return []                              # only the first 14-day chunk has events
            ids = [int(args[i + 1]) for i, a in enumerate(args) if a == "--institution-profile-id"]
            return COMBINED if not ids else [r for i in ids for r in per_child[i]]
        if cmd == "important-dates":
            return [{"id": 100, "title": "Juletræsfest"},
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


def test_each_childs_events_end_up_in_their_own_feed(monkeypatch):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="ben")
    assert sorted(feeds) == ["anna.ics", "ben.ics", "skole.ics"]
    assert titles(feeds, "anna.ics") == sorted(
        ["Fælles tur", "Forældremøde", "❗ Juletræsfest", "❗ Pædagogisk dag"])
    assert titles(feeds, "ben.ics") == sorted(
        ["Fælles tur", "Forældremøde", "Skole-hjem-samtale", "Skolefoto", "❗ Pædagogisk dag",
         "🎒 Husk madpakke · Idrætstøj"])
    assert feeds["anna.ics"][0] == "Anna" and feeds["ben.ics"][0] == "Ben"


def test_the_kindergarten_party_stays_out_of_the_school_childs_feed(monkeypatch):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="ben")
    assert "❗ Juletræsfest" not in titles(feeds, "ben.ics")
    assert "❗ Juletræsfest" not in titles(feeds, "skole.ics")   # skole.ics = Ben's feed


def test_events_not_attributable_to_a_child_are_shown_for_everyone_and_logged(monkeypatch, capsys):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="ben")
    assert "Forældremøde" in titles(feeds, "anna.ics") and "Forældremøde" in titles(feeds, "ben.ics")
    out = capsys.readouterr().out
    assert "not attributed by Aula: 'Forældremøde'" in out and "-> shown for everyone" in out
    assert "important date found in no child's calendar" in out and "Pædagogisk dag" in out


def test_day_lines_are_cut_per_child(monkeypatch):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="ben")
    assert not [t for t in titles(feeds, "anna.ics") if t.startswith("🎒")]
    uids = {e["uid"] for e in feeds["ben.ics"][1]}
    assert "aula-day-ben-20261119@instant-aula" in uids


def test_combined_feed_without_a_school_child_labels_whose_event_it_is(monkeypatch):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="")
    combined = titles(feeds, "skole.ics")
    assert "Anna: ❗ Juletræsfest" in combined
    assert "Ben: Skolefoto" in combined and "Ben: Skole-hjem-samtale" in combined
    assert "Fælles tur" in combined and "Forældremøde" in combined   # belong to both: no prefix
    assert "❗ Pædagogisk dag" in combined


def test_school_child_that_matches_nobody_falls_back_to_the_combined_feed(monkeypatch, capsys):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="zoe")
    assert "Anna: ❗ Juletræsfest" in titles(feeds, "skole.ics")
    assert "matches 0 children" in capsys.readouterr().out


def test_unreadable_profile_falls_back_to_one_combined_feed(monkeypatch, capsys):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake(fail_profile=True))
    feeds = build_feeds(None, TODAY, school_child="ben")
    assert list(feeds) == ["skole.ics"]
    assert "could not read the children" in capsys.readouterr().out
    assert "❗ Juletræsfest" in titles(feeds, "skole.ics")      # today's behaviour, nothing lost


def test_each_child_is_fetched_with_their_own_profile_id(monkeypatch):
    fake = make_fake()
    monkeypatch.setattr(calendar_sync, "run_aula", fake)
    build_feeds(None, TODAY, school_child="ben")
    per_child = [c for c in fake.calls if c[0] == "calendar" and "--institution-profile-id" in c]
    assert {c[c.index("--institution-profile-id") + 1] for c in per_child} == {str(ANNA), str(BEN)}


# --- helpers ----------------------------------------------------------------


def test_slugify_and_children():
    assert slugify("Ærø") == "aeroe" and slugify("Åse-Marie") == "aasemarie" and slugify("!!!") == "child"
    kids = parse_children({"children": [{"id": 1, "name": "Ella Testesen"}, {"id": 2, "name": "Ella Andersen"},
                                        {"id": None, "name": "Ghost"}, {"id": 3}]})
    assert [(k.slug, k.first) for k in kids] == [("ella", "Ella"), ("ella-2", "Ella")]
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
    # unfiltered keeps today's behaviour: both children, headings carry the name
    merged = day_summary.days_from_weekly_summary(both)[0]
    assert sorted(merged.notes) == ["Anna - Matematik", "Ben - Dansk"]


@pytest.mark.parametrize("path,ok", [("/anna.ics", True), ("/skole.ics", True), ("/ben-2.ics", True),
                                     ("/state.json", False), ("/../state.json", False),
                                     ("/a/b.ics", False), ("/ANNA.ics", False), ("/anna.ics.tmp", False),
                                     ("/", False), ("/.ics", False)])
def test_feed_server_only_serves_ics_files_in_the_feed_directory(path, ok):
    assert bool(FEED_PATH.fullmatch(path)) is ok


# --- event_owners: rules that place events -----------------------------------

KIDS = [Child(ANNA, "Anna Testesen", "Anna", "anna"), Child(BEN, "Ben Testesen", "Ben", "ben")]


def test_owner_rules_parse_and_match():
    rules = parse_owner_rules("julefest=ben, Forældremøde = Anna ;; tur=ben+anna, broken, =x, y=")
    assert rules == [("julefest", ["ben"]), ("forældremøde", ["anna"]), ("tur", ["ben", "anna"])]
    assert [c.first for c in owners_of("🎄 Julefest 🎄", rules, KIDS)] == ["Ben"]
    assert [c.first for c in owners_of("Forældremøde 3.A", rules, KIDS)] == ["Anna"]
    assert [c.first for c in owners_of("Skovtur", rules, KIDS)] == ["Ben", "Anna"]       # several children
    assert owners_of("Skolefoto", rules, KIDS) == []
    assert owners_of("Julefest", [("julefest", ["zoe"])], KIDS) == []                    # names nobody
    assert owners_of("Julefest", [("julefest", ["testesen"])], KIDS) == []               # names both
    assert len(rule_problems([("a", ["zoe", "ben"]), ("b", ["testesen"])], KIDS)) == 2


def test_a_rule_assigns_an_unattributed_event_to_one_child_only(monkeypatch, capsys):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="ben", event_owners="forældremøde=anna")
    assert "Forældremøde" in titles(feeds, "anna.ics")
    assert "Forældremøde" not in titles(feeds, "ben.ics")
    assert "Forældremøde" not in titles(feeds, "skole.ics")                     # skole.ics = Ben's feed
    assert "-> Anna (event_owners rule)" in capsys.readouterr().out


def test_a_rule_can_name_several_children(monkeypatch):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="ben", event_owners="forældremøde=anna+ben")
    assert "Forældremøde" in titles(feeds, "anna.ics") and "Forældremøde" in titles(feeds, "ben.ics")


def test_a_rule_adds_to_what_aula_attributes_and_never_removes_it(monkeypatch, capsys):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    # Aula says the party is Anna's (it comes back in her own fetch); the rule also names Ben
    feeds = build_feeds(None, TODAY, school_child="ben", event_owners="juletræsfest=ben")
    assert "❗ Juletræsfest" in titles(feeds, "anna.ics")        # Aula's answer stays
    assert "❗ Juletræsfest" in titles(feeds, "ben.ics")         # and the rule adds Ben
    assert "also added to Ben by an event_owners rule" in capsys.readouterr().out


def test_the_rule_child_keeps_the_event_when_aula_later_attributes_it_to_the_sibling(monkeypatch):
    """The party is Anna's. If Aula starts listing it for Ben too (he is invited), it must
    not disappear from Anna's calendar because Aula now 'attributes' it."""
    rows = {ANNA: [TRIP], BEN: [LESSON, PHOTO, TRIP, PARTY]}      # party now returned for Ben only
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake(rows=rows))
    feeds = build_feeds(None, TODAY, school_child="ben", event_owners="juletræsfest=anna")
    assert "❗ Juletræsfest" in titles(feeds, "anna.ics")        # the rule keeps it with Anna
    assert "❗ Juletræsfest" in titles(feeds, "ben.ics")         # Aula's new answer: Ben is invited
    # without the rule the party would have moved to Ben alone
    feeds = build_feeds(None, TODAY, school_child="ben")
    assert "❗ Juletræsfest" not in titles(feeds, "anna.ics")


def test_a_rule_also_places_important_dates_found_in_no_calendar(monkeypatch):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="ben", event_owners="pædagogisk=anna")
    assert "❗ Pædagogisk dag" in titles(feeds, "anna.ics")
    assert "❗ Pædagogisk dag" not in titles(feeds, "ben.ics")


def test_a_rule_that_names_no_child_is_ignored_and_reported(monkeypatch, capsys):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="ben", event_owners="forældremøde=zoe")
    assert "Forældremøde" in titles(feeds, "anna.ics") and "Forældremøde" in titles(feeds, "ben.ics")
    assert "matches 0 children -- that name is ignored" in capsys.readouterr().out
