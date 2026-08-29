"""Thin subprocess wrapper around the `aula` CLI (github.com/nickknissen/aula).

Shelling out to the CLI (rather than importing the async client) means we
track its stable, documented `--output json` surface instead of internal
APIs the project explicitly marks as subject to change.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

from .config import PROJECT_ROOT, Settings

# Windows Task Scheduler can wake a sleeping machine to run these jobs
# (WakeToRun), and WSL's network stack isn't always ready the instant it
# comes back -- DNS resolution in particular can fail for the first few
# seconds. Retry on these rather than treating a transient blip as a real
# failure worth an email alert.
_TRANSIENT_MARKERS = (
    "NetworkError",
    "Temporary failure in name resolution",
    "ConnectError",
    "Connection refused",
    "Connection reset",
    "Network is unreachable",
)


class AulaCliError(RuntimeError):
    def __init__(self, args: tuple[str, ...], returncode: int, stderr: str) -> None:
        self.args_run = args
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"`aula {' '.join(args)}` failed (exit {returncode}):\n{stderr.strip()}")


def run_aula(settings: Settings, *args: str) -> Any:
    """Run an `aula` CLI command and return its parsed JSON output.

    First run of any command will require interactive MitID approval
    (QR scan in the terminal); tokens are then cached by the `aula` CLI
    itself at ~/.config/aula/tokens.json and refreshed automatically.
    """
    # Cron gives jobs a minimal PATH that doesn't include ~/.local/bin, where
    # uv actually lives -- a bare "uv" lookup fails there. Default to the
    # absolute path instead of relying on PATH; still overridable via UV.
    uv = os.environ.get("UV", "/home/cs/.local/bin/uv")
    cmd = [uv, "run", "aula", "--output", "json", *args]
    env = {
        **os.environ,
        "AULA_MITID_USERNAME": settings.aula_username,
        "AULA_AUTH_METHOD": settings.aula_auth_method,
    }
    if settings.aula_mitid_password:
        env["AULA_MITID_PASSWORD"] = settings.aula_mitid_password

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            # Some failures inside the CLI (e.g. one widget fetch failing)
            # are caught internally and only logged as a warning, not
            # raised -- the command still exits 0 with incomplete data.
            # Surface that instead of silently discarding it.
            if result.stderr.strip():
                print(f"[aula stderr] {result.stderr.strip()}")
            return json.loads(result.stdout)

        transient = any(marker in result.stderr for marker in _TRANSIENT_MARKERS)
        if not transient or attempt == max_attempts:
            raise AulaCliError(args, result.returncode, result.stderr)

        print(f"Transient network error on attempt {attempt}/{max_attempts}, retrying in 15s...")
        time.sleep(15)
