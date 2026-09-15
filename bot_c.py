"""Bot C v2 – "Post-Graduation Momentum" (Pump.fun -> Raydium/PumpSwap, Paper via echte Jupiter-Quotes).
Kauft NICHT auf der Bonding Curve, sondern ENTRY_MIN..ENTRY_MAX Minuten NACH der Migration – dann ist der
Token ueber Jupiter handelbar und der erste Dump der Curve-Kaeufer ist meist durch.

Datensammlung (wichtig): Fuer JEDEN migrierten Token (gekauft oder nicht) wird der Preis-/Liq-Pfad der ersten
PATH_HOURS Stunden in data/bot_c_paths.json gespeichert. analyze_c.py wertet damit aus, welches Einstiegs-
fenster und welche Haltezeit statistisch am besten waren -> ENTRY_MIN/ENTRY_MAX/Exits danach anpassen.

Codex-Budget: 1 Aufruf je POLL_MIN Minuten (Kandidaten). Kurse kommen von DexScreener (kostenlos)."""
import os, time
from common import *

CODEX_KEY = os.environ.get("CODEX_KEY")
CODEX_URL = "https://graph.codex.io/graphql"
SOLANA = 1399811149
POLL_MIN = 10                        # Codex-Abfrage hoechstens alle 10 Min (~4.300 Calls/Monat)
PATH_HOURS = 3                       # Pfad-Logging je Token nach Migration
# ---- Einstieg ----
ENTRY_MIN, ENTRY_MAX = 10, 40        # Minuten nach Migration (Startwert; nach analyze_c.py anpassen)
# v2.3 - kalibriert an 574 geloggten Pfaden (siehe analyze_c.py):
# Rug-Quote ohne Filter 38 %. ALLE 14 gekauften Rugs hatten > 189.000 $ Startliquiditaet -
# eine echte Pump.fun-Migration startet mit ~12-15k. Hohe Liquiditaet = kuenstlich aufgeblasen.
MIN_LIQ, MAX_LIQ = 15_000, 50_000      # Obergrenze ist der wichtigste Filter ueberhaupt
MAX_HOLDERS = 600                      # Rugs hatten im Median 1.674 Holder, Ueberlebende 277 (Fake-Holder)
MAX_INSIDER_STRICT = 2.0
MIN_BUYS_1H, MIN_UBUYS_1H = 20, 12
MAX_SELL_RATIO = 0.9
MAX_TOP10, MAX_BUNDLER, MAX_SNIPER, MAX_INSIDER = 35.0, 10.0, 15.0, MAX_INSIDER_STRICT
MIN_OBS = 2                          # mind. 2 eigene Pfad-Punkte, und der Preis darf zuletzt nicht gefallen sein
# ---- Position / Exits ----
POS_USD, MAX_POS = 30.0, 8
# Exits ebenfalls aus den Pfaddaten (rug-ehrlich gerechnet, inkl. Gebuehren):
# alt (Stop -30 %, TP 1.5x/halb) ergab EV -8 %; neu (enger Stop, hohes Ziel, ganz raus) ergab EV +5 %.
# Enger Stop hilft, weil die meisten Tokens nach dem Einstieg weiter fallen statt sich zu erholen.
TP1_X, TP1_FRAC = 3.0, 1.0           # bei 3x KOMPLETT raus (Teilverkauf war messbar schlechter)
TRAIL, STOP, MAX_HOLD_H = -0.25, -0.15, 3
COOLDOWN_D = 3

def codex(query, variables=None):
    if not CODEX_KEY: return None
    try:
        r = requests.post(CODEX_URL, json={"query": query, "variables": variables or {}},
                          headers={"Authorization": CODEX_KEY, "Content-Type": "application/json", **UA}, timeout=30)
        r.raise_for_status(); out = r.json()
        if out.get("errors"): print("codex:", str(out["errors"])[:200]); return None
        return out.get("data")
    except Exception as e:
        print("codex fehler:", e); return None

Q_MIGRATED = """
query($net: [Int!], $after: Int!) {
  filterTokens(
    filters: { network: $net, launchpadName: ["Pump.fun"], launchpadMigrated: true, createdAt: { gte: $after } }
    rankings: [{ attribute: volume1, direction: DESC }]
    limit: 100
  ) {
    results {
      liquidity volume1 buyCount1 sellCount1 uniqueBuys1 holders
      top10HoldersPercent bundlerHeldPercentage sniperHeldPercentage insiderHeldPercentage
      token { address symbol launchpad { migratedAt completedAt } }
    }
  }
}"""

def fnum(x, default=0.0):
    try: return float(x) if x is not None else default
    except Exception: return default

def fetch_migrated():
    d = codex(Q_MIGRATED, {"net": [SOLANA], "after": int(time.time() - 3 * 86400)})
    if not d: return []
    out = []
    for r in d["filterTokens"]["results"]:
        t = r["token"]; lp = t.get("launchpad") or {}
        mig = fnum(lp.get("migratedAt")) or fnum(lp.get("completedAt"))
        if not mig: continue
        out.append({"addr": t["address"], "sym": t.get("symbol") or "?", "mig": mig,
                    "liq": fnum(r.get("liquidity")), "buys1": int(fnum(r.get("buyCount1"))), "sells1": int(fnum(r.get("sellCount1"))),
                    "ubuys1": int(fnum(r.get("uniqueBuys1"))), "holders": int(fnum(r.get("holders"))),
                    "top10": fnum(r.get("top10HoldersPercent"), None), "bundler": fnum(r.get("bundlerHeldPercentage"), None),
                    "sniper": fnum(r.get("sniperHeldPercentage"), None), "insider": fnum(r.get("insiderHeldPercentage"), None)})
    return out

def batch_pairs(addrs):
    out = {}
    for k in range(0, len(addrs), 30):
        d = get(f"{DS}/latest/dex/tokens/{','.join(addrs[k:k+30])}") or {}
        for p in (d.get("pairs") or []):
            if p.get("chainId") != "solana": continue
            a = p["baseToken"]["address"]
            if a not in out or (p.get("liquidity") or {}).get("usd", 0) > (out[a].get("liquidity") or {}).get("usd", 0):
                out[a] = p
        time.sleep(1.1)
    return out

def quality(c):
    if c["liq"] < MIN_LIQ: return f"liq {c['liq']:.0f}"
    if c["liq"] > MAX_LIQ: return f"liq {c['liq']:.0f} zu hoch (fake?)"
    if c["holders"] > MAX_HOLDERS: return f"holders {c['holders']}"
    if c["buys1"] < MIN_BUYS_1H: return f"buys1 {c['buys1']}"
    if c["ubuys1"] < MIN_UBUYS_1H: return f"ubuys1 {c['ubuys1']}"
    if c["buys1"] and c["sells1"] / c["buys1"] > MAX_SELL_RATIO: return f"sell-ratio {c['sells1']/c['buys1']:.2f}"
    if c["top10"] is not None and c["top10"] > MAX_TOP10: return f"top10 {c['top10']:.0f}%"
    if c["bundler"] is not None and c["bundler"] > MAX_BUNDLER: return f"bundler {c['bundler']:.0f}%"
    if c["sniper"] is not None and c["sniper"] > MAX_SNIPER: return f"sniper {c['sniper']:.0f}%"
    if c["insider"] is not None and c["insider"] > MAX_INSIDER: return f"insider {c['insider']:.0f}%"
    return None

def main():
    pf = Paper("bot_c"); st = pf.s
    st["strategy"] = "post_graduation"; st["codex"] = bool(CODEX_KEY)
    for k in ("lev", "last_bar", "liquidations", "last_poll"): st.pop(k, None)
    now = time.time(); today = int(now // 86400)
    paths = load("bot_c_paths.json", {}); cooldown = load("bot_c_cooldown.json", {})
    meta = load("bot_c_meta.json", {"cands": [], "last_poll": 0})
    # 1) Kandidaten von Codex (max. alle POLL_MIN Minuten), sonst die vom letzten Mal weiterbeobachten
    if now - meta["last_poll"] >= POLL_MIN * 60 - 30:
        fresh = fetch_migrated()
        if fresh: meta["cands"] = fresh; meta["last_poll"] = now
    cands = {c["addr"]: c for c in meta["cands"]}
    for a, c in cands.items():
        paths.setdefault(a, {"sym": c["sym"], "mig": c["mig"], "pts": [], "q": {k: c[k] for k in ("top10", "bundler", "sniper", "insider", "holders")}})
    # 2) Kurse (DexScreener) fuer Kandidaten + Positionen; Pfade fortschreiben
    watch = [a for a, p in paths.items() if now - p["mig"] <= PATH_HOURS * 3600]
    pairs = batch_pairs(list(set(watch) | set(st["positions"].keys())))
    prices = {}
    for a, p in pairs.items():
        px = float(p.get("priceUsd") or 0); liq = (p.get("liquidity") or {}).get("usd") or 0
        if px > 0: prices[a] = px
        if a in paths and now - paths[a]["mig"] <= PATH_HOURS * 3600:
            paths[a]["pts"].append([round((now - paths[a]["mig"]) / 60, 1), px, liq])
    # 3) Positionen verwalten
    for a, pos in list(st["positions"].items()):
        px = prices.get(a)
        if not px: continue
        liq = (pairs[a].get("liquidity") or {}).get("usd") or 0
        pos["peak"] = max(pos.get("peak", px), px)
        x = px / pos["entry"]; held_h = held_seconds(pos) / 3600
        why = None
        if x <= 1 + STOP: why = "stop"
        elif x >= TP1_X and not pos.get("tp1"): pos["tp1"] = True; pf.sell(a, px, TP1_FRAC, liq, "tp1"); continue
        elif pos.get("tp1") and px / pos["peak"] - 1 <= TRAIL: why = "trail"
        elif held_h >= MAX_HOLD_H: why = "time"
        if why:
            pf.sell(a, px, 1.0, liq, why)
            if px < pos["entry"]: cooldown[a] = today + COOLDOWN_D
    # 4) Einstiege im Zeitfenster
    checks = []
    for a, c in cands.items():
        if a in st["positions"] or cooldown.get(a, 0) > today or len(st["positions"]) >= MAX_POS: continue
        age_min = (now - c["mig"]) / 60
        if not (ENTRY_MIN <= age_min <= ENTRY_MAX): continue
        why = quality(c)
        if why: checks.append((c["sym"], why)); continue
        pts = [p for p in paths.get(a, {}).get("pts", []) if p[1] > 0]
        if len(pts) < MIN_OBS: checks.append((c["sym"], f"beobachte ({len(pts)})")); continue
        if pts[-1][1] < pts[-2][1]: checks.append((c["sym"], "preis faellt")); continue
        px = prices.get(a); liq = (pairs.get(a, {}).get("liquidity") or {}).get("usd") or 0
        if not px or liq < MIN_LIQ: checks.append((c["sym"], "kein ds-kurs/liq")); continue
        ok, risks = rug_ok(a); time.sleep(1.1)
        if not ok:
            checks.append((c["sym"], "rugcheck"))
            if risks != ["rugcheck unavailable"]: cooldown[a] = today + COOLDOWN_D
            continue
        if st["cash"] < POS_USD + 2: break
        if pf.buy(c["sym"], a, px, POS_USD, liq, f"postgrad {age_min:.0f}min"):
            st["positions"][a]["mig"] = c["mig"]
            checks.append((c["sym"], f"GEKAUFT {age_min:.0f}min nach migration, liq {liq:.0f}"))
            append_jsonl("bot_c_signals.jsonl", {"t": now_iso(), "addr": a, "sym": c["sym"], "age_min": round(age_min, 1), "liq": liq, **{k: c[k] for k in ("buys1", "ubuys1", "holders", "top10", "bundler", "sniper", "insider")}})
    # 5) Pfade aelter als 7 Tage archivieren (analyze_c.py liest bot_c_paths.json UND das Archiv)
    for a in list(paths):
        if now - paths[a]["mig"] > 7 * 86400:
            append_jsonl("bot_c_paths_archive.jsonl", {"addr": a, **paths[a]}); del paths[a]
    save("bot_c_paths.json", paths); save("bot_c_meta.json", meta)
    save("bot_c_cooldown.json", {k: v for k, v in cooldown.items() if v > today})
    v = pf.mark(prices); pf.commit()
    print(f"Bot C [post-grad{'' if CODEX_KEY else ', ohne Codex'}]: equity {v:.2f} | cash {st['cash']:.2f} | positions {len(st['positions'])} | kandidaten {len(cands)} | pfade {len(paths)}")
    for sym, why in checks[:12]: print(f"   {sym:<10} {why}")

if __name__ == "__main__":
    main()
