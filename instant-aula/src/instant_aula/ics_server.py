"""Serve the school calendar feed to Home Assistant's Remote calendar.

Serves the calendar feeds -- `/<name>.ics` files, lower-case letters, digits,
`-` and `_` only -- from ICS_DIR; every other path is a 404, so nothing else in
the add-on's /data can leak through it.

The add-on publishes no ports (config.yaml has no `ports:`), so this is only
reachable on the Supervisor's internal network -- by Home Assistant Core at
http://<add-on hostname>:<ICS_PORT>/<name>.ics -- not from the LAN. The school
data therefore never sits in /config/www, which Home Assistant serves to
anyone on the network without a login.
"""

from __future__ import annotations

import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .calendar_sync import ics_dir

FEED_PATH = re.compile(r"/([a-z0-9_-]+\.ics)")  # the whole path, no directories


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        match = FEED_PATH.fullmatch(self.path.split("?", 1)[0])
        if not match:
            self.send_error(404)
            return
        try:
            body = (ics_dir() / match.group(1)).read_bytes()
        except FileNotFoundError:
            # No such feed, or not generated yet (first start, before calendar_sync has run).
            self.send_error(503, "Feed not available (yet)")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/calendar; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass  # one line per Home Assistant poll would drown the job output


def main() -> None:
    port = int(os.environ.get("ICS_PORT", "8099"))
    print(f"ics_server: serving /<name>.ics feeds from {ics_dir()} on port {port}")
    ThreadingHTTPServer(("0.0.0.0", port), _Handler).serve_forever()


if __name__ == "__main__":
    main()
