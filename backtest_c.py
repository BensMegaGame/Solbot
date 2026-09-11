"""Backtest Bot C: Long/Short mit Hebel auf Jupiter Perps (SOL, ETH, BTC), 4h-Kerzen + Funding.
Testet Strategien x Hebel (3/5/10) x Risiko je Trade (4 %/8 %). Schreibt data/backtest_c.json."""
from common import *
from strategies_c import STRATEGIES, exit_signal, margin_for, CONTEXT
from data_c import klines, funding, align_funding, BARS_PER_DAY

LEVERAGES, RISKS, MAX_POS, DAYS = (3, 5), (0.04, 0.08), 3, 365
FUNDING_BAR = FUNDING_DAY / BARS_PER_DAY

def universe():
    out = {"solana": "SOL", "ethereum": "ETH", "bitcoin": "BTC"}
    save("universe_c.json", out); return out

def run(strategy, lev, risk, data, start=0, end=None):
    fn = STRATEGIES[strategy]
    n = min(len(d["bars"]) for d in data.values()); end = n if end is None else end
    cash, pos, trades, eq, liqs = START_CAPITAL, {}, [], [], 0
    for i in range(max(start, 200), end):
        for sym in list(pos):
            p = pos[sym]; b = data[sym]["bars"][i]; px = b["c"]
            # Intra-Kerze: Liquidation/Stop zuerst am Hoch/Tief pruefen
            worst = b["l"] if p["side"] == "long" else b["h"]
            p["peak"] = max(p["peak"], b["h"]) if p["side"] == "long" else min(p["peak"], b["l"])
            p["funding"] += p["size"] * FUNDING_BAR
            liq_hit = worst <= p["liq"] if p["side"] == "long" else worst >= p["liq"]
            if liq_hit: net, why = 0.0, "liquidation"; liqs += 1
            else:
                why = exit_signal(strategy, p["side"], p["entry"], worst, p["peak"], i - p["bar"], p["stop"])
                if why == "stop": fill = p["entry"] * (1 - p["stop"]) if p["side"] == "long" else p["entry"] * (1 + p["stop"])
                else:
                    why = exit_signal(strategy, p["side"], p["entry"], px, p["peak"], i - p["bar"], p["stop"])
                    if not why: continue
                    fill = px
                fill = fill * (1 - PERP_SLIP) if p["side"] == "long" else fill * (1 + PERP_SLIP)
                d = (fill / p["entry"] - 1) * (1 if p["side"] == "long" else -1)
                net = max(p["margin"] + p["size"] * d - p["funding"] - p["size"] * PERP_FEE, 0.0)
            cash += net; trades.append({"sym": sym, "side": p["side"], "pnl": net - p["margin"], "bars": i - p["bar"], "why": why}); del pos[sym]
        CONTEXT["data"] = data
        for sym, d in data.items():
            if sym in pos or len(pos) >= MAX_POS: continue
            CONTEXT["sym"] = sym
            sig = fn(d["bars"], d["fund"], i)
            if not sig: continue
            side, stop = sig
            equity = cash + sum(p["margin"] for p in pos.values())
            margin = margin_for(risk, lev, equity, stop)
            if margin > cash: continue
            px = d["bars"][i]["c"]; fill = px * (1 + PERP_SLIP) if side == "long" else px * (1 - PERP_SLIP)
            size = margin * lev; cash -= margin + size * PERP_FEE
            pos[sym] = {"side": side, "entry": fill, "size": size, "margin": margin, "liq": liq_price(fill, side, lev),
                        "bar": i, "peak": fill, "funding": 0.0, "stop": stop}
        eq.append(cash + sum(p["margin"] + p["size"] * ((data[s]["bars"][i]["c"] / p["entry"] - 1) * (1 if p["side"] == "long" else -1)) - p["funding"] for s, p in pos.items()))
    final = eq[-1] if eq else START_CAPITAL
    peak, mdd = -1, 0
    for v in eq: peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    wins = [t for t in trades if t["pnl"] > 0]; total = sum(t["pnl"] for t in trades); top = max((t["pnl"] for t in trades), default=0)
    return {"strategy": strategy, "lev": lev, "risk_pct": int(risk * 100), "return_pct": round((final / START_CAPITAL - 1) * 100, 1),
            "trades": len(trades), "longs": sum(t["side"] == "long" for t in trades), "shorts": sum(t["side"] == "short" for t in trades),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0, "max_drawdown_pct": round(mdd * 100, 1),
            "liquidations": liqs, "top_trade_share_pct": round(top / total * 100, 1) if total > 0 else None,
            "avg_hold_days": round(sum(t["bars"] for t in trades) / len(trades) / BARS_PER_DAY, 1) if trades else 0,
            "equity": [round(v, 2) for v in eq[::BARS_PER_DAY]]}

def load_data():
    data = {}
    for sym in universe().values():
        bars = klines(sym, DAYS)
        if not bars: print("keine Kerzen", sym); continue
        f = funding(sym, DAYS)
        data[sym] = {"bars": bars, "fund": align_funding(bars, f)}
        print(f"{sym}: {len(bars)} Kerzen, {len(f)} Funding-Werte")
    n = min(len(d["bars"]) for d in data.values())
    for d in data.values(): d["bars"], d["fund"] = d["bars"][-n:], d["fund"][-n:]
    return data

def main():
    data = load_data()
    if len(data) < 2: print("zu wenig Daten"); return
    n = min(len(d["bars"]) for d in data.values()); half = n // 2
    results = []
    for s in STRATEGIES:
        for lev in LEVERAGES:
            for risk in RISKS:
                r = run(s, lev, risk, data)
                h1, h2 = run(s, lev, risk, data, 0, half), run(s, lev, risk, data, half - 200, n)
                r.update(half1_pct=h1["return_pct"], half1_trades=h1["trades"], half2_pct=h2["return_pct"], half2_trades=h2["trades"])
                results.append(r)
    robust = lambda r: r["trades"] >= 12 and r["return_pct"] > 0 and r["half1_pct"] > 0 and r["half2_pct"] > 0 and r["liquidations"] == 0
    results.sort(key=lambda r: (robust(r), r["return_pct"] / max(1, -r["max_drawdown_pct"])), reverse=True)
    hold = {s: round((d["bars"][-1]["c"] / d["bars"][200]["c"] - 1) * 100, 1) for s, d in data.items()}
    save("backtest_c.json", {"run": now_iso(), "days": round(n / BARS_PER_DAY), "bars": n, "tokens": list(data),
                             "buy_and_hold_pct": hold, "results": results})
    cur = load("strategy_c.json", {}); best = results[0]
    if cur.get("manual"): verdict = f"manuelle Wahl bleibt: {cur['strategy']} {cur.get('lev')}x"
    elif robust(best):
        save("strategy_c.json", {"strategy": best["strategy"], "lev": best["lev"], "risk_pct": best["risk_pct"], "chosen_at": now_iso(), "auto": True})
        verdict = f"gewaehlt: {best['strategy']} {best['lev']}x risk {best['risk_pct']}%"
    else: verdict = "keine robuste Strategie (>=12 Trades, beide Haelften positiv, 0 Liquidationen) – Bot C bleibt im Sammelmodus"
    for r in results:
        print(f"{r['strategy']:<18} {r['lev']:>2}x r{r['risk_pct']}%  {r['return_pct']:>7}%  n {r['trades']:>3} (L{r['longs']}/S{r['shorts']})  win {r['win_rate']}%  mdd {r['max_drawdown_pct']}%  liq {r['liquidations']}  hold {r['avg_hold_days']}d  | H1 {r['half1_pct']}%  H2 {r['half2_pct']}%")
    print("Buy&Hold:", hold); print(verdict)

if __name__ == "__main__":
    main()
