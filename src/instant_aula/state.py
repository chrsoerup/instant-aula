"""Local JSON state tracking which Aula items have already been processed.

Keeps `urgent_check` from re-classifying (and re-emailing) the same message,
post, or notification on every run.
"""

from __future__ import annotations

import json
from pathlib import Path

_CATEGORIES = ("seen_message_ids", "seen_post_ids", "seen_notification_ids")


class State:
    def __init__(self, path: Path) -> None:
        self._path = path
        self.is_first_run = not path.exists()
        self._data: dict[str, list[int]] = {category: [] for category in _CATEGORIES}
        if not self.is_first_run:
            self._data.update(json.loads(path.read_text()))

    def is_new(self, category: str, item_id: int) -> bool:
        return item_id not in self._data[category]

    def mark_seen(self, category: str, item_id: int) -> None:
        if item_id not in self._data[category]:
            self._data[category].append(item_id)

    def save(self) -> None:
        self._path.write_text(json.dumps(self._data, indent=2))
