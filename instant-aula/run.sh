#!/bin/bash
set -euo pipefail

mkdir -p /data/home /data/state
export HOME=/data/home
export TZ=Europe/Copenhagen

OPTS=/data/options.json
export AULA_MITID_USERNAME=$(jq -r '.aula_mitid_username' "$OPTS")
export AULA_AUTH_METHOD=$(jq -r '.aula_auth_method' "$OPTS")
export AULA_MITID_PASSWORD=$(jq -r '.aula_mitid_password // empty' "$OPTS")
HA_NOTIFY_SERVICE=$(jq -r '.ha_notify_service' "$OPTS")
RUN_MITID_LOGIN=$(jq -r '.run_mitid_login' "$OPTS")
RUN_NOW=$(jq -r '.run_now // "none"' "$OPTS")

# Exported, not just written into the cron file below: the run_now block runs
# these same modules directly from this script, and they need the identical
# environment the cron jobs get. STATE_DIR especially -- without it the state
# file defaults to /app/state/, so a run here would baseline itself somewhere
# the scheduled runs never look, and the next cron run would re-alert the whole
# backlog.
export HA_NOTIFY_SERVICE
export STATE_DIR=/data/state

# Adopt a token file staged in Home Assistant's own config directory, if one
# is there. This is the no-MitID re-auth path: log in on a PC (where the QR is
# easy to scan), drop the resulting ~/.config/aula/tokens.json into HA's config
# folder as instant_aula_tokens.json, restart this app. It exists because the
# obvious alternative -- docker exec into this container -- needs Protection
# mode off on the Terminal & SSH app, which recent Home Assistant no longer
# exposes a way to toggle.
#
# The staged file is deleted after import: it's a live Aula session credential,
# and HA's config directory is backed up and readable by every other app.
# Deleting it also makes the import once-only, so a later restart can't
# resurrect a stale token over a newer one.
SEEDED_TOKENS=/homeassistant/instant_aula_tokens.json
if [ -f "$SEEDED_TOKENS" ]; then
  install -D -m 600 "$SEEDED_TOKENS" "$HOME/.config/aula/tokens.json"
  rm -f "$SEEDED_TOKENS"
  echo "Imported MitID tokens from $SEEDED_TOKENS (staged copy removed)."
fi

# Only attempt the interactive login when explicitly requested via the
# run_mitid_login option, not on every container start -- MitID rate-limits
# repeated login attempts for the same user ID, and an unattended restart
# (crash, HA update, Supervisor watchdog) silently retrying this with nobody
# there to scan the QR is exactly how that limit gets hit for no benefit.
if [ ! -f "$HOME/.config/aula/tokens.json" ]; then
  if [ "$RUN_MITID_LOGIN" = "true" ]; then
    echo "run_mitid_login is enabled -- running one-time interactive login."
    echo "This prints a QR page URL below once ready; open it in a browser on a"
    echo "different device (e.g. a PC -- you can't scan a QR on the same phone"
    echo "that's displaying it) and scan both codes on that page with the MitID"
    echo "app. The page refreshes itself, so don't reload it."
    cd /app && uv run python scripts/mitid_login.py --output text -v login \
      || echo "MitID login did not complete (see above). Turn run_mitid_login off then back on, or just restart, to retry -- but see Known limitations in README before retrying repeatedly, MitID rate-limits this."
  else
    echo "No cached MitID token found, and run_mitid_login is off -- skipping login."
    echo "Set run_mitid_login to true in this app's Configuration, save, and restart when you're ready to scan the QR codes."
  fi
fi

# One-shot manual trigger. Without a shell in this container (getting one needs
# Protection mode off on the Terminal & SSH app, which Home Assistant no longer
# exposes a toggle for), this is the only way to run a job on demand rather than
# waiting up to two hours for the next cron tick.
#
# Each run is wrapped so a failure only logs -- set -euo pipefail would
# otherwise take the whole container down over one bad run and leave nothing
# scheduled. Subshells keep the cd out of the rest of this script.
_run_now() {
  echo "run_now: starting $1"
  ( cd /app && uv run python -m "instant_aula.$1" ) \
    && echo "run_now: $1 finished" \
    || echo "run_now: $1 FAILED (traceback above)"
}

case "$RUN_NOW" in
  none) ;;
  urgent) _run_now urgent_check ;;
  digest) _run_now weekly_digest ;;
  both)   _run_now urgent_check; _run_now weekly_digest ;;
  *)      echo "run_now: unrecognised value '$RUN_NOW' -- skipping." ;;
esac

if [ "$RUN_NOW" != "none" ]; then
  echo "Set run_now back to 'none' in this app's Configuration, or it runs again on every restart."
fi

# Normally auto-injected by Supervisor (homeassistant_api: true). Guarded so a
# missing token degrades to "jobs run, notifications fail, log says why" rather
# than set -u killing this script before anything gets scheduled at all.
if [ -z "${SUPERVISOR_TOKEN:-}" ]; then
  echo "WARNING: SUPERVISOR_TOKEN is not set -- scheduled runs will fail to send notifications."
fi

cat > /etc/cron.d/instant-aula <<EOF
SHELL=/bin/bash
PATH=/root/.local/bin:/usr/local/bin:/usr/bin:/bin
TZ=Europe/Copenhagen
HOME=/data/home
STATE_DIR=/data/state
AULA_MITID_USERNAME=$AULA_MITID_USERNAME
AULA_AUTH_METHOD=$AULA_AUTH_METHOD
AULA_MITID_PASSWORD=$AULA_MITID_PASSWORD
HA_NOTIFY_SERVICE=$HA_NOTIFY_SERVICE
SUPERVISOR_TOKEN=${SUPERVISOR_TOKEN:-}

0 6 * * 1 root cd /app && uv run python -m instant_aula.weekly_digest >> /proc/1/fd/1 2>> /proc/1/fd/2
0 */2 * * * root cd /app && uv run python -m instant_aula.urgent_check >> /proc/1/fd/1 2>> /proc/1/fd/2
EOF
chmod 0644 /etc/cron.d/instant-aula

echo "instant-aula: cron schedule installed, starting."
exec cron -f
