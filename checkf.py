"""Diagnose der Codex-Abfrage von Bot D und Bot F. Aufruf im Repo-Ordner:  python3 checkf.py

Testet die Abfrage schrittweise und zeigt, woran sie scheitert: am createdAt-Filter, an einem
Ausgabefeld, oder ob die Filterkombination schlicht keine Tokens mehr trifft.
"""
import json, time, requests
import bot_f

if not bot_f.CODEX_KEY:
    raise SystemExit("CODEX_KEY ist in dieser Shell nicht gesetzt.\n"
                     "  set -a; . ~/.env; set +a    dann erneut starten.")

HEAD = {"Authorization": bot_f.CODEX_KEY, "Content-Type": "application/json"}
now = time.time()
AFTER, BEFORE = float(now - bot_f.MAX_AGE_H * 3600), float(now - bot_f.MIN_AGE_H * 3600)


def run(label, query, variables):
    try:
        r = requests.post(bot_f.CODEX_URL, json={"query": query, "variables": variables},
                          headers=HEAD, timeout=30)
        d = r.json()
        if d.get("errors"):
            print(f"  {label:<44} FEHLER: {json.dumps(d['errors'])[:230]}")
            return None
        res = ((d.get("data") or {}).get("filterTokens") or {}).get("results") or []
        print(f"  {label:<44} OK, {len(res)} Treffer")
        return res
    except Exception as e:
        print(f"  {label:<44} AUSNAHME: {e}")
        return None


print("A) Die echte Abfrage aus bot_f.py (identisch zu bot_d.py):")
run("Q_F wie im Bot", bot_f.Q_F, {"net": [bot_f.SOLANA], "after": AFTER, "before": BEFORE})

# Bausteine einzeln. FELDER = die Ausgabefelder, FILT = der Filterblock.
FELDER = "liquidity marketCap volume24 buyCount24 sellCount24 holders top10HoldersPercent " \
         "bundlerHeldPercentage sniperHeldPercentage insiderHeldPercentage"
VOL, MCL, MCH = bot_f.MIN_VOL24, bot_f.MIN_MCAP, bot_f.MAX_MCAP
LQL, LQH = bot_f.MIN_LIQ, bot_f.MAX_LIQ


def q(filt, felder=FELDER, varteil="$net: [Int!]"):
    return ("query(%s) {\n  filterTokens(filters: %s, rankings: [{attribute: volume24, direction: DESC}], "
            "limit: 20) {\n    results { %s token { address symbol } }\n  }\n}" % (varteil, filt, felder))


print("\nB) createdAt schrittweise weglassen (Filter sonst unveraendert):")
basis = "network: $net, volume24: {gte: %d}, marketCap: {gte: %d, lte: %d}, liquidity: {gte: %d, lte: %d}" % (VOL, MCL, MCH, LQL, LQH)
run("mit gte UND lte (wie im Bot)",
    q("{%s, createdAt: {gte: $after, lte: $before}}" % basis, varteil="$net: [Int!], $after: Float!, $before: Float!"),
    {"net": [bot_f.SOLANA], "after": AFTER, "before": BEFORE})
run("nur gte",
    q("{%s, createdAt: {gte: $after}}" % basis, varteil="$net: [Int!], $after: Float!"),
    {"net": [bot_f.SOLANA], "after": AFTER})
run("createdAt als feste Zahl statt Variable",
    q("{%s, createdAt: {gte: %d, lte: %d}}" % (basis, int(AFTER), int(BEFORE))),
    {"net": [bot_f.SOLANA]})
ohne_alter = run("GANZ OHNE createdAt", q("{%s}" % basis), {"net": [bot_f.SOLANA]})

print("\nC) Filter schrittweise weiten (ohne createdAt), um zu sehen, ob die Spanne zu eng ist:")
run("ohne Liquiditaets-Obergrenze",
    q("{network: $net, volume24: {gte: %d}, marketCap: {gte: %d, lte: %d}, liquidity: {gte: %d}}" % (VOL, MCL, MCH, LQL)),
    {"net": [bot_f.SOLANA]})
run("ohne MCap-Obergrenze",
    q("{network: $net, volume24: {gte: %d}, marketCap: {gte: %d}, liquidity: {gte: %d, lte: %d}}" % (VOL, MCL, LQL, LQH)),
    {"net": [bot_f.SOLANA]})
run("nur Netzwerk + Volumen",
    q("{network: $net, volume24: {gte: %d}}" % VOL), {"net": [bot_f.SOLANA]})

print("\nD) Ausgabefelder einzeln (nur falls oben etwas mit FEHLER endete):")
for feld in FELDER.split():
    run(feld, q("{network: $net, volume24: {gte: %d}}" % VOL, felder=feld), {"net": [bot_f.SOLANA]})

if ohne_alter:
    print("\nE) Wie alt sind die Treffer OHNE Altersfilter? (zeigt, ob 12h-7d ueberhaupt getroffen wird)")
    print("   -> Das Alter steht nicht in der Antwort; bitte die Symbole mit dem Dashboard abgleichen.")
    for x in ohne_alter[:8]:
        t = x.get("token") or {}
        print(f"   {t.get('symbol'):<12} mcap {float(x.get('marketCap') or 0):>10,.0f}  liq {float(x.get('liquidity') or 0):>9,.0f}")
