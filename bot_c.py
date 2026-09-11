"""Bot C – Long/Short mit Hebel auf Jupiter Perps (SOL, ETH, BTC), Perp-Simulation auf 4h-Kerzen.
Laeuft mit jedem Workflow-Lauf; handelt nur, wenn eine neue 4h-Kerze abgeschlossen ist."""
from common import *
from strategies_c import STRATEGIES, exit_signal, margin_for
from data_c import klines, funding, align_funding, BARS_PER_DAY
from backtest_c import universe, MAX_POS
from bot_b import resolve

def main():
    cfg = load("strategy_c.json", None)
    pf = PaperPerp("bot_c")
    uni = load("universe_c.json", {}) or universe()
    strat, lev, risk = (cfg["strategy"], cfg.get("lev", 3), cfg.get("risk_pct", 4) / 100) if cfg else (None, 1, 0.04)
    fn = STRATEGIES.get(strat) if strat else None
    prices = {}
    for cid, sym in uni.items():
        addr = resolve(sym)
        pair = solana_pair(addr) if addr else None
        bars = klines(sym, 45)
        if not bars: continue
        px = float((pair or {}).get("priceUsd") or bars[-1]["c"]); prices[addr or sym] = px
        key = addr or sym
        last_closed = bars[-2]                      # bars[-1] ist die laufende Kerze
        i = len(bars) - 2
        pos = pf.s["positions"].get(key)
        if pos and fn:
            held_bars = int((time.time() - time.mktime(time.strptime(pos["opened"], "%Y-%m-%dT%H:%M:%SZ"))) / 14400)
            peak = max(pos["peak"], px) if pos["side"] == "long" else min(pos["peak"], px)
            why = exit_signal(strat, pos["side"], pos["entry"], px, peak, held_bars, pos.get("stop", 0.05))
            if why: pf.close(key, px, why)
        elif fn and len(pf.s["positions"]) < MAX_POS:
            seen = pf.s.setdefault("last_bar", {})
            if seen.get(sym) == last_closed["t"]: continue     # diese Kerze schon ausgewertet
            seen[sym] = last_closed["t"]
            f = align_funding(bars, funding(sym, 45))
            sig = fn(bars[:-1], f[:-1], i)
            if sig:
                side, stop = sig
                equity = pf.s["cash"] + sum(p["margin"] + pf.pnl(p, prices.get(k, p["entry"])) for k, p in pf.s["positions"].items())
                margin = margin_for(risk, lev, equity, stop)
                if margin <= pf.s["cash"] and pf.open(sym, key, side, px, margin, lev, strat):
                    pf.s["positions"][key]["stop"] = stop
        time.sleep(1.1)
    pf.s["strategy"] = strat; pf.s["lev"] = lev
    v = pf.mark(prices); pf.commit()
    print(f"Bot C [{strat} {lev}x]: equity {v:.2f} | cash {pf.s['cash']:.2f} | positions {len(pf.s['positions'])} | liquidations {pf.s['liquidations']}")

if __name__ == "__main__":
    main()
