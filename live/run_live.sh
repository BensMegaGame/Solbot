#!/bin/bash
# Startet Bot E LIVE einmal. Wird vom Cronjob alle 2 Minuten aufgerufen (Eintrag macht einrichten.py).
# KEIN git pull hier: der Code dieser Kopie aendert sich nur, wenn du selbst "git pull" eingibst.
LIVE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$(dirname "$LIVE")")"
exec 8>/tmp/solbot_live.lock
flock -n 8 || exit 0
cd "$LIVE"
source "$ROOT/venv/bin/activate"
if [ -f "$ROOT/.env" ]; then set -a; source "$ROOT/.env"; set +a; fi
echo "--- $(date -u +%FT%TZ) ---" >> "$ROOT/live.log"
timeout 115 python bot_e_live.py >> "$ROOT/live.log" 2>&1
