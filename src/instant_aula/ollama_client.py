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


def chat(host: str, model: str, system: str, user: str, json_mode: bool = False) -> str:
    """Send one system+user turn to a local Ollama model, return the reply text."""
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
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
        with urllib.request.urlopen(request, timeout=300) as response:
            body = json.loads(response.read())
    except urllib.error.URLError as exc:
        raise OllamaError(
            f"Could not reach Ollama at {host} (is `ollama serve` running and the "
            f"model pulled with `ollama pull {model}`?): {exc}"
        ) from exc

    return body["message"]["content"]
