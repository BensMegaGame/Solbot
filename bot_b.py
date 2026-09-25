"""Bot B v3 – "A nur pump.fun": exakt die Regeln von Bot A, aber es werden NUR pump.fun-Tokens gekauft
(Mint-Adresse endet auf "pump"). Test, ob die Auffaelligkeit aus Bot A traegt.

WARUM: In Bot As ersten 48 abgeschlossenen Positionen brachten die 18 pump.fun-Tokens +363 $ (44 % Gewinner),
die 30 anderen -194 $ (20 % Gewinner) - und das in beiden Zeithaelften (+189/-82 $ und +174/-112 $).
Bei n=48 kann das Zufall sein. Bot B misst es jetzt vorwaerts: gleiche Kandidatenquelle, gleiche Filter,
gleiche Groesse, gleiche Ausstiege wie Bot A - der einzige Unterschied ist der pump.fun-Filter.
Liegt B nach ~50 Positionen klar vor A, ist die Auffaelligkeit echt; sonst war es Zufall.

Die alte Strategie "Zweite Welle" (v2.8) ist eingestellt: -35 %, 17 von 29 Verkaeufen per Stop. Ihre eigenen
Daten (98.000 stuendliche Zeilen, 308 Tokens) zeigen, dass 7-56 Tage alte Micro-Caps im Median fallen
(-4 bis -7 % in 24-72 h); mit ihren Ausstiegen haette ein beliebiger Kauf -9,5 % je Trade gebracht.
Alter Stand und alle Daten bleiben erhalten (data/archive/, data/bot_b_shadow.jsonl, data/bot_b_history.json).
"""
import os, time
from common import *

VERSION = "b3"

# ---- identisch mit Bot A ----
MIN_AGE_H, MAX_AGE_H = 48, 14 * 24
MIN_LIQ, MIN_MCAP, MAX_MCAP, MIN_VOL = 30_000, 100_000, 3_000_000, 100_000
MIN_LIQ_RATIO, MIN_BUY_RATIO = 0.05, 0.45
POS_USD, MAX_POS = 50.0, 8
USE_TP0 = True
TP0_X, TP0_FRAC = 1.8, 0.25
TP1_X, TP1_FRAC = 3.0, 0.5
TP2_X = 10.0
STOP = -0.5
TRAIL = -0.35
MAX_HOLD_D = 10
COOLDOWN_D = 14

# ---- der einzige Unterschied ----
def ist_pumpfun(addr): return (addr or "").endswith("pump")


def candidates():
    addrs = set()
    for ep in ("/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"):
        for t in get(DS + ep) or []:
            if t.get("chainId") == "solana" and ist_pumpfun(t.get("tokenAddress")): addrs.add(t["tokenAddress"])
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


def versionswechsel():
    """Alte Strategie -> Zustand archivieren und mit 500 $ neu beginnen. Datensammlungen bleiben liegen."""
    alt = load("bot_b_state.json", None)
    if not alt or alt.get("version") == VERSION: return
    os.makedirs(os.path.join(DATA, "archive"), exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M", time.gmtime())
    for name in ("bot_b_state.json", "bot_b_meta.json"):
        p = os.path.join(DATA, name)
        if os.path.exists(p): os.replace(p, os.path.join(DATA, "archive", f"{name[:-5]}_v2_{stamp}.json"))
    print(f"Bot B: Strategiewechsel auf {VERSION} - alter Stand archiviert, Neustart mit {START_CAPITAL:.0f} $")


def main():
    versionswechsel()
    pf = Paper("bot_b"); pf.s["version"] = VERSION; pf.s["strategy"] = "a_pumpfun"
    prices = {}
    today = int(time.time() // 86400)
    cooldown = load("bot_b_cooldown.json", {})
    # 1) offene Positionen verwalten (wie Bot A)
    for addr, pos in list(pf.s["positions"].items()):
        p = solana_pair(addr)
        if not p: continue
        m = metrics(p); px = m["price"]; prices[addr] = px
        pos["peak"] = max(pos.get("peak", px), px)
        x = px / pos["entry"]; held_d = held_seconds(pos) / 86400
        why = None
        if x <= 1 + STOP: why = "stop"
        elif x >= TP2_X: why = "tp2"
        elif x >= TP1_X and not pos.get("tp1"): pos["tp1"] = True; pf.sell(addr, px, TP1_FRAC, m["liq"], "tp1")
        elif USE_TP0 and x >= TP0_X and not pos.get("tp0"): pos["tp0"] = True; pf.sell(addr, px, TP0_FRAC, m["liq"], "tp0")
        elif pos.get("tp1") and px / pos["peak"] - 1 <= TRAIL: why = "trail"
        elif held_d >= MAX_HOLD_D: why = "time"
        if why:
            pf.sell(addr, px, 1.0, m["liq"], why)
            if why in ("stop", "trail") or px < pos["entry"]:
                cooldown[addr] = today + COOLDOWN_D
        time.sleep(1.1)
    # 2) neue Kandidaten - nur pump.fun
    kand = candidates()
    for addr in kand:
        if addr in pf.s["positions"] or len(pf.s["positions"]) >= MAX_POS: continue
        if cooldown.get(addr, 0) > today: continue
        p = solana_pair(addr)
        if not p: continue
        m = metrics(p)
        if passes(m):
            ok, risks = rug_ok(addr)
            if ok and pf.s["cash"] >= POS_USD + 5:
                sym = p["baseToken"]["symbol"]
                if pf.buy(sym, addr, m["price"], POS_USD, m["liq"], "screener pump.fun"):
                    prices[addr] = m["price"]
                    append_jsonl("bot_b_v3_signals.jsonl", {"t": now_iso(), "addr": addr, "sym": sym, "risks": risks,
                                                            **{k: round(v, 4) for k, v in m.items()}})
        time.sleep(1.1)
    save("bot_b_cooldown.json", {k: v for k, v in cooldown.items() if v > today})
    v = pf.mark(prices); pf.commit()
    print(f"Bot B [A nur pump.fun]: equity {v:.2f} | cash {pf.s['cash']:.2f} | positions {len(pf.s['positions'])} | kandidaten {len(kand)}")

if __name__ == "__main__":
    main()
