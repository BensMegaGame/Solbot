"""Backtest-Idee fuer Bot F: "Kaufe den Solana-Coin, der gerade NEU in die Top 200 (nach MCap) kommt".

Aufruf im Repo-Ordner (laeuft ~30 Min, darf im Hintergrund laufen):
    nohup python3 bt_top.py &
Fortschritt ansehen:  tail nohup.out
Bricht es ab, einfach neu starten - bereits geladene Coins werden nicht nochmal abgefragt.

Holt 365 Tage Tagesdaten (Kurs, MCap, Volumen) fuer die ~600 groessten Solana-Coins von CoinGecko,
rekonstruiert fuer jeden Tag die Rangliste und misst, wie Coins NACH dem Eintritt in die Top 200 liefen.
Ergebnis: data/bt_top_result.json + data/top_hist.json (Rohdaten). Beides landet beim naechsten
Paper-Lauf automatisch auf GitHub. Kein Einfluss auf die laufenden Bots.
"""
import os, json, time, statistics
import requests

CG = "https://api.coingecko.com/api/v3"
KEY = os.environ.get("COINGECKO_KEY")
HDR = {"User-Agent": "solbot-backtest", **({"x-cg-demo-api-key": KEY} if KEY else {})}
HIST = "data/top_hist.json"
RESULT = "data/bt_top_result.json"
PAGES = 3                     # 3 x 250 = die 750 groessten Coins der Kategorie (vor Filter)
EXCLUDE_SYM = {"USDC", "USDT", "USDS", "PYUSD", "USD1", "DAI", "FDUSD", "USDE", "EURC", "USDG", "USDY", "CASH",
               "WBTC", "CBBTC", "TBTC", "WETH", "WSOL", "SOL", "PAXG", "XAUT"}
EXCLUDE_SUB = ("USD", "EUR", "GBP", "CHF", "JPY", "XAU", "SOL")
FEE = 0.01                    # je Seite, grosszuegig fuer Large Caps (Swap + Slippage)


def tradeable(sym):
    sym = sym.upper()
    if sym in EXCLUDE_SYM: return False
    return not any(s in sym for s in EXCLUDE_SUB)


def cg(path, params=None):
    for versuch in range(6):
        try:
            r = requests.get(f"{CG}{path}", params=params, headers=HDR, timeout=40)
        except Exception as e:
            print("  Netzwerkfehler:", e); time.sleep(10); continue
        if r.status_code == 429:
            print("  Limit erreicht, warte 60 s ..."); time.sleep(60); continue
        if r.status_code != 200:
            print(f"  CoinGecko {r.status_code} bei {path}"); return None
        return r.json()
    return None


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, path)


def sig(x):  # 5 signifikante Stellen reichen, haelt die Datei klein
    return float(f"{x:.5g}") if x else 0.0


# ---------------------------------------------------------------- 1) Daten holen
def download():
    hist = json.load(open(HIST)) if os.path.exists(HIST) else {}
    print("Hole Adressliste ...")
    lst = cg("/coins/list", {"include_platform": "true"}) or []
    sol_addr = {c["id"]: (c.get("platforms") or {}).get("solana") for c in lst}
    sol_addr = {k: v for k, v in sol_addr.items() if v}
    print(f"  {len(sol_addr)} Coins mit Solana-Adresse")
    time.sleep(2.2)
    coins = []
    for page in range(1, PAGES + 1):
        res = cg("/coins/markets", {"vs_currency": "usd", "category": "solana-ecosystem", "order": "market_cap_desc",
                                    "per_page": 250, "page": page}) or []
        for c in res:
            if c["id"] in sol_addr and tradeable(c["symbol"]):
                coins.append((c["id"], c["symbol"].upper()))
        time.sleep(2.2)
    coins.append(("solana", "_SOL"))        # SOL selbst als Marktbarometer (nicht in der Rangliste)
    print(f"  {len(coins)} handelbare Solana-Coins, davon schon geladen: {sum(1 for i, _ in coins if i in hist)}")
    for n, (cid, sym) in enumerate(coins, 1):
        if cid in hist: continue
        d = cg(f"/coins/{cid}/market_chart", {"vs_currency": "usd", "days": 365, "interval": "daily"})
        time.sleep(2.2)
        if not d or not d.get("prices"): hist[cid] = {"sym": sym, "skip": 1}; continue
        mc = {int(t // 86400000): v for t, v in d.get("market_caps", [])}
        vo = {int(t // 86400000): v for t, v in d.get("total_volumes", [])}
        days = {}
        for t, p in d["prices"]:
            k = int(t // 86400000)
            days[k] = [sig(p), sig(mc.get(k)), sig(vo.get(k))]
        hist[cid] = {"sym": sym, "addr": sol_addr.get(cid, ""), "d": days}
        if n % 20 == 0:
            save_json(HIST, hist); print(f"  {n}/{len(coins)} geladen")
    save_json(HIST, hist)
    return hist


# ---------------------------------------------------------------- 2) Auswerten
def analyse(hist):
    coins = {cid: {int(k): v for k, v in h["d"].items()} for cid, h in hist.items() if h.get("d") and not h["sym"].startswith("_")}
    sym = {cid: hist[cid]["sym"] for cid in coins}
    alle_tage = sorted({k for d in coins.values() for k in d})
    rank = {}                                     # (cid, tag) -> Rang
    for t in alle_tage:
        liste = sorted(((d[t][1], cid) for cid, d in coins.items() if t in d and d[t][1] > 0), reverse=True)
        for r, (_, cid) in enumerate(liste, 1): rank[(cid, t)] = r

    def fwd(cid, t, h):
        d = coins[cid]
        if t in d and t + h in d and d[t][0] > 0: return d[t + h][0] / d[t][0]
        return None

    def sim(cid, t, stop=-0.10, tp=0.20, max_d=14):
        """Tageskurse: Stop/TP werden erst am Tagesschluss erkannt (grob, aber ohne Blick in die Zukunft)."""
        d = coins[cid]
        if t not in d: return None
        p0 = d[t][0]
        for k in range(1, max_d + 1):
            if t + k not in d: return None
            x = d[t + k][0] / p0 - 1
            if x <= stop or x >= tp or k == max_d: return (1 + x) * (1 - FEE) ** 2 - 1
        return None

    res = {}
    mitte = alle_tage[len(alle_tage) // 2]
    for N in (100, 200):
        for lookback in (3, 7):
            events = []
            for (cid, t), r in rank.items():
                if r > N: continue
                vorher = [rank.get((cid, t - k)) for k in range(1, lookback + 1)]
                if any(v is None for v in vorher): continue         # zu neu, keine Vorgeschichte
                if all(v > N for v in vorher): events.append((cid, t))
            basis = [(cid, t) for (cid, t), r in rank.items() if r <= N]
            key = f"top{N}_neu_seit_{lookback}d"
            out = {"n": len(events)}
            for h in (1, 3, 7, 14, 30):
                ev = [x for x in (fwd(c, t, h) for c, t in events) if x]
                bs = [x for x in (fwd(c, t, h) for c, t in basis) if x]
                if ev:
                    out[f"{h}d"] = {"median": round(statistics.median(ev), 3), "mittel": round(statistics.mean(ev), 3),
                                    "im_plus": round(sum(1 for x in ev if x > 1) / len(ev), 2), "n": len(ev),
                                    "vergleich_alle_topN_median": round(statistics.median(bs), 3) if bs else None}
            for stop, tp in ((-0.10, 0.20), (-0.15, 0.30), (-0.20, 0.50)):
                for teil, flt in (("gesamt", lambda t: True), ("1.Haelfte", lambda t: t < mitte),
                                  ("2.Haelfte", lambda t: t >= mitte)):
                    r_ = [x for x in (sim(c, t, stop, tp) for c, t in events if flt(t)) if x is not None]
                    if r_:
                        out[f"sim stop{stop:.0%} tp{tp:+.0%} {teil}"] = {
                            "n": len(r_), "mittel": round(statistics.mean(r_), 4),
                            "gewinner": round(sum(1 for x in r_ if x > 0) / len(r_), 2)}
            out["beispiele"] = [(sym[c], time.strftime("%Y-%m-%d", time.gmtime(t * 86400)), rank[(c, t)])
                                for c, t in sorted(events, key=lambda x: -x[1])[:15]]
            res[key] = out
    res["_info"] = {"coins": len(coins), "tage": len(alle_tage),
                    "von": time.strftime("%Y-%m-%d", time.gmtime(alle_tage[0] * 86400)),
                    "bis": time.strftime("%Y-%m-%d", time.gmtime(alle_tage[-1] * 86400)),
                    "hinweis": "Nur Coins, die HEUTE noch unter den ~750 groessten sind (Survivorship-Bias nach oben)."}
    save_json(RESULT, res)
    return res


if __name__ == "__main__":
    if not os.path.isdir("data"): raise SystemExit("Bitte im Solbot-Ordner starten (dort, wo data/ liegt).")
    h = download()
    r = analyse(h)
    print("\n===== ERGEBNIS =====")
    for k, v in r.items():
        if k.startswith("_"): print(k, v); continue
        print(f"\n{k}: {v['n']} Eintritte")
        for h_ in ("1d", "3d", "7d", "14d", "30d"):
            if h_ in v:
                x = v[h_]
                print(f"   nach {h_:>3}: Median {x['median']:.3f}x  im Plus {x['im_plus']:.0%}   (alle Top-Coins: {x['vergleich_alle_topN_median']})")
        for s, x in v.items():
            if s.startswith("sim"): print(f"   {s:<34} n={x['n']:<4} Ø {x['mittel']:+.1%}  Gewinner {x['gewinner']:.0%}")
    print("\nFertig. Ergebnis liegt in", RESULT)
