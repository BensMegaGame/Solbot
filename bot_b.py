"""Bot B v5 – "Nach dem Absturz" (Paper, Small Caps). Ersetzt "Tiefer Dip" (v4, ausgeschaltet am 20.11.).

IDEE (getestet 20.11. auf 2.895 Kursverlaeufen von Bot D, 5-Min-Kurse, bis 72 h, mit Zeitsplit):
  Ein kleiner Coin ist seit Beobachtungsbeginn auf mind. 1,5x gestiegen und danach 80 % vom Hoch gefallen.
  Dann kaufen. Rueckblick: 876 Trades, +12 % je Trade nach 3 % Kosten, in allen 4 Zeitvierteln positiv -
  ABER: ohne die besten 5 % der Trades negativ. Der Gewinn haengt an seltenen grossen Spruengen abgestuerzter,
  duenn gehandelter Coins. Ob diese Spruenge zu echten Kursen handelbar sind, ist genau das, was dieser
  Paper-Bot mit echten Jupiter-Quotes misst.

UNIVERSUM (wie Bot D, soweit ohne Codex moeglich): Coins aus den DexScreener-Listen (neue Profile, Boosts) und,
  falls vorhanden, aus Bot D's Kandidatenliste. Beobachtet wird ein Coin, sobald er 12 h bis 7 Tage alt ist,
  15-150k $ Marktkapitalisierung, 10-50k $ Liquiditaet, >= 25k $ Volumen in 24 h, >= 30 Kaeufe und nicht
  ueberwiegend Verkaeufe hat. Ab dann 72 h lang alle 5 Min: Startkurs, Hoechststand, Absturz.

KAUF: 30 $ je Trade (Jupiter-Quote, wie alle Paper-Bots), einmal je Coin, Start 500 $, kein Abbruch.
VERKAUF:
  - Stop -20 % (vor dem Teilverkauf)
  - bei +30 %: 75 % verkaufen, Rest bleibt als "Moonbag"
  - ohne Teilverkauf nach 24 h: alles raus
  - Moonbag: raus bei 20x vom Einstieg oder nach 10 Tagen.
    Hinweis aus dem Rueckblick: Der Moonbag war im Schnitt 6 h nach dem Teilverkauf noch ~1,0x wert, nach 24 h
    ~0,8x, nach 48 h ~0,6x. Darum wird sein Wert nach 6 h, 24 h, 3 Tagen und 7 Tagen in bot_b_moon.jsonl
    protokolliert - so sehen wir spaeter, welche Haltedauer wirklich am besten gewesen waere.
"""
import os, time
from common import *

BOT = "Bot B"
VERSION = "b5"
POS_USD = 30.0
STOP = -0.20
TP, TP_FRAC = 0.30, 0.75
MAX_HOLD_H = 24                   # ohne Teilverkauf
MOON_X, MOON_MAX_D = 20.0, 10     # Moonbag
PUMP_X, ABSTURZ = 1.5, -0.80      # Signal: Hoch >= 1,5x Startkurs, dann <= 20 % des Hochs
WATCH_H = 72
DEAD_PRICE_H = 24
MOON_LOG_H = (6, 24, 72, 168)

MIN_AGE_H, MAX_AGE_H = 12, 7 * 24
MIN_MCAP, MAX_MCAP = 15_000, 150_000
MIN_LIQ, MAX_LIQ = 10_000, 50_000
MIN_VOL24, MIN_BUYS24, MAX_SELL_RATIO = 25_000, 30, 0.9
CHECK_EVERY_S = 30 * 60           # noch zu junge Coins alle 30 Min neu pruefen
MAX_CHECKS_PRO_LAUF = 300         # = 10 Abfragen a 30 Tokens


def f(p, *ks):
    for k in ks: p = (p or {}).get(k)
    try: return float(p) if p is not None else None
    except (TypeError, ValueError): return None

def fetch_pairs(pair_addrs):
    out = {}; pair_addrs = list(dict.fromkeys(a for a in pair_addrs if a))
    for k in range(0, len(pair_addrs), 30):
        d = get(f"{DS}/latest/dex/pairs/solana/{','.join(pair_addrs[k:k + 30])}") or {}
        for p in (d.get("pairs") or []):
            if p and p.get("chainId") == "solana" and p.get("pairAddress"): out[p["pairAddress"]] = p
        time.sleep(0.4)
    return out

def best_pairs(token_addrs):
    """{token: groesster Pool} ueber den Token-Endpunkt (30 je Abfrage)."""
    out = {}
    for k in range(0, len(token_addrs), 30):
        for p in get(f"{DS}/tokens/v1/solana/{','.join(token_addrs[k:k + 30])}") or []:
            a = (p.get("baseToken") or {}).get("address")
            if p.get("chainId") != "solana" or not a: continue
            if a not in out or (f(p, "liquidity", "usd") or 0) > (f(out[a], "liquidity", "usd") or 0): out[a] = p
        time.sleep(0.4)
    return out

def eignung(p, now):
    """None = passt, sonst Grund. 'zu jung' = spaeter nochmal pruefen."""
    age_h = (now * 1000 - (p.get("pairCreatedAt") or now * 1000)) / 3.6e6
    if age_h < MIN_AGE_H: return "zu jung"
    if age_h > MAX_AGE_H: return "zu alt"
    mcap = f(p, "marketCap") or f(p, "fdv") or 0; liq = f(p, "liquidity", "usd") or 0; vol = f(p, "volume", "h24") or 0
    b = ((p.get("txns") or {}).get("h24") or {}).get("buys", 0); s = ((p.get("txns") or {}).get("h24") or {}).get("sells", 0)
    if not (MIN_MCAP <= mcap <= MAX_MCAP): return f"mcap {mcap:.0f}"
    if not (MIN_LIQ <= liq <= MAX_LIQ): return f"liq {liq:.0f}"
    if vol < MIN_VOL24: return f"vol {vol:.0f}"
    if b < MIN_BUYS24: return f"buys {b}"
    if b and s / b > MAX_SELL_RATIO: return f"sell-ratio {s / b:.2f}"
    return None


def versionswechsel():
    alt = load("bot_b_state.json", None)
    if not alt or alt.get("version") == VERSION: return
    os.makedirs(os.path.join(DATA, "archive"), exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M", time.gmtime())
    for name in ("bot_b_state.json", "bot_b_meta.json"):
        p = os.path.join(DATA, name)
        if os.path.exists(p): os.replace(p, os.path.join(DATA, "archive", f"{name[:-5]}_{alt.get('version', 'b4')}_{stamp}.json"))
    print(f"{BOT}: Strategiewechsel auf {VERSION} - alter Stand archiviert, Neustart mit {START_CAPITAL:.0f} $")


def main():
    versionswechsel()
    pf = Paper("bot_b", rebuy_sperre_h=24); st = pf.s; st["version"] = VERSION; st["strategy"] = "nach_dem_absturz"
    now = time.time()
    meta = load("bot_b_meta.json", {"seen": {}, "watch": {}})
    seen, watch = meta["seen"], meta["watch"]
    log = []

    # 1) neue Coins sammeln (DexScreener-Listen + Bot D's Kandidaten)
    neu = 0
    for ep in ("/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"):
        for t in get(DS + ep) or []:
            a = t.get("tokenAddress")
            if t.get("chainId") == "solana" and a and a not in seen and a not in watch:
                seen[a] = {"t": now, "next": now}; neu += 1
    for c in (load("bot_d_meta.json", {}).get("cands") or []):
        a = c.get("addr")
        if a and a not in seen and a not in watch: seen[a] = {"t": now, "next": now}; neu += 1

    # 2) faellige Coins auf Eignung pruefen -> Beobachtung starten
    faellig = [a for a, s in seen.items() if s.get("next", 0) <= now][:MAX_CHECKS_PRO_LAUF]
    gestartet = 0
    for a, p in best_pairs(faellig).items():
        grund = eignung(p, now)
        if grund is None:
            px = f(p, "priceUsd")
            if px and px > 0:
                watch[a] = {"sym": (p.get("baseToken") or {}).get("symbol") or a[:4], "pair": p["pairAddress"],
                            "t0": now, "px0": px, "peak": px, "mcap0": f(p, "marketCap") or f(p, "fdv"), "liq0": f(p, "liquidity", "usd")}
                seen.pop(a, None); gestartet += 1
        elif grund == "zu jung":
            seen[a]["next"] = now + CHECK_EVERY_S
        else:
            seen[a]["next"] = now + 6 * 3600          # passt gerade nicht (z.B. mcap) - in 6 h nochmal
    for a in faellig:                                 # ohne Pool bei DexScreener
        if a in seen and seen[a].get("next", 0) <= now: seen[a]["next"] = now + 6 * 3600
    for a in [a for a, s in seen.items() if now - s["t"] > MAX_AGE_H * 3600]: del seen[a]

    # 3) Kurse: Beobachtungsliste + Positionen
    paare = fetch_pairs([w["pair"] for w in watch.values()] + [q.get("pair") for q in st["positions"].values()])
    prices = {}

    # 4) Positionen verwalten
    for a, pos in list(st["positions"].items()):
        p = paare.get(pos.get("pair")); px = f(p, "priceUsd") or 0; liq = f(p, "liquidity", "usd") or 0
        if px <= 0:
            seit = pos.setdefault("no_px_since", now_iso())
            if (now - parse_iso(seit)) / 3600 >= DEAD_PRICE_H and pos.get("qty", 0) > 0:
                verlust = round(pos.get("cost", 0.0), 2)
                st["trades"].append({"t": now_iso(), "side": "sell", "sym": pos["sym"], "addr": a, "price": 0.0, "usd": 0.0,
                                     "pnl": -verlust, "frac": 1.0, "reason": "abgeschrieben (kein kurs)"})
                del st["positions"][a]; log.append((pos["sym"], "abgeschrieben - 24 h ohne Kurs"))
            continue
        pos.pop("no_px_since", None); prices[a] = px
        pos["peak"] = max(pos.get("peak", px), px)
        x = px / pos["entry"] - 1
        if not pos.get("tp"):
            warum, frac = ("stop", 1.0) if x <= STOP else ("tp30", TP_FRAC) if x >= TP else \
                          ("zeit 24h", 1.0) if held_seconds(pos) >= MAX_HOLD_H * 3600 else (None, 0)
            if warum and pf.sell(a, px, frac, liq, warum):
                log.append((pos["sym"], f"verkauft {frac:.0%} ({warum}) bei {x:+.0%}"))
                if warum == "tp30" and a in st["positions"]:
                    st["positions"][a].update(tp=True, tp_t=now, tp_px=px, moon_log=[])
        else:
            for h in MOON_LOG_H:
                if h not in pos.setdefault("moon_log", []) and now - pos["tp_t"] >= h * 3600:
                    pos["moon_log"].append(h)
                    append_jsonl("bot_b_moon.jsonl", {"t": now_iso(), "sym": pos["sym"], "addr": a, "h": h,
                                                      "x_vs_teilverkauf": round(px / pos["tp_px"], 4), "x_vs_einstieg": round(px / pos["entry"], 4)})
            warum = "20x" if px / pos["entry"] >= MOON_X else "10 tage" if now - pos["tp_t"] >= MOON_MAX_D * 86400 else None
            if warum and pf.sell(a, px, 1.0, liq, f"moonbag {warum}"):
                log.append((pos["sym"], f"Moonbag verkauft ({warum}) bei {px / pos['entry']:.2f}x"))

    # 5) Beobachtung: Hoch merken, Absturz erkennen -> kaufen
    signale = 0
    for a, w in list(watch.items()):
        p = paare.get(w["pair"]); px = f(p, "priceUsd") or 0
        if now - w["t0"] > WATCH_H * 3600:
            del watch[a]; continue
        if px <= 0 or w.get("signal"): continue
        w["peak"] = max(w["peak"], px); w["last"] = px
        if w["peak"] >= w["px0"] * PUMP_X and px <= w["peak"] * (1 + ABSTURZ):
            w["signal"] = now; signale += 1
            info = {"t": now_iso(), "addr": a, "sym": w["sym"], "px0": w["px0"], "peak": w["peak"], "px": px,
                    "liq": f(p, "liquidity", "usd"), "mcap": f(p, "marketCap") or f(p, "fdv"),
                    "std_seit_start": round((now - w["t0"]) / 3600, 1)}
            if a in st["positions"]: info["ergebnis"] = "schon investiert"
            elif st["cash"] < POS_USD + 1: info["ergebnis"] = "kein Cash"
            elif pf.buy(w["sym"], a, px, POS_USD, f(p, "liquidity", "usd") or 0,
                        f"absturz {px / w['peak'] - 1:.0%} vom hoch ({w['peak'] / w['px0']:.1f}x)"):
                st["positions"][a]["pair"] = w["pair"]; prices[a] = px; info["ergebnis"] = "gekauft"
                log.append((w["sym"], f"GEKAUFT 30 $ nach {px / w['peak'] - 1:.0%} vom Hoch ({w['peak'] / w['px0']:.1f}x)"))
            else:
                info["ergebnis"] = "kein Kauf (Jupiter: keine Route / Impact / Abweichung)"
            append_jsonl("bot_b_signals.jsonl", info)

    st["coverage"] = {"t": now_iso(), "wartend": len(seen), "beobachtet": len(watch), "neu": neu,
                      "beobachtung_gestartet": gestartet, "signale": signale,
                      "moonbags": sum(1 for q in st["positions"].values() if q.get("tp"))}
    save("bot_b_meta.json", meta)
    val = pf.mark(prices); pf.commit()
    print(f"{BOT} [nach dem absturz]: equity {val:.2f} | cash {st['cash']:.2f} | positionen {len(st['positions'])} "
          f"| beobachtet {len(watch)} | wartend {len(seen)} | neu {neu} | signale {signale}")
    for sym, txt in log[:12]: print(f"   {str(sym):<12} {txt}")


if __name__ == "__main__":
    main()
