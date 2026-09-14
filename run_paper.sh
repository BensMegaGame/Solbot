#!/bin/bash
# Holt den neuesten Code von GitHub, fuehrt ALLE vorhandenen bot_*.py aus und pusht die Ergebnisse.
# flock: laeuft ein vorheriger Durchlauf noch, wird dieser sofort beendet (keine parallelen Schreibzugriffe auf data/).
exec 9>/tmp/solbot.lock
if ! flock -n 9; then echo "$(date -u +%FT%TZ) skip: previous run still active" >> "$(dirname "$0")/paper.log"; exit 0; fi
cd "$(dirname "$0")/solbot"
git pull --quiet >> ../paper.log 2>&1
source ../venv/bin/activate
# Optionale Schluessel (z.B. HELIUS_KEY, COINGECKO_KEY, CODEX_KEY) aus ~/.env laden - Datei bleibt nur auf dem Server
if [ -f ../.env ]; then set -a; source ../.env; set +a; fi
for f in bot_*.py; do
  echo "--- $f ($(date -u +%FT%TZ)) ---" >> ../paper.log
  timeout 240 python "$f" >> ../paper.log 2>&1 || echo "!! $f exit $? (timeout=124)" >> ../paper.log
done
git add data
if ! git diff --cached --quiet; then
  git commit -m "paper run $(date -u +%FT%TZ)" >> ../paper.log 2>&1
  git push >> ../paper.log 2>&1
fi
