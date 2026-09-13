"""Bot C – "Graduation Momentum" auf Pump.fun (Codex-Daten, Paper-Simulation).
Kauft Tokens, deren Bonding Curve zwischen 90 und 99,5 % steht UND die in den letzten Minuten
sichtbar Fortschritt machen. Sicherheits-/Qualitaetsfilter direkt aus Codex (Bundler, Sniper, Insider, Top-10).
Exits: bei 3x die Haelfte, bei 5x die Haelfte des Rests, alles nach 24 h. Stop -50 %.
Codex Free-Tier = 10.000 Anfragen/Monat -> der Bot fragt hoechstens alle 10 Minuten ab (2 Anfragen je Lauf).
ACHTUNG fuer spaeter: Kaeufe auf der Bonding Curve laufen live NICHT ueber Jupiter, sondern ueber das Pump.fun-Programm."""
import os, time
from common import *
from bot_a import rug_ok

CODEX_KEY = os.environ.get("CODEX_KEY")
CODEX_URL = "https://graph.codex.io/graphql"
SOLANA = 1399811149
# ---- Einstieg ----
GRAD_MIN, GRAD_MAX = 90.0, 99.5
MIN_PROGRESS_30M = 3.0            # Kurve muss in den letzten ~30 Min mind. 3 Punkte gestiegen sein
MIN_OBS = 2                       # mind. 2 Beobachtungen (>= 10 Min) vor dem Kauf
MIN_BUYS_1H, MAX_SELL_RATIO = 15, 0.8
MAX_TOP10, MAX_BUNDLER, MAX_SNIPER, MAX_INSIDER = 35.0, 10.0, 15.0, 10.0
# Codex liefert fuer Bonding-Curve-Tokens (noch nicht migriert) KEINE volle Markt-
# kapitalisierung/DEX-Liquiditaet - beides ist dort strukturell klein/0. Nicht als Sicherheitsfilter
# nutzen; die eigentliche Reife/Sicherheit kommt aus grad + den Holder-/Bot-Filtern unten.
MIN_MCAP, MIN_LIQ = 0, 0
# ---- Position / Exits ----
POS_USD, MAX_POS = 30.0, 8
TP1_X, TP1_FRAC = 3.0, 0.5        # bei 3x: Haelfte raus
TP2_X, TP2_FRAC = 5.0, 0.5        # bei 5x: Haelfte des Rests raus
STOP, MAX_HOLD_H = -0.5, 24
COOLDOWN_D = 7
POLL_MIN = 10                     # Codex-Abfrage hoechstens alle 10 Minuten (Free-Tier-Budget)

def codex(query, variables=None):
    if not CODEX_KEY: return None
    r = requests.post(CODEX_URL, json={"query": query, "variables": variables or {}},
                      headers={"Authorization": CODEX_KEY, "Content-Type": "application/json", **UA}, timeout=30)
    r.raise_for_status()
    out = r.json()
    if out.get("errors"): print("codex:", out["errors"][0].get("message", out["errors"])[:200]); return None
    return out.get("data")

Q_COMPLETING = """
query($net: [Int!]) {
  filterTokens(
    filters: { network: $net, launchpadName: ["Pump.fun"], launchpadCompleted: false, launchpadMigrated: false,
               launchpadGraduationPercent: { gte: %s, lt: %s } }
    rankings: [{ attribute: graduationPercent, direction: DESC }]
    limit: 60
  ) {
    results {
      priceUSD marketCap liquidity volume1 buyCount1 sellCount1 uniqueBuys1 createdAt
      top10HoldersPercent bundlerHeldPercentage sniperHeldPercentage insiderHeldPercentage
      token { address name symbol launchpad { graduationPercent completed migrated } }
    }
  }
}""" % (GRAD_MIN, GRAD_MAX)

Q_PRICES = """
query($inputs: [GetPriceInput!]) { getTokenPrices(inputs: $inputs) { address priceUsd } }"""

def fnum(x, default=0.0):
    try: return float(x) if x is not None else default
    except Exception: return default

def fetch_candidates():
    d = codex(Q_COMPLETING, {"net": [SOLANA]})
    if not d: return []
    out = []
    for r in d["filterTokens"]["results"]:
        t = r["token"]; lp = t.get("launchpad") or {}
        out.append({"addr": t["address"], "sym": t.get("symbol") or "?", "name": t.get("name"),
                    "grad": fnum(lp.get("graduationPercent")), "completed": lp.get("completed"), "migrated": lp.get("migrated"),
                    "price": fnum(r.get("priceUSD")), "mcap": fnum(r.get("marketCap")), "liq": fnum(r.get("liquidity")),
                    "vol1": fnum(r.get("volume1")), "buys1": int(fnum(r.get("buyCount1"))), "sells1": int(fnum(r.get("sellCount1"))),
                    "ubuys1": int(fnum(r.get("uniqueBuys1"))),
                    "top10": fnum(r.get("top10HoldersPercent"), None), "bundler": fnum(r.get("bundlerHeldPercentage"), None),
                    "sniper": fnum(r.get("sniperHeldPercentage"), None), "insider": fnum(r.get("insiderHeldPercentage"), None)})
    return out

def fetch_prices(addrs):
    if not addrs: return {}
    d = codex(Q_PRICES, {"inputs": [{"address": a, "networkId": SOLANA} for a in addrs]})
    if not d: return {}
    return {p["address"]: fnum(p.get("priceUsd")) for p in d["getTokenPrices"] if p}

def quality_check(c):
    if not (GRAD_MIN <= c["grad"] < GRAD_MAX): return "grad"
    if c["price"] <= 0: return "kein preis"
    if c["buys1"] < MIN_BUYS_1H: return f"buys1 {c['buys1']}"
    if c["buys1"] and c["sells1"] / c["buys1"] > MAX_SELL_RATIO: return f"sell-ratio {c['sells1']/c['buys1']:.2f}"
    if c["top10"] is not None and c["top10"] > MAX_TOP10: return f"top10 {c['top10']:.0f}%"
    if c["bundler"] is not None and c["bundler"] > MAX_BUNDLER: return f"bundler {c['bundler']:.0f}%"
    if c["sniper"] is not None and c["sniper"] > MAX_SNIPER: return f"sniper {c['sniper']:.0f}%"
    if c["insider"] is not None and c["insider"] > MAX_INSIDER: return f"insider {c['insider']:.0f}%"
    return None

def main():
    pf = Paper("bot_c"); st = pf.s
    for k in ("lev", "last_bar", "liquidations"): st.pop(k, None)     # Reste der alten Perp-Version
    st["strategy"] = "graduation"; st["codex"] = bool(CODEX_KEY)
    now = time.time(); today = int(now // 86400)
    if now - st.get("last_poll", 0) < POLL_MIN * 60 - 30:
        print(f"Bot C [graduation]: warte (Codex-Budget), naechste Abfrage in {int((POLL_MIN*60 - (now - st.get('last_poll', 0)))/60)} min"); pf.commit(); return
    st["last_poll"] = now
    curve = load("bot_c_curve.json", {}); cooldown = load("bot_c_cooldown.json", {})
    # 1) Positionen verwalten (Kurse per Codex, funktioniert vor und nach der Migration)
    prices = fetch_prices(list(st["positions"].keys()))
    for addr, pos in list(st["positions"].items()):
        px = prices.get(addr, 0)
        if px <= 0: continue
        pos["peak"] = max(pos.get("peak", px), px)
        x = px / pos["entry"]; held_h = (now - time.mktime(time.strptime(pos["opened"], "%Y-%m-%dT%H:%M:%SZ"))) / 3600
        liq = pos.get("liq", 20_000)
        if x <= 1 + STOP: pf.sell(addr, px, 1.0, liq, "stop"); cooldown[addr] = today + COOLDOWN_D
        elif x >= TP2_X and pos.get("tp1") and not pos.get("tp2"): pos["tp2"] = True; pf.sell(addr, px, TP2_FRAC, liq, "tp2")
        elif x >= TP1_X and not pos.get("tp1"): pos["tp1"] = True; pf.sell(addr, px, TP1_FRAC, liq, "tp1")
        elif held_h >= MAX_HOLD_H: pf.sell(addr, px, 1.0, liq, "time")
    # 2) Kandidaten auf der Kurve
    cands = fetch_candidates(); checks = []
    for c in cands:
        a = c["addr"]
        hist = curve.setdefault(a, []); hist.append([now, c["grad"]]); curve[a] = hist[-24:]
        if a in st["positions"] or cooldown.get(a, 0) > today or len(st["positions"]) >= MAX_POS: continue
        why = quality_check(c)
        if why: checks.append((c["sym"], why)); continue
        if len(hist) < MIN_OBS: checks.append((c["sym"], f"beobachte ({len(hist)})")); continue
        old = [g for t, g in hist if now - t >= 25 * 60] or [hist[0][1]]
        progress = c["grad"] - old[-1]
        if progress < MIN_PROGRESS_30M: checks.append((c["sym"], f"stagniert +{progress:.1f}")); continue
        ok, risks = rug_ok(a); time.sleep(1.1)
        if not ok: checks.append((c["sym"], "rugcheck")); cooldown[a] = today + COOLDOWN_D; continue
        if st["cash"] < POS_USD + 2: break
        if pf.buy(c["sym"], a, c["price"], POS_USD, max(c["liq"], 15_000), f"grad {c['grad']:.0f}% +{progress:.1f}"):
            st["positions"][a]["liq"] = c["liq"]; prices[a] = c["price"]
            checks.append((c["sym"], f"GEKAUFT grad {c['grad']:.0f}% buys {c['buys1']} top10 {c['top10']}"))
            append_jsonl("bot_c_signals.jsonl", {"t": now_iso(), **c, "progress": round(progress, 1)})
    # Kurvenverlauf aufraeumen (aeltere als 6 h raus)
    curve = {a: [x for x in h if now - x[0] < 6 * 3600] for a, h in curve.items()}
    curve = {a: h for a, h in curve.items() if h}
    save("bot_c_curve.json", curve); save("bot_c_cooldown.json", {k: v for k, v in cooldown.items() if v > today})
    v = pf.mark(prices); pf.commit()
    print(f"Bot C [graduation{'' if CODEX_KEY else ', ohne Codex'}]: equity {v:.2f} | cash {st['cash']:.2f} | positions {len(st['positions'])} | kandidaten {len(cands)}")
    for sym, why in checks[:12]: print(f"   {sym:<10} {why}")

if __name__ == "__main__":
    main()
