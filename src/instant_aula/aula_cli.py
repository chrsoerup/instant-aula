"""Thin subprocess wrapper around the `aula` CLI (github.com/nickknissen/aula).

Shelling out to the CLI (rather than importing the async client) means we
track its stable, documented `--output json` surface instead of internal
APIs the project explicitly marks as subject to change.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from .config import PROJECT_ROOT, Settings


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
    uv = os.environ.get("UV", "uv")
    cmd = [uv, "run", "aula", "--output", "json", *args]
    env = {
        **os.environ,
        "AULA_MITID_USERNAME": settings.aula_username,
        "AULA_AUTH_METHOD": settings.aula_auth_method,
    }
    if settings.aula_mitid_password:
        env["AULA_MITID_PASSWORD"] = settings.aula_mitid_password

    result = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise AulaCliError(args, result.returncode, result.stderr)
    return json.loads(result.stdout)
