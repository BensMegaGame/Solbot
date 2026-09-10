"""Bot B – langsame Strategie auf grossen Tokens. Handelt hoechstens einmal pro Tag.
Ausgefuehrt wird zu Solana-DEX-Preisen (DexScreener), Signale aus CoinGecko-Tageskerzen."""
import os, time, requests
from common import *
from strategies_b import STRATEGIES, exit_signal
from backtest_b import universe, history, POS_USD, MAX_POS

SEARCH_SYMBOL = {"BTC": "cbBTC", "ETH": "WETH"}   # so heissen sie auf Solana

def resolve(sym):
    """Findet den liquidesten Solana-Pool fuer ein Symbol und merkt sich die Adresse."""
    cache = load("bot_b_tokens.json", {})
    if sym in cache: return cache[sym]
    q = SEARCH_SYMBOL.get(sym, sym)
    d = get(f"{DS}/latest/dex/search", {"q": q}) or {}
    pairs = [p for p in (d.get("pairs") or []) if p.get("chainId") == "solana"
             and p["baseToken"]["symbol"].upper() == q.upper() and (p.get("liquidity") or {}).get("usd", 0) > 200_000]
    if not pairs: return None
    best = max(pairs, key=lambda p: p["liquidity"]["usd"])
    cache[sym] = best["baseToken"]["address"]; save("bot_b_tokens.json", cache)
    return cache[sym]

def main():
    cfg = load("strategy_b.json", None)
    pf = Paper("bot_b")
    today = now_iso()[:10]
    if pf.s.get("last_day") == today:
        print("Bot B: heute schon gelaufen"); return
    if not cfg:
        print("Bot B: keine Strategie festgelegt – erst Backtest laufen lassen"); pf.s["last_day"] = today; pf.commit(); return
    strat = cfg["strategy"]; fn = STRATEGIES[strat]
    prices = {}
    uni = load("universe_b.json", {}) or universe()
    for cid, sym in uni.items():
        addr = resolve(sym)
        if not addr: continue
        pair = solana_pair(addr)
        if not pair: continue
        px = float(pair.get("priceUsd") or 0); liq = (pair.get("liquidity") or {}).get("usd", 0)
        if not px: continue
        prices[addr] = px
        pos = pf.s["positions"].get(addr)
        if pos:
            pos["peak"] = max(pos.get("peak", px), px)
            held = (time.time() - time.mktime(time.strptime(pos["opened"], "%Y-%m-%dT%H:%M:%SZ"))) / 86400
            why = exit_signal(strat, pos["entry"], px, pos["peak"], held)
            if why: pf.sell(addr, px, 1.0, liq, why)
        elif len(pf.s["positions"]) < MAX_POS and pf.s["cash"] >= POS_USD + 1:
            h = history(cid, 60)
            if h and fn(h[0], h[1]) == "buy":
                pf.buy(sym, addr, px, POS_USD, liq, strat)
            time.sleep(2.5 if os.environ.get("COINGECKO_KEY") else 6)
        time.sleep(1.1)
    pf.s["last_day"] = today; pf.s["strategy"] = strat
    v = pf.mark(prices); pf.commit()
    print(f"Bot B [{strat}]: equity {v:.2f} | cash {pf.s['cash']:.2f} | positions {len(pf.s['positions'])}")

if __name__ == "__main__":
    main()
