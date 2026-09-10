"""Backtest fuer Bot B: 365 Tage, drei Strategien, gleiches Fee-Modell wie Paper-Bot.
Schreibt data/backtest_b.json und legt die beste Strategie in data/strategy_b.json ab."""
import time, os
from common import *
from strategies_b import STRATEGIES, exit_signal

CG = "https://api.coingecko.com/api/v3"
POS_USD, MAX_POS, SLIP = 125.0, 4, 0.005
KEY = os.environ.get("COINGECKO_KEY")
TOP_N, MIN_LIQ, MIN_DAYS = 50, 500_000, 300
EXCLUDE = {"WBTC", "TBTC", "WETH", "WSOL", "INF", "LST", "SPYX", "TSLAX", "NVDAX", "AAPLX", "MSTRX"}
EXCLUDE_SUB = ("USD", "EUR", "GBP", "CHF", "JPY", "XAU", "DAI", "SOL")   # Stables, Gold, LSTs (…SOL)

def tradeable(sym):
    if sym == "SOL" or sym == "CBBTC": return True
    if sym in EXCLUDE: return False
    return not any(s in sym for s in EXCLUDE_SUB)

def solana_liquidity(sym):
    """Liquiditaet des besten Solana-Pools laut DexScreener; 0 wenn keiner."""
    d = get(f"{DS}/latest/dex/search", {"q": sym}) or {}
    pairs = [p for p in (d.get("pairs") or []) if p.get("chainId") == "solana" and p["baseToken"]["symbol"].upper() == sym]
    return max([(p.get("liquidity") or {}).get("usd", 0) for p in pairs] or [0])

def universe():
    """Top-N Solana-Tokens nach Liquiditaet (ueber CoinGecko-Kategorie), ohne Stables/LSTs.
    Liefert {coingecko_id: symbol} und merkt sich die Liste in data/universe_b.json."""
    hdr = {"x-cg-demo-api-key": KEY} if KEY else {}
    out = {}
    for page in (1, 2):
        r = requests.get(f"{CG}/coins/markets", params={"vs_currency": "usd", "category": "solana-ecosystem",
                         "order": "volume_desc", "per_page": 100, "page": page}, headers={**UA, **hdr}, timeout=30)
        if r.status_code != 200:
            print("coingecko markets", r.status_code); break
        for c in r.json():
            sym = c["symbol"].upper()
            if not tradeable(sym) or sym in out.values(): continue
            if (c.get("total_volume") or 0) < MIN_LIQ: continue
            liq = solana_liquidity("CBBTC" if sym == "CBBTC" else sym)
            time.sleep(1.1)
            if liq < MIN_LIQ:
                print(f"skip {sym}: Solana-Liquiditaet {liq:,.0f}"); continue
            out[c["id"]] = sym
            if len(out) >= TOP_N: break
        if len(out) >= TOP_N: break
        time.sleep(3)
    if not out:  # Fallback, falls CoinGecko nicht antwortet
        out = load("universe_b.json", {}) or {"solana": "SOL", "jupiter-exchange-solana": "JUP", "jito-governance-token": "JTO",
               "pyth-network": "PYTH", "render-token": "RENDER", "raydium": "RAY", "kamino": "KMNO"}
    save("universe_b.json", out)
    return out

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
    data = {k: (v[0][-n:], v[1][-n:]) for k, v in data.items()}
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
    top = max((t["pnl"] for t in trades), default=0)
    total = sum(t["pnl"] for t in trades)
    return {"top_trade_pnl": round(top, 2), "top_trade_share_pct": round(top / total * 100, 1) if total > 0 else None,"strategy": strategy, "return_pct": round((final / START_CAPITAL - 1) * 100, 1),
            "trades": len(trades), "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
            "max_drawdown_pct": round(mdd * 100, 1),
            "avg_pnl": round(sum(t["pnl"] for t in trades) / len(trades), 2) if trades else 0,
            "equity": [round(v, 2) for v in eq]}

def main():
    data = {}
    uni = universe()
    print(f"Universum: {len(uni)} Tokens")
    for cid, sym in uni.items():
        h = history(cid)
        if h and len(h[0]) >= MIN_DAYS: data[sym] = h
        elif h: print(f"skip {sym}: nur {len(h[0])} Tage Historie")
        time.sleep(2.5 if KEY else 6)
    save("universe_b.json", {cid: sym for cid, sym in uni.items() if sym in data})
    if not data:
        print("keine Daten"); return
    hold = {s: round((d[0][-1] / d[0][31] - 1) * 100, 1) for s, d in data.items()}
    results = [run(s, data) for s in STRATEGIES]
    # Robustheit: erste vs. zweite Jahreshaelfte getrennt
    half = min(len(v[0]) for v in data.values()) // 2
    d1 = {k: (v[0][:half], v[1][:half]) for k, v in data.items()}
    d2 = {k: (v[0][half:], v[1][half:]) for k, v in data.items()}
    for r in results:
        h1, h2 = run(r["strategy"], d1), run(r["strategy"], d2)
        r["half1_pct"], r["half1_trades"] = h1["return_pct"], h1["trades"]
        r["half2_pct"], r["half2_trades"] = h2["return_pct"], h2["trades"]
    results.sort(key=lambda r: (r["trades"] >= 15, r["return_pct"]), reverse=True)
    out = {"run": now_iso(), "days": min(len(v[0]) for v in data.values()), "tokens": list(data),
           "buy_and_hold_pct": hold, "results": results}
    save("backtest_b.json", out)
    best = results[0]
    cur = load("strategy_b.json", {})
    if cur.get("manual"):
        verdict = f"manuelle Wahl bleibt: {cur['strategy']}"
    elif best["trades"] >= 15 and best["return_pct"] > 0:
        save("strategy_b.json", {"strategy": best["strategy"], "chosen_at": now_iso(), "auto": True})
        verdict = f"gewaehlt: {best['strategy']}"
    else:
        verdict = "keine Strategie erfuellt die Kriterien (>=15 Trades, positiv) – Bot B bleibt im Sammelmodus"
    for r in results:
        print(f"{r['strategy']:<16} return {r['return_pct']:>6}%  trades {r['trades']:>3}  win {r['win_rate']}%  mdd {r['max_drawdown_pct']}%"
              f"  | H1 {r['half1_pct']}% ({r['half1_trades']})  H2 {r['half2_pct']}% ({r['half2_trades']})")
    print("Buy&Hold:", hold); print(verdict)

if __name__ == "__main__":
    main()
