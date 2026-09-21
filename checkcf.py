"""Warum finden Bot C und Bot F kein Universum? Aufruf im Repo-Ordner:  python3 checkcf.py

Testet die Codex-Abfrage mit verschiedenen Limits und Filtern und zeigt, wo sie kippt.
"""
import json, requests
import bot_c

KEY = bot_c.CODEX_KEY
if not KEY:
    raise SystemExit("CODEX_KEY ist in dieser Shell nicht gesetzt:\n  set -a; . ~/.env; set +a")
HEAD = {"Authorization": KEY, "Content-Type": "application/json"}


def q(limit, liq, vol, felder="marketCap token { address symbol }"):
    return """
query($net:[Int!]) {
  filterTokens(filters:{ network:$net, liquidity:{gte:%d}, volume24:{gte:%d} },
               rankings:[{attribute:marketCap, direction:DESC}], limit:%d)
  { results { %s } }
}""" % (liq, vol, limit, felder)


def run(label, query):
    try:
        r = requests.post(bot_c.CODEX_URL, json={"query": query, "variables": {"net": [bot_c.SOLANA]}},
                          headers=HEAD, timeout=40)
        d = r.json()
        if d.get("errors"):
            print(f"  {label:<38} FEHLER: {json.dumps(d['errors'])[:200]}")
            return None
        res = ((d.get("data") or {}).get("filterTokens") or {}).get("results") or []
        print(f"  {label:<38} OK, {len(res)} Treffer")
        return res
    except Exception as e:
        print(f"  {label:<38} AUSNAHME: {e}")
        return None


print("A) Limit schrittweise erhoehen (Filter wie im Bot: liq>=100k, vol>=50k):")
letzte = None
for limit in (50, 100, 200, 250, 300, 420):
    res = run(f"limit {limit}", q(limit, bot_c.U_MIN_LIQ, bot_c.U_MIN_VOL24))
    if res: letzte = res

print("\nB) Filter lockern (limit 200):")
run("ohne Liquiditaetsfilter", q(200, 0, bot_c.U_MIN_VOL24))
run("ohne Volumenfilter", q(200, bot_c.U_MIN_LIQ, 0))
run("nur Netzwerk", q(200, 0, 0))

print("\nC) Ausgabefelder einzeln (limit 20):")
for feld in ("marketCap", "liquidity", "volume24", "token { address symbol }"):
    run(feld, q(20, 0, 0, felder=feld))

if letzte:
    print(f"\nD) So sieht die Rangliste aus ({len(letzte)} Tokens, Rang 1, 30, 150, 200, letzter):")
    for i in (1, 30, 150, 200, len(letzte)):
        if i <= len(letzte):
            x = letzte[i - 1]; t = x.get("token") or {}
            mc = float(x.get("marketCap") or 0)
            print(f"   Rang {i:>3}: {t.get('symbol'):<12} MCap {mc/1e6:>8.1f} Mio $")
    print(f"\n   Bot C braucht Rang {bot_c.RANG_VON}-{bot_c.RANG_BIS} -> "
          f"{'reicht' if len(letzte) >= bot_c.RANG_VON else 'ZU WENIG TREFFER'}")

print("\nE) Aktueller Stand der Bots:")
for b in ("c", "f"):
    try:
        st = json.load(open(f"data/bot_{b}_state.json"))
        me = json.load(open(f"data/bot_{b}_meta.json"))
        print(f"   Bot {b.upper()}: equity {st['equity'][-1]['v']:.2f} | cash {st['cash']:.2f} | "
              f"positionen {len(st['positions'])} | trades {len(st['trades'])} | "
              f"universum {len(me.get('universe') or [])} | start {st.get('started')}")
        print(f"            coverage {st.get('coverage')}")
    except Exception as e:
        print(f"   Bot {b.upper()}: {e}")
