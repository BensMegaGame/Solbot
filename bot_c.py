"""Bot C – Long/Short mit Hebel auf Jupiter Perps (SOL, ETH, BTC), Perp-Simulation. Einmal taeglich."""
import os, time
from common import *
from strategies_c import STRATEGIES, exit_signal, margin_for
from backtest_b import history
from backtest_c import universe, MAX_POS
from bot_b import resolve

def main():
    cfg = load("strategy_c.json", None)
    pf = PaperPerp("bot_c")
    today = now_iso()[:10]
    if pf.s.get("last_day") == today: print("Bot C: heute schon gelaufen"); return
    prices = {}
    uni = load("universe_c.json", {}) or universe()
    if not cfg:
        print("Bot C: keine Strategie festgelegt – erst Backtest laufen lassen")
    strat, lev = (cfg["strategy"], cfg.get("lev", 3)) if cfg else (None, 1)
    fn = STRATEGIES[strat] if strat else None
    for cid, sym in uni.items():
        addr = resolve(sym)
        if not addr: continue
        pair = solana_pair(addr)
        if not pair: continue
        px = float(pair.get("priceUsd") or 0)
        if not px: continue
        prices[addr] = px
        pos = pf.s["positions"].get(addr)
        if pos and fn:
            held = (time.time() - time.mktime(time.strptime(pos["opened"], "%Y-%m-%dT%H:%M:%SZ"))) / 86400
            peak = max(pos["peak"], px) if pos["side"] == "long" else min(pos["peak"], px)
            why = exit_signal(strat, pos["side"], pos["entry"], px, peak, held)
            if why: pf.close(addr, px, why)
        elif fn and len(pf.s["positions"]) < MAX_POS:
            h = history(cid, 60)
            side = fn(h[0], h[1]) if h else None
            if side:
                equity = pf.s["cash"] + sum(p["margin"] + pf.pnl(p, prices.get(k, p["entry"])) for k, p in pf.s["positions"].items())
                margin = margin_for(strat, lev, equity)
                if margin <= pf.s["cash"]: pf.open(sym, addr, side, px, margin, lev, strat)
            time.sleep(2.5 if os.environ.get("COINGECKO_KEY") else 6)
        time.sleep(1.1)
    pf.s["last_day"] = today; pf.s["strategy"] = strat; pf.s["lev"] = lev
    v = pf.mark(prices); pf.commit()
    print(f"Bot C [{strat} {lev}x]: equity {v:.2f} | cash {pf.s['cash']:.2f} | positions {len(pf.s['positions'])} | liquidations {pf.s['liquidations']}")

if __name__ == "__main__":
    main()
