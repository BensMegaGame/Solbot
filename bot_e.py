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

DS_SEARCH_PAGES_GT = 3          # GeckoTerminal-Seiten fuer breiteres Universum (etablierte Tokens)
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
    {"name": "T1", "mcap": (15_000, 35_000),   "window": "m5", "drop": (0.40, 0.50), "liq_max_drop": 0.25,
     "stop": 0.40, "usd": 25.0, "tp1_gain": 1.00, "moon_x": 5.0, "max_hold_h": 24, "hist_needed_s": 4 * 60},
    {"name": "T2", "mcap": (36_000, 60_000),   "window": "h1", "drop": (0.35, 0.45), "liq_max_drop": 0.20,
     "stop": 0.35, "usd": 40.0, "tp1_gain": 0.75, "moon_x": 5.0, "max_hold_h": 24, "hist_needed_s": 50 * 60},
    {"name": "T3", "mcap": (61_000, 100_000),  "window": "h1", "drop": (0.30, 0.40), "liq_max_drop": 0.20,
     "stop": 0.30, "usd": 60.0, "tp1_gain": 0.65, "moon_x": 3.0, "max_hold_h": 36, "hist_needed_s": 50 * 60},
    {"name": "T4", "mcap": (101_000, 300_000), "window": "h6", "drop": (0.25, 0.35), "liq_max_drop": 0.15,
     "stop": 0.25, "usd": 80.0, "tp1_gain": 0.40, "moon_x": 3.0, "max_hold_h": 48, "hist_needed_s": 5.5 * 3600},
]
TP1_FRAC = 0.8
GT = "https://api.geckoterminal.com/api/v2/networks/solana"
RC_HARD = ("mint", "freeze", "unlocked", "top 10", "single holder")

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
    addrs = set()
    for src in ("bot_a_seen.json",):
        addrs |= set(load(src, {}).keys())
    addrs |= set(load("bot_b_history.json", {}).keys())
    for ep in ("/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"):
        for t in get(DS + ep) or []:
            if t.get("chainId") == "solana": addrs.add(t["tokenAddress"])
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
    for t, liq in points:
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
        pts.append([now, liq]); liq_hist[a] = [x for x in pts if now - x[0] <= 7 * 3600][-100:]

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
        if not pos.get("tp1"):
            if x <= -pos["stop"]: why = "stop"
            elif x >= pos["tp1_gain"]:
                pos["tp1"] = True; pf.sell(a, px, TP1_FRAC, liq, "tp1")
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
        if liq_now <= 0: continue
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
