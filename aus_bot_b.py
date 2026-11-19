"""Bot B v4 – "Tiefer Dip": die Idee von Bot E, aber nur fuer die GROSSEN Ausreisser nach unten.

Bot E kauft etablierte Coins, die in 24 h zwischen -10 % und -30 % gefallen sind und deutlich schlechter
liefen als SOL. Bot B faengt genau die Faelle auf, die E auslaesst: Coins, die in 24 h zwischen -30 % und -60 %
gefallen sind, mindestens 15 Prozentpunkte schlechter als SOL, und bei denen sich der Kurs bereits wieder
vom Tief loest. Groessere Uebertreibung -> groessere moegliche Gegenbewegung, aber auch mehr Risiko
(schlechte Nachrichten, Hack, Delisting). Deshalb etwas weiterer Stop und vorsichtigere Bedingungen.

UNIVERSUM: die Coins von Bot E (~Top 100, Liquiditaet ab 300k $) und Bot C (Rang 30-200 ohne Aktien/Stablecoins).
B liest deren Coin-Listen und Paar-Adressen und braucht dafuer weder Codex noch CoinGecko.

MARKTFILTER (Vorgabe): ist SOL in den letzten 24 h um mehr als 8 % gefallen, kauft B NICHTS - dann faellt alles,
und ein tiefer Dip ist kein Einzelfall, sondern der Markt.

STAND: ungetestet. Die Regeln arbeiten auf 5-Minuten-Kursen und lassen sich mit unseren Tagesdaten nicht
nachrechnen. Bot B ist ein Live-Test. ABBRUCHREGEL (vorab festgelegt): unter 300 $ keine neuen Kaeufe.

Die alte Strategie "A nur pump.fun" (v3) ist eingestellt: 9 Positionen, 1 Gewinner, 500 $ -> ~350 $.
Alter Stand in data/archive/.
"""
import os, time
from common import *

BOT = "Bot B"
VERSION = "b4"
SOL_MINT = "So11111111111111111111111111111111111111112"
GT = "https://api.geckoterminal.com/api/v2/networks/solana"
SOL_REF_PAIRS = ["58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2",   # Raydium SOL/USDC
                 "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"]   # Orca SOL/USDC (Reserve)

# ---------- Einstieg ----------
DROP_24H = (-60.0, -30.0)     # tiefer als Bot E (der kauft nur bis -30 %)
REL_TO_SOL_PP = 15.0          # mind. 15 Prozentpunkte schlechter als SOL
SOL_24H_MIN = -8.0            # Vorgabe: SOL > 8 % im Minus -> gar nichts kaufen
STAB_1H_MIN = -2.0            # letzte Stunde nicht mehr deutlich fallend
BOUNCE_6H = (2.0, 10.0)       # 2-10 % ueber dem 6h-Tief: Erholung hat begonnen, ist aber noch nicht gelaufen
MAX_VORLAUF_7D = -30.0        # VOR dem Absturz (Kurs vor 24 h gegen Kurs vor 7 Tagen) nicht schon im Abwaertstrend
VOL_TO_MCAP_MIN = 0.05        # echter Umsatz am Absturztag
MIN_LIQ = 100_000             # Pool-Liquiditaet beim Kauf
COOLDOWN_D = 5
MAX_CONSEC_STOPS, PAUSE_D = 3, 3

# ---------- Position / Ausstieg ----------
MAX_POS = 3
POS_FRAC = 0.30               # 30 % des Gesamtkapitals je Position
STOP = -0.15                  # weiter als E (-10 %): nach einem -40 %-Tag ist die Schwankung groesser
TP1_GAIN, TP1_FRAC = 0.20, 0.5
TRAIL_ARM, TRAIL_GIVEBACK = 0.10, -0.08
BREAKEVEN_AFTER_TP1 = True
MAX_HOLD_D, FLAT_EXIT_GAIN = 7, 0.03
HIST_KEEP_S = 7 * 3600
STOP_UNTER = 300.0            # Abbruchregel


def _liq(p): return float(((p or {}).get("liquidity") or {}).get("usd") or 0)

def f(p, *keys):
    v = p
    for k in keys: v = (v or {}).get(k)
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None

def fetch_pairs(pair_addrs):
    out = []
    pair_addrs = list(dict.fromkeys(a for a in pair_addrs if a))
    for k in range(0, len(pair_addrs), 30):
        d = get(f"{DS}/latest/dex/pairs/solana/{','.join(pair_addrs[k:k+30])}") or {}
        out += [p for p in (d.get("pairs") or []) if p and p.get("chainId") == "solana"]
        time.sleep(1.1)
    return out

def vorlauf_7d(pool, px, c24):
    """Kursveraenderung VOR dem Absturz in %: Kurs vor 24 h gegen Schlusskurs vor 7 Tagen (GeckoTerminal)."""
    if not pool or c24 is None or c24 <= -100: return None
    d = get(f"{GT}/pools/{pool}/ohlcv/day", {"limit": 9}); time.sleep(2.1)
    rows = sorted(((d or {}).get("data") or {}).get("attributes", {}).get("ohlcv_list") or [])
    if len(rows) < 8: return None
    ref = rows[-8][4]
    px_vor_24h = px / (1 + c24 / 100)
    return (px_vor_24h / ref - 1) * 100 if ref else None


def universum():
    """{token: paar-adresse} aus den Listen von Bot E und Bot C."""
    out = {}
    for name in ("bot_e_meta.json", "bot_c_meta.json"):
        m = load(name, {})
        po = m.get("pair_of") or {}
        for x in m.get("universe") or []:
            a = x[0] if isinstance(x, (list, tuple)) else x
            if po.get(a): out[a] = po[a]
    return out


def versionswechsel():
    alt = load("bot_b_state.json", None)
    if not alt or alt.get("version") == VERSION: return
    os.makedirs(os.path.join(DATA, "archive"), exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M", time.gmtime())
    for name in ("bot_b_state.json", "bot_b_meta.json", "bot_b_cooldown.json"):
        p = os.path.join(DATA, name)
        if os.path.exists(p): os.replace(p, os.path.join(DATA, "archive", f"{name[:-5]}_{alt.get('version', 'v3')}_{stamp}.json"))
    print(f"{BOT}: Strategiewechsel auf {VERSION} - alter Stand archiviert, Neustart mit {START_CAPITAL:.0f} $")


def main():
    versionswechsel()
    pf = Paper("bot_b", rebuy_sperre_h=24); st = pf.s; st["version"] = VERSION; st["strategy"] = "tiefer_dip"
    now = time.time(); today = int(now // 86400)
    meta = load("bot_b_meta.json", {}); hist = load("bot_b_hist.json", {}); cooldown = load("bot_b_cooldown.json", {})

    uni = universum()
    for a, pos in st["positions"].items():
        if pos.get("pair"): uni.setdefault(a, pos["pair"])
    paare = {}
    for p in fetch_pairs(list(uni.values()) + SOL_REF_PAIRS):
        a = (p.get("baseToken") or {}).get("address")
        if a == SOL_MINT:
            if _liq(p) > _liq(paare.get(SOL_MINT)): paare[SOL_MINT] = p
        elif a in uni and uni[a] == p.get("pairAddress"):
            paare[a] = p
    sol24 = f(paare.get(SOL_MINT), "priceChange", "h24")
    prices, checks = {}, []

    # 1) eigene 5-Minuten-Historie (fuer das 6h-Tief)
    for a, p in paare.items():
        px = f(p, "priceUsd") or 0
        if px > 0:
            pts = hist.setdefault(a, []); pts.append([now, px])
            hist[a] = [x for x in pts if now - x[0] <= HIST_KEEP_S][-90:]
    for a in list(hist):
        if a not in uni and a != SOL_MINT: del hist[a]

    # 2) Positionen verwalten
    for a, pos in list(st["positions"].items()):
        p = paare.get(a); px = f(p, "priceUsd") if p else None
        if not px or px <= 0: continue
        prices[a] = px; liq = _liq(p)
        pos["peak"] = max(pos.get("peak", px), px)
        x = px / pos["entry"] - 1; peak_x = pos["peak"] / pos["entry"] - 1; from_peak = px / pos["peak"] - 1
        held_d = held_seconds(pos) / 86400
        why = None
        if not pos.get("tp1"):
            if x <= STOP: why = "stop"
            elif x >= TP1_GAIN: pos["tp1"] = True; pf.sell(a, px, TP1_FRAC, liq, "tp1")
            elif peak_x >= TRAIL_ARM and from_peak <= TRAIL_GIVEBACK: why = "trail"
            elif held_d >= MAX_HOLD_D and x < FLAT_EXIT_GAIN: why = "time"
        if not why and pos.get("tp1") and a in st["positions"]:
            if BREAKEVEN_AFTER_TP1 and x <= 0.0: why = "breakeven"
            elif from_peak <= TRAIL_GIVEBACK: why = "trail"
            elif held_d >= MAX_HOLD_D: why = "time"
        if why:
            pf.sell(a, px, 1.0, liq, why)
            if why == "stop":
                cooldown[a] = today + COOLDOWN_D; meta["consec_stops"] = meta.get("consec_stops", 0) + 1
                if meta["consec_stops"] >= MAX_CONSEC_STOPS:
                    meta["paused_until"] = today + PAUSE_D; meta["consec_stops"] = 0
            else: meta["consec_stops"] = 0

    # 3) Einstieg
    equity = st["cash"] + sum(q["qty"] * (q.get("mark") or q.get("cur_price") or q["entry"]) for q in st["positions"].values())
    gestoppt = equity < STOP_UNTER
    cands = []
    if sol24 is None: checks.append(("SOL", "Referenz fehlt -> kein Kauf"))
    elif sol24 < SOL_24H_MIN: checks.append(("SOL", f"SOL 24h {sol24:+.1f}% (< {SOL_24H_MIN:.0f}%) -> kein Kauf"))
    elif gestoppt: checks.append(("ABBRUCH", f"Kapital {equity:.0f} $ unter {STOP_UNTER:.0f} $ - keine neuen Kaeufe"))
    elif meta.get("paused_until", 0) > today: checks.append(("PAUSE", f"{MAX_CONSEC_STOPS} Stops in Folge - Pause"))
    else:
        for a, p in paare.items():
            if a == SOL_MINT or a in st["positions"] or cooldown.get(a, 0) > today or pf.gesperrt(a): continue
            c24, c1 = f(p, "priceChange", "h24"), f(p, "priceChange", "h1")
            mcap = f(p, "marketCap") or f(p, "fdv") or 0; liq = _liq(p)
            vol = f(p, "volume", "h24") or 0; px = f(p, "priceUsd") or 0
            sym = (p.get("baseToken") or {}).get("symbol") or "?"
            if None in (c24, c1) or px <= 0 or mcap <= 0: continue
            if not (DROP_24H[0] <= c24 <= DROP_24H[1]): continue
            why = None
            if liq < MIN_LIQ: why = f"liq {liq/1e3:.0f}k"
            elif c24 > sol24 - REL_TO_SOL_PP: why = f"nur {c24 - sol24:+.0f}pp vs SOL"
            elif c1 < STAB_1H_MIN: why = f"1h {c1:+.1f}% faellt noch"
            elif vol / mcap < VOL_TO_MCAP_MIN: why = f"vol/mcap {vol/mcap:.1%}"
            else:
                pts = [x[1] for x in hist.get(a, []) if now - x[0] <= 6 * 3600]
                if len(pts) < 6: why = "historie fehlt"
                elif not (BOUNCE_6H[0] / 100 <= px / min(pts) - 1 <= BOUNCE_6H[1] / 100): why = f"{px/min(pts)-1:+.1%} ueber 6h-Tief"
                else:
                    v7 = vorlauf_7d(p.get("pairAddress"), px, c24)
                    if v7 is None: why = "7d-daten fehlen"
                    elif v7 < MAX_VORLAUF_7D: why = f"schon vorher {v7:+.0f}% in 7d = abwaertstrend"
            if why: checks.append((sym, f"24h {c24:+.0f}% | {why}")); continue
            cands.append((c24 - sol24, a, sym, px, liq, c24, c1, mcap, p.get("pairAddress")))
        cands.sort()
        for rel, a, sym, px, liq, c24, c1, mcap, pair in cands[:max(0, MAX_POS - len(st["positions"]))]:
            usd = min(st["cash"] - 1, equity * POS_FRAC)
            if usd < 20: break
            if pf.buy(sym, a, px, usd, liq, f"tiefer dip 24h {c24:+.0f}% (SOL {sol24:+.0f}%) 1h {c1:+.1f}%"):
                st["positions"][a]["pair"] = pair; prices[a] = px
                checks.append((sym, f"GEKAUFT {usd:.0f}$ | 24h {c24:+.0f}% vs SOL {sol24:+.0f}%"))
                append_jsonl("bot_b_v4_signals.jsonl", {"t": now_iso(), "addr": a, "sym": sym, "c24": c24, "c1": c1,
                                                        "sol24": sol24, "mcap": mcap, "liq": round(liq), "usd": round(usd, 2)})

    save("bot_b_meta.json", meta); save("bot_b_hist.json", hist)
    save("bot_b_cooldown.json", {k: v for k, v in cooldown.items() if v > today})
    st["coverage"] = {"t": now_iso(), "kurse": sum(1 for a in uni if a in paare), "von": len(uni),
                      "sol": sol24 is not None, "sol24": None if sol24 is None else round(sol24, 1),
                      "kandidaten": len(cands), "gestoppt": gestoppt}
    v = pf.mark(prices); pf.commit()
    print(f"{BOT} [tiefer dip]: equity {v:.2f} | cash {st['cash']:.2f} | positionen {len(st['positions'])}/{MAX_POS}"
          f" | kurse {st['coverage']['kurse']}/{len(uni)} | kandidaten {len(cands)} | SOL 24h {st['coverage']['sol24']}")
    for sym, why in checks[:12]: print(f"   {sym:<10} {why}")


if __name__ == "__main__":
    main()
