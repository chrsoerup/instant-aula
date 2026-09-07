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

# Only attempt the interactive login when explicitly requested via the
# run_mitid_login option, not on every container start -- MitID rate-limits
# repeated login attempts for the same user ID, and an unattended restart
# (crash, HA update, Supervisor watchdog) silently retrying this with nobody
# there to scan the QR is exactly how that limit gets hit for no benefit.
if [ ! -f "$HOME/.config/aula/tokens.json" ]; then
  if [ "$RUN_MITID_LOGIN" = "true" ]; then
    echo "run_mitid_login is enabled -- running one-time interactive login."
    echo "This will print two QR image paths/URLs below once ready; scan them"
    echo "with the MitID app on your phone (view them from a different device,"
    echo "e.g. a PC browser, since you can't scan a QR on the same phone)."
    cd /app && uv run python scripts/mitid_login.py --output text -v login \
      || echo "MitID login did not complete (see above). Turn run_mitid_login off then back on, or just restart, to retry -- but see Known limitations in README before retrying repeatedly, MitID rate-limits this."
  else
    echo "No cached MitID token found, and run_mitid_login is off -- skipping login."
    echo "Set run_mitid_login to true in this app's Configuration, save, and restart when you're ready to scan the QR codes."
  fi
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
SUPERVISOR_TOKEN=$SUPERVISOR_TOKEN

0 8 * * 6 root cd /app && uv run python -m instant_aula.weekly_digest >> /proc/1/fd/1 2>> /proc/1/fd/2
0 */2 * * * root cd /app && uv run python -m instant_aula.urgent_check >> /proc/1/fd/1 2>> /proc/1/fd/2
EOF
chmod 0644 /etc/cron.d/instant-aula

echo "instant-aula: cron schedule installed, starting."
exec cron -f
