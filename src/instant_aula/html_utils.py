"""Strip Aula's rich-text HTML fields down to plain text for LLM prompts."""

from __future__ import annotations

import re

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(html: str | None) -> str:
    if not html:
        return ""
    text = _TAG_RE.sub(" ", html)
    return re.sub(r"\s+", " ", text).strip()
