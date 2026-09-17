"""Diagnose der Codex-Abfrage von Bot C. Aufruf im Repo-Ordner:  python3 checkc.py

Testet die Abfrage stufenweise und zeigt, welcher Filter die Antwort leer macht."""
import json, time, requests
import bot_c

now = int(time.time())
HEAD = {"Authorization": bot_c.CODEX_KEY, "Content-Type": "application/json"}

TESTS = [
    ("1) wie im Bot (Pump.fun + migriert + createdAt)",
     '{ network: $net, launchpadName: ["Pump.fun"], launchpadMigrated: true, createdAt: { gte: %d } }' % (now - 3 * 86400)),
    ("2) ohne createdAt",
     '{ network: $net, launchpadName: ["Pump.fun"], launchpadMigrated: true }'),
    ("3) ohne launchpadName",
     '{ network: $net, launchpadMigrated: true }'),
    ("4) nur Netzwerk + Liquiditaet",
     '{ network: $net, liquidity: { gte: 20000 } }'),
]

Q = """
query($net: [Int!]) {
  filterTokens(filters: %s, rankings: [{ attribute: volume1, direction: DESC }], limit: 5) {
    results { liquidity token { address symbol launchpad { migratedAt completedAt } } }
  }
}"""

if not bot_c.CODEX_KEY:
    raise SystemExit("CODEX_KEY ist in dieser Shell nicht gesetzt - Test aussagelos.")

for name, filt in TESTS:
    print("\n" + "=" * 60)
    print(name)
    try:
        r = requests.post(bot_c.CODEX_URL, json={"query": Q % filt, "variables": {"net": [bot_c.SOLANA]}},
                          headers=HEAD, timeout=30)
        print("HTTP", r.status_code)
        d = r.json()
        if d.get("errors"):
            print("FEHLER:", json.dumps(d["errors"])[:400])
            continue
        res = ((d.get("data") or {}).get("filterTokens") or {}).get("results") or []
        print("Treffer:", len(res))
        for x in res[:3]:
            t = x["token"]; lp = t.get("launchpad") or {}
            mig = lp.get("migratedAt") or lp.get("completedAt")
            alter = round((time.time() - float(mig)) / 3600, 1) if mig else None
            print(f"   {t.get('symbol'):<12} liq {x.get('liquidity')} | migriert vor {alter} h")
    except Exception as e:
        print("AUSNAHME:", e)
