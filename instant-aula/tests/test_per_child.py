"""One feed per child: attribution, events addressed to the parent, fallbacks.

Aula is faked at the `run_aula` boundary. Names and ids are made up. The key
properties: an event comes back from `aula calendar --institution-profile-id
<child>` only for the child it belongs to, and what Aula addresses to the parent
alone is placed with the child it concerns (groups, then institution), described
with what Aula says, and shown for every child only when Aula's record settles
nothing. There is no parent calendar: every event concerns a child.
"""

import datetime

import pytest

from instant_aula import calendar_sync, day_summary
from instant_aula.calendar_sync import (Child, build_feeds, children_for_event, describe_parent_event,
                                        parse_children, slugify)
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
MY = 9000001                                               # the guardian's own profile at the kindergarten
PARTY = {**ev(600, "Juletræsfest"), "belongs_to_profiles": [MY]}   # parent only, and an important date

ROWS = {ANNA: [OUTING, TRIP], BEN: [LESSON, PHOTO, TRIP]}
COMBINED = [LESSON, OUTING, PHOTO, TRIP, PARENTS, PARTY]    # PARENTS, PARTY: addressed to the guardian only

PARTY_RAW = {                                              # Aula's own record of the party
    "id": 600, "title": "Juletræsfest", "institutionName": "Børnehaven Stjernen",
    "invitedGroupHomeChildren": [{"id": ANNA}],
    "institutionCode": KINDERGARTEN, "creatorName": "Pædagog Testesen",
    "invitedGroups": [{"name": "Stuen", "institutionName": "Børnehaven Stjernen"},
                      {"name": "Kattegat", "institutionName": "Børnehaven Stjernen"}],
    "responseRequired": True, "responseDeadline": "2026-11-27T12:00:00+01:00", "responseStatus": None,
    "description": {"html": "<p>Vi holder juletræsfest med forældre og søskende.</p><p>Medbring en kage.</p>"},
    "invitees": [{"instProfile": {"id": MY, "name": "Forælder Testesen"}, "numberOfAdultParticipants": 2,
                  "numberOfChildParticipants": 1, "responseType": "WAITING"}],
}

MEETING_RAW = {"id": 400, "title": "Forældremøde", "institutionName": "Testskolen", "institutionCode": SCHOOL}

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
            wanted = args[args.index("--event-id") + 1]
            return {"600": PARTY_RAW, "400": MEETING_RAW}.get(wanted, {"id": int(wanted)})   # 500: says nothing
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
    assert sorted(feeds) == ["anna.ics", "ben.ics", "skole.ics"]           # no parent calendar
    assert feeds["anna.ics"][0] == "Anna" and feeds["ben.ics"][0] == "Ben"
    assert {"Børnehavens udflugt", "Fælles tur"} <= set(titles(feeds, "anna.ics"))
    assert {"Fælles tur", "Skole-hjem-samtale", "Skolefoto", "🎒 Husk madpakke · Idrætstøj"} <= set(titles(feeds, "ben.ics"))
    assert "Børnehavens udflugt" not in titles(feeds, "ben.ics") and "Skolefoto" not in titles(feeds, "anna.ics")


def test_an_event_for_the_parent_goes_to_the_child_in_the_invited_groups(monkeypatch, capsys):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="ben")
    assert "❗ Juletræsfest" in titles(feeds, "anna.ics")
    assert "❗ Juletræsfest" not in titles(feeds, "ben.ics")
    assert "'❗ Juletræsfest' -> Anna (groups)" in capsys.readouterr().out


def test_otherwise_it_goes_to_the_child_at_the_institution_that_sent_it(monkeypatch, capsys):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="ben")
    assert "Forældremøde" in titles(feeds, "ben.ics") and "Forældremøde" not in titles(feeds, "anna.ics")
    assert "'Forældremøde' -> Ben (institution)" in capsys.readouterr().out


def test_when_aulas_record_settles_nothing_the_event_is_shown_for_every_child_and_says_so(monkeypatch, capsys):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="ben")
    for name in ("anna.ics", "ben.ics"):
        assert "❗ Pædagogisk dag" in titles(feeds, name)
    text = event(feeds, "anna.ics", "❗ Pædagogisk dag")["description"]
    assert "Kunne ikke afgøre hvilket barn det gælder – vist hos alle børn." in text
    assert "could not tell, shown for all" in capsys.readouterr().out


def test_the_event_says_why_it_is_there_and_who_is_invited(monkeypatch):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake())
    feeds = build_feeds(None, TODAY, school_child="ben")
    text = event(feeds, "anna.ics", "❗ Juletræsfest")["description"]
    lines = text.splitlines()
    assert lines[0] == "Sendt til forældre i Aula – ikke direkte til et af børnene."
    assert lines[1] == "Vist hos Anna: barnet er i de inviterede grupper."
    assert "Institution: Børnehaven Stjernen (samme institution som Anna)" in text
    assert "Inviterede grupper: Stuen, Kattegat" in text
    assert "Oprettet af: Pædagog Testesen" in text
    assert "Svar kræves senest 2026-11-27" in text
    assert "Dit svar: mangler svar (2 voksne, 1 barn)" in text            # found via my profile on the row
    assert "Står på Aulas liste over vigtige datoer." in text
    assert text.endswith("Vi holder juletræsfest med forældre og søskende.\nMedbring en kage.")
    assert "Vist hos Ben: samme institution som invitationen." in event(feeds, "ben.ics", "Forældremøde")["description"]


def test_a_failed_detail_lookup_shows_the_event_for_every_child(monkeypatch, capsys):
    monkeypatch.setattr(calendar_sync, "run_aula", make_fake(fail_details=True))
    feeds = build_feeds(None, TODAY, school_child="ben")
    for name in ("anna.ics", "ben.ics"):
        assert "❗ Juletræsfest" in titles(feeds, name)          # nothing hidden when Aula can't be asked
    assert event(feeds, "ben.ics", "❗ Juletræsfest")["description"].startswith("Sendt til forældre i Aula")
    assert "details for event 600 unavailable" in capsys.readouterr().out


def test_details_come_from_one_lookup_per_parent_event(monkeypatch):
    fake = make_fake()
    monkeypatch.setattr(calendar_sync, "run_aula", fake)
    build_feeds(None, TODAY, school_child="ben")
    looked_up = sorted(c[c.index("--event-id") + 1] for c in fake.calls if "--event-id" in c)
    assert looked_up == ["400", "500", "600"]            # only the events addressed to the parent


def test_the_old_parent_feed_file_from_1_4_is_removed(monkeypatch, tmp_path):
    for key, value in (("ICS_DIR", str(tmp_path)), ("AULA_MITID_USERNAME", "x"),
                       ("HA_NOTIFY_SERVICE", "x"), ("STATE_DIR", str(tmp_path))):
        monkeypatch.setenv(key, value)
    (tmp_path / "parent.ics").write_text("stale")
    monkeypatch.setattr(calendar_sync, "build_feeds", lambda *a, **k: {"anna.ics": ("Anna", [])})
    calendar_sync.main()
    assert not (tmp_path / "parent.ics").exists() and (tmp_path / "anna.ics").exists()


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
    assert "Anna: ❗ Juletræsfest" in combined and "Ben: Forældremøde" in combined
    assert "❗ Pædagogisk dag" in combined                           # shown for both: no prefix


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


# --- describing an event ------------------------------------------------------

KIDS = [Child(ANNA, "Anna Testesen", "Anna", "anna", KINDERGARTEN, "Børnehaven Stjernen"),
        Child(BEN, "Ben Testesen", "Ben", "ben", SCHOOL, "Testskolen")]


def test_description_works_with_nothing_but_the_calendar_row():
    base = "Sendt til forældre i Aula – ikke direkte til et af børnene."
    assert describe_parent_event(None, KIDS) == base
    assert describe_parent_event({}, KIDS, event_type="parental_meeting", location="Lokale 4").splitlines() == [
        base, "Sted: Lokale 4", "Type: parental_meeting"]


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


def test_description_text_may_be_a_plain_string_or_html():
    assert describe_parent_event({"description": "Kom og se!"}, KIDS).endswith("\n\nKom og se!")
    assert describe_parent_event({"description": {"html": "<p>Kom og se!</p>"}}, KIDS).endswith("\n\nKom og se!")


# --- the fields Aula really returns (names taken from a live record) ---------

REAL = {
    "creator": {"name": "Pædagog Testesen", "profileId": 1},
    "coOrganizers": [{"name": "Hjælper Hansen"}, "Praktikant Pedersen"],
    "institutionName": "Børnehaven Stjernen", "institutionCode": KINDERGARTEN,
    "invitedGroups": [{"name": "Stuen"}], "inviteeGroups": [{"name": "Stuen"}, {"name": "Kattegat"}],
    "invitedGroupHomeChildren": [{"id": ANNA}, {"id": 999}],
    "invitees": [{"displayName": "Barn A"}, {"institutionProfile": {"name": "Barn B"}}],
    "attachments": [{"name": "invitation.pdf"}, {"name": "program.pdf"}],
    "responseRequired": True, "responseDeadline": "2026-11-27T12:00:00+01:00", "isDeadlineExceeded": False,
    "doRequestNumberOfParticipants": True, "maximumNumberOfParticipants": 40, "actualNumberOfParticipants": 17,
    "primaryResource": {"id": 5, "name": "Fællesrummet"},
    "description": {"html": "<p>Kom og vær med.</p>"},
}


def test_description_reads_the_fields_aula_really_returns():
    text = describe_parent_event(REAL, KIDS)
    assert "Inviterede grupper: Stuen, Kattegat" in text                 # both group fields, no repeats
    assert "Inviterede: Barn A, Barn B" in text                          # a name wrapped in a profile
    assert "Oprettet af: Pædagog Testesen" in text                       # `creator`, not `creatorName`
    assert "Medarrangører: Hjælper Hansen, Praktikant Pedersen" in text
    assert "Sted: Fællesrummet" in text
    assert "Svar kræves senest 2026-11-27" in text and "overskredet" not in text
    assert "Tilmeldte: 17 af højst 40" in text
    assert "Vedhæftede filer: invitation.pdf, program.pdf" in text
    assert text.endswith("Kom og vær med.")


@pytest.mark.parametrize("creator", ["Pædagog Testesen", {"fullName": "Pædagog Testesen"},
                                     {"institutionProfile": {"name": "Pædagog Testesen"}},
                                     {"firstName": "Pædagog", "lastName": "Testesen"}])
def test_the_creator_may_be_a_string_a_dict_or_a_wrapped_profile(creator):
    assert "Oprettet af: Pædagog Testesen" in describe_parent_event({"creator": creator}, KIDS)


def test_the_children_an_event_concerns_come_from_the_groups_first_then_the_institution():
    assert [c.first for c in children_for_event({"invitedGroupHomeChildren": [ANNA, BEN]}, KIDS)[0]] == ["Anna", "Ben"]
    assert children_for_event({"invitedGroupHomeChildren": [{"id": ANNA}]}, KIDS)[1] == "groups"
    both = {"invitedGroupHomeChildren": [ANNA], "institutionCode": SCHOOL}               # groups win
    assert [c.first for c in children_for_event(both, KIDS)[0]] == ["Anna"]
    assert [c.first for c in children_for_event({"institutionCode": SCHOOL}, KIDS)[0]] == ["Ben"]
    assert [c.first for c in children_for_event({"institutionName": "Børnehaven Stjernen"}, KIDS)[0]] == ["Anna"]
    via_group = {"invitedGroups": [{"name": "Stuen", "institutionCode": KINDERGARTEN}]}
    assert children_for_event(via_group, KIDS) == ([KIDS[0]], "institution")
    for nothing in ({"invitedGroupHomeChildren": [999, "x", None]}, {"invitedGroupHomeChildren": []},
                    {"institutionName": "Idrætsforeningen"}, {}, None, "not a dict"):    # settle nothing
        assert children_for_event(nothing, KIDS) == ([], "")


def test_empty_or_missing_fields_add_no_lines():
    text = describe_parent_event({"attachments": [], "coOrganizers": [], "invitees": None,
                                  "maximumNumberOfParticipants": 0, "responseRequired": False}, KIDS)
    assert text == "Sendt til forældre i Aula – ikke direkte til et af børnene."


def test_a_passed_reply_deadline_and_open_participation_are_shown():
    text = describe_parent_event({"responseRequired": True, "responseDeadline": "2026-11-20T12:00:00+01:00",
                                  "isDeadlineExceeded": True, "doRequestNumberOfParticipants": True,
                                  "actualNumberOfParticipants": 9}, KIDS)
    assert "Svar kræves senest 2026-11-20 (fristen er overskredet)" in text and "Tilmeldte: 9" in text


def test_the_log_shows_the_structure_of_the_fields_but_never_names_or_text(monkeypatch, capsys):
    from instant_aula.calendar_sync import event_details
    monkeypatch.setattr(calendar_sync, "run_aula", lambda settings, *a: REAL)
    event_details(None, 600)
    out = capsys.readouterr().out
    assert "shapes (names only, no values)" in out and "creator={name,profileId}" in out
    assert "invitees=list[2]{displayName}" in out and "invitedGroupHomeChildren=list[2]{id}" in out
    for private in ("Pædagog Testesen", "Barn A", "Hjælper Hansen", "Kom og vær med", "invitation.pdf"):
        assert private not in out


# --- the live record: names one level down, 210 reply records ----------------


def reply(pid, name, kind, adults=0, kids=0):
    return {"instProfile": {"id": pid, "name": name}, "numberOfAdultParticipants": adults,
            "numberOfChildParticipants": kids, "responseDateTime": None, "responseType": kind}


def live(extra_invitees=()):
    invitees = ([reply(i, f"Gæst {i}", "ACCEPTED") for i in range(31)]
                + [reply(100 + i, f"Gæst {100 + i}", "DECLINED") for i in range(12)]
                + [reply(200 + i, f"Gæst {200 + i}", "WAITING") for i in range(166)]
                + [reply(MY, "Forælder Testesen", "WAITING", adults=2, kids=1)] + list(extra_invitees))
    return {"creator": {"id": 1, "name": "Pædagog Testesen", "institutionName": "Børnehaven Stjernen",
                        "institutionCode": KINDERGARTEN, "mainGroup": {"name": "Stuen"}, "role": "employee"},
            "coOrganizers": [reply(2, "Medarrangør Testesen", "ACCEPTED")],
            "inviteeGroups": [{"group": {"name": "Stuen"}, "invitedPortalRoles": ["GUARDIAN", "CHILD"]}],
            "invitees": invitees, "institutionName": "Børnehaven Stjernen", "institutionCode": KINDERGARTEN}


def test_names_one_level_down_are_found_and_roles_are_translated():
    text = describe_parent_event(live(), KIDS, my_profiles=[MY])
    assert "Inviterede grupper: Stuen (forældre, børn)" in text          # the name is inside `group`
    assert "Medarrangører: Medarrangør Testesen" in text                 # the name is inside `instProfile`
    assert "Oprettet af: Pædagog Testesen" in text


def test_a_long_invitee_list_is_counted_not_listed():
    text = describe_parent_event(live(), KIDS, my_profiles=[MY])
    assert "Inviterede: 210 personer" in text
    assert "Svar indtil nu: 31 ja, 12 nej, 167 mangler svar" in text
    assert "Gæst 0" not in text


def test_a_short_invitee_list_is_listed_with_names():
    raw = {"invitees": [reply(1, "Anna A", "ACCEPTED"), reply(2, "Bo B", "DECLINED"), reply(3, "Cy C", "going")]}
    text = describe_parent_event(raw, KIDS)
    assert "Inviterede: Anna A, Bo B, Cy C" in text
    assert "Svar indtil nu: 1 ja, 1 nej, 1 going" in text                 # unfamiliar replies shown as written


def test_your_own_reply_is_found_by_your_guardian_profile():
    assert "Dit svar: mangler svar (2 voksne, 1 barn)" in describe_parent_event(live(), KIDS, my_profiles=[MY])
    answered = live()
    answered["invitees"][-1] = reply(MY, "Forælder Testesen", "ACCEPTED", adults=1)
    assert "Dit svar: ja (1 voksen)" in describe_parent_event(answered, KIDS, my_profiles=[MY])
    assert "Dit svar" not in describe_parent_event(live(), KIDS, my_profiles=[123])      # not among them
    assert "Dit svar" not in describe_parent_event(live(), KIDS)                         # profile unknown


def test_the_log_shows_one_level_of_nested_field_names_and_still_no_values(monkeypatch, capsys):
    from instant_aula.calendar_sync import event_details
    monkeypatch.setattr(calendar_sync, "run_aula", lambda settings, *a: live())
    event_details(None, 600)
    out = capsys.readouterr().out
    assert "invitees=list[210]{instProfile{id,name}" in out and "inviteeGroups=list[1]{group{name}" in out
    for private in ("Forælder Testesen", "Gæst 0", "Pædagog Testesen", "Medarrangør", "Stuen"):
        assert private not in out.split("shapes (names only, no values):")[1]


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


@pytest.mark.parametrize("path,ok", [("/anna.ics", True), ("/skole.ics", True),
                                     ("/ben-2.ics", True), ("/state.json", False), ("/../state.json", False),
                                     ("/a/b.ics", False), ("/ANNA.ics", False), ("/anna.ics.tmp", False),
                                     ("/", False), ("/.ics", False)])
def test_feed_server_only_serves_ics_files_in_the_feed_directory(path, ok):
    assert bool(FEED_PATH.fullmatch(path)) is ok
