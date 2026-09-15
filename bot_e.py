"""Bot E – "Overreaction Fade": kauft nach starken, aber (wahrscheinlich) nicht durch einen
Liquiditaets-Rugpull verursachten Kursstuerzen bei etablierten Solana-Tokens. Vier Stufen nach
Marktkapitalisierung, jede mit eigenem Zeitfenster, Drop-Spanne, Positionsgroesse und Zielen.

Sicherheitslogik (wichtig): DexScreener liefert die Kursaenderung (priceChange.m5/h1/h6) fertig,
aber KEINE historische Liquiditaet. Wir fuehren deshalb selbst eine kleine, rollierende Snapshot-
Historie pro beobachtetem Token (data/bot_e_liq.json), um zu pruefen, ob die Liquiditaet im selben
Fenster AEHNLICH STARK gefallen ist wie der Preis (= wahrscheinlich Rugpull, NICHT kaufen) oder
weitgehend intakt blieb (= wahrscheinlich Ueberreaktion/Panik, Kaufkandidat).
Ohne genug eigene Historie fuer ein Fenster wird die betroffene Stufe uebersprungen, nicht geraten.

Laeuft live 1:1 wie Bot A/B/D ueber Jupiter-Swaps (normale AMM-Pools, kein Sonderweg noetig)."""
import os, time
from common import *

CODEX_KEY = os.environ.get("CODEX_KEY")
CODEX_URL = "https://graph.codex.io/graphql"
SOLANA = 1399811149
# v3: eigener Codex-Call. Vorher kam das Universum zu grossen Teilen aus token-boosts/token-profiles -
# also aus BEZAHLTER Promotion, genau der Quelle, die bei Bot A das Problem war.
E_MIN_AGE_D = 2                 # "etabliert": mind. 48 h alt - E's These stand bisher nur im Text, nicht im Code
E_MIN_LIQ, E_MIN_VOL24 = 15_000, 20_000
E_MAX_LIQ_OVER_MCAP = 1.5       # Liquiditaet deutlich ueber Marktkapitalisierung = kuenstlich (Lehre aus Bot C)
DS_SEARCH_PAGES_GT = 8          # GeckoTerminal-Seiten (kostenlos, neutral) - ersetzt die Promo-Feeds
MAX_UNIVERSE = 500
DISCOVER_EVERY_S = 20 * 60      # Universum alle 20 Minuten neu zusammenstellen (alles kostenlos)
COOLDOWN_D = 10
MAX_POS_TOTAL, MAX_POS_PER_TIER = 8, 2
EXCLUDE_SYM = {"USDC", "USDT", "USDS", "PYUSD", "USD1", "DAI", "FDUSD", "USDE", "EURC", "USDG", "USDY",
               "ETH", "WETH", "BNB", "WBNB", "BTC", "WBTC", "CBBTC", "SOL", "WSOL", "XRP", "ADA", "DOGE",
               "AVAX", "MATIC", "POL", "DOT", "LINK", "LTC", "TRX", "TON", "SUI", "NEAR", "ATOM"}
EXCLUDE_SUB = ("USD", "EUR", "GBP", "CHF", "JPY", "XAU")

def tradeable(sym):
    sym = (sym or "").upper()
    if sym in EXCLUDE_SYM: return False
    return not any(s in sym for s in EXCLUDE_SUB)

# ---- Vier Stufen ----
# window: dexscreener-Feld (m5/h1/h6); drop: (min, max) als positive Prozentzahl (z.B. 0.40 = -40%)
# liq_max_drop: max. erlaubter Liquiditaetsrueckgang im selben Fenster (sonst: wahrscheinlich Rug)
# stop: zusaetzlicher harter Stop ab Einstieg; tp1_gain: Kursgewinn ab Einstieg, bei dem 80% verkauft werden
# moon_x: Ziel-Vielfaches ab Einstieg fuer die restlichen 20%; max_hold_h: Zeitstop in Stunden
TIERS = [
    # T1 lief mit dem 5-Minuten-Fenster nie an: -40 % in 5 Min bei 15-35k MCap plus passende
    # Liquiditaetshistorie kam in der gesamten Laufzeit kein einziges Mal vor. Jetzt 1-Stunden-Fenster.
    {"name": "T1", "mcap": (15_000, 35_000),   "window": "h1", "drop": (0.40, 0.50), "liq_max_drop": 0.25,
     "stop": 0.40, "usd": 25.0, "tp1_gain": 1.00, "moon_x": 5.0, "max_hold_h": 24, "hist_needed_s": 50 * 60},
    {"name": "T2", "mcap": (36_000, 60_000),   "window": "h1", "drop": (0.35, 0.45), "liq_max_drop": 0.20,
     "stop": 0.35, "usd": 40.0, "tp1_gain": 0.75, "moon_x": 5.0, "max_hold_h": 24, "hist_needed_s": 50 * 60},
    # T3 ueberarbeitet. Grund ist nicht nur die Bilanz, sondern die Konstruktion: ein Ruecksetzer von
    # 30-40 % in einer Stunde liegt bei 61-100k MCap noch im normalen Schwankungsbereich - das Fenster
    # fing also gewoehnliche Volatilitaet statt echter Panik. Jetzt tiefer angesetzt (selektiver),
    # strengerer Liquiditaetscheck, und die Positionsgroesse an die Risikostaffelung der anderen Stufen
    # angeglichen (T3 riskierte mit 60 $ bei -30 % mehr als T4 mit 80 $ bei -25 %).
    {"name": "T3", "mcap": (61_000, 100_000),  "window": "h1", "drop": (0.35, 0.45), "liq_max_drop": 0.12,
     "stop": 0.35, "usd": 45.0, "tp1_gain": 0.70, "moon_x": 3.0, "max_hold_h": 36, "hist_needed_s": 50 * 60},
    {"name": "T4", "mcap": (101_000, 300_000), "window": "h6", "drop": (0.25, 0.35), "liq_max_drop": 0.15,
     "stop": 0.25, "usd": 80.0, "tp1_gain": 0.40, "moon_x": 3.0, "max_hold_h": 48, "hist_needed_s": 5.5 * 3600},
]
TP1_FRAC = 0.8
# v3 - Exits proportional zur Stufe statt pauschal. Ein einheitliches Trailing haette die grossen Gewinner
# gekillt: WOW (T2) lief auf 5,8x, ein fester 25-%-Rueckschlag haette den Trade bei ~1,2x beendet.
TRAIL_ARM_FRAC = 0.5      # scharf ab der Haelfte des Stufenziels (T1 +50 %, T4 +20 %)
TRAIL_GIVEBACK_FRAC = 0.9 # Rueckgabe = 90 % des Stufen-Stops: gleiche Schwankungstoleranz wie beim
                          # Einstiegsstop, nur wandert der Bezugspunkt mit dem Hoechststand mit.
PEAK_CRASH_EXIT = -0.50   # harte Notbremse: mehr als 50 % unter den Hoechststand -> immer raus,
                          # auch vor dem Scharfstellen des Trailings und auch nach TP1.
GT = "https://api.geckoterminal.com/api/v2/networks/solana"
RC_HARD = ("mint", "freeze", "unlocked", "top 10", "single holder")

Q_E = """
query($net: [Int!], $before: Int!) {
  filterTokens(
    filters: { network: $net, createdAt: { lte: $before }, marketCap: { gte: %s, lte: %s },
               liquidity: { gte: %s }, volume24: { gte: %s } }
    rankings: [{ attribute: volume24, direction: DESC }]
    limit: 200
  ) { results { token { address } } }
}""" % (TIERS[0]["mcap"][0], TIERS[-1]["mcap"][1], E_MIN_LIQ, E_MIN_VOL24)

def codex_universe():
    """None = Fehler (bald erneut versuchen), Liste = Ergebnis. ~1.440 Calls/Monat bei 30-Min-Takt."""
    if not CODEX_KEY: return None
    try:
        r = requests.post(CODEX_URL, headers={"Authorization": CODEX_KEY, "Content-Type": "application/json", **UA},
                          json={"query": Q_E, "variables": {"net": [SOLANA], "before": int(time.time() - E_MIN_AGE_D * 86400)}}, timeout=30)
        r.raise_for_status(); d = r.json()
        if d.get("errors"): print("codex:", str(d["errors"])[:200]); return None
        return [x["token"]["address"] for x in d["data"]["filterTokens"]["results"]]
    except Exception as e:
        print("codex fehler:", e); return None

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

def discover():
    """Nur neutrale Quellen. Die DexScreener-Promo-Endpunkte (token-boosts/token-profiles) und
    bot_a_seen.json (das daraus entstand) sind bewusst NICHT mehr dabei."""
    addrs = set()
    cx = codex_universe()
    if cx is None: print("Bot E: Codex nicht erreichbar, nutze nur die kostenlosen Quellen")
    else: addrs |= set(cx)
    addrs |= set(load("bot_b_history.json", {}).keys())                      # Codex-gefiltert (Bot B)
    addrs |= {c["addr"] for c in load("bot_d_meta.json", {}).get("cands", []) if c.get("addr")}
    addrs |= set(load("bot_c_paths.json", {}).keys())                        # migrierte Tokens (Bot C)
    for pg in range(1, DS_SEARCH_PAGES_GT + 1):
        d = get(f"{GT}/pools?page={pg}&sort=h24_volume_usd_desc")
        for pool in (d or {}).get("data", []):
            base = (pool.get("relationships", {}).get("base_token", {}).get("data", {}).get("id") or "").replace("solana_", "")
            if base: addrs.add(base)
        time.sleep(1.1)
    return list(addrs)[:MAX_UNIVERSE]

def tier_for(mcap):
    for t in TIERS:
        if t["mcap"][0] <= mcap <= t["mcap"][1]: return t
    return None

def liq_change(points, now, window_s):
    """Sucht den aeltesten Punkt, der mind. window_s zurueckliegt (aber nicht mehr als 2x window_s,
    sonst zu ungenau). Gibt (liq_dann, gefunden) zurueck."""
    best = None
    for pt in points:
        t, liq = pt[0], pt[1]
        age = now - t
        if window_s * 0.85 <= age <= window_s * 2.2:
            if best is None or abs(age - window_s) < abs(now - best[0] - window_s): best = (t, liq)
    return (best[1], True) if best else (None, False)

def main():
    pf = Paper("bot_e"); st = pf.s
    today = int(time.time() // 86400); now = time.time()
    liq_hist = load("bot_e_liq.json", {})
    cooldown = load("bot_e_cooldown.json", {})
    meta = load("bot_e_meta.json", {})
    if now - meta.get("last_discover", 0) >= DISCOVER_EVERY_S or "universe" not in meta:
        meta["universe"] = discover(); meta["last_discover"] = now; save("bot_e_meta.json", meta)
    universe = list(set(meta.get("universe", [])) | set(st["positions"].keys()))
    pairs = batch_pairs(universe)
    prices = {}

    # 1) Snapshots (Liquiditaet) fuer ALLE beobachteten Tokens fortschreiben
    for a, p in pairs.items():
        liq = (p.get("liquidity") or {}).get("usd") or 0
        pts = liq_hist.setdefault(a, [])
        pts.append([now, liq, float(p.get("priceUsd") or 0)])
        liq_hist[a] = [x for x in pts if now - x[0] <= 7 * 3600][-100:]
    # Tokens, die aus dem Universum gefallen sind, wurden bisher nie geloescht -> Datei wuchs unbegrenzt
    for a in list(liq_hist):
        pts = liq_hist[a]
        if not pts or now - pts[-1][0] > 7 * 3600:
            if a not in st["positions"]: del liq_hist[a]

    # 2) Offene Positionen verwalten
    for a, pos in list(st["positions"].items()):
        p = pairs.get(a)
        if not p: continue
        px = float(p.get("priceUsd") or 0)
        if px <= 0: continue
        prices[a] = px; liq = (p.get("liquidity") or {}).get("usd") or 0
        pos["peak"] = max(pos.get("peak", px), px)
        x = px / pos["entry"] - 1
        held_h = held_seconds(pos) / 3600
        why = None
        peak_x = pos["peak"] / pos["entry"]
        from_peak = px / pos["peak"] - 1
        arm = 1 + pos["tp1_gain"] * TRAIL_ARM_FRAC        # z.B. T1 +50 %, T4 +20 %
        giveback = -pos["stop"] * TRAIL_GIVEBACK_FRAC     # z.B. T1 -36 %, T4 -22,5 %
        if from_peak <= PEAK_CRASH_EXIT and peak_x > 1:
            why = "peak-crash"                            # gilt in jeder Phase, auch nach TP1
        elif not pos.get("tp1"):
            if x <= -pos["stop"]: why = "stop"
            elif x >= pos["tp1_gain"]:
                pos["tp1"] = True; pf.sell(a, px, TP1_FRAC, liq, "tp1")
            elif peak_x >= arm and from_peak <= giveback: why = "trail"
            elif held_h >= pos["max_hold_h"]: why = "time"
        else:
            if px / pos["entry"] >= pos["moon_x"]: why = "moon"
            elif held_h >= pos["max_hold_h"]: why = "time"
        if why:
            pf.sell(a, px, 1.0, liq, why)
            if why == "stop": cooldown[a] = today + COOLDOWN_D

    # 3) Neue Kandidaten pruefen
    per_tier_open = {t["name"]: 0 for t in TIERS}
    for pos in st["positions"].values(): per_tier_open[pos.get("tier", "")] = per_tier_open.get(pos.get("tier", ""), 0) + 1
    checks = []
    for a, p in pairs.items():
        if a in st["positions"] or cooldown.get(a, 0) > today: continue
        if len(st["positions"]) >= MAX_POS_TOTAL: break
        sym = p["baseToken"]["symbol"]
        if not tradeable(sym): continue
        mcap = p.get("marketCap") or p.get("fdv") or 0
        tier = tier_for(mcap)
        if not tier: continue
        if per_tier_open.get(tier["name"], 0) >= MAX_POS_PER_TIER: continue
        pc = (p.get("priceChange") or {}).get(tier["window"])
        if pc is None: continue
        pc = float(pc)
        lo, hi = -tier["drop"][1] * 100, -tier["drop"][0] * 100   # z.B. -50 .. -40
        if not (lo <= pc <= hi): 
            checks.append((sym, tier["name"], f"chg {pc:.0f}%")); continue
        liq_now = (p.get("liquidity") or {}).get("usd") or 0
        if liq_now < E_MIN_LIQ: checks.append((sym, tier["name"], f"liq {liq_now:.0f}")); continue
        created = p.get("pairCreatedAt")
        if created and (now - created / 1000) < E_MIN_AGE_D * 86400:
            checks.append((sym, tier["name"], "zu jung")); continue
        if mcap > 0 and liq_now / mcap > E_MAX_LIQ_OVER_MCAP:
            checks.append((sym, tier["name"], f"liq/mcap {liq_now/mcap:.1f} (kuenstlich?)")); cooldown[a] = today + COOLDOWN_D; continue
        liq_then, found = liq_change(liq_hist.get(a, []), now, tier["hist_needed_s"])
        if not found: checks.append((sym, tier["name"], "liq-historie fehlt")); continue
        liq_drop = (liq_then - liq_now) / liq_then if liq_then > 0 else 1.0
        if liq_drop > tier["liq_max_drop"]:
            checks.append((sym, tier["name"], f"liq -{liq_drop*100:.0f}% (wahrsch. rug)")); cooldown[a] = today + COOLDOWN_D; continue
        px = float(p.get("priceUsd") or 0)
        if px <= 0: continue
        ok, risks = rug_ok(a); time.sleep(1.1)
        if not ok:
            checks.append((sym, tier["name"], "rugcheck"))
            if risks != ["rugcheck unavailable"]: cooldown[a] = today + COOLDOWN_D
            continue
        if st["cash"] < tier["usd"] + 2: continue
        if pf.buy(sym, a, px, tier["usd"], liq_now, f"{tier['name']} chg {pc:.0f}%"):
            pos = st["positions"][a]
            pos.update(tier=tier["name"], stop=tier["stop"], tp1_gain=tier["tp1_gain"], moon_x=tier["moon_x"], max_hold_h=tier["max_hold_h"])
            per_tier_open[tier["name"]] = per_tier_open.get(tier["name"], 0) + 1
            checks.append((sym, tier["name"], f"GEKAUFT chg {pc:.0f}% liq-drop {liq_drop*100:.0f}%"))
            append_jsonl("bot_e_signals.jsonl", {"t": now_iso(), "addr": a, "sym": sym, "tier": tier["name"], "chg": pc, "liq_drop": round(liq_drop, 3), "mcap": mcap})

    save("bot_e_liq.json", liq_hist); save("bot_e_cooldown.json", {k: v for k, v in cooldown.items() if v > today})
    st["strategy"] = "overreaction_fade"; st["universe_n"] = len(universe)
    v = pf.mark(prices); pf.commit()
    print(f"Bot E [overreaction]: equity {v:.2f} | cash {st['cash']:.2f} | positions {len(st['positions'])} | universum {len(universe)}")
    for sym, tname, why in checks[:12]: print(f"   {sym:<10} {tname:<3} {why}")

if __name__ == "__main__":
    main()
