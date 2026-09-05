#!/bin/bash
set -euo pipefail

mkdir -p /data/home /data/state
export HOME=/data/home

OPTS=/data/options.json
AULA_MITID_USERNAME=$(jq -r '.aula_mitid_username' "$OPTS")
AULA_AUTH_METHOD=$(jq -r '.aula_auth_method' "$OPTS")
AULA_MITID_PASSWORD=$(jq -r '.aula_mitid_password // empty' "$OPTS")
HA_NOTIFY_SERVICE=$(jq -r '.ha_notify_service' "$OPTS")

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

echo "instant-aula add-on started."
echo "One-time step if not done yet: docker exec into this container and run"
echo "  cd /app && uv run python scripts/mitid_login.py --output text -v login"
echo "to complete the interactive MitID login (tokens then persist in /data)."

exec cron -f
