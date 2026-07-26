#!/bin/bash
# Portal-triggered update: git pull, refresh deps, then restart flightscnr.service.
# User presets live outside the repo (/var/lib/flightscnr, /etc/flightscnr.env).
#
# Restart is deferred (systemd-run / sleep fallback) so this script can write
# update-status.json + drop the lock BEFORE systemctl restart. Restarting the
# service from inside its own cgroup with KillMode=mixed would SIGKILL this
# script and leave the portal stuck on "Update in progress…".
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DATA_DIR="/var/lib/flightscnr"
STATUS_FILE="$DATA_DIR/update-status.json"
LOCK_FILE="$DATA_DIR/update.lock"
LOG_FILE="$DATA_DIR/update.log"
RESTART_DELAY_S="${FLIGHTSCNR_UPDATE_RESTART_DELAY_S:-2}"

# Detach from the web portal process (new session / nohup). Still stays in the
# flightscnr.service cgroup — deferred restart below is what avoids self-kill.
if [ -z "${FLIGHTSCNR_PORTAL_UPDATE:-}" ]; then
    export FLIGHTSCNR_PORTAL_UPDATE=1
    mkdir -p "$DATA_DIR"
    nohup "$0" >>"$LOG_FILE" 2>&1 </dev/null &
    exit 0
fi

write_status() {
    local state="$1"
    local message="${2:-}"
    mkdir -p "$DATA_DIR"
    python3 - "$STATUS_FILE" "$state" "$message" <<'PY'
import json, sys
from datetime import datetime, timezone

path, state, message = sys.argv[1:4]
payload = {
    "state": state,
    "message": message,
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)
PY
}

release_lock() {
    exec 9>&- || true
    rm -f "$LOCK_FILE"
}

schedule_service_restart() {
    # Prefer a transient systemd timer so restart runs outside this unit's cgroup.
    local unit="flightscnr-portal-restart-$$"
    if command -v systemd-run >/dev/null 2>&1; then
        if systemd-run \
            --quiet \
            --collect \
            --unit="$unit" \
            --on-active="${RESTART_DELAY_S}s" \
            /bin/systemctl restart flightscnr.service
        then
            echo "Scheduled flightscnr restart in ${RESTART_DELAY_S}s ($unit)" | tee -a "$LOG_FILE"
            return 0
        fi
        echo "systemd-run scheduling failed — falling back to background sleep" | tee -a "$LOG_FILE"
    fi
    # Fallback still works for portal status (already written) even if this
    # sleeper is later swept by KillMode=mixed during the restart.
    nohup bash -c "sleep ${RESTART_DELAY_S}; systemctl restart flightscnr.service" \
        >>"$LOG_FILE" 2>&1 </dev/null &
    echo "Scheduled flightscnr restart in ${RESTART_DELAY_S}s (sleep fallback, pid $!)" | tee -a "$LOG_FILE"
}

fail_cleanup() {
    local code=$?
    trap - EXIT
    write_status "failed" "Update failed (exit $code). See $LOG_FILE"
    release_lock
    exit "$code"
}

mkdir -p "$DATA_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "Update already running" >&2
    exit 1
fi

echo $$ >"$LOCK_FILE"
trap fail_cleanup EXIT

{
    echo ""
    echo "==> Portal update $(date -Iseconds)"
    echo "    Repo: $REPO_ROOT"
} | tee -a "$LOG_FILE"

write_status "running" "Pulling latest changes…"

if [ ! -x "$REPO_ROOT/install-pi.sh" ]; then
    echo "install-pi.sh not found" | tee -a "$LOG_FILE"
    exit 1
fi

# Sync code/deps/unit without restarting from inside this cgroup.
# FLIGHTSCNR_SKIP_RESTART is a belt-and-suspenders guard for start_service().
export FLIGHTSCNR_SKIP_RESTART=1
bash "$REPO_ROOT/install-pi.sh" update --no-start 2>&1 | tee -a "$LOG_FILE"

# Status + lock must be cleared before restart can kill this cgroup member.
trap - EXIT
write_status "success" "Update finished successfully. Restarting display…"
release_lock
schedule_service_restart
exit 0
