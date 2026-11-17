"""Analyse der Ausstiege von Bot E (nur Lesen, kauft nichts, braucht keine Schluessel).

Aufruf (keine Sonderzeichen noetig): im Solbot-Ordner
    cd data
    python3 analyse_e.py
Dauer: ~6-10 Minuten (GeckoTerminal erlaubt nur ~30 Abfragen pro Minute).

WAS ES MACHT:
  1) Nimmt alle abgeschlossenen Positionen von Bot E aus data/bot_e_state.json.
  2) Holt je Position die 5-Minuten-Kerzen des Pools (GeckoTerminal) ab dem Einstieg bis 8 Tage danach.
  3) Spielt E's Ausstiegsregeln auf den Kerzen nach - zuerst mit den HEUTIGEN Einstellungen (Kontrolle: stimmt die
     Nachbildung mit den echten Ergebnissen ueberein?), dann mit vielen anderen Einstellungen
     (Stop, Teilverkauf, Trailing, Einstand-Stop).
  4) Zeitsplit: Positionen nach Einstiegszeit in zwei Haelften. Eine Einstellung ist nur interessant, wenn sie in
     BEIDEN Haelften besser ist als heute UND ihre Nachbarwerte auch gut sind (sonst Zufall).
  5) Zusaetzlich: Wie lief der Kurs 1 h / 6 h / 24 h / 72 h NACH jedem echten Verkauf? (Verkauft E zu frueh?)
Wie E selbst wird nur der Schlusskurs jeder 5-Min-Kerze betrachtet (E sieht alle 5 Min einen Preis).
Ergebnis auf dem Bildschirm und in data/analyse_e.json (kommt mit dem naechsten Bot-Lauf auf GitHub).
"""
import json, os, sys, time, calendar, itertools, statistics as S

HIER = os.path.dirname(os.path.abspath(__file__))
SOLBOT = os.path.dirname(HIER) if os.path.basename(HIER) == "data" else HIER
os.chdir(SOLBOT)
import requests

GT = "https://api.geckoterminal.com/api/v2/networks/solana"
H = {"Accept": "application/json;version=20230302", "User-Agent": "solbot-analyse/1.0"}
KOSTEN_JE_VERKAUF = 0.003          # ~0,3 % je Verkauf (Gebuehr + Slippage bei E's liquiden Coins)
CACHE = os.path.join(os.path.dirname(SOLBOT), "analyse_e_cache.json")   # ausserhalb von data/ (nicht auf GitHub)

# heutige Einstellungen von Bot E (bot_e.py)
HEUTE = dict(stop=-0.10, tp1=0.15, tp1_frac=0.5, arm=0.08, give=-0.06, be=True, hold_d=7, flat=0.03)


def iso(s): return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))

def get(url, params=None):
    for versuch in range(4):
        try:
            r = requests.get(url, params=params, headers=H, timeout=30)
            if r.status_code == 429: time.sleep(20 + 10 * versuch); continue
            if r.status_code != 200: return None
            return r.json()
        except Exception:
            time.sleep(5)
    return None


def positionen():
    s = json.load(open("data/bot_e_state.json"))
    offen, fertig = {}, []
    for t in s["trades"]:
        a = t["addr"]
        if t["side"] == "buy":
            if a not in offen: offen[a] = {"sym": t["sym"], "addr": a, "t0": iso(t["t"]), "px0": t["price"], "cost": t["usd"],
                                           "pnl": 0.0, "verkaeufe": []}
        elif a in offen:
            p = offen[a]; p["pnl"] += t.get("pnl") or 0
            p["verkaeufe"].append({"t": iso(t["t"]), "px": t["price"], "frac": t.get("frac") or 1, "grund": t.get("reason")})
            if (t.get("frac") or 1) >= 0.999:
                p["t1"] = iso(t["t"]); p["echt"] = p["pnl"] / p["cost"]; fertig.append(offen.pop(a))
    return fertig


def pool_fuer(addr, pair_of):
    if pair_of.get(addr): return pair_of[addr]
    d = get(f"{GT}/tokens/{addr}/pools", {"page": 1}) or {}
    pools = [((p.get("attributes") or {}).get("reserve_in_usd") or 0, (p.get("attributes") or {}).get("address")) for p in d.get("data") or []]
    pools = [(float(r or 0), a) for r, a in pools if a]
    time.sleep(2.2)
    return max(pools)[1] if pools else None


def kerzen(pool, von, bis):
    """5-Min-Kerzen [t, o, h, l, c] von 'von' bis 'bis' (mehrere Abfragen zu je 1000 Kerzen = ~3,5 Tage)."""
    out, ende = {}, min(bis, int(time.time()))
    while ende > von:
        d = get(f"{GT}/pools/{pool}/ohlcv/minute", {"aggregate": 5, "before_timestamp": int(ende), "limit": 1000,
                                                    "currency": "usd", "token": "base"}) or {}
        rows = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        time.sleep(2.2)
        if not rows: break
        for r in rows: out[int(r[0])] = [int(r[0])] + [float(x) for x in r[1:5]]
        aeltester = min(int(r[0]) for r in rows)
        if aeltester >= ende: break
        ende = aeltester
    return [out[k] for k in sorted(out) if von - 600 <= k <= bis]


def simulieren(k, t0, cfg):
    """E's Ausstiegsregeln auf Schlusskursen ab t0. Gibt Ergebnis in Prozent (nach Kosten) oder None."""
    k = [r for r in k if r[0] >= t0 - 300]
    if len(k) < 3: return None
    p0 = k[0][4]; peak = p0; tp1 = False; rest = 1.0; erloes = 0.0; n_verk = 0
    for r in k[1:]:
        px = r[4]; x = px / p0 - 1; peak = max(peak, px)
        peak_x = peak / p0 - 1; vom_hoch = px / peak - 1; tage = (r[0] - t0) / 86400
        raus = False
        if not tp1:
            if x <= cfg["stop"]: raus = True
            elif cfg["tp1"] is not None and x >= cfg["tp1"]:
                erloes += cfg["tp1_frac"] * (1 + x); rest -= cfg["tp1_frac"]; tp1 = True; n_verk += 1
            elif peak_x >= cfg["arm"] and vom_hoch <= cfg["give"]: raus = True
            elif tage >= cfg["hold_d"] and x < cfg["flat"]: raus = True
        else:
            if cfg["be"] and x <= 0: raus = True
            elif vom_hoch <= cfg["give"]: raus = True
            elif tage >= cfg["hold_d"]: raus = True
        if raus:
            erloes += rest * (1 + x); n_verk += 1
            return erloes - 1 - KOSTEN_JE_VERKAUF * n_verk
    return erloes + rest * (k[-1][4] / p0) - 1 - KOSTEN_JE_VERKAUF * (n_verk + 1)   # Daten zu Ende: zum letzten Kurs


def main():
    meta = json.load(open("data/bot_e_meta.json"))
    pos = positionen()
    print(f"{len(pos)} abgeschlossene Positionen von Bot E")
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    for i, p in enumerate(pos):
        key = f"{p['addr']}_{p['t0']}"
        if key in cache and cache[key].get("k"): p["k"] = cache[key]["k"]; continue
        pool = pool_fuer(p["addr"], meta.get("pair_of") or {})
        p["k"] = kerzen(pool, p["t0"] - 600, p["t0"] + 8 * 86400) if pool else []
        cache[key] = {"pool": pool, "k": p["k"]}
        print(f"  {i + 1}/{len(pos)} {p['sym']}: {len(p['k'])} Kerzen")
        json.dump(cache, open(CACHE, "w"))
    pos = [p for p in pos if len(p.get("k") or []) >= 12]
    pos.sort(key=lambda p: p["t0"])
    print(f"{len(pos)} Positionen mit Kursdaten")
    if len(pos) < 10: raise SystemExit("Zu wenige Positionen mit Kursdaten.")
    h = len(pos) // 2

    def auswerten(cfg):
        r = [simulieren(p["k"], p["t0"], cfg) for p in pos]
        ok = [(i, x) for i, x in enumerate(r) if x is not None]
        a = [x for i, x in ok if i < h]; b = [x for i, x in ok if i >= h]
        return {"n": len(ok), "schnitt": S.mean(x for _, x in ok), "h1": S.mean(a) if a else None, "h2": S.mean(b) if b else None,
                "treffer": sum(x > 0 for _, x in ok) / len(ok), "einzeln": r}

    # 1) Kontrolle: Nachbildung der heutigen Regeln vs. echte Ergebnisse
    basis = auswerten(HEUTE)
    paare = [(x, p["echt"]) for x, p in zip(basis["einzeln"], pos) if x is not None]
    abw = S.median(abs(a - b) for a, b in paare)
    echt_schnitt = S.mean(b for _, b in paare)
    print(f"\nKONTROLLE heutige Regeln: Nachbildung {basis['schnitt']:+.2%} je Trade, echt {echt_schnitt:+.2%}, "
          f"typische Abweichung je Trade {abw:.2%}")

    # 2) Raster anderer Einstellungen
    raster = dict(stop=[-0.06, -0.08, -0.10, -0.12, -0.15], tp1=[0.10, 0.15, 0.20, 0.30, None],
                  arm=[0.05, 0.08, 0.12], give=[-0.04, -0.06, -0.08, -0.10, -0.12], be=[True, False])
    ergebnisse = []
    for werte in itertools.product(*raster.values()):
        cfg = {**HEUTE, **dict(zip(raster.keys(), werte))}
        e = auswerten(cfg); e.pop("einzeln")
        ergebnisse.append({"cfg": {k: cfg[k] for k in raster}, **e})
    b = {k: basis[k] for k in ("schnitt", "h1", "h2")}

    def nachbarn(c):
        """Durchschnitt der Varianten, die sich in genau einem Wert um eine Stufe unterscheiden."""
        vals = []
        for k, liste in raster.items():
            i = liste.index(c[k])
            for j in (i - 1, i + 1):
                if 0 <= j < len(liste):
                    cc = {**c, k: liste[j]}
                    m = next(e for e in ergebnisse if e["cfg"] == cc); vals.append(m["schnitt"])
        return S.mean(vals)

    robust = [e for e in ergebnisse if e["h1"] > b["h1"] and e["h2"] > b["h2"]]
    for e in robust: e["nachbarn"] = nachbarn(e["cfg"])
    robust.sort(key=lambda e: -min(e["schnitt"], e["nachbarn"]))

    def zeile(e):
        c = e["cfg"]
        return (f"Stop {c['stop']:+.0%} | Teilverkauf {('bei %+.0f%%' % (c['tp1'] * 100)) if c['tp1'] is not None else 'keiner':>11} | "
                f"Trailing ab {c['arm']:+.0%}, {c['give']:+.0%} vom Hoch | Einstand-Stop {'ja' if c['be'] else 'nein'}  ->  "
                f"{e['schnitt']:+.2%} je Trade (H1 {e['h1']:+.2%}, H2 {e['h2']:+.2%}, Treffer {e['treffer']:.0%}"
                + (f", Nachbarn {e['nachbarn']:+.2%})" if "nachbarn" in e else ")"))

    print(f"\nHEUTE: {basis['schnitt']:+.2%} je Trade (H1 {basis['h1']:+.2%}, H2 {basis['h2']:+.2%}, Treffer {basis['treffer']:.0%})")
    print(f"\n{len(robust)} von {len(ergebnisse)} Varianten sind in BEIDEN Haelften besser als heute. Die besten (nach dem "
          f"schlechteren Wert aus eigenem Ergebnis und Nachbarn):")
    for e in robust[:12]: print("  " + zeile(e))

    # 3) einzelne Stellschrauben (alles andere wie heute)
    print("\nEINZELNE STELLSCHRAUBEN (Rest wie heute):")
    einzel = {}
    for k, liste in raster.items():
        einzel[k] = []
        for v in liste:
            e = auswerten({**HEUTE, k: v}); einzel[k].append({"wert": v, "schnitt": e["schnitt"], "h1": e["h1"], "h2": e["h2"]})
            print(f"  {k:>5} = {str(v):>6}: {e['schnitt']:+.2%} (H1 {e['h1']:+.2%}, H2 {e['h2']:+.2%})")

    # 4) Kurs nach dem echten Verkauf
    nach = {f"{s}h": [] for s in (1, 6, 24, 72)}
    for p in pos:
        t1 = p.get("t1"); kk = p["k"]
        ref = next((r[4] for r in kk if r[0] >= t1), None)
        if not ref: continue
        for s in (1, 6, 24, 72):
            r = next((r[4] for r in kk if r[0] >= t1 + s * 3600), None)
            if r: nach[f"{s}h"].append(r / ref - 1)
    print("\nKURS NACH DEM ECHTEN VERKAUF (Median, Anteil gestiegen):")
    for k, v in nach.items():
        if v: print(f"  nach {k:>3}: {S.median(v):+.1%}  ({sum(x > 0 for x in v) / len(v):.0%} gestiegen, n={len(v)})")

    json.dump({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "positionen": len(pos),
               "kontrolle": {"nachbildung": basis["schnitt"], "echt": echt_schnitt, "abweichung_median": abw},
               "heute": b, "robust_top": robust[:20], "einzeln": einzel,
               "nach_verkauf": {k: {"median": S.median(v), "gestiegen": sum(x > 0 for x in v) / len(v), "n": len(v)}
                                for k, v in nach.items() if v}},
              open("data/analyse_e.json", "w"), indent=1)
    print("\nFertig. Ergebnis in data/analyse_e.json - kommt mit dem naechsten Bot-Lauf auf GitHub, ich lese es dort.")


if __name__ == "__main__":
    main()
