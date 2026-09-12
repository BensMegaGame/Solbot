#!/bin/bash
# Holt den neuesten Code von GitHub, fuehrt ALLE vorhandenen bot_*.py aus und pusht die Ergebnisse.
cd "$(dirname "$0")/solbot"
git pull --quiet >> ../paper.log 2>&1
source ../venv/bin/activate
# Optionale Schluessel (z.B. HELIUS_KEY, COINGECKO_KEY) aus ~/.env laden – Datei bleibt nur auf dem Server
if [ -f ../.env ]; then set -a; source ../.env; set +a; fi
for f in bot_*.py; do
  echo "--- $f ($(date -u +%FT%TZ)) ---" >> ../paper.log
  python "$f" >> ../paper.log 2>&1
done
git add data
if ! git diff --cached --quiet; then
  git commit -m "paper run $(date -u +%FT%TZ)" >> ../paper.log 2>&1
  git push >> ../paper.log 2>&1
fi
