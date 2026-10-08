"""Bot A – Micro-Cap-Lotterie (Solana). Einstieg per Screener, feste Ausstiegsregeln.

v3 (08.10.) Ausstieg neu: bei 1,8x die HAELFTE verkaufen, danach Trailing -35 % vom Hoechststand fuer den Rest,
spaetestens bei 8x alles raus.
Grund (94 Positionen ausgewertet): 12 Positionen stiegen auf 1,8x und mehr, verkauften ein Viertel und fielen
dann mit dem Rest bis auf den Stop bei -50 % zurueck (z.B. HI 2,76x -> -9 $). Mit dem Trailing nach 1,8x liegt
der schlechteste Verkauf bei 1,8 x 0,65 = 1,17x - ueber dem Einstand. -35 % statt enger, weil Micro-Caps auf dem
Weg zu 3-10x fast immer 15-20 % Ruecksetzer haben und die grossen Treffer A tragen (15 Positionen >= 3x: +1.227 $).
"""
import time
from common import *

# Einstieg
MIN_AGE_H, MAX_AGE_H = 48, 14 * 24
MIN_LIQ, MIN_MCAP, MAX_MCAP, MIN_VOL = 30_000, 100_000, 3_000_000, 100_000
MIN_LIQ_RATIO, MIN_BUY_RATIO = 0.05, 0.45
# Positionen
POS_USD, MAX_POS = 50.0, 8          # 50 $ pro Ticket, max. 8 offene
# Ausstieg
TP0_X, TP0_FRAC = 1.8, 0.5          # v3: bei 1,8x die Haelfte raus
TRAIL = -0.35                       # v3: danach Rest raus, sobald 35 % unter dem Hoechststand
TP_REST_X = 8.0                     # v3: oder spaetestens bei 8x komplett raus (Memecoin-Gipfel fallen oft binnen Minuten)
STOP = -0.5                         # vor dem ersten Verkauf: -50 %
MAX_HOLD_D = 10                     # Zeitstop - nur solange noch nichts verkauft wurde (Laeufer schuetzt das Trailing)
COOLDOWN_D = 14                     # Tage Sperre nach Verkauf mit Verlust (verhindert sofortiges Rebuy)


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

def main():
    pf = Paper("bot_a", rebuy_sperre_h=24)
    prices = {}
    today = int(time.time() // 86400)
    cooldown = load("bot_a_cooldown.json", {})
    # 1) offene Positionen verwalten
    for addr, pos in list(pf.s["positions"].items()):
        p = solana_pair(addr)
        if not p: continue
        m = metrics(p); px = m["price"]; prices[addr] = px
        pos["peak"] = max(pos.get("peak", px), px)
        x = px / pos["entry"]; held_d = held_seconds(pos) / 86400
        why = None
        if not pos.get("tp0"):
            if x <= 1 + STOP: why = "stop"
            elif x >= TP0_X: pos["tp0"] = True; pf.sell(addr, px, TP0_FRAC, m["liq"], "tp0")
            elif held_d >= MAX_HOLD_D: why = "time"
        elif x >= TP_REST_X: why = "tp8x"
        elif px / pos["peak"] - 1 <= TRAIL: why = "trail"
        if why:
            pf.sell(addr, px, 1.0, m["liq"], why)
            if why == "stop" or px < pos["entry"]:   # nur Verlust-Exits sperren den Token 14 Tage (sonst 24 h, common.py)
                cooldown[addr] = today + COOLDOWN_D
        time.sleep(1.1)
    # 2) neue Kandidaten
    seen = load("bot_a_seen.json", {})
    for addr in candidates():
        if addr in pf.s["positions"] or len(pf.s["positions"]) >= MAX_POS: continue
        if cooldown.get(addr, 0) > today or pf.gesperrt(addr): continue
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
    save("bot_a_cooldown.json", {k: v for k, v in cooldown.items() if v > today})   # abgelaufene Sperren aufraeumen
    v = pf.mark(prices); pf.commit()
    print(f"Bot A: equity {v:.2f} | cash {pf.s['cash']:.2f} | positions {len(pf.s['positions'])}")

if __name__ == "__main__":
    main()
