"""Poll Aula for new messages and important posts; push them as Home
Assistant notifications immediately.

Deterministic, no LLM: a new message is personally addressed by a teacher
(not a broadcast), so it's forwarded as-is; a post is only alerted on if
Aula's own "is_important" flag is set -- a signal the school sets itself,
not inferred from content. Notifications (photo uploads, presence changes,
etc.) are intentionally not checked -- they're largely duplicative of the
above and low-signal.

Run on a schedule (e.g. every couple of hours via cron). Safe to run
repeatedly: already-seen items are tracked in state/state.json and never
re-alerted.
"""

from __future__ import annotations

import sys

from .aula_cli import run_aula
from .config import Settings, load_settings
from .ha_notify import notify
from .html_utils import strip_html
from .notify_failure import notify_failure
from .state import State


def _check_messages(settings: Settings, state: State) -> list[tuple[str, str, int]]:
    alerts = []
    threads = run_aula(settings, "messages", "--unread", "--limit", "20")
    for thread in threads:
        thread_id = thread.get("thread_id")
        if thread_id is None or not state.is_new("seen_message_ids", thread_id):
            continue
        body = "\n\n".join(
            strip_html(m.get("content_html")) for m in thread.get("messages", [])
        )
        if not state.is_first_run:
            subject = thread.get("subject") or "(uden emne)"
            alerts.append((f"Besked: {subject}", body or "(intet indhold)", 0))
        state.mark_seen("seen_message_ids", thread_id)
    return alerts


def _check_important_posts(settings: Settings, state: State) -> list[tuple[str, str, int]]:
    alerts = []
    posts = run_aula(settings, "posts", "--limit", "10")
    for post in posts:
        post_id = post.get("id")
        if post_id is None or not state.is_new("seen_post_ids", post_id):
            continue
        if post.get("is_important") and not state.is_first_run:
            title = post.get("title") or "(uden titel)"
            body = strip_html(post.get("content_html"))
            attachment_count = len(post.get("attachments") or [])
            alerts.append((f"Vigtigt opslag: {title}", body or "(intet indhold)", attachment_count))
        state.mark_seen("seen_post_ids", post_id)
    return alerts


def main() -> int:
    settings = load_settings()
    state = State(settings.state_file)

    if state.is_first_run:
        print("First run: baselining state from the current backlog, no alerts will be sent.")

    alerts = [
        *_check_messages(settings, state),
        *_check_important_posts(settings, state),
    ]
    state.save()

    if not alerts:
        print("No new must-read items.")
        return 0

    for label, body, attachment_count in alerts:
        print(f"ALERT: {label}" + (f" ({attachment_count} attachment(s))" if attachment_count else ""))
        message = body
        if attachment_count:
            message += f"\n\n({attachment_count} vedhæftet fil(er) - se Aula for indhold)"
        notify(settings, title=f"[Aula] {label}", message=message)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        notify_failure("urgent_check", exc)
        raise
