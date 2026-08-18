"""Minimal client for a local Ollama instance's /api/chat endpoint.

Uses only the stdlib so no HTTP dependency is needed for what is a single
non-streaming call per invocation.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class OllamaError(RuntimeError):
    pass

# Ollama silently truncates the prompt to fit whatever num_ctx is in effect
# (default 2048 tokens) rather than erroring -- a week's worth of calendar +
# weekplan JSON blows past that easily, and a truncated prompt doesn't fail
# loudly, it just makes the model respond to a mangled fragment. Size the
# context window to the actual prompt instead of trusting the default.
_CONTEXT_SIZES = (2048, 4096, 8192, 16384, 32768, 65536)


def _pick_num_ctx(system: str, user: str) -> int:
    approx_tokens = (len(system) + len(user)) // 3 + 1024  # + headroom for the reply
    for size in _CONTEXT_SIZES:
        if size >= approx_tokens:
            return size
    return _CONTEXT_SIZES[-1]


def chat(host: str, model: str, system: str, user: str, json_mode: bool = False) -> str:
    """Send one system+user turn to a local Ollama model, return the reply text."""
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"num_ctx": _pick_num_ctx(system, user)},
    }
    if json_mode:
        payload["format"] = "json"

    request = urllib.request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # CPU-only inference of an 8k+ token prompt can take a long time;
        # this runs unattended via cron, so a generous bound is fine.
        with urllib.request.urlopen(request, timeout=1800) as response:
            body = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError) as exc:
        raise OllamaError(
            f"Could not reach Ollama at {host} (is `ollama serve` running and the "
            f"model pulled with `ollama pull {model}`?): {exc}"
        ) from exc

    return body["message"]["content"]
