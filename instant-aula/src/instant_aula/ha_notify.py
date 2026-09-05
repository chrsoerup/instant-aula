"""Push notification delivery via Home Assistant, replacing SMTP email.
Only works when running inside the instant-aula HA add-on, which gets an
auto-injected SUPERVISOR_TOKEN and homeassistant_api access -- see config.yaml.
"""

from __future__ import annotations

import os

import httpx

from .config import Settings

_SUPERVISOR_API = "http://supervisor/core/api"


def notify(settings: Settings, title: str, message: str) -> None:
    token = os.environ["SUPERVISOR_TOKEN"]
    response = httpx.post(
        f"{_SUPERVISOR_API}/services/notify/{settings.ha_notify_service}",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": title, "message": message},
        timeout=10,
    )
    response.raise_for_status()
