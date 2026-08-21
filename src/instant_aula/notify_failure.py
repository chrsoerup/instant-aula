"""Best-effort email alert for when a scheduled job crashes.

Cron jobs fail silently otherwise -- e.g. if the cached MitID session ever
needs re-authenticating again, a digest would just quietly stop arriving.
This sends a plain failure notice via the same SMTP settings (which don't
depend on Aula auth), so a broken job surfaces immediately instead of via
"I haven't gotten a digest in three weeks".
"""

from __future__ import annotations

import traceback

from .config import load_settings
from .emailer import send


def notify_failure(job_name: str, exc: BaseException) -> None:
    try:
        settings = load_settings()
        body = "".join(traceback.format_exception(exc))
        send(settings, subject=f"[Aula] {job_name} failed", body=body)
    except Exception:
        pass  # don't let a broken notification path hide the original error
