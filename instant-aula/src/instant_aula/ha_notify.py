"""Push notification delivery via Home Assistant, replacing SMTP email.
Only works when running inside the instant-aula HA add-on, which gets an
auto-injected SUPERVISOR_TOKEN and homeassistant_api access -- see config.yaml.
"""

from __future__ import annotations

import os

import httpx

from .config import Settings

_SUPERVISOR_API = "http://supervisor/core/api"


def _targets(settings: Settings) -> list[str]:
    """Notify services to deliver to, from a comma-separated option value.

    Multiple targets exist for one reason: a phone push is the only thing that
    reaches you away from home, but iOS renders a 3500-character weekly digest
    as a two-line preview and the Companion app has no inbox to open it in.
    Adding "persistent_notification" puts the same text in Home Assistant's own
    Notifications panel, where it renders in full and stays until dismissed.

    A leading "notify." is stripped: the option wants the service name, but the
    UI shows actions as "notify.mobile_app_x", and pasting that verbatim
    produced a 400 that read as a broken integration rather than a typo.
    """
    targets = []
    for raw in settings.ha_notify_service.split(","):
        name = raw.strip().removeprefix("notify.")
        if name:
            targets.append(name)
    return targets


def notify(settings: Settings, title: str, message: str) -> None:
    token = os.environ["SUPERVISOR_TOKEN"]
    targets = _targets(settings)
    if not targets:
        raise RuntimeError("No notify service configured (ha_notify_service is empty)")

    failures: list[str] = []
    for target in targets:
        try:
            response = httpx.post(
                f"{_SUPERVISOR_API}/services/notify/{target}",
                headers={"Authorization": f"Bearer {token}"},
                json={"title": title, "message": message},
                timeout=10,
            )
            response.raise_for_status()
        except Exception as exc:
            # One bad target must not lose a message the others accepted -- the
            # phone push is the one that matters, and a typo in a secondary
            # service should cost a log line, not the digest.
            failures.append(f"{target}: {exc}")
            print(f"[notify] Delivery to '{target}' failed: {exc}")

    if len(failures) == len(targets):
        raise RuntimeError("Notification delivery failed for every target: " + "; ".join(failures))
