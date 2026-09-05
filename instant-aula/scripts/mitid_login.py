"""One-off interactive login helper.

The `aula` CLI renders its MitID QR codes as terminal text/block art, which
depends on the terminal's font, colors, and rendering timing all lining up
correctly -- in practice this has proven unreliable. This monkeypatches its
QR renderer to save real PNG images instead, which sidesteps all of that:
open the file and scan it like any other QR code.

Run this once to complete the interactive MitID login. Tokens are then
cached at ~/.config/aula/tokens.json and every other `aula`/instant_aula
command works headlessly from there -- this script is not needed again
unless the cached tokens are cleared.

Usage: uv run python scripts/mitid_login.py --output text -v login
"""

import asyncio
import sys
import time
from pathlib import Path

import qrcode

import aula.cli as aula_cli
from aula.auth.browser_client import BrowserClient
from aula.auth.exceptions import MitIDError

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_HA_WWW = Path("/config/www")
# Inside the Home Assistant add-on, save under HA's own www/ folder so the
# QR is viewable at http://<ha-host>:8123/local/... from any browser on the
# LAN (e.g. your PC), without needing shell/file access into the container --
# MitID needs the QR scanned by your phone, so it can't be viewed on the same
# device that's scanning it anyway. Falls back to the project root for local/
# WSL testing, where the original VS Code file-preview workflow still works.
# www/ itself may not exist yet on a fresh Home Assistant install -- create
# it if /config (the mounted volume) is there, rather than silently falling
# back to a path nothing outside the container can reach.
if Path("/config").is_dir():
    _HA_WWW.mkdir(exist_ok=True)
    _QR_DIR = _HA_WWW
else:
    _QR_DIR = _PROJECT_ROOT
_QR1_PATH = _QR_DIR / "instant_aula_mitid_qr_1.png"
_QR2_PATH = _QR_DIR / "instant_aula_mitid_qr_2.png"

_first_seen: float | None = None
_last_payload: bytes | None = None
_call_count = 0


def _rebuild_scannable(qr: qrcode.QRCode) -> qrcode.QRCode:
    """The library builds its QR codes with border=1 -- well below the
    standard-recommended quiet zone of 4 modules, which is a common cause of
    camera scan failures once the image sits inside any UI chrome. Rebuild
    from the same underlying data with a proper border and higher resolution."""
    fresh = qrcode.QRCode(border=4, box_size=15, error_correction=qr.error_correction)
    fresh.add_data(qr.data_list[0].data)
    fresh.make(fit=True)
    return fresh


def _print_qr_codes_image(qr1, qr2) -> None:
    global _first_seen, _last_payload, _call_count
    _call_count += 1
    now = time.monotonic()
    if _first_seen is None:
        _first_seen = now
    elapsed = now - _first_seen

    payload = qr1.data_list[0].data
    changed = payload != _last_payload
    _last_payload = payload

    _rebuild_scannable(qr1).make_image(fill_color="black", back_color="white").save(_QR1_PATH)
    _rebuild_scannable(qr2).make_image(fill_color="black", back_color="white").save(_QR2_PATH)
    print("=" * 60)
    print(f"[diag] call #{_call_count}, t+{elapsed:.1f}s, payload changed since last call: {changed}")
    print("SCAN THESE QR CODES WITH YOUR MITID APP")
    if _QR_DIR == _HA_WWW:
        print("Open these on a computer/browser (not the phone doing the scanning):")
        print("  QR CODE 1 (scan first):  http://homeassistant.local:8123/local/instant_aula_mitid_qr_1.png")
        print("  QR CODE 2 (scan second): http://homeassistant.local:8123/local/instant_aula_mitid_qr_2.png")
    else:
        print(f"QR CODE 1 (scan this first):  {_QR1_PATH}")
        print(f"QR CODE 2 (scan this second): {_QR2_PATH}")
        print("Open each file (e.g. click it in the VS Code file explorer to preview it) and scan.")
    print("=" * 60)


# Workaround for an upstream gap: MitID's poll endpoint can return
# {"status": "OK", "confirmation": false/absent} as a transient in-between
# state (observed right after the user approves in the app) that the
# library's state machine doesn't recognize -- it only checks for
# status == "OK" AND confirmation is True, and treats anything else
# matching "OK" as a fatal "Unexpected poll status". Tolerate a bounded
# number of these before giving up, instead of failing immediately.
_original_poll = BrowserClient._poll_for_app_confirmation


async def _poll_for_app_confirmation_patched(self, poll_url: str, ticket: str):
    ok_without_confirmation = 0
    while True:
        r = await self._client.post(poll_url, json={"ticket": ticket})
        data = r.json()

        if not r.is_success:
            raise MitIDError("Login request was not accepted")

        if data.get("status") == "OK" and data.get("confirmation") is True:
            return data["payload"]["response"], data["payload"]["responseSignature"]

        if data.get("status") == "OK":
            ok_without_confirmation += 1
            print(
                f"[diag] status=OK without confirmation yet (attempt {ok_without_confirmation}/20), retrying..."
            )
            if ok_without_confirmation >= 20:
                raise MitIDError("Gave up waiting for confirmation after status=OK many times")
            await asyncio.sleep(0.5)
            continue

        return await _original_poll(self, poll_url, ticket)


BrowserClient._poll_for_app_confirmation = _poll_for_app_confirmation_patched

aula_cli._print_qr_codes_in_terminal = _print_qr_codes_image

if __name__ == "__main__":
    print(f"[diag] saving QR codes to: {_QR_DIR}")
    sys.exit(aula_cli.cli())
