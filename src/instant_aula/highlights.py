"""Optional local-LLM pass that pulls the parent-actionable reminders
(bring gym clothes, homework due, bring the 'læsemappe', permission slip
needed, etc.) out of the week's structured schedule/notes, via a locally
running Ollama model.

Kept separate from the deterministic table-building in weekly_digest.py:
spotting an implicit "you need to prepare something for this day" signal
buried in a teacher's free-text note is a genuine judgment call an LLM is
suited for -- unlike grouping/formatting the same text, which stays plain
Python (see the module docstring in weekly_digest.py for why).

Treated as a non-critical extra: any failure here (Ollama not running,
timeout, bad output) is swallowed and logged rather than raised, so a
broken local LLM never blocks the weekly digest itself from sending.
"""

from __future__ import annotations

import json

import httpx

from .config import Settings

_PROMPT = """Du er assistent for en forælder til et skolebarn. Herunder er ugens skema og noter/lektier fra skolen, opdelt pr. dato.

Find KUN konkrete ting forælderen skal huske at gøre eller medbringe FØR eller PÅ den pågældende dag - fx medbring idrætstøj, medbring læsemappe, lav lektier, medbring bestemte ting til en begivenhed, aflever en tilmelding/underskrift, eller at en forælder selv skal deltage i noget. Ignorer almindelig skemainformation (fag, lokaler, lærernavne, vikartimer) som ikke kræver nogen forberedelse.

Svar udelukkende med gyldig JSON på formen: {{"highlights": [{{"date": "YYYY-MM-DD", "text": "kort saetning"}}]}}. Hvis intet relevant findes, returner {{"highlights": []}}. Skriv ikke andet end JSON'en.

Ugens data:
{data}
"""


def _build_input(dates: list[str], events: dict[str, list[str]], notes: dict[str, list[str]]) -> str:
    blocks = []
    for date in dates:
        lines = [f"Dato {date}:"]
        lines.extend(f"  Skema: {item}" for item in events.get(date, []))
        lines.extend(f"  Note: {item}" for item in notes.get(date, []))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def extract_highlights(
    settings: Settings,
    dates: list[str],
    events: dict[str, list[str]],
    notes: dict[str, list[str]],
) -> list[tuple[str, str]] | None:
    """Return (date, reminder) pairs picked out by the local model, or
    None if the call failed or returned nothing usable. Callers should
    treat None the same as "no highlights this week" -- send the digest
    without the section rather than fail the run."""
    data = _build_input(dates, events, notes)
    if not data.strip():
        return []

    try:
        response = httpx.post(
            f"{settings.ollama_host}/api/generate",
            json={
                "model": settings.ollama_model,
                "prompt": _PROMPT.format(data=data),
                "stream": False,
                "format": "json",
            },
            timeout=settings.ollama_timeout,
        )
        response.raise_for_status()
        payload = json.loads(response.json()["response"])
    except Exception as exc:
        print(f"[highlights] Skipping reminder highlights, Ollama call failed: {exc}")
        return None

    highlights = []
    for item in payload.get("highlights", []):
        date, text = item.get("date"), item.get("text")
        if date and text:
            highlights.append((date, text))
    return highlights