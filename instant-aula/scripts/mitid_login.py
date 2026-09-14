"""One-off interactive login helper.

The `aula` CLI renders its MitID QR codes as terminal text/block art, which
depends on the terminal's font, colors, and rendering timing all lining up
correctly -- in practice this has proven unreliable. This monkeypatches its
QR renderer to render a single, live-refreshing image instead, viewable in
any browser on the LAN.

Run this once to complete the interactive MitID login. Tokens are then
cached at ~/.config/aula/tokens.json and every other `aula`/instant_aula
command works headlessly from there -- this script is not needed again
unless the cached tokens are cleared.

Usage: uv run python scripts/mitid_login.py --output text -v login
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

import aula.cli as aula_cli
from aula.auth.browser_client import BrowserClient
from aula.auth.exceptions import MitIDError

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
# The homeassistant_config:rw map in config.yaml mounts HA's config
# directory at /homeassistant in this container -- NOT /config. The
# /config -> /homeassistant symlink seen in some official add-ons (ssh,
# samba) is something those images set up themselves, not something
# Supervisor provides automatically.
_HA_CONFIG = Path("/homeassistant")
_HA_WWW = _HA_CONFIG / "www"
# Save under HA's own www/ folder so the QR page is viewable at
# http://<ha-host>:8123/local/... from any browser on the LAN (e.g. your
# PC), without needing shell/file access into the container -- MitID needs
# the QR scanned by your phone, so it can't be viewed on the same device
# that's scanning it anyway. Falls back to the project root for local/WSL
# testing, where opening the same HTML file directly works.
# www/ itself may not exist yet on a fresh Home Assistant install -- create
# it if the mounted config volume is there, rather than silently falling
# back to a path nothing outside the container can reach.
if _HA_CONFIG.is_dir():
    _HA_WWW.mkdir(exist_ok=True)
    _QR_DIR = _HA_WWW
else:
    _QR_DIR = _PROJECT_ROOT
_QR_NAME = "instant_aula_mitid_qr.png"
_QR_PATH = _QR_DIR / _QR_NAME
_PAGE_PATH = _QR_DIR / "instant_aula_mitid.html"
_PAGE_URL = f"http://homeassistant.local:8123/local/{_PAGE_PATH.name}"

_first_seen: float | None = None
_last_payload: bytes | None = None
_call_count = 0

# MitID rotates the channel-binding value behind these codes continuously, and
# both codes carry two halves of the *same* generation of it -- scanning half
# of one generation and half of the next fails. Rendering the pair into one
# image, refreshed as a unit, is what makes them scannable from a single camera
# frame: no tab switching, no chance of straddling a rotation.
_PAGE_HTML = f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MitID login - instant-aula</title>
<style>
  body {{ background:#fff; color:#222; font-family:system-ui,sans-serif;
         text-align:center; margin:0; padding:16px; }}
  img {{ max-width:100%; height:auto; }}
  #status {{ color:#666; font-size:14px; }}
</style>
<h2>Scan both QR codes with the MitID app</h2>
<p>Keep this page open until the login completes. The codes refresh on their
own &mdash; do not reload manually.</p>
<img id="qr" alt="waiting for QR codes...">
<p id="status">Waiting for the login to produce QR codes&hellip;</p>
<script>
  // Home Assistant serves /local/ with long-lived cache headers, so every
  // request needs a unique URL or the browser will happily show the same
  // frozen QR image for days. Preload into a detached Image first, then swap:
  // that avoids both flicker and showing a half-written file.
  // Not named `status`: a global `var status` collides with window.status,
  // which coerces whatever is assigned to it into a string.
  var img = document.getElementById('qr'), msg = document.getElementById('status');
  function tick() {{
    var next = new Image();
    next.onload = function () {{
      img.src = next.src;
      msg.textContent = 'Updated ' + new Date().toLocaleTimeString();
    }};
    next.onerror = function () {{
      msg.textContent = 'Waiting for the login to produce QR codes\\u2026';
    }};
    next.src = '{_QR_NAME}?t=' + Date.now();
  }}
  setInterval(tick, 700);
  tick();
</script>
"""


def _rebuild_scannable(qr: qrcode.QRCode) -> Image.Image:
    """The library builds its QR codes with border=1 -- well below the
    standard-recommended quiet zone of 4 modules, which is a common cause of
    camera scan failures once the image sits inside any UI chrome. Rebuild
    from the same underlying data with a proper border and higher resolution."""
    fresh = qrcode.QRCode(border=4, box_size=10, error_correction=qr.error_correction)
    fresh.add_data(qr.data_list[0].data)
    fresh.make(fit=True)
    image = fresh.make_image(fill_color="black", back_color="white")
    # qrcode's PIL factory wraps the real image; older versions only expose it
    # as _img.
    return getattr(image, "get_image", lambda: image._img)().convert("RGB")


def _update_count(qr: qrcode.QRCode) -> int | None:
    """Pull MitID's rotation counter out of the QR payload, for display."""
    try:
        return json.loads(qr.data_list[0].data)["uc"]
    except Exception:
        return None


def _compose(qr1: qrcode.QRCode, qr2: qrcode.QRCode) -> Image.Image:
    left, right = _rebuild_scannable(qr1), _rebuild_scannable(qr2)
    gap, margin, caption_h = 40, 30, 70
    width = margin * 2 + left.width + gap + right.width
    height = margin * 2 + max(left.height, right.height) + caption_h
    canvas = Image.new("RGB", (width, height), "white")
    canvas.paste(left, (margin, margin + caption_h))
    canvas.paste(right, (margin + left.width + gap, margin + caption_h))

    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=36)
    draw.text((margin, margin), "1", fill="black", font=font)
    draw.text((margin + left.width + gap, margin), "2", fill="black", font=font)
    uc = _update_count(qr1)
    if uc is not None:
        draw.text(
            (width - margin - 200, margin),
            f"update {uc}",
            fill="#888888",
            font=ImageFont.load_default(size=24),
        )
    return canvas


def _save_atomic(image: Image.Image) -> None:
    """Write via a temp file + rename, so a browser fetching the image mid-poll
    never reads a half-written PNG."""
    tmp = _QR_PATH.with_suffix(".png.tmp")
    image.save(tmp, format="PNG")
    os.replace(tmp, _QR_PATH)


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

    _save_atomic(_compose(qr1, qr2))

    # The page refreshes itself; only announce it once, so the log stays
    # readable while the codes rotate once a second.
    if _call_count == 1:
        print("=" * 60)
        print("SCAN BOTH QR CODES WITH YOUR MITID APP")
        print("Open this on a computer/browser (not the phone doing the scanning):")
        if _QR_DIR == _HA_WWW:
            print(f"  {_PAGE_URL}")
        else:
            print(f"  {_PAGE_PATH.as_uri()}")
            print(f"  (or preview the image directly: {_QR_PATH})")
        print("Both codes are on that one page and refresh themselves -- keep it")
        print("open and scan both without reloading.")
        print("=" * 60)
    elif _call_count % 10 == 0:
        print(f"[diag] QR refresh #{_call_count}, t+{elapsed:.1f}s, payload changed: {changed}")


# Workaround for an upstream gap: MitID's poll endpoint can return
# {"status": "OK", "confirmation": false/absent} as a transient in-between
# state (observed right after the user approves in the app) that the
# library's state machine doesn't recognize -- it only checks for
# status == "OK" AND confirmation is True, and treats anything else
# matching "OK" as a fatal "Unexpected poll status".
#
# The first version of this patch only intercepted the *first* poll call and
# delegated every subsequent one to the original method once it saw any
# other (normal, in-progress) status. That's broken: the original method has
# its own internal polling loop with no knowledge of our workaround, so once
# delegated to, it hits the exact same "OK without confirmation" gap on a
# later poll. Fix: never delegate: reimplement the full state machine here
# (mirroring aula.auth.browser_client.BrowserClient._poll_for_app_confirmation)
# so our tolerance applies to every poll, not just the first.
#
# Note this must mirror upstream *exactly* apart from the tolerance itself --
# an earlier version of this file called a self._end_qr_phase() that does not
# exist on BrowserClient in aula 1.7.0, which turned every successful scan
# into an AttributeError at the moment MitID reported channel_verified.
_POLL_SECONDS = 0.5
_QR_POLL_SECONDS = 1.0
# How long to tolerate status=OK-without-confirmation before giving up. This is
# the "waiting for you to tap approve" window, so it has to be measured in
# minutes, not in poll attempts -- a tight retry count fails a slow tap.
_OK_GRACE_SECONDS = 180.0


async def _poll_for_app_confirmation_patched(self, poll_url: str, ticket: str):
    ok_since: float | None = None
    ok_last_logged = 0.0
    while True:
        r = await self._client.post(poll_url, json={"ticket": ticket})
        data = r.json()

        if not r.is_success:
            raise MitIDError("Login request was not accepted")

        status = data["status"]

        if status != "OK":
            ok_since = None

        if status == "OK" and data.get("confirmation") is True:
            return data["payload"]["response"], data["payload"]["responseSignature"]

        if status == "OK":
            now = time.monotonic()
            if ok_since is None:
                ok_since = now
                ok_last_logged = 0.0
            waited = now - ok_since
            if waited - ok_last_logged >= 5.0:
                ok_last_logged = waited
                print(
                    f"[diag] status=OK without confirmation for {waited:.0f}s -- "
                    "approve the login in the MitID app if you haven't yet."
                )
            if waited >= _OK_GRACE_SECONDS:
                raise MitIDError(
                    f"No confirmation from the MitID app after {_OK_GRACE_SECONDS:.0f}s"
                )
            await asyncio.sleep(_POLL_SECONDS)
            continue

        if status == "timeout":
            await asyncio.sleep(_POLL_SECONDS)
            continue

        if status == "channel_validation_otp":
            otp_code = data["channelBindingValue"]
            self.status_message = f"Please use the following OTP code in the app: {otp_code}"
            if otp_code != self.otp_code:
                self.otp_code = otp_code
                if self._on_otp_code:
                    self._on_otp_code(otp_code)
            await asyncio.sleep(_POLL_SECONDS)
            continue

        if status == "channel_validation_tqr":
            self._handle_qr_code_poll(data)
            await asyncio.sleep(_QR_POLL_SECONDS)
            continue

        if status == "channel_verified":
            self.status_message = (
                "The OTP/QR code has been verified, now waiting user to approve login"
            )
            print("[diag] QR codes verified -- now approve the login in the MitID app.")
            await asyncio.sleep(_POLL_SECONDS)
            continue

        raise MitIDError(f"Unexpected poll status: {status}")


BrowserClient._poll_for_app_confirmation = _poll_for_app_confirmation_patched

aula_cli._print_qr_codes_in_terminal = _print_qr_codes_image

if __name__ == "__main__":
    # Earlier versions wrote one PNG per code. Remove them: their URLs are
    # likely still in browser history (and cached by HA's static handler for
    # weeks), and a stale QR that scans but no longer matches the session is a
    # worse failure than a 404.
    for stale in ("instant_aula_mitid_qr_1.png", "instant_aula_mitid_qr_2.png"):
        (_QR_DIR / stale).unlink(missing_ok=True)

    # Written up front so the page can be opened before the codes appear --
    # it shows a "waiting" placeholder until the first poll renders them.
    _PAGE_PATH.write_text(_PAGE_HTML, encoding="utf-8")
    print(f"[diag] QR page: {_PAGE_URL if _QR_DIR == _HA_WWW else _PAGE_PATH}")
    sys.exit(aula_cli.cli())
