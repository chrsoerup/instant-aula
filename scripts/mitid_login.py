"""One-off interactive login helper.

The `aula` CLI renders its MitID QR codes as plain ASCII text art, which
non-square terminal fonts stretch/distort and phone cameras often can't
read. This monkeypatches its QR renderer to use qrcode's block-character
`print_tty()` output instead, which is far more reliably scannable.

Run this once to complete the interactive MitID login. Tokens are then
cached at ~/.config/aula/tokens.json and every other `aula`/instant_aula
command works headlessly from there -- this script is not needed again
unless the cached tokens are cleared.

Usage: uv run python scripts/mitid_login.py --output text -v login
"""

import sys

import aula.cli as aula_cli


def _print_qr_codes_tty(qr1, qr2) -> None:
    print("=" * 60)
    print("SCAN THESE QR CODES WITH YOUR MITID APP")
    print("=" * 60)
    print("QR CODE 1 (scan this first):")
    qr1.print_tty()
    print("QR CODE 2 (scan this second):")
    qr2.print_tty()
    print("=" * 60)
    print("Waiting for you to scan the QR codes...")
    print("=" * 60)


aula_cli._print_qr_codes_in_terminal = _print_qr_codes_tty

if __name__ == "__main__":
    sys.exit(aula_cli.cli())
