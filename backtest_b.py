"""Backtest fuer Bot B: 365 Tage, drei Strategien, gleiches Fee-Modell wie Paper-Bot.
Schreibt data/backtest_b.json und legt die beste Strategie in data/strategy_b.json ab."""
import time, os
from common import *
from strategies_b import STRATEGIES, exit_signal

UNIVERSE = {"solana": "SOL", "jupiter-exchange-solana": "JUP", "jito-governance-token": "JTO",
            "pyth-network": "PYTH", "render-token": "RENDER", "raydium": "RAY", "kamino": "KMNO",
            "bitcoin": "BTC", "ethereum": "ETH"}
CG = "https://api.coingecko.com/api/v3"
POS_USD, MAX_POS, SLIP = 125.0, 4, 0.005
KEY = os.environ.get("COINGECKO_KEY")

def history(cid, days=365):
    hdr = {"x-cg-demo-api-key": KEY} if KEY else {}
    r = requests.get(f"{CG}/coins/{cid}/market_chart", params={"vs_currency": "usd", "days": days, "interval": "daily"},
                     headers={**UA, **hdr}, timeout=30)
    if r.status_code != 200:
        print("coingecko", cid, r.status_code); return None
    d = r.json()
    return [x[1] for x in d["prices"]], [x[1] for x in d["total_volumes"]]

def run(strategy, data):
    fn = STRATEGIES[strategy]
    n = min(len(v[0]) for v in data.values())
    cash, pos, trades, eq = START_CAPITAL, {}, [], []
    for i in range(31, n):
        # Ausstiege
        for sym in list(pos):
            p = pos[sym]; px = data[sym][0][i]; p["peak"] = max(p["peak"], px)
            why = exit_signal(strategy, p["entry"], px, p["peak"], i - p["day"])
            if why:
                net = p["qty"] * px * (1 - SWAP_FEE - SLIP) - GAS_USD
                cash += net; trades.append({"sym": sym, "pnl": net - p["cost"], "days": i - p["day"], "why": why}); del pos[sym]
        # Einstiege
        for sym, (pr, vo) in data.items():
            if sym in pos or len(pos) >= MAX_POS or cash < POS_USD: continue
            if fn(pr[:i + 1], vo[:i + 1]) == "buy":
                px = pr[i]; cost = POS_USD; qty = POS_USD * (1 - SWAP_FEE - SLIP) / px
                cash -= cost + GAS_USD; pos[sym] = {"entry": px, "qty": qty, "cost": cost + GAS_USD, "day": i, "peak": px}
        eq.append(cash + sum(p["qty"] * data[s][0][i] for s, p in pos.items()))
    # offene Positionen zum Schluss bewerten
    final = eq[-1] if eq else START_CAPITAL
    peak, mdd = -1, 0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    wins = [t for t in trades if t["pnl"] > 0]
    return {"strategy": strategy, "return_pct": round((final / START_CAPITAL - 1) * 100, 1),
            "trades": len(trades), "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
            "max_drawdown_pct": round(mdd * 100, 1),
            "avg_pnl": round(sum(t["pnl"] for t in trades) / len(trades), 2) if trades else 0,
            "equity": [round(v, 2) for v in eq]}

def main():
    data = {}
    for cid, sym in UNIVERSE.items():
        h = history(cid)
        if h and len(h[0]) > 60: data[sym] = h
        time.sleep(3)
    if not data:
        print("keine Daten"); return
    hold = {s: round((d[0][-1] / d[0][31] - 1) * 100, 1) for s, d in data.items()}
    results = [run(s, data) for s in STRATEGIES]
    results.sort(key=lambda r: (r["trades"] >= 15, r["return_pct"]), reverse=True)
    out = {"run": now_iso(), "days": min(len(v[0]) for v in data.values()), "tokens": list(data),
           "buy_and_hold_pct": hold, "results": results}
    save("backtest_b.json", out)
    best = results[0]
    if best["trades"] >= 15 and best["return_pct"] > 0:
        save("strategy_b.json", {"strategy": best["strategy"], "chosen_at": now_iso(), "auto": True})
        verdict = f"gewaehlt: {best['strategy']}"
    else:
        verdict = "keine Strategie erfuellt die Kriterien (>=15 Trades, positiv) – Bot B bleibt im Sammelmodus"
    for r in results:
        print(f"{r['strategy']:<14} return {r['return_pct']:>6}%  trades {r['trades']:>3}  win {r['win_rate']}%  mdd {r['max_drawdown_pct']}%")
    print("Buy&Hold:", hold); print(verdict)

if __name__ == "__main__":
    main()
