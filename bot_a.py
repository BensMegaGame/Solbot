"""Bot A – Micro-Cap-Lotterie (Solana). Einstieg per Screener, feste Ausstiegsregeln."""
import time
from common import *

# Einstieg
MIN_AGE_H, MAX_AGE_H = 48, 14 * 24
MIN_LIQ, MIN_MCAP, MAX_MCAP, MIN_VOL = 30_000, 100_000, 3_000_000, 100_000
MIN_LIQ_RATIO, MIN_BUY_RATIO = 0.05, 0.45
# Positionen
POS_USD, MAX_POS = 50.0, 8          # 50 $ pro Ticket, max. 8 offene
# Ausstieg
TP1_X, TP1_FRAC = 3.0, 0.5          # bei 3x die Haelfte raus
TP2_X = 10.0                        # Rest bei 10x
STOP = -0.5                         # oder -50 %
TRAIL = -0.35                       # nach TP1: Rest raus wenn 35 % unter Hoch
MAX_HOLD_D = 10                     # Zeitstop

RC = "https://api.rugcheck.xyz/v1/tokens"
HARD_RISKS = ("mint", "freeze", "unlocked", "top 10", "single holder")

def candidates():
    addrs = set()
    for ep in ("/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"):
        for t in get(DS + ep) or []:
            if t.get("chainId") == "solana": addrs.add(t["tokenAddress"])
    return list(addrs)

def metrics(p):
    now = time.time() * 1000
    age_h = (now - (p.get("pairCreatedAt") or now)) / 3.6e6
    liq = (p.get("liquidity") or {}).get("usd") or 0
    mcap = p.get("marketCap") or p.get("fdv") or 0
    vol = (p.get("volume") or {}).get("h24") or 0
    tx = (p.get("txns") or {}).get("h24") or {}
    b, s = tx.get("buys", 0), tx.get("sells", 0)
    return dict(age_h=age_h, liq=liq, mcap=mcap, vol=vol, buy_ratio=b / (b + s) if b + s else 0,
                price=float(p.get("priceUsd") or 0))

def passes(m):
    return (MIN_AGE_H <= m["age_h"] <= MAX_AGE_H and m["liq"] >= MIN_LIQ and MIN_MCAP <= m["mcap"] <= MAX_MCAP
            and m["vol"] >= MIN_VOL and m["liq"] / max(m["mcap"], 1) >= MIN_LIQ_RATIO and m["buy_ratio"] >= MIN_BUY_RATIO
            and m["price"] > 0)

def rug_ok(addr):
    s = get(f"{RC}/{addr}/report/summary")
    if not s: return True, []
    risks = [r.get("name", "") for r in (s.get("risks") or [])]
    return not any(k in r.lower() for r in risks for k in HARD_RISKS), risks

def main():
    pf = Paper("bot_a")
    prices = {}
    # 1) offene Positionen verwalten
    for addr, pos in list(pf.s["positions"].items()):
        p = solana_pair(addr)
        if not p: continue
        m = metrics(p); px = m["price"]; prices[addr] = px
        pos["peak"] = max(pos.get("peak", px), px)
        x = px / pos["entry"]; held_d = (time.time() - time.mktime(time.strptime(pos["opened"], "%Y-%m-%dT%H:%M:%SZ"))) / 86400
        if x <= 1 + STOP: pf.sell(addr, px, 1.0, m["liq"], "stop")
        elif x >= TP2_X: pf.sell(addr, px, 1.0, m["liq"], "tp2")
        elif x >= TP1_X and not pos.get("tp1"): pos["tp1"] = True; pf.sell(addr, px, TP1_FRAC, m["liq"], "tp1")
        elif pos.get("tp1") and px / pos["peak"] - 1 <= TRAIL: pf.sell(addr, px, 1.0, m["liq"], "trail")
        elif held_d >= MAX_HOLD_D: pf.sell(addr, px, 1.0, m["liq"], "time")
        time.sleep(1.1)
    # 2) neue Kandidaten
    seen = load("bot_a_seen.json", {})
    for addr in candidates():
        if addr in pf.s["positions"] or len(pf.s["positions"]) >= MAX_POS: continue
        p = solana_pair(addr)
        if not p: continue
        m = metrics(p)
        rec = {"t": now_iso(), "addr": addr, "sym": p["baseToken"]["symbol"], **{k: round(v, 4) for k, v in m.items()}}
        if addr not in seen:                        # alle Kandidaten mitschreiben (fuer spaeteren Backtest)
            seen[addr] = rec["t"]; append_jsonl("bot_a_candidates.jsonl", rec)
        if passes(m):
            ok, risks = rug_ok(addr)
            rec["rug_ok"] = ok; rec["risks"] = risks
            append_jsonl("bot_a_signals.jsonl", rec)
            if ok and pf.s["cash"] >= POS_USD + 5:
                pf.buy(rec["sym"], addr, m["price"], POS_USD, m["liq"], "screener")
                prices[addr] = m["price"]
        time.sleep(1.1)
    save("bot_a_seen.json", seen)
    v = pf.mark(prices); pf.commit()
    print(f"Bot A: equity {v:.2f} | cash {pf.s['cash']:.2f} | positions {len(pf.s['positions'])}")

if __name__ == "__main__":
    main()
