"""Bot B v2 – "Zweite Welle": kleine Solana-Tokens, die Woche 1–2 ueberlebt haben und deren
Volumen und Kaeuferueberhang anhaltend wachsen. Handelt Spot ueber Jupiter (Paper-Simulation).

Ablauf pro Lauf (stuendlich):
  1. Beobachtungsliste pflegen: Kandidaten aus Bot A's Log + DexScreener, Alter 21–56 Tage.
  2. Fuer alle beobachteten Tokens einen Tages-Snapshot speichern (Preis, Volumen, Liquiditaet, Kaeufe/Verkaeufe).
  3. Offene Positionen verwalten (Teilverkauf, Trailing, Stop, These gebrochen, Zeitstop, Nachkauf).
  4. Einstiege pruefen – nur mit mind. 3 Tagen eigener Snapshot-Historie.
Alles ist in data/bot_b_history.json und data/bot_b_state.json nachvollziehbar."""
import time, os
from common import *

# ---------- Universum ----------
MIN_AGE_D, MAX_AGE_D   = 21, 56
MIN_MCAP, MAX_MCAP     = 1_000_000, 30_000_000
MIN_LIQ, MIN_LIQ_RATIO = 100_000, 0.04
MIN_VOL24              = 100_000
# ---------- Einstieg ----------
MIN_HISTORY_DAYS = 3          # eigene Snapshots, bevor gekauft wird
VOL_TREND_MIN    = 1.20       # 3d-Volumen vs. 3d davor (wenn >= 6 Tage Historie), sonst gegen Vortag
MIN_BUY_RATIO    = 0.52
MAX_ABOVE_AVG    = 0.60       # nicht kaufen, wenn Preis > 60 % ueber 7-Tage-Schnitt
# ---------- Position ----------
ENTRY_USD, ADD_USD, MAX_POS = 60.0, 40.0, 5
ADD_AT_X   = 1.25             # Nachkauf bei +25 %, wenn Volumen weiter steigt
TP1_X, TP1_FRAC = 2.0, 0.5
TRAIL, HARD_STOP, MAX_HOLD_D = -0.30, -0.35, 30
LIQ_DROP_EXIT, VOL_DROP_EXIT = -0.40, 0.50
# ---------- Schutz ----------
COOLDOWN_D, STREAK_HALVE = 14, 3

RC = "https://api.rugcheck.xyz/v1/tokens"
HARD_RISKS = ("mint", "freeze", "unlocked", "top 10", "single holder")
DAY = 86400

def day_key(): return int(time.time() // DAY)

# ---------- Datenbeschaffung ----------
def batch_pairs(addrs):
    """DexScreener liefert bis zu 30 Tokens je Aufruf. Gibt {addr: bester Solana-Pool} zurueck."""
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

def snapshot(p):
    tx = (p.get("txns") or {}).get("h24") or {}
    b, s = tx.get("buys", 0), tx.get("sells", 0)
    return {"d": day_key(), "t": now_iso(), "px": float(p.get("priceUsd") or 0),
            "vol": (p.get("volume") or {}).get("h24") or 0, "vol6": (p.get("volume") or {}).get("h6") or 0,
            "liq": (p.get("liquidity") or {}).get("usd") or 0, "mcap": p.get("marketCap") or p.get("fdv") or 0,
            "buy_ratio": b / (b + s) if b + s else 0,
            "age_d": (time.time() * 1000 - (p.get("pairCreatedAt") or time.time() * 1000)) / 86400000}

def rug_ok(addr):
    s = get(f"{RC}/{addr}/report/summary")
    if not s: return True, []
    risks = [r.get("name", "") for r in (s.get("risks") or [])]
    return not any(k in r.lower() for r in risks for k in HARD_RISKS), risks

def discover(hist):
    """Adressen: Bot A's Kandidatenlog (Alter passt in 3–8 Wochen), DexScreener-Profile/Boosts, bereits beobachtete."""
    addrs = set(hist.keys())
    seen = load("bot_a_seen.json", {})
    now = time.time()
    for a, first in seen.items():
        try: age = (now - time.mktime(time.strptime(first, "%Y-%m-%dT%H:%M:%SZ"))) / DAY
        except Exception: continue
        if MIN_AGE_D - 7 <= age <= MAX_AGE_D: addrs.add(a)     # etwas frueher beobachten, damit Historie da ist
    for ep in ("/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"):
        for t in get(DS + ep) or []:
            if t.get("chainId") == "solana": addrs.add(t["tokenAddress"])
    return list(addrs)

def resolve(sym):
    """(fuer Bot C) liquidester Solana-Pool eines Symbols."""
    cache = load("bot_b_tokens.json", {})
    if sym in cache: return cache[sym]
    q = {"BTC": "cbBTC", "ETH": "WETH"}.get(sym, sym)
    d = get(f"{DS}/latest/dex/search", {"q": q}) or {}
    pairs = [p for p in (d.get("pairs") or []) if p.get("chainId") == "solana"
             and p["baseToken"]["symbol"].upper() == q.upper() and (p.get("liquidity") or {}).get("usd", 0) > 200_000]
    if not pairs: return None
    best = max(pairs, key=lambda p: p["liquidity"]["usd"])
    cache[sym] = best["baseToken"]["address"]; save("bot_b_tokens.json", cache)
    return cache[sym]

# ---------- Signal-Logik ----------
def daily(rows):
    """Ein Snapshot je Tag (der letzte des Tages)."""
    by = {}
    for r in rows: by[r["d"]] = r
    return [by[k] for k in sorted(by)]

def entry_check(rows, cur):
    """Gibt (ok, grund) zurueck. rows = Tages-Snapshots (aelteste zuerst), cur = aktueller Snapshot."""
    if len(rows) < MIN_HISTORY_DAYS: return False, f"historie {len(rows)}d"
    if not (MIN_AGE_D <= cur["age_d"] <= MAX_AGE_D): return False, "alter"
    if not (MIN_MCAP <= cur["mcap"] <= MAX_MCAP): return False, "mcap"
    if cur["liq"] < MIN_LIQ or cur["liq"] / max(cur["mcap"], 1) < MIN_LIQ_RATIO: return False, "liquiditaet"
    if cur["vol"] < MIN_VOL24: return False, "volumen"
    # 1 Sicherheit: Liquiditaet faellt nicht
    if cur["liq"] < 0.85 * max(r["liq"] for r in rows[-3:]): return False, "liq faellt"
    # 2 Substanz: Volumen-Trend aus eigenen Snapshots
    vols = [r["vol"] for r in rows] + [cur["vol"]]
    if len(vols) >= 6: trend = sum(vols[-3:]) / 3 / max(sum(vols[-6:-3]) / 3, 1)
    else: trend = vols[-1] / max(vols[-2], 1)
    if trend < VOL_TREND_MIN: return False, f"vol-trend {trend:.2f}"
    # 3 Nachfrage: Kaeuferueberhang, kein Einbruch in den letzten 6h
    if cur["buy_ratio"] < MIN_BUY_RATIO: return False, f"buy-ratio {cur['buy_ratio']:.2f}"
    if cur["vol6"] * 4 < 0.5 * cur["vol"]: return False, "6h-volumen eingebrochen"
    # 4 Struktur: ueber dem Schnitt, aber nicht dem Spike hinterher
    avg = sum(r["px"] for r in rows[-7:]) / len(rows[-7:])
    if cur["px"] <= avg: return False, "unter 7d-schnitt"
    if cur["px"] > avg * (1 + MAX_ABOVE_AVG): return False, "zu weit ueber schnitt"
    return True, f"ok vol-trend {trend:.2f} buy {cur['buy_ratio']:.2f}"

def main():
    pf = Paper("bot_b")
    st = pf.s; st.setdefault("cooldown", {}); st.setdefault("streak", 0); st.setdefault("half_left", 0)
    hist = load("bot_b_history.json", {})
    addrs = discover(hist)
    pairs = batch_pairs(addrs)
    prices = {}
    today = day_key()
    # 2. Snapshots
    for a, p in pairs.items():
        s = snapshot(p)
        if s["px"] <= 0: continue
        rows = hist.setdefault(a, {"sym": p["baseToken"]["symbol"], "rows": []})["rows"]
        if rows and rows[-1]["d"] == today: rows[-1] = s
        else: rows.append(s)
        hist[a]["rows"] = rows[-70:]
        prices[a] = s["px"]
    # tote/zu alte Tokens aus der Beobachtung nehmen (nicht aus Positionen)
    for a in list(hist):
        r = hist[a]["rows"]
        if a in st["positions"]: continue
        if not r or r[-1]["age_d"] > MAX_AGE_D + 5 or (len(r) >= 3 and all(x["liq"] < 20_000 for x in r[-3:])): del hist[a]
    # 3. Positionen verwalten
    for a, pos in list(st["positions"].items()):
        p = pairs.get(a)
        if not p: continue
        cur = snapshot(p); px = cur["px"]; liq = cur["liq"]
        pos["peak"] = max(pos.get("peak", px), px)
        x = px / pos["entry"]
        held = (time.time() - time.mktime(time.strptime(pos["opened"], "%Y-%m-%dT%H:%M:%SZ"))) / DAY
        why = None
        if x <= 1 + HARD_STOP: why = "stop"
        elif liq < pos.get("entry_liq", liq) * (1 + LIQ_DROP_EXIT): why = "these: liq"
        elif held >= 3 and cur["vol"] < pos.get("entry_vol", cur["vol"]) * VOL_DROP_EXIT: why = "these: vol"
        elif x >= TP1_X and not pos.get("tp1"):
            pos["tp1"] = True; pf.sell(a, px, TP1_FRAC, liq, "tp1"); continue
        elif px / pos["peak"] - 1 <= TRAIL and x > 1: why = "trail"
        elif held >= MAX_HOLD_D: why = "time"
        if why:
            pnl_neg = px < pos["entry"]
            pf.sell(a, px, 1.0, liq, why)
            if why in ("stop", "these: liq", "these: vol") or pnl_neg:
                st["cooldown"][a] = today + COOLDOWN_D; st["streak"] += 1
                if st["streak"] >= STREAK_HALVE: st["half_left"] = 5; st["streak"] = 0
            else: st["streak"] = 0
            continue
        # Nachkauf: nur Gewinner, nur einmal, nur wenn Volumen weiter waechst
        if x >= ADD_AT_X and not pos.get("added") and cur["vol"] > pos.get("entry_vol", 0) and st["cash"] >= ADD_USD + 5:
            pos["added"] = True; pf.buy(pos["sym"], a, px, ADD_USD, liq, "add")
    # 4. Einstiege
    checks = []
    for a, p in pairs.items():
        if a in st["positions"] or len(st["positions"]) >= MAX_POS: continue
        if st["cooldown"].get(a, 0) > today: continue
        rows = daily(hist.get(a, {}).get("rows", []))
        cur = rows[-1] if rows else None
        if not cur: continue
        ok, why = entry_check(rows[:-1], cur)
        checks.append((p["baseToken"]["symbol"], why))
        if not ok: continue
        safe, risks = rug_ok(a); time.sleep(1.1)
        if not safe: st["cooldown"][a] = today + 30; continue
        size = ENTRY_USD * (0.5 if st["half_left"] > 0 else 1.0)
        if st["cash"] < size + 5: continue
        if pf.buy(p["baseToken"]["symbol"], a, cur["px"], size, cur["liq"], why):
            pos = st["positions"][a]; pos["entry_liq"] = cur["liq"]; pos["entry_vol"] = cur["vol"]
            if st["half_left"] > 0: st["half_left"] -= 1
            append_jsonl("bot_b_signals.jsonl", {"t": now_iso(), "addr": a, "sym": pos["sym"], "why": why, "risks": risks, **cur})
    save("bot_b_history.json", hist)
    st["watchlist"] = len(hist); st["strategy"] = "second_wave"
    v = pf.mark(prices); pf.commit()
    passed = [c for c in checks if c[1].startswith("ok")]
    print(f"Bot B [zweite Welle]: equity {v:.2f} | cash {st['cash']:.2f} | positions {len(st['positions'])} | beobachtet {len(hist)} | signale {len(passed)}")
    for sym, why in checks[:15]: print(f"   {sym:<10} {why}")

if __name__ == "__main__":
    main()
