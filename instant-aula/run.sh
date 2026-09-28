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

# Exported rather than plain shell variables: both the run_now block and the
# scheduler loop below start these modules as child processes of this script,
# so the environment they see is this one. STATE_DIR especially -- without it
# the state file defaults to /app/state/, so a run would baseline itself
# somewhere the next run never looks, and that next run would re-alert the
# whole backlog.
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
# waiting up to two hours for the next scheduled tick.
#
# Each run is wrapped so a failure only logs -- set -euo pipefail would
# otherwise take the whole container down over one bad run and leave nothing
# scheduled. Subshells keep the cd out of the rest of this script. The $1 label
# distinguishes manual runs from scheduled ones in the log.
_run_job() {
  local label=$1 module=$2
  echo "$label: starting $module"
  ( cd /app && uv run python -m "instant_aula.$module" ) \
    && echo "$label: $module finished" \
    || echo "$label: $module FAILED (traceback above)"
}

case "$RUN_NOW" in
  none) ;;
  urgent) _run_job run_now urgent_check ;;
  digest) _run_job run_now weekly_digest ;;
  both)   _run_job run_now urgent_check; _run_job run_now weekly_digest ;;
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

# Scheduling, in this script rather than via cron.
#
# cron was used up to and including 1.0.12 and never fired a single job. It
# accepted /etc/cron.d/instant-aula without complaint and logged nothing, but
# across two weeks of confirmed container uptime the app log contained no
# output at all from urgent_check -- which prints "No new must-read items." on
# every run, unconditionally, every two hours. Every digest that ever arrived
# came from the run_now path above, never from the schedule.
#
# Rather than keep guessing which of cron-in-a-slim-container's quiet failure
# modes it was, the schedule lives here now. Two fixed jobs don't need a
# daemon, and this drops every moving part that made the cron version both
# fragile and un-debuggable: no /etc/cron.d parsing, no PAM, no second copy of
# the environment to keep in sync (jobs inherit this script's exports, and
# hand-copying HA_NOTIFY_SERVICE and STATE_DIR into the cron block is where
# earlier bugs came from), and no >> /proc/1/fd/1 redirect, because this loop
# *is* the container's foreground process so job output reaches the log
# directly.
DIGEST_MARKER=/data/state/last_digest_week
URGENT_MARKER=/data/state/last_urgent_run
URGENT_INTERVAL=7200   # seconds -- every 2 hours, matching the old cron entry
DIGEST_HOUR=6          # Monday 06:00 local, before school starts

# Due when the current ISO week hasn't had a digest yet and its Monday 06:00
# slot has passed. Deliberately "this week's digest is still owed" rather than
# "it is exactly 06:00 on Monday": if the app happens to be down at that one
# moment -- which is exactly what cost the 2026-09-21 digest -- a later start
# still delivers the current week's plan instead of silently skipping to next
# week. The week marker keeps it to once per week however often this loops.
_digest_due() {
  local this_week last_week hour
  this_week=$(date +%G-W%V)
  last_week=$(cat "$DIGEST_MARKER" 2>/dev/null || true)
  [ "$this_week" = "$last_week" ] && return 1
  hour=$((10#$(date +%H)))   # 10# so an hour like "06" isn't parsed as octal
  [ "$(date +%u)" -eq 1 ] && [ "$hour" -lt "$DIGEST_HOUR" ] && return 1
  return 0
}

_urgent_due() {
  local last
  last=$(cat "$URGENT_MARKER" 2>/dev/null || true)
  [[ "$last" =~ ^[0-9]+$ ]] || last=0   # missing or corrupt marker: run now
  [ $(( $(date +%s) - last )) -ge "$URGENT_INTERVAL" ]
}

echo "instant-aula: scheduler starting -- digest Mondays 0${DIGEST_HOUR}:00, urgent check every $((URGENT_INTERVAL / 3600))h, TZ=$TZ, now $(date '+%F %T %Z')."

while true; do
  # Markers are written after each attempt whether it succeeded or not. A
  # failing job must not be retried every 60 seconds: cron wouldn't have, and
  # notify_failure already pushes the traceback. The cost is that a failed
  # digest waits for next Monday, which is the better trade against turning one
  # broken run into a notification flood.
  if _digest_due; then
    _run_job scheduler weekly_digest
    date +%G-W%V > "$DIGEST_MARKER"
  fi
  if _urgent_due; then
    _run_job scheduler urgent_check
    date +%s > "$URGENT_MARKER"
  fi
  sleep 60
done
