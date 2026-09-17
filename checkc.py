"""Diagnose 2: Welches Ausgabefeld bricht die echte Bot-C-Abfrage? Aufruf: python3 checkc.py"""
import json, time, requests
import bot_c

if not bot_c.CODEX_KEY: raise SystemExit("CODEX_KEY fehlt in dieser Shell.")
HEAD = {"Authorization": bot_c.CODEX_KEY, "Content-Type": "application/json"}
VARS = {"net": [bot_c.SOLANA], "after": int(time.time() - 3 * 86400)}

def run(label, query, variables):
    try:
        r = requests.post(bot_c.CODEX_URL, json={"query": query, "variables": variables}, headers=HEAD, timeout=30)
        d = r.json()
        if d.get("errors"):
            print(f"  {label:<26} FEHLER: {json.dumps(d['errors'])[:160]}")
            return False
        n = len(((d.get("data") or {}).get("filterTokens") or {}).get("results") or [])
        print(f"  {label:<26} OK, {n} Treffer")
        return True
    except Exception as e:
        print(f"  {label:<26} AUSNAHME: {e}")
        return False

print("A) Die echte Abfrage aus bot_c.py:")
run("Q_MIGRATED", bot_c.Q_MIGRATED, VARS)

print("\nB) Felder einzeln getestet:")
FIELDS = ["liquidity", "volume1", "buyCount1", "sellCount1", "uniqueBuys1", "holders",
          "top10HoldersPercent", "bundlerHeldPercentage", "sniperHeldPercentage", "insiderHeldPercentage"]
TPL = """
query($net: [Int!]) {
  filterTokens(filters: { network: $net, launchpadName: ["Pump.fun"], launchpadMigrated: true },
               rankings: [{ attribute: volume1, direction: DESC }], limit: 3) {
    results { %s token { address symbol } }
  }
}"""
gut = []
for f in FIELDS:
    if run(f, TPL % f, {"net": [bot_c.SOLANA]}): gut.append(f)

print("\nC) Alle funktionierenden Felder zusammen:")
run("kombiniert", TPL % " ".join(gut), {"net": [bot_c.SOLANA]})
print("\nBrauchbare Felder:", " ".join(gut))
