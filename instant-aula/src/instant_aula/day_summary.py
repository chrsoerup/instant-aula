"""Condense a school day's Meebook notes into one calendar line.

One all-day event per school day, titled with what a parent has to act on:

    🎒 Udflugt kl. 10-13 · Slut kl. 12.55 · Medbring bamse · Idrætstøj · Lektier: Mat

and the teacher's full notes, grouped by subject, in the description. A day
with notes but nothing the rules recognise is titled "📝 5 noter" instead of
being left out -- so a miss is visible on the calendar rather than silent,
which is the failure that got the LLM version (highlights.py) removed: it
dropped a week's maths homework and invented a packed lunch, and nothing on
screen showed either.

Deterministic and deliberately precise rather than eager. Every rule matches a
phrase in context, not a bare word, because the bare words misfire on real
weeks (2026-W47, a project week):

- "opgave" is mostly classwork ("tegner/skriver vi i en opgave"), so homework
  needs a homework phrase: lektie, hjemme, skal laves/læses, side 10-11, ...
- The timetable says IDR, but the note says "Projektuge, der er derfor ikke
  idræt". Gear guessed from the timetable is dropped when the same day's notes
  cancel that subject.

Pure functions only; calendar_sync does the fetching and writing. Tests and
fixtures live in tests/test_day_summary.py -- each week that reveals a miss or
a false hit becomes a fixture there, anonymised (the repo is public).
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

# --- reading Meebook ------------------------------------------------------

_DA_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
              "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12}
_DATE_LABEL_RE = re.compile(r"(\d{1,2})\.\s*([a-zæøå]+)", re.IGNORECASE)
_SEPARATOR_RE = re.compile(r"_{3,}")
# Sentence break: after . ! ? when the next word starts with a capital letter.
# "Kl. 10 - 13" and "8.00" therefore stay in one piece.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÆØÅ])")

# Subject shown for a Meebook "pill" heading such as "andet, Dansk, Temauge".
_SUBJECTS = (
    ("dansk", "Dansk"), ("matematik", "Mat"), ("engelsk", "Engelsk"), ("tysk", "Tysk"),
    ("fransk", "Fransk"), ("natur", "N/T"), ("idræt", "Idræt"), ("musik", "Musik"),
    ("billedkunst", "Billedkunst"), ("håndværk", "H&D"), ("historie", "Historie"),
    ("kristendom", "Kristendom"), ("samfundsfag", "Samfundsfag"), ("geografi", "Geografi"),
    ("biologi", "Biologi"), ("fysik", "Fysik/kemi"), ("svømning", "Svømning"),
)


def meebook_date(label: str, week_monday: datetime.date) -> datetime.date | None:
    """'mandag 17. aug.' -> a date, choosing the year that puts it in this week.

    The label has no year; near New Year the week's Monday and its Friday can be
    in different years, so pick whichever candidate is closest to the Monday.
    """
    match = _DATE_LABEL_RE.search(label or "")
    month = _DA_MONTHS.get(match.group(2).lower()[:3]) if match else None
    if not month:
        return None
    candidates = []
    for year in (week_monday.year - 1, week_monday.year, week_monday.year + 1):
        try:
            candidates.append(datetime.date(year, month, int(match.group(1))))
        except ValueError:
            pass
    return min(candidates, key=lambda d: abs((d - week_monday).days), default=None)


def note_lines(content: str | None) -> list[str]:
    lines = []
    for block in _SEPARATOR_RE.split(content or ""):
        lines += [line.strip() for line in block.splitlines() if line.strip()]
    return lines


def subject_label(heading: str) -> str:
    low = heading.lower()
    for key, label in _SUBJECTS:
        if key in low:
            return label
    return heading.split(",")[0].strip().capitalize() or "Noter"


# --- rules ----------------------------------------------------------------

_ARTICLES = r"(?:et par|en|et|nogle|din|dit|dine|jeres|deres|sin|sit|sine)"
_BRING_RE = re.compile(r"\b(medbring\w*)\s+(.+)", re.IGNORECASE)
_HUSK_RE = re.compile(r"\bhusk\b[:!]?\s+(.+)", re.IGNORECASE)  # imperative only, not "huske"
_TAKE_RE = re.compile(r"\btag\w*\s+(.{2,40}?)\s+med\b", re.IGNORECASE)
_HOMEWORK_RE = re.compile(
    r"\blektie\w*|\bhjemmearbejde\b|\bhjemme\b"
    r"|\btil (?:i morgen|næste gang|på (?:mandag|tirsdag|onsdag|torsdag|fredag))\b"
    r"|\bskal (?:laves|læses|afleveres|øves)\b"
    r"|\bskal(?:le)? gerne kunne\b"                      # "skulle gerne kunne læse ..." (W48)
    r"|\blæse\w*\s+(?:en|et|\d+)\s+sider?\b"
    r"|\bsider?\s*\d+\s*(?:-|–|til)\s*\d+",
    re.IGNORECASE,
)
_EVENT_RE = re.compile(
    r"\b(?:(?:udflugt|ekskursion|motionsdag|cykeltur|skolefoto|fotografering|teatertur"
    r"|biograftur|lejrskole|skolefri|fridag|juleafslutning|sommerafslutning|idrætsdag"
    r"|emnedag|temadag|forældremøde|forældrekaffe|forældredag)\w*"
    r"|klippe\w*dag"                       # klippedag, klippeklistredag (W48)
    r"|\w*fest(?:en|erne|er)?\b"          # lanternefest, julefest (W48) -- not "festlig"
    r"|tur til)",
    re.IGNORECASE,
)
_TIME_RANGE_RE = re.compile(
    r"\bkl\.?\s*(\d{1,2}(?:[.:]\d{2})?)\s*(?:-|–|til)\s*(\d{1,2}(?:[.:]\d{2})?)", re.IGNORECASE)
# A school-day span in the notes ("Kl 8.00-12.55"): starts in the morning, has minutes.
_SCHOOL_HOURS_RE = re.compile(
    r"\bkl\.?\s*([7-9])[.:](\d{2})\s*(?:-|–|til)\s*(\d{1,2})[.:](\d{2})", re.IGNORECASE)
EARLY_FINISH = datetime.timedelta(minutes=15)  # less than this is noise, not an early pickup

# Gear the timetable implies: timetable code prefix -> (subject word in notes, item).
_GEAR = {"IDR": ("idræt", "Idrætstøj"), "SVØ": ("svøm", "Badetøj")}


def _cancel_re(word: str) -> re.Pattern:
    # "ikke idræt", "ingen idræt", "idræt er aflyst" -- within one sentence.
    return re.compile(
        rf"\b(?:ikke|ingen|intet)\b[^.!?]{{0,30}}\b{word}\w*|\b{word}\w*\b[^.!?]{{0,30}}\baflys\w*",
        re.IGNORECASE,
    )


def _calm(text: str) -> str:
    """'LÆSEMAPPE' -> 'læsemappe'. Two-letter abbreviations (DR, GR) are kept."""
    return " ".join(w.lower() if len(w) > 2 and w.isupper() else w for w in text.split(" "))


def _short(text: str, limit: int = 40) -> str:
    text = _calm(text.strip(" .,:;-"))
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;-") + "…"


def _clause(rest: str) -> str:
    """The object of a medbring/husk: up to the end of the clause."""
    rest = re.split(r"[.!?;(]|\s[-–]\s|,\s*(?:så|da|fordi|hvis|når|men)\b", rest, maxsplit=1)[0]
    return rest.strip(" ,:")


def _bring_item(sentence: str) -> str | None:
    if m := _BRING_RE.search(sentence):
        thing = re.sub(rf"^{_ARTICLES}\s+", "", _clause(m.group(2)), flags=re.IGNORECASE)
        thing = re.sub(rf"\s+(?:samt|og)\s+(?:{_ARTICLES}\s+)?", " + ", thing, flags=re.IGNORECASE)
        return "Medbring " + _short(thing) if thing else None
    if m := _HUSK_RE.search(sentence):
        thing = _clause(m.group(1))
        return "Husk " + _short(thing) if thing else None
    if m := _TAKE_RE.search(sentence):
        thing = re.sub(rf"^{_ARTICLES}\s+", "", m.group(1), flags=re.IGNORECASE)
        return "Medbring " + _short(thing)
    return None


def _event_item(sentence: str) -> str | None:
    m = _EVENT_RE.search(sentence)
    if not m:
        return None
    word = "Tur" if m.group(0).lower() == "tur til" else m.group(0)[0].upper() + m.group(0)[1:].lower()
    if t := _TIME_RANGE_RE.search(sentence):
        return f"{word} kl. {t.group(1)}-{t.group(2)}"
    return word


@dataclass
class Day:
    date: datetime.date
    timetable: list[str] = field(default_factory=list)            # lesson codes, e.g. "IDR"
    notes: dict[str, list[str]] = field(default_factory=dict)     # heading -> lines
    timetable_end: datetime.time | None = None                     # end of the last lesson


@dataclass
class Summary:
    title: str | None
    description: str
    items: list[str]
    note_count: int
    unmatched: list[str]  # note lines no rule used -- for the log
    cancelled: list[str]  # timetable gear dropped because the notes cancel it


def _early_finish(sentence: str, timetable_end: datetime.time | None) -> str | None:
    """'Kl 8.00-12.55' when the timetable runs to 13.40 -> 'Slut kl. 12.55'."""
    m = _SCHOOL_HOURS_RE.search(sentence)
    if not m or timetable_end is None:
        return None
    end = datetime.time(int(m.group(3)), int(m.group(4)))
    today = datetime.date(2000, 1, 1)
    gap = datetime.datetime.combine(today, timetable_end) - datetime.datetime.combine(today, end)
    return f"Slut kl. {m.group(3)}.{m.group(4)}" if gap >= EARLY_FINISH else None


def summarise(day: Day) -> Summary:
    events: list[str] = []
    hours: list[str] = []
    bring: list[str] = []
    homework: list[str] = []
    unmatched: list[str] = []
    all_text = " ".join(line for lines in day.notes.values() for line in lines)

    for heading, lines in day.notes.items():
        for line in lines:
            used = False
            for sentence in _SENTENCE_RE.split(line):
                if (item := _event_item(sentence)) and item not in events:
                    events.append(item)
                    used = True
                if (item := _early_finish(sentence, day.timetable_end)) and item not in hours:
                    hours.append(item)
                    used = True
                if (item := _bring_item(sentence)) and item not in bring:
                    bring.append(item)
                    used = True
                if _HOMEWORK_RE.search(sentence):
                    label = subject_label(heading)
                    if label not in homework:
                        homework.append(label)
                    used = True
            if not used:
                unmatched.append(line)

    gear, cancelled = [], []
    codes = {code.upper() for code in day.timetable}
    for prefix, (word, item) in _GEAR.items():
        if not any(code.startswith(prefix) for code in codes):
            continue
        if _cancel_re(word).search(all_text):
            cancelled.append(item)
        elif not any(word in b.lower() for b in bring):  # already said by a "medbring"
            gear.append(item)

    items = events + hours + bring + gear + ([f"Lektier: {', '.join(homework)}"] if homework else [])
    note_count = sum(len(lines) for lines in day.notes.values())
    if items:
        title = "🎒 " + " · ".join(items)
    elif note_count:
        title = f"📝 {note_count} {'note' if note_count == 1 else 'noter'}"
    else:
        title = None

    parts = []
    for heading, lines in day.notes.items():
        parts.append(f"{heading}:\n" + "\n".join(f"- {line}" for line in lines))
    if cancelled:
        parts.append(f"(Ikke med i dag ifølge ugeplanen: {', '.join(cancelled)})")
    return Summary(title, "\n\n".join(parts), items, note_count, unmatched, cancelled)


# --- weekly-summary JSON -> Days -----------------------------------------


def days_from_weekly_summary(summary: dict, child_id: int | None = None,
                             first_name: str | None = None) -> list[Day]:
    """Build Days from `aula --output json weekly-summary --provider meebook`.

    The summary covers every child. With child_id / first_name given, only that
    child's lessons (matched on the event's profile ids; an event that names no
    profile is kept) and that child's Meebook notes (matched on the first name)
    are used, so each child gets days of their own.
    """
    week = re.fullmatch(r"(\d{4})-W(\d{1,2})", summary.get("week") or "")
    if not week:
        return []
    monday = datetime.date.fromisocalendar(int(week[1]), int(week[2]), 1)
    days: dict[datetime.date, Day] = {}

    def day(d: datetime.date) -> Day:
        return days.setdefault(d, Day(d))

    for ev in summary.get("calendar_events") or []:
        if (ev.get("event_type") or "lesson").lower() != "lesson":
            continue
        if child_id is not None:
            owners = ev.get("belongs_to_profiles") or ([ev["belongs_to"]] if ev.get("belongs_to") else [])
            if owners and child_id not in owners:
                continue
        try:
            start = datetime.datetime.fromisoformat(ev["start_datetime"])
        except (KeyError, ValueError):
            continue
        d = day(start.date())
        if title := (ev.get("title") or "").strip():
            d.timetable.append(title)
        try:
            end = datetime.datetime.fromisoformat(ev["end_datetime"]).time()
        except (KeyError, ValueError):
            continue
        if d.timetable_end is None or end > d.timetable_end:
            d.timetable_end = end

    students = summary.get("meebook_weekplan") or []
    if first_name:
        students = [s for s in students
                    if (s.get("name") or "").split(" ")[0].lower() == first_name.lower()]
    with_notes = {s.get("name") for s in students for d in s.get("week_plan", []) if d.get("tasks")}
    for student in students:
        first_name = (student.get("name") or "").split(" ")[0]
        for entry in student.get("week_plan", []):
            d = meebook_date(entry.get("date", ""), monday)
            if d is None:
                continue
            for task in entry.get("tasks", []):
                lines = note_lines(task.get("content"))
                if not lines:
                    continue
                heading = (task.get("pill") or "Noter").strip()
                if len(with_notes) > 1 and first_name:  # don't merge two children's homework
                    heading = f"{first_name} - {heading}"
                day(d).notes.setdefault(heading, []).extend(lines)

    return [days[d] for d in sorted(days) if d.weekday() < 5]
