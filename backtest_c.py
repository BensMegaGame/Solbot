"""Backtest Bot C: Long/Short mit Hebel auf Jupiter Perps (SOL, ETH, BTC).
Testet jede Strategie bei 3x, 5x, 10x mit dynamischer Positionsgroesse.
Schreibt data/backtest_c.json, waehlt ggf. data/strategy_c.json."""
import os, time
from common import *
from strategies_c import STRATEGIES, exit_signal, margin_for
from backtest_b import CG, KEY, history

MIN_DAYS = 300
LEVERAGES = (3, 5, 10)
MAX_POS = 3

def universe():
    """Genau die Maerkte, die Jupiter Perps anbietet."""
    out = {"solana": "SOL", "ethereum": "ETH", "bitcoin": "BTC"}
    save("universe_c.json", out)
    return out

def run(strategy, lev, data):
    fn = STRATEGIES[strategy]
    n = min(len(v[0]) for v in data.values())
    data = {k: (v[0][-n:], v[1][-n:]) for k, v in data.items()}
    cash, pos, trades, eq, liqs = START_CAPITAL, {}, [], [], 0
    for i in range(31, n):
        for sym in list(pos):
            p = pos[sym]; px = data[sym][0][i]
            p["peak"] = max(p["peak"], px) if p["side"] == "long" else min(p["peak"], px)
            p["funding"] += p["size"] * FUNDING_DAY
            d = (px / p["entry"] - 1) * (1 if p["side"] == "long" else -1)
            pnl = p["size"] * d - p["funding"]
            liq_hit = px <= p["liq"] if p["side"] == "long" else px >= p["liq"]
            if liq_hit or p["margin"] + pnl <= 0:
                net, why = 0.0, "liquidation"; liqs += 1
            else:
                why = exit_signal(strategy, p["side"], p["entry"], px, p["peak"], i - p["day"])
                if not why: continue
                fill = px * (1 - PERP_SLIP) if p["side"] == "long" else px * (1 + PERP_SLIP)
                d = (fill / p["entry"] - 1) * (1 if p["side"] == "long" else -1)
                net = max(p["margin"] + p["size"] * d - p["funding"] - p["size"] * PERP_FEE, 0.0)
            cash += net; trades.append({"sym": sym, "side": p["side"], "pnl": net - p["margin"], "days": i - p["day"], "why": why}); del pos[sym]
        for sym, (pr, vo) in data.items():
            if sym in pos or len(pos) >= MAX_POS: continue
            side = fn(pr[:i + 1], vo[:i + 1])
            if side in ("long", "short"):
                equity = cash + sum(p["margin"] for p in pos.values())
                margin = margin_for(strategy, lev, equity)
                if margin > cash: continue
                px = pr[i]; fill = px * (1 + PERP_SLIP) if side == "long" else px * (1 - PERP_SLIP)
                size = margin * lev; cash -= margin + size * PERP_FEE
                pos[sym] = {"side": side, "entry": fill, "size": size, "margin": margin, "liq": liq_price(fill, side, lev),
                            "day": i, "peak": fill, "funding": 0.0}
        eq.append(cash + sum(p["margin"] + p["size"] * ((data[s][0][i] / p["entry"] - 1) * (1 if p["side"] == "long" else -1)) - p["funding"] for s, p in pos.items()))
    final = eq[-1] if eq else START_CAPITAL
    peak, mdd = -1, 0
    for v in eq: peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    wins = [t for t in trades if t["pnl"] > 0]; total = sum(t["pnl"] for t in trades)
    top = max((t["pnl"] for t in trades), default=0)
    return {"strategy": strategy, "lev": lev, "return_pct": round((final / START_CAPITAL - 1) * 100, 1),
            "trades": len(trades), "longs": sum(t["side"] == "long" for t in trades), "shorts": sum(t["side"] == "short" for t in trades),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0, "max_drawdown_pct": round(mdd * 100, 1),
            "liquidations": liqs, "top_trade_share_pct": round(top / total * 100, 1) if total > 0 else None,
            "equity": [round(v, 2) for v in eq]}

def main():
    data = {}
    uni = universe(); print(f"Universum: {len(uni)} Tokens")
    for cid, sym in uni.items():
        h = history(cid)
        if h and len(h[0]) >= MIN_DAYS: data[sym] = h
        elif h: print(f"skip {sym}: nur {len(h[0])} Tage")
        time.sleep(2.5 if KEY else 6)
    save("universe_c.json", {c: s for c, s in uni.items() if s in data})
    if len(data) < 2: print("zu wenig Daten"); return
    results = []
    for s in STRATEGIES:
        for lev in LEVERAGES:
            r = run(s, lev, data)
            n = min(len(v[0]) for v in data.values()); half = n // 2
            d1 = {k: (v[0][-n:][:half], v[1][-n:][:half]) for k, v in data.items()}
            d2 = {k: (v[0][-n:][half:], v[1][-n:][half:]) for k, v in data.items()}
            h1, h2 = run(s, lev, d1), run(s, lev, d2)
            r.update(half1_pct=h1["return_pct"], half1_trades=h1["trades"], half2_pct=h2["return_pct"], half2_trades=h2["trades"])
            results.append(r)
    robust = lambda r: r["trades"] >= 12 and r["return_pct"] > 0 and r["half1_pct"] > 0 and r["half2_pct"] > 0 and r["liquidations"] == 0
    results.sort(key=lambda r: (robust(r), r["return_pct"] / max(1, -r["max_drawdown_pct"])), reverse=True)
    hold = {s: round((d[0][-1] / d[0][31] - 1) * 100, 1) for s, d in data.items()}
    save("backtest_c.json", {"run": now_iso(), "days": min(len(v[0]) for v in data.values()), "tokens": list(data),
                             "buy_and_hold_pct": hold, "results": results})
    cur = load("strategy_c.json", {})
    best = results[0]
    if cur.get("manual"): verdict = f"manuelle Wahl bleibt: {cur['strategy']} {cur.get('lev')}x"
    elif robust(best):
        save("strategy_c.json", {"strategy": best["strategy"], "lev": best["lev"], "chosen_at": now_iso(), "auto": True})
        verdict = f"gewaehlt: {best['strategy']} {best['lev']}x"
    else: verdict = "keine robuste Strategie (>=12 Trades, beide Haelften positiv, 0 Liquidationen) – Bot C bleibt im Sammelmodus"
    for r in results:
        print(f"{r['strategy']:<14} {r['lev']}x  {r['return_pct']:>7}%  trades {r['trades']:>3} (L{r['longs']}/S{r['shorts']})  win {r['win_rate']}%  mdd {r['max_drawdown_pct']}%  liq {r['liquidations']}  | H1 {r['half1_pct']}%  H2 {r['half2_pct']}%")
    print(verdict)

if __name__ == "__main__":
    main()
