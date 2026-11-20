"""Test: liefert SolanaTracker alles fuer den 10M-Backtest (9.-20.11.)? Nur Lesen, ~4 Abfragen.

Aufruf (keine Sonderzeichen noetig): im Solbot-Ordner
    cd data
    python3 test_tracker.py
Fragt beim ersten Mal nach dem SolanaTracker-Schluessel und speichert ihn selbst in der .env (SOLANATRACKER_KEY).
Ergebnis auf dem Bildschirm und in data/tracker_test.json (kommt mit dem naechsten Bot-Lauf auf GitHub).
"""
import json, os, sys, time, getpass

HIER = os.path.dirname(os.path.abspath(__file__))
SOLBOT = os.path.dirname(HIER) if os.path.basename(HIER) == "data" else HIER
ENV = os.path.join(os.path.dirname(SOLBOT), ".env")
os.chdir(SOLBOT)
import requests

def env_lesen():
    d = {}
    if os.path.exists(ENV):
        for z in open(ENV):
            z = z.strip()
            if z.startswith("export "): z = z[7:]
            if "=" in z and not z.startswith("#"):
                k, v = z.split("=", 1); d[k.strip()] = v.strip().strip('"').strip("'")
    return d

KEY = env_lesen().get("SOLANATRACKER_KEY")
if not KEY:
    KEY = "".join(getpass.getpass("SolanaTracker API-Key einfuegen (wird nicht angezeigt), dann Enter: ").split())
    if len(KEY) < 10: sys.exit("Kein Schluessel eingegeben.")
    with open(ENV, "a") as f: f.write(f"\nSOLANATRACKER_KEY={KEY}\n")
    os.chmod(ENV, 0o600)
    print(f"Gespeichert in {ENV} ({len(KEY)} Zeichen, beginnt mit {KEY[:3]}...)")

B = "https://data.solanatracker.io"
H = {"x-api-key": KEY, "User-Agent": "solbot-backtest/1.0"}
VON = int(time.mktime(time.strptime("2026-11-09", "%Y-%m-%d"))); BIS = int(time.time())
erg = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

def abfrage(name, pfad, params):
    print(f"\n=== {name}")
    try:
        r = requests.get(B + pfad, headers=H, params=params, timeout=40)
        kopf = {k: v for k, v in r.headers.items() if "limit" in k.lower() or "remaining" in k.lower() or "credit" in k.lower()}
        print("HTTP", r.status_code, kopf)
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"text": r.text[:300]}
        erg[name] = {"http": r.status_code, "kopf": kopf}
        if r.status_code != 200: print(str(j)[:400]); erg[name]["fehler"] = str(j)[:400]; return None
        return j
    except Exception as ex:
        print("FEHLER", ex); erg[name] = {"fehler": str(ex)[:300]}; return None

# 1) Suche: Coins erstellt seit 9.11., sortiert nach Gesamtzahl Transaktionen (waechst nur - auch abgestuerzte Coins bleiben oben)
s = abfrage("suche", "/search", {"minCreatedAt": VON * 1000, "maxCreatedAt": BIS * 1000, "sortBy": "totalTransactions",
                                 "sortOrder": "desc", "limit": 20})
top = None
if s:
    d = s.get("data") or []
    print(f"Treffer gesamt: {s.get('total')} | Seiten: {s.get('pages')}")
    for x in d[:10]:
        print(f"  {str(x.get('symbol')):12} mcap {x.get('marketCapUsd') or 0:>14,.0f} $ | holders {x.get('holders')} | tx {x.get('totalTransactions')} "
              f"| erstellt {time.strftime('%m-%d %H:%M', time.gmtime((x.get('createdAt') or 0) / 1000))} | {x.get('mint')}")
    erg["suche"].update(total=s.get("total"), beispiel=[{k: x.get(k) for k in ("symbol", "mint", "marketCapUsd", "priceUsd", "holders",
                                                                                "totalTransactions", "createdAt", "poolAddress")} for x in d[:10]])
    top = d[0] if d else None
time.sleep(1.1)

if top:
    mint = top["mint"]
    vorrat = (top.get("marketCapUsd") or 0) / (top.get("priceUsd") or 1)   # Tokenmenge = Marketcap / Kurs
    t0 = max(VON, int((top.get("createdAt") or VON * 1000) / 1000))
    # 2) Kerzen (Kurs + Volumen) ab Start
    c = abfrage("kerzen", f"/chart/{mint}", {"type": "5m", "time_from": t0, "time_to": BIS})
    if c:
        k = c.get("oclhv") or c.get("data") or (c if isinstance(c, list) else [])
        if k:
            hoch = max(float(x.get("high") or 0) for x in k)
            print(f"{len(k)} Kerzen (5 Min), erste {time.strftime('%m-%d %H:%M', time.gmtime(k[0]['time']))}, letzte "
                  f"{time.strftime('%m-%d %H:%M', time.gmtime(k[-1]['time']))} | hoechste Marketcap ~{hoch * vorrat:,.0f} $")
            erg["kerzen"].update(n=len(k), erste=k[0], letzte=k[-1], peak_mcap=hoch * vorrat, felder=list(c.keys()) if isinstance(c, dict) else "liste")
        else:
            print("Antwort ohne Kerzen:", str(c)[:300]); erg["kerzen"]["antwort"] = str(c)[:300]
    time.sleep(1.1)
    # 3) Holder-Verlauf
    h = abfrage("holder", f"/holders/chart/{mint}", {"type": "1h", "time_from": t0, "time_to": BIS})
    if h:
        hs = h.get("holders") or []
        if hs:
            print(f"{len(hs)} Holder-Punkte (1 h), erster {time.strftime('%m-%d %H:%M', time.gmtime(hs[0]['time']))}: {hs[0]['holders']}"
                  f" | letzter {time.strftime('%m-%d %H:%M', time.gmtime(hs[-1]['time']))}: {hs[-1]['holders']}")
            erg["holder"].update(n=len(hs), erster=hs[0], letzter=hs[-1])
        else:
            print("Antwort ohne Holder-Punkte:", str(h)[:300]); erg["holder"]["antwort"] = str(h)[:300]
    time.sleep(1.1)
    # 4) Holder-Verlauf feiner (5 Min) - fuer die ersten Stunden nach dem Start
    h5 = abfrage("holder_5m", f"/holders/chart/{mint}", {"type": "5m", "time_from": t0, "time_to": t0 + 24 * 3600})
    if h5:
        hs = h5.get("holders") or []
        print(f"{len(hs)} Holder-Punkte (5 Min) in den ersten 24 h"); erg["holder_5m"]["n"] = len(hs)

json.dump(erg, open("data/tracker_test.json", "w"), indent=1)
print("\nFertig. Ergebnis in data/tracker_test.json - kommt mit dem naechsten Bot-Lauf auf GitHub, ich lese es dort.")
