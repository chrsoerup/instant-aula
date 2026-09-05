"""Best-effort push notification for when a scheduled job crashes.

Cron jobs fail silently otherwise -- e.g. if the cached MitID session ever
needs re-authenticating again, a digest would just quietly stop arriving.
This sends a plain failure notice via Home Assistant, so a broken job
surfaces immediately instead of via "I haven't gotten a digest in three
weeks". If Home Assistant itself is unreachable, this has nowhere left to
go -- that's an accepted gap of having a single delivery channel, visible
in the add-on's own logs.
"""

from __future__ import annotations

import traceback

from .config import load_settings
from .ha_notify import notify


def notify_failure(job_name: str, exc: BaseException) -> None:
    try:
        settings = load_settings()
        body = "".join(traceback.format_exception(exc))
        notify(settings, title=f"[Aula] {job_name} failed", message=body)
    except Exception:
        pass  # don't let a broken notification path hide the original error
