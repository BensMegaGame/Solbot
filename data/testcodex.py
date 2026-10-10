"""Test: liefert Codex die ERSTEN Trades eines Coins (ab dem Start)? Nur Lesen, kauft nichts, kein Helius.

Aufruf im Solbot-Ordner (dort, wo data/ liegt):
    set -a; . ../.env; set +a; python3 testcodex.py

Nimmt automatisch 4 Coins aus data/bot_d_early.json:
  - 2, fuer die Helius die Fruehkaeufer schon kennt (Vergleich: findet Codex dieselben Wallets?)
  - 2, bei denen Helius gescheitert ist (viele Transaktionen - genau die brauchen wir)
und fragt je Coin bei Codex ab (insgesamt ~16 Abfragen):
  1) Startzeit des Coins (createdAt)
  2) die aeltesten 50 Trades (getTokenEvents, direction ASC) - einmal normal, einmal mit crossPools
  3) die Pools des Coins
Ergebnis auf dem Bildschirm und in data/codex_test.json (landet beim naechsten Lauf auf GitHub).
"""
import json, os, time
import requests

KEY = os.environ.get("CODEX_KEY")
URL = "https://graph.codex.io/graphql"
NET = 1399811149
if not KEY:
    raise SystemExit("CODEX_KEY fehlt. Vorher:  set -a; . ../.env; set +a")
if not os.path.exists("data/bot_d_early.json"):
    raise SystemExit("Bitte im Solbot-Ordner starten (dort, wo data/ liegt).")


def q(query):
    try:
        r = requests.post(URL, json={"query": query}, timeout=40,
                          headers={"Authorization": KEY, "Content-Type": "application/json"})
        d = r.json()
        if d.get("errors"): return None, json.dumps(d["errors"])[:300]
        return d.get("data"), None
    except Exception as e:
        return None, str(e)[:300]
    finally:
        time.sleep(1.2)


EV = "items { timestamp blockNumber maker eventDisplayType transactionHash }"

def events(addr, cross=False):
    extra = ", crossPools: true" if cross else ""
    d, err = q('{ getTokenEvents(query: {address: "%s", networkId: %d%s}, direction: ASC, limit: 50) { %s } }'
               % (addr, NET, extra, EV))
    return ((d or {}).get("getTokenEvents") or {}).get("items") or [], err

def created(addr):
    d, err = q('{ token(input: {address: "%s", networkId: %d}) { createdAt symbol } }' % (addr, NET))
    return ((d or {}).get("token") or {}).get("createdAt"), err

def pools(addr):
    d, err = q('{ listPairsWithMetadataForToken(tokenAddress: "%s", networkId: %d) { results { pair { address } exchange { name } liquidity } } }'
               % (addr, NET))
    res = ((d or {}).get("listPairsWithMetadataForToken") or {}).get("results") or []
    return [((x.get("exchange") or {}).get("name"), (x.get("pair") or {}).get("address"), x.get("liquidity")) for x in res], err


early = json.load(open("data/bot_d_early.json"))
mit = sorted([(len(v["kaeufer"]), a) for a, v in early.items() if v.get("kaeufer") and len(v["kaeufer"]) >= 8], reverse=True)[:2]
ohne = [a for a, v in early.items() if not v.get("kaeufer") and "zu viele" in (v.get("fehler") or "")]
ohne = sorted(ohne, key=lambda a: -(early[a].get("peak", 0) / early[a]["px0"] if early[a].get("px0") else 0))[:2]
auswahl = [("Helius kennt Fruehkaeufer", a) for _, a in mit] + [("Helius gescheitert", a) for a in ohne]

ergebnis = []
for art, a in auswahl:
    e = early[a]
    print(f"\n=== {e.get('sym')} ({art})  {a}")
    t_create, err_c = created(a)
    print(f"  Codex createdAt: {t_create}  {'FEHLER ' + err_c if err_c else ''}")
    if e.get("t0"): print(f"  Helius Start (erste Transaktion): {e['t0']}")
    pl, err_p = pools(a)
    print(f"  Pools: {pl if pl else ''} {'FEHLER ' + err_p if err_p else ''}")
    zeile = {"sym": e.get("sym"), "addr": a, "art": art, "createdAt": t_create, "helius_t0": e.get("t0"), "pools": pl,
             "fehler": {"created": err_c, "pools": err_p}}
    for cross in (False, True):
        items, err = events(a, cross)
        name = "crossPools" if cross else "normal"
        if err: print(f"  Events {name}: FEHLER {err}"); zeile[name] = {"fehler": err}; continue
        if not items: print(f"  Events {name}: keine"); zeile[name] = {"n": 0}; continue
        t_first = items[0].get("timestamp")
        buys = [x for x in items if x.get("eventDisplayType") == "Buy"]
        makers = list(dict.fromkeys(x.get("maker") for x in buys if x.get("maker")))
        abstand = (t_first - t_create) / 60 if t_first and t_create else None
        treffer = None
        if e.get("kaeufer"):
            hel = {k[0] for k in e["kaeufer"]}
            treffer = len(hel & set(makers))
        print(f"  Events {name}: {len(items)} Trades, {len(buys)} Kaeufe, {len(makers)} Kaeufer-Wallets | erster Trade "
              f"{'%.0f min' % abstand if abstand is not None else '?'} nach createdAt"
              + (f" | gleiche Wallets wie Helius: {treffer} von {len(e['kaeufer'])}" if treffer is not None else ""))
        zeile[name] = {"n": len(items), "kaeufe": len(buys), "wallets": len(makers), "erster_trade": t_first,
                       "min_nach_start": abstand, "gleich_wie_helius": treffer, "beispiel": items[:3]}
    ergebnis.append(zeile)

json.dump({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "coins": ergebnis},
          open("data/codex_test.json", "w"), indent=1)
print("\nFertig. Ergebnis in data/codex_test.json - kommt mit dem naechsten Bot-Lauf auf GitHub, ich lese es dort.")
