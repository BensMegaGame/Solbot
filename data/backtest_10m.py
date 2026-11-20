"""Backtest 10M: Wie sahen Solana-Coins, die vom 9.11. bis heute die 10 Mio. $ Marketcap erreichten, in ihren ersten
Stunden aus - verglichen mit Coins, die es nur auf 1-5 Mio. schafften? Nur Lesen, kauft nichts.

Aufruf (keine Sonderzeichen noetig): im Solbot-Ordner
    cd data
    python3 backtest_10m.py
Braucht SOLANATRACKER_KEY in der .env (hat test_tracker.py schon gespeichert).
Dauer: ~15-25 Minuten. Bricht es ab (z.B. SSH getrennt) oder ist das Abfrage-Limit erreicht: einfach neu starten,
es macht dort weiter, wo es aufgehoert hat.

ABLAUF
  1) Kandidaten: Coins, erstellt seit dem 9.11., aus drei Listen (meiste Holder, meiste Gebuehren, hoechste Marketcap).
     ~10 Abfragen (500 Coins je Abfrage).
  2) Vorauswahl je Kandidat (1 Abfrage): 15-Min-Kerzen ueber den ganzen Zeitraum -> hoechste Marketcap, gemessen an
     SCHLUSSKURSEN, mind. 2 Kerzen hintereinander (= 30 Min), die erste Viertelstunde nach Start zaehlt nicht
     (die allererste Kerze ist oft unsinnig, z.B. UODF: angeblich 306 Mio. $).
       Gruppe "10M":  >= 10 Mio. $          Gruppe "1-5M" (Vergleich): 1 bis 5 Mio. $
  3) Details je Coin beider Gruppen (6-8 Abfragen): Kerzen und Holder JEDE MINUTE fuer die ersten 6 h, alle 5 Min fuer
     die ersten 24 h, danach stuendlich - und alle 5 Min bis zum Erreichen der 10M, falls das erst nach 24 h war.
     Daraus entstehen in der Excel die Meilensteine in 50k-Schritten (50k, 100k, ... 10M): Uhrzeit, Minuten seit
     Start, Volumen seit Start und Holder im Moment des Erreichens.
     Vergleichsgruppe hoechstens so gross wie die 10M-Gruppe x 1,5 (zufaellig, aber jedes Mal dieselbe Auswahl).
ERGEBNIS: Ordner data/bt10m/ (kandidaten.json, stand.json, je Coin eine Datei). Kommt mit dem naechsten Bot-Lauf
auf GitHub - daraus baue ich die Excel-Datei mit Timeline und Diagrammen.
LIMIT: hoechstens 2.300 Abfragen insgesamt (Gratis-Plan 2.500/Monat). Anders: python3 backtest_10m.py 150000
"""
import json, os, sys, time, random

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
if not KEY: sys.exit("SOLANATRACKER_KEY fehlt in der .env - zuerst test_tracker.py starten.")
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 2300

B = "https://data.solanatracker.io"
H = {"x-api-key": KEY, "User-Agent": "solbot-backtest/1.0"}
ORDNER = "data/bt10m"
VON = int(time.mktime(time.strptime("2026-11-09", "%Y-%m-%d")))
ZIEL, VGL_MIN, VGL_MAX = 10_000_000, 1_000_000, 5_000_000
LISTEN = [("holders", 1000), ("fees.total", 1000), ("marketCapUsd", 500)]
os.makedirs(ORDNER, exist_ok=True)

def laden(name, default):
    p = os.path.join(ORDNER, name)
    return json.load(open(p)) if os.path.exists(p) else default

def speichern(name, obj):
    p = os.path.join(ORDNER, name); tmp = p + ".tmp"
    json.dump(obj, open(tmp, "w"), separators=(",", ":")); os.replace(tmp, p)

stand = laden("stand.json", {"abfragen": 0, "start": VON, "bis": int(time.time()), "schritt": 1})
BIS = stand["bis"]                                      # fester Endzeitpunkt (auch beim Weitermachen)


class LimitErreicht(Exception): pass

def abfrage(pfad, params):
    if stand["abfragen"] >= LIMIT: raise LimitErreicht()
    for versuch in range(4):
        stand["abfragen"] += 1
        try:
            r = requests.get(B + pfad, headers=H, params=params, timeout=40)
        except requests.RequestException:
            time.sleep(3); continue
        if r.status_code == 429: time.sleep(5 * (versuch + 1)); continue
        if r.status_code in (401, 402, 403):
            speichern("stand.json", stand)
            sys.exit(f"SolanaTracker lehnt ab (HTTP {r.status_code}): {r.text[:200]} - Limit/Plan pruefen, dann neu starten.")
        time.sleep(0.4)                                 # Gratis-Plan: max. 3 Abfragen je Sekunde
        if r.status_code != 200: return None
        try: return r.json()
        except ValueError: return None
    return None

def kerzen(mint, typ, von, bis):
    j = abfrage(f"/chart/{mint}", {"type": typ, "time_from": int(von), "time_to": int(bis)})
    k = (j or {}).get("oclhv") or []
    return [[int(x["time"]), float(x.get("close") or 0), float(x.get("high") or 0), float(x.get("volume") or 0)] for x in k]

def holder(mint, typ, von, bis):
    j = abfrage(f"/holders/chart/{mint}", {"type": typ, "time_from": int(von), "time_to": int(bis)})
    return [[int(x["time"]), int(x.get("holders") or 0)] for x in (j or {}).get("holders") or []]


def schritt1():
    kand = laden("kandidaten.json", {})
    if stand.get("schritt1_fertig"): return kand
    for sort, anzahl in LISTEN:
        for seite in range(1, anzahl // 500 + 1):
            j = abfrage("/search", {"minCreatedAt": VON * 1000, "maxCreatedAt": BIS * 1000, "sortBy": sort,
                                    "sortOrder": "desc", "limit": 500, "page": seite})
            for x in (j or {}).get("data") or []:
                m = x.get("mint")
                if not m or m in kand: continue
                px, mc = x.get("priceUsd") or 0, x.get("marketCapUsd") or 0
                kand[m] = {"sym": x.get("symbol"), "name": x.get("name"), "created": int((x.get("createdAt") or 0) / 1000),
                           "vorrat": mc / px if px else None, "mcap_jetzt": mc, "holders_jetzt": x.get("holders"),
                           "top10": x.get("top10"), "dev": x.get("dev"), "insiders": x.get("insiders"), "snipers": x.get("snipers"),
                           "launchpad": (x.get("launchpad") or {}).get("name") if isinstance(x.get("launchpad"), dict) else x.get("launchpad"),
                           "socials": bool(x.get("hasSocials")), "liste": sort}
            print(f"  Liste {sort} Seite {seite}: jetzt {len(kand)} Kandidaten"); speichern("kandidaten.json", kand)
    stand["schritt1_fertig"] = True; speichern("stand.json", stand)
    return kand


def peak_messen(k, vorrat, created):
    """Hoechste Marketcap aus Schlusskursen, mind. 2 aufeinanderfolgende Kerzen, erste 15 Min nach Start ignoriert."""
    k = [x for x in k if x[0] >= created + 15 * 60 and x[1] > 0]
    best, t_best = 0.0, None
    for a, b in zip(k, k[1:]):
        m = min(a[1], b[1]) * vorrat
        if m > best: best, t_best = m, b[0]
    return best, t_best


def schritt2(kand):
    offen = [m for m, c in kand.items() if "peak" not in c and c.get("vorrat") and c.get("created")]
    print(f"\nVorauswahl: {len(offen)} Kandidaten offen (Abfragen bisher {stand['abfragen']} von {LIMIT})")
    for i, m in enumerate(offen):
        c = kand[m]
        k, t = [], max(c["created"], VON)
        while t < BIS:                                  # in Stuecken zu ~10 Tagen (max. ~1.000 Kerzen je Abfrage)
            ende = min(BIS, t + 990 * 15 * 60); k += kerzen(m, "15m", t, ende); t = ende
        k = sorted({x[0]: x for x in k}.values())
        c["peak"], c["t_peak"] = peak_messen(k, c["vorrat"], c["created"]) if k else (0.0, None)
        if k:
            for schwelle in (1e6, 3e6, 10e6):          # Zeitpunkt, an dem die Schwelle erstmals 30 Min gehalten wurde
                kk = [x for x in k if x[0] >= c["created"] + 15 * 60]
                t = next((b[0] for a, b in zip(kk, kk[1:]) if min(a[1], b[1]) * c["vorrat"] >= schwelle), None)
                c[f"t_{int(schwelle / 1e6)}m"] = t
        if i % 25 == 0:
            n10 = sum(1 for x in kand.values() if (x.get("peak") or 0) >= ZIEL)
            print(f"  {i + 1}/{len(offen)} | bisher {n10} Coins mit 10M | Abfragen {stand['abfragen']}")
            speichern("kandidaten.json", kand); speichern("stand.json", stand)
    speichern("kandidaten.json", kand); speichern("stand.json", stand)


def schritt3(kand):
    sieger = sorted(m for m, c in kand.items() if (c.get("peak") or 0) >= ZIEL)
    vgl = sorted(m for m, c in kand.items() if VGL_MIN <= (c.get("peak") or 0) < VGL_MAX)
    random.Random(42).shuffle(vgl); vgl = vgl[:max(20, int(len(sieger) * 1.5))]
    for m in sieger: kand[m]["gruppe"] = "10M"
    for m in vgl: kand[m]["gruppe"] = "1-5M"
    speichern("kandidaten.json", kand)
    def fertig(m):
        p = os.path.join(ORDNER, f"coin_{m}.json")
        return os.path.exists(p) and "k1" in json.load(open(p))
    todo = [m for m in sieger + vgl if not fertig(m)]
    print(f"\nDetails: {len(sieger)} Coins mit 10M, {len(vgl)} Vergleichs-Coins, noch {len(todo)} offen")
    for i, m in enumerate(todo):
        c = kand[m]; t0 = max(c["created"], VON); t24 = t0 + 24 * 3600
        p = os.path.join(ORDNER, f"coin_{m}.json")
        d = json.load(open(p)) if os.path.exists(p) else {}
        d.update({"mint": m, **c})
        if "k5" not in d:
            d.update(k5=kerzen(m, "5m", t0, min(t24, BIS)), h5=holder(m, "5m", t0, min(t24, BIS)),
                     k1h=kerzen(m, "1h", t24, BIS) if t24 < BIS else [], h1h=holder(m, "1h", t24, BIS) if t24 < BIS else [])
        t6 = min(t0 + 6 * 3600, BIS)                    # jede Minute fuer die ersten 6 h (fuer die 50k-Meilensteine)
        d["k1"], d["h1"] = kerzen(m, "1m", t0, t6), holder(m, "1m", t0, t6)
        d["k5b"], d["h5b"] = [], []
        t10 = c.get("t_10m")
        if t10 and t10 > t24:                           # 10M erst nach 24 h erreicht: bis dahin alle 5 Min
            t, ende_ges = t24, min(BIS, t10 + 3600)
            while t < ende_ges:
                ende = min(ende_ges, t + 990 * 300); d["k5b"] += kerzen(m, "5m", t, ende); d["h5b"] += holder(m, "5m", t, ende); t = ende
        speichern(f"coin_{m}.json", d); speichern("stand.json", stand)
        print(f"  {i + 1}/{len(todo)} {c.get('sym')} ({c['gruppe']}, Hoch {c['peak'] / 1e6:.1f}M): "
              f"{len(d['k1'])}+{len(d['k5'])}+{len(d['k5b'])}+{len(d['k1h'])} Kerzen, {len(d['h5'])}+{len(d['h1h'])} Holder-Punkte | Abfragen {stand['abfragen']}")


def main():
    print(f"Zeitraum {time.strftime('%d.%m. %H:%M', time.gmtime(VON))} bis {time.strftime('%d.%m. %H:%M', time.gmtime(BIS))} UTC "
          f"| Abfragen bisher {stand['abfragen']} | Limit {LIMIT}")
    try:
        kand = schritt1(); schritt2(kand); schritt3(kand)
        stand["fertig"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(f"\nFERTIG. {stand['abfragen']} Abfragen verbraucht. Daten in {ORDNER}/ - kommen mit dem naechsten Bot-Lauf auf GitHub.")
    except LimitErreicht:
        print(f"\nLimit von {LIMIT} Abfragen erreicht - Zwischenstand gespeichert. Mit hoeherem Limit oder naechsten Monat "
              f"neu starten, es macht dort weiter.")
    finally:
        speichern("stand.json", stand)


if __name__ == "__main__":
    main()
