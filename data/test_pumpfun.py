"""Test: liefert SolanaTracker die pump.fun-Phase (vor dem Pool-Start)? Nur Lesen, genau 6 Abfragen.

Aufruf im Solbot-Ordner:
    cd data
    python3 test_pumpfun.py
Ergebnis auf dem Bildschirm und in data/test_pumpfun.json (kommt mit dem naechsten Bot-Lauf auf GitHub).
"""
import json, os, sys, time

HIER = os.path.dirname(os.path.abspath(__file__))
SOLBOT = os.path.dirname(HIER) if os.path.basename(HIER) == "data" else HIER
ENV = os.path.join(os.path.dirname(SOLBOT), ".env")
os.chdir(SOLBOT)
import requests

KEY = None
if os.path.exists(ENV):
    for z in open(ENV):
        z = z.strip()
        if z.startswith("export "): z = z[7:]
        if z.startswith("SOLANATRACKER_KEY="): KEY = z.split("=", 1)[1].strip().strip('"').strip("'")
if not KEY: sys.exit("Kein SOLANATRACKER_KEY in " + ENV)

B = "https://data.solanatracker.io"
H = {"x-api-key": KEY, "User-Agent": "solbot-backtest/1.0"}
# Mint, Symbol, "Erstellt" laut altem Backtest (= Pool-Start, Daten begannen dort erst bei 2-3 Mio $)
COINS = [("9hjBLBrc7WZW2H8UQvBAvG5WiTZotXUv2Bdcn4f4pump", "DCTY", 1795017399),
         ("4CxjZyMNoF1p6eXHXHv1TWpS2X47CuUmLwtGe18spump", "mihao", 1794352663)]
erg = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "coins": {}}
zt = lambda t: time.strftime("%d.%m. %H:%M", time.gmtime(t))


def get(pfad, params=None):
    time.sleep(1.2)
    try:
        r = requests.get(B + pfad, headers=H, params=params or {}, timeout=40)
        print(f"  HTTP {r.status_code} {pfad}")
        return r.json() if r.status_code == 200 else {"_fehler": r.status_code, "_text": r.text[:300]}
    except Exception as ex:
        print("  FEHLER", ex); return {"_fehler": str(ex)[:300]}


def zeiten(obj, pfad="", out=None):
    """sucht alle Felder, die nach Zeitstempel aussehen (fuer das echte Startdatum)"""
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items(): zeiten(v, f"{pfad}.{k}", out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:10]): zeiten(v, f"{pfad}[{i}]", out)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        t = obj / 1000 if obj > 1e12 else obj
        if 1.6e9 < t < 1.9e9 and any(w in pfad.lower() for w in ("time", "creat", "date", "graduat")): out.append((pfad, int(t)))
    return out


for mint, sym, alt in COINS:
    print(f"\n===== {sym}  (alter Datenbeginn {zt(alt)})")
    c = erg["coins"][sym] = {"alt_erstellt": alt}
    # 1) Token-Info: echtes Startdatum + Pools (z.B. pumpfun)
    info = get(f"/tokens/{mint}")
    pools = [{k: p.get(k) for k in ("poolId", "market", "createdAt", "liquidity", "marketCap")} for p in (info.get("pools") or [])]
    zs = zeiten(info)
    c["pools"], c["zeitfelder"], c["info_fehler"] = pools, zs, info.get("_fehler")
    for p in pools:
        ca = p.get('createdAt') or 0; ca = ca / 1000 if ca > 1e12 else ca
        print(f"  Pool {p.get('market')}: erstellt {zt(ca) if ca else '?'}")
    for pf, t in zs: print(f"  Zeitfeld {pf}: {zt(t)}")
    kand = [t for _, t in zs if t <= alt]
    start = min(kand) if kand else alt - 3 * 86400       # nichts gefunden -> 3 Tage vorher probieren
    c["start_gewaehlt"] = start
    print(f"  -> frage Kurse ab {zt(start)} ab ({(alt - start) / 3600:.1f} h vor altem Datenbeginn)")
    # 2) Marketcap-Kerzen 1 Min ab echtem Start
    ch = get(f"/chart/{mint}", {"type": "1m", "time_from": start, "time_to": alt + 3600, "marketCap": "true"})
    k = ch.get("oclhv") or []
    c["kerzen_n"] = len(k); c["kerzen_fehler"] = ch.get("_fehler")
    if k:
        c["kerzen_erste20"] = k[:20]
        vor = [x for x in k if x["time"] < alt]
        c["kerzen_vor_altem_start"] = len(vor)
        print(f"  {len(k)} Kerzen, davon {len(vor)} VOR dem alten Datenbeginn")
        print(f"  erste Kerze {zt(k[0]['time'])}: MCAP {k[0]['close']:,.0f} $ | tiefste {min(x['low'] for x in k):,.0f} $")
        for x in k[:8]: print(f"     {zt(x['time'])}  MCAP {x['close']:>14,.0f} $  Vol {x['volume']:>12,.0f} $")
    else:
        print("  keine Kerzen:", str(ch)[:200])
    # 3) Holder ab echtem Start
    h = get(f"/holders/chart/{mint}", {"type": "5m", "time_from": start, "time_to": alt + 3600})
    hs = h.get("holders") or []
    c["holder_n"] = len(hs); c["holder_erste10"] = hs[:10]; c["holder_fehler"] = h.get("_fehler")
    if hs: print(f"  {len(hs)} Holder-Punkte, erster {zt(hs[0]['time'])}: {hs[0]['holders']} Holder")
    else: print("  keine Holder-Punkte:", str(h)[:200])

json.dump(erg, open("data/test_pumpfun.json", "w"), indent=1)
ok = all(v.get("kerzen_vor_altem_start", 0) > 0 and v.get("kerzen_erste20", [{}])[0].get("close", 1e9) < 200_000 for v in erg["coins"].values())
print("\nERGEBNIS:", "pump.fun-Phase ist DA (Start unter 200k $ MCAP)" if ok else "pump.fun-Phase fehlt oder unklar - ich schaue mir data/test_pumpfun.json an")
print("Fertig. 6 Abfragen verbraucht.")
