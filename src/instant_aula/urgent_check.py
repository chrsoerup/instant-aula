"""Poll Aula for new messages/notifications/posts; email only what a local
LLM judges urgent against a fixed rubric.

Run on a schedule (e.g. every couple of hours via cron). Safe to run
repeatedly: already-seen items are tracked in state/state.json and never
re-classified or re-alerted.
"""

from __future__ import annotations

import json
import sys

from .aula_cli import run_aula
from .config import Settings, load_settings
from .emailer import send
from .html_utils import strip_html
from .ollama_client import chat
from .state import State

URGENCY_SYSTEM_PROMPT = """You triage messages from a Danish school's parent \
communication app (Aula) for a parent who only wants to be interrupted for \
genuinely urgent matters.

Mark something urgent ONLY if it is one of:
- A school closure, cancellation, or schedule change affecting today or tomorrow.
- A safety, illness, or pickup-time matter needing attention within about 24 hours.
- An explicit request that requires the parent to reply or act, with a deadline \
within about 24 hours.

Everything else -- general announcements, event invites weeks out, routine \
praise or photos, newsletters -- is NOT urgent, even if long or formally worded.

Respond with ONLY a JSON object: {"urgent": true|false, "reason": "<one short \
sentence in English explaining the verdict>"}."""


def _classify(settings: Settings, label: str, content: str) -> tuple[bool, str]:
    reply = chat(
        settings.ollama_host,
        settings.ollama_model,
        URGENCY_SYSTEM_PROMPT,
        f"{label}\n\n{content or '(no content)'}",
        json_mode=True,
    )
    try:
        data = json.loads(reply)
        return bool(data.get("urgent")), str(data.get("reason", ""))
    except (json.JSONDecodeError, AttributeError):
        # Fail open rather than silently drop something we couldn't parse.
        return True, f"Could not parse LLM classification; raw reply: {reply[:200]}"


def _check_messages(settings: Settings, state: State) -> list[tuple[str, str, str]]:
    alerts = []
    threads = run_aula(settings, "messages", "--unread", "--limit", "20")
    for thread in threads:
        thread_id = thread.get("thread_id")
        if thread_id is None or not state.is_new("seen_message_ids", thread_id):
            continue
        body = "\n\n".join(
            strip_html(m.get("content_html")) for m in thread.get("messages", [])
        )
        label = f"Message: {thread.get('subject') or '(no subject)'}"
        if not state.is_first_run:
            urgent, reason = _classify(settings, label, body)
            if urgent:
                alerts.append((label, reason, body))
        state.mark_seen("seen_message_ids", thread_id)
    return alerts


def _check_notifications(settings: Settings, state: State) -> list[tuple[str, str, str]]:
    alerts = []
    notifications = run_aula(settings, "notifications", "--limit", "30")
    for item in notifications:
        item_id = item.get("id")
        if item_id is None or not state.is_new("seen_notification_ids", item_id):
            continue
        details = ", ".join(
            f"{key}={value}" for key, value in item.items() if value not in (None, "") and key != "id"
        )
        label = f"Notification: {item.get('title') or '(no title)'}"
        if not state.is_first_run:
            urgent, reason = _classify(settings, label, details)
            if urgent:
                alerts.append((label, reason, details))
        state.mark_seen("seen_notification_ids", item_id)
    return alerts


def _check_posts(settings: Settings, state: State) -> list[tuple[str, str, str]]:
    alerts = []
    posts = run_aula(settings, "posts", "--limit", "10")
    for post in posts:
        post_id = post.get("id")
        if post_id is None or not state.is_new("seen_post_ids", post_id):
            continue
        body = strip_html(post.get("content_html"))
        flag = "Aula marked this post important. " if post.get("is_important") else ""
        label = f"Post: {post.get('title') or '(no title)'}"
        if not state.is_first_run:
            urgent, reason = _classify(settings, label, flag + body)
            if urgent:
                alerts.append((label, reason, body))
        state.mark_seen("seen_post_ids", post_id)
    return alerts


def main() -> int:
    settings = load_settings()
    state = State(settings.state_file)

    if state.is_first_run:
        print("First run: baselining state from the current backlog, no alerts will be sent.")

    alerts = [
        *_check_messages(settings, state),
        *_check_notifications(settings, state),
        *_check_posts(settings, state),
    ]
    state.save()

    if not alerts:
        print("No new urgent items.")
        return 0

    for label, reason, body in alerts:
        print(f"URGENT: {label} -- {reason}")
        send(settings, subject=f"[Aula - Urgent] {label}", body=f"{reason}\n\n---\n\n{body}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
