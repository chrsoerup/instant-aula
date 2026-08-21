"""Strip Aula's rich-text HTML fields down to plain text for LLM prompts."""

from __future__ import annotations

import re

_BLOCK_BREAK_RE = re.compile(r"</(p|div|li|h[1-6])>|<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(html: str | None) -> str:
    if not html:
        return ""
    text = _BLOCK_BREAK_RE.sub("\n", html)
    text = _TAG_RE.sub("", text)
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)
