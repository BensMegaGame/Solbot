"""Bot C v6 – "Dip, weites Netz": dieselben Regeln wie Bot E, aber auf einer breiteren Coin-Auswahl.
Kauft einen Solana-Coin, nachdem er in 24 h deutlich staerker gefallen ist als SOL und der Verkaufsdruck
sichtbar nachgelassen hat. Stop -10 %, halber Gewinn bei +15 %, Rest mit Trailing.

WARUM: Die Rueckschlag-Strategie von v5 hielt einer sauberen Nachpruefung nicht stand. Ohne Aktien-Tokens,
Stablecoins und Fonds (221 von 665 Coins im Backtest) lag der Median der aktiven Wochen bei -5,8 % bzw.
-2,5 % statt +6,7 %/+8,5 %, und die ganze Auswertung stand auf nur 57 Kaeufen. Auf Tagesdaten hat im
letzten Jahr ueberhaupt keine getestete Regel fuer Rang 30-200 bestanden (Korb, Momentum, Verlierer,
Rueckschlag). Bot E dagegen verdient live Geld (+46 %, 18 von 21 Verkaeufen im Plus) - mit Regeln, die auf
5-Minuten-Kursen arbeiten und sich mit Tagesdaten gar nicht pruefen lassen.
Bot C prueft deshalb LIVE eine klare Frage: traegt der Vorteil von Bot E auch ausserhalb seiner eigenen
Coins? Das ist ein Test, keine belegte Strategie.

UNIVERSUM: Rang 30-200 der Solana-Kategorie auf CoinGecko, gerechnet NACH Entfernen von Aktien-Tokens
(Ondo "...ondo", xStocks "Xs..."), Stablecoins, Fonds, Gold und gebrueckten/verpackten Coins. Ohne die
Coins von Bot E (Universum und offene Positionen) - die beiden Bots halten nie denselben Coin.
Gekauft wird nur mit einem Solana-Pool ab 100k $ Liquiditaet.

Datenquellen: CoinGecko (Rangliste, 2x taeglich), DexScreener (Kurse je Lauf ueber feste Paar-Adressen),
GeckoTerminal (7-Tage-Aenderung, nur fuer echte Kandidaten), Jupiter (Fill-Preise ueber common.Paper).
"""
import os, time
from common import *

BOT = "Bot C"
VERSION = "c6"
SOL_MINT = "So11111111111111111111111111111111111111112"
GT = "https://api.geckoterminal.com/api/v2/networks/solana"
CG = "https://api.coingecko.com/api/v3"
CG_KEY = os.environ.get("COINGECKO_KEY")
CG_HDR = {**UA, **({"x-cg-demo-api-key": CG_KEY} if CG_KEY else {})}

# ---------- Universum ----------
RANG_VON, RANG_BIS = 30, 200
U_MIN_LIQ = 100_000              # Mindest-Liquiditaet des Pools beim Kauf (wird jetzt wirklich geprueft)
U_MIN_VOL24 = 50_000
DISCOVER_EVERY_S = 12 * 3600
ADDR_CACHE_S = 24 * 3600
PAIR_LOOKUPS_PER_RUN = 25
NO_PAIR_RETRY_S = 24 * 3600      # Coin ohne Solana-Pool: erst nach 24 h wieder suchen (sonst blockiert er die Liste)

# ---------- Einstieg: identisch mit Bot E ----------
DROP_24H = (-30.0, -10.0)
REL_TO_SOL_PP = 6.0
SOL_24H_MIN = -12.0
STAB_1H_MIN = -1.5
BOUNCE_6H = (1.5, 6.0)
MAX_DROP_7D = -35.0
VOL_TO_MCAP_MIN = 0.03
COOLDOWN_D = 5
MAX_CONSEC_STOPS, PAUSE_D = 3, 3

# ---------- Positionen / Ausstieg: identisch mit Bot E, nur 3 statt 2 Positionen ----------
MAX_POS = 3                      # breiteres Netz -> eine Position mehr, dafuer kleiner
POS_FRAC = 0.30                  # 30 % des Gesamtkapitals je Position
STOP = -0.10
TP1_GAIN, TP1_FRAC = 0.15, 0.5
TRAIL_ARM, TRAIL_GIVEBACK = 0.08, -0.06
BREAKEVEN_AFTER_TP1 = True
MAX_HOLD_D, FLAT_EXIT_GAIN = 7, 0.03
HIST_KEEP_S = 7 * 3600

# ---------- Was kein Krypto-Coin im Sinne der Strategie ist ----------
EXCLUDE_SYM = {"USDC", "USDT", "USDS", "PYUSD", "USD1", "DAI", "FDUSD", "USDE", "EURC", "USDG", "USDY", "CASH",
               "SOL", "WSOL", "ETH", "WETH", "BTC", "WBTC", "CBBTC", "TBTC", "WBNB", "BNB",
               "JITOSOL", "MSOL", "BSOL", "JUPSOL", "INF", "BNSOL", "HSOL", "DSOL", "VSOL", "JLP"}
EXCLUDE_SUB = ("USD", "EUR", "GBP", "CHF", "JPY", "XAU", "SOL")
EXCLUDE_ID = ("xstock", "ondo-tokenized", "tokenized", "pre-ipo", "stablecoin", "usd", "treasury", "fund",
              "bridged", "wrapped", "-btc", "btc-", "staked", "securitize", "yield", "naira", "dollar", "euro-",
              "reserve-vehicle", "-vault", "gold-token", "silver-token", "xaut", "paxg")
MIN_TAGESSPANNE = 0.01           # Hoch/Tief der letzten 24 h weniger als 1 % auseinander -> gebunden (Stablecoin, Fonds)


def kein_coin(cid, sym, addr, x=None):
    """Grund, warum das kein handelbarer Krypto-Coin fuer die Strategie ist - oder None."""
    sym = (sym or "").upper(); cid = cid or ""; addr = addr or ""
    if addr.endswith("ondo") or addr.startswith("Xs"): return "aktie"
    if sym in EXCLUDE_SYM or any(s in sym for s in EXCLUDE_SUB): return "stable/major"
    if any(k in cid for k in EXCLUDE_ID): return "stable/fonds/bruecke"
    if x:
        hi, lo, px = x.get("high_24h") or 0, x.get("low_24h") or 0, x.get("current_price") or 0
        if px > 0 and hi > 0 and lo > 0 and (hi - lo) / px < MIN_TAGESSPANNE: return "gebunden"
    return None


# ---------------------------------------------------------------- Universum ueber CoinGecko
def cg_get(pfad, params):
    try:
        r = requests.get(f"{CG}{pfad}", params=params, headers=CG_HDR, timeout=40)
        if r.status_code != 200:
            print(f"{BOT}: CoinGecko {r.status_code} bei {pfad}"); return None
        return r.json()
    except Exception as e:
        print(f"{BOT}: CoinGecko Fehler: {e}"); return None


def solana_adressen(now):
    c = load("cg_solana_addr.json", {})
    if c.get("t", 0) and now - c["t"] < ADDR_CACHE_S and c.get("a"): return c["a"]
    lst = cg_get("/coins/list", {"include_platform": "true"})
    if not lst: return c.get("a") or {}
    a = {x["id"]: (x.get("platforms") or {}).get("solana") for x in lst}
    a = {k: v for k, v in a.items() if v}
    save("cg_solana_addr.json", {"t": now, "a": a})
    return a


def universum_laden(now):
    """[(addr, sym)] fuer Rang RANG_VON..RANG_BIS, gezaehlt NUR unter echten Krypto-Coins. None = Fehler."""
    adr = solana_adressen(now)
    if not adr: return None
    reihe, raus = [], {}
    for seite in range(1, 4):
        res = cg_get("/coins/markets", {"vs_currency": "usd", "category": "solana-ecosystem",
                                        "order": "market_cap_desc", "per_page": 250, "page": seite})
        if res is None: break
        for x in res:
            a = adr.get(x["id"]); sym = (x.get("symbol") or "").upper()
            if not a: continue
            grund = kein_coin(x["id"], sym, a, x)
            if grund: raus[grund] = raus.get(grund, 0) + 1; continue
            if (x.get("total_volume") or 0) >= U_MIN_VOL24: reihe.append((a, sym))
        if len(reihe) >= RANG_BIS or len(res) < 250: break
        time.sleep(2.2)
    if len(reihe) < RANG_VON:
        print(f"{BOT}: CoinGecko lieferte nur {len(reihe)} Coins - zu wenig fuer Rang {RANG_VON}"); return None
    print(f"{BOT}: Rangliste neu - ausgeschlossen {raus}")
    return reihe[RANG_VON - 1:RANG_BIS]


# ---------------------------------------------------------------- DexScreener ueber feste Paar-Adressen
SOL_REF_PAIRS = ["58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2",   # Raydium SOL/USDC
                 "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"]   # Orca SOL/USDC (Reserve)

def _liq(p): return float(((p or {}).get("liquidity") or {}).get("usd") or 0)

def f(p, *keys):
    v = p
    for k in keys: v = (v or {}).get(k)
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None

def best_pair(token):
    d = get(f"{DS}/token-pairs/v1/solana/{token}")
    time.sleep(0.4)
    if isinstance(d, dict): d = d.get("pairs")
    paare = [p for p in (d or []) if p.get("chainId") == "solana"
             and (p.get("baseToken") or {}).get("address") == token and p.get("pairAddress")]
    return max(paare, key=_liq) if paare else None

def fetch_pairs(pair_addrs):
    out = []
    for k in range(0, len(pair_addrs), 30):
        d = get(f"{DS}/latest/dex/pairs/solana/{','.join(pair_addrs[k:k+30])}") or {}
        out += [p for p in (d.get("pairs") or []) if p and p.get("chainId") == "solana"]
        time.sleep(1.1)
    return out

def kurse(addrs, meta, now):
    """{token: paar} plus SOL. Coins ohne Pool werden gemerkt und erst nach 24 h erneut gesucht -
    in v5 blockierten sie die Suche dauerhaft, und ~110 handelbare Coins kamen nie an die Reihe."""
    pair_of = meta.setdefault("pair_of", {}); kein = meta.setdefault("no_pair", {})
    out = {}
    bekannt = [pair_of[a] for a in addrs if pair_of.get(a)]
    for p in fetch_pairs(bekannt) + fetch_pairs(SOL_REF_PAIRS):
        a = (p.get("baseToken") or {}).get("address")
        if a == SOL_MINT:
            if _liq(p) > _liq(out.get(SOL_MINT)): out[SOL_MINT] = p
        elif a in addrs and pair_of.get(a) == p.get("pairAddress"):
            out[a] = p
    offen = [a for a in addrs if a not in out and now - kein.get(a, 0) >= NO_PAIR_RETRY_S][:PAIR_LOOKUPS_PER_RUN]
    for a in offen:
        p = best_pair(a)
        if p: pair_of[a] = p["pairAddress"]; out[a] = p; kein.pop(a, None)
        else: kein[a] = now
    if SOL_MINT not in out:
        p = best_pair(SOL_MINT)
        if p: out[SOL_MINT] = p
    meta["no_pair"] = {a: t for a, t in kein.items() if a in addrs}
    return out

def change_7d(pool, px):
    if not pool: return None
    d = get(f"{GT}/pools/{pool}/ohlcv/day", {"limit": 9}); time.sleep(2.1)
    rows = sorted(((d or {}).get("data") or {}).get("attributes", {}).get("ohlcv_list") or [])
    if len(rows) < 8: return None
    ref = rows[-8][4]
    return (px / ref - 1) * 100 if ref else None


# ---------------------------------------------------------------- Neustart bei Strategiewechsel
def versionswechsel():
    """Alte Strategie -> Zustand archivieren und mit 500 $ neu beginnen. Nichts wird geloescht."""
    alt = load("bot_c_state.json", None)
    if not alt or alt.get("version") == VERSION: return
    os.makedirs(os.path.join(DATA, "archive"), exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M", time.gmtime())
    for name in ("bot_c_state.json", "bot_c_meta.json", "bot_c_daily.json", "bot_c_hist.json"):
        p = os.path.join(DATA, name)
        if os.path.exists(p): os.replace(p, os.path.join(DATA, "archive", f"{name[:-5]}_{alt.get('version', 'v5')}_{stamp}.json"))
    print(f"{BOT}: Strategiewechsel auf {VERSION} - alter Stand archiviert, Neustart mit {START_CAPITAL:.0f} $")


def main():
    versionswechsel()
    pf = Paper("bot_c"); st = pf.s; st["version"] = VERSION
    now = time.time(); today = int(now // 86400)
    meta = load("bot_c_meta.json", {}); hist = load("bot_c_hist.json", {}); cooldown = load("bot_c_cooldown.json", {})

    # 1) Universum
    if now - meta.get("last_discover", 0) >= DISCOVER_EVERY_S or not meta.get("universe"):
        u = universum_laden(now)
        if u: meta["universe"], meta["last_discover"] = u, now
        elif meta.get("universe"): print(f"{BOT}: Rangliste nicht erreichbar, nutze alte Liste")
    e_meta = load("bot_e_meta.json", {}); e_state = load("bot_e_state.json", {})
    tabu = set(e_meta.get("universe") or []) | set((e_state.get("positions") or {}))
    universe = [(a, s) for a, s in (meta.get("universe") or []) if a not in tabu]
    addrs = list(dict.fromkeys([a for a, _ in universe] + list(st["positions"])))
    if not addrs:
        print(f"{BOT}: kein Universum (COINGECKO_KEY?)"); pf.mark({}); pf.commit(); return

    pairs = kurse(addrs, meta, now)
    sol = pairs.get(SOL_MINT); sol24 = f(sol, "priceChange", "h24") if sol else None
    prices, checks = {}, []

    # 2) eigene 5-Minuten-Historie (fuer das 6h-Tief)
    for a, p in pairs.items():
        px = f(p, "priceUsd") or 0
        if px > 0:
            pts = hist.setdefault(a, []); pts.append([now, px])
            hist[a] = [x for x in pts if now - x[0] <= HIST_KEEP_S][-90:]
    for a in list(hist):
        if a not in addrs and a != SOL_MINT: del hist[a]

    # 3) Positionen verwalten (exakt wie Bot E)
    for a, pos in list(st["positions"].items()):
        p = pairs.get(a); px = f(p, "priceUsd") if p else None
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

    # 4) Kandidaten (werden IMMER ermittelt - auch bei vollem Depot, fuer Bot F)
    cands = []
    markt_ok = sol24 is not None and sol24 >= SOL_24H_MIN
    if sol24 is None: checks.append(("SOL", "Referenz fehlt -> kein Kauf"))
    elif not markt_ok: checks.append(("SOL", f"markt {sol24:+.1f}% -> kein Kauf"))
    else:
        for a, s in universe:
            p = pairs.get(a)
            if not p or a in st["positions"] or cooldown.get(a, 0) > today: continue
            c24, c1 = f(p, "priceChange", "h24"), f(p, "priceChange", "h1")
            mcap = f(p, "marketCap") or f(p, "fdv") or 0; liq = _liq(p)
            vol = f(p, "volume", "h24") or 0; px = f(p, "priceUsd") or 0
            if None in (c24, c1) or px <= 0 or mcap <= 0: continue
            if not (DROP_24H[0] <= c24 <= DROP_24H[1]): continue
            why = None
            if liq < U_MIN_LIQ: why = f"liq {liq/1e3:.0f}k < {U_MIN_LIQ/1e3:.0f}k"
            elif c24 > sol24 - REL_TO_SOL_PP: why = f"nur {c24 - sol24:+.0f}pp vs SOL"
            elif c1 < STAB_1H_MIN: why = f"1h {c1:+.1f}% faellt noch"
            elif vol / mcap < VOL_TO_MCAP_MIN: why = f"vol/mcap {vol/mcap:.1%}"
            else:
                pts = [x[1] for x in hist.get(a, []) if now - x[0] <= 6 * 3600]
                if len(pts) < 6: why = "historie fehlt"
                elif not (BOUNCE_6H[0] / 100 <= px / min(pts) - 1 <= BOUNCE_6H[1] / 100): why = f"{px/min(pts)-1:+.1%} ueber 6h-Tief"
                else:
                    c7 = change_7d(p.get("pairAddress"), px)
                    if c7 is None: why = "7d-daten fehlen"
                    elif c7 < MAX_DROP_7D: why = f"7d {c7:+.0f}% = abwaertstrend"
            if why: checks.append((s, f"24h {c24:+.0f}% | {why}")); continue
            cands.append((c24 - sol24, a, s, px, liq, c24, c1, mcap, p.get("pairAddress")))
        cands.sort()
    save("signale_c.json", {"t": now, "bot": "C", "kand": [{"addr": a, "sym": s, "px": px, "pair": pr, "liq": round(liq)}
                                                           for _, a, s, px, liq, _, _, _, pr in cands]})

    # 5) Kaufen
    if markt_ok and meta.get("paused_until", 0) <= today:
        equity = st["cash"] + sum(q["qty"] * (q.get("mark") or q.get("cur_price") or q["entry"]) for q in st["positions"].values())
        for rel, a, sym, px, liq, c24, c1, mcap, pr in cands[:max(0, MAX_POS - len(st["positions"]))]:
            usd = min(st["cash"] - 1, equity * POS_FRAC)
            if usd < 20: break
            if pf.buy(sym, a, px, usd, liq, f"dip 24h {c24:+.0f}% (SOL {sol24:+.0f}%) 1h {c1:+.1f}%"):
                prices[a] = px
                checks.append((sym, f"GEKAUFT {usd:.0f}$ | 24h {c24:+.0f}% vs SOL {sol24:+.0f}%"))
                append_jsonl("bot_c_signals.jsonl", {"t": now_iso(), "addr": a, "sym": sym, "c24": c24, "c1": c1,
                                                     "sol24": sol24, "mcap": mcap, "liq": round(liq), "usd": round(usd, 2)})

    save("bot_c_meta.json", meta); save("bot_c_hist.json", hist)
    save("bot_c_cooldown.json", {k: v for k, v in cooldown.items() if v > today})
    st["strategy"] = "dip_weites_netz"; st["universe_n"] = len(universe)
    st["coverage"] = {"t": now_iso(), "kurse": sum(1 for a in addrs if a in pairs), "von": len(addrs),
                      "ohne_pool": len(meta.get("no_pair", {})), "sol": sol24 is not None, "kandidaten": len(cands)}
    v = pf.mark(prices); pf.commit()
    print(f"{BOT} [dip weites netz]: equity {v:.2f} | cash {st['cash']:.2f} | positionen {len(st['positions'])}/{MAX_POS}"
          f" | kurse {st['coverage']['kurse']}/{len(addrs)} (ohne pool {st['coverage']['ohne_pool']})"
          f" | kandidaten {len(cands)} | SOL 24h {sol24 if sol24 is None else round(sol24, 1)}")
    for sym, why in checks[:12]: print(f"   {sym:<10} {why}")


if __name__ == "__main__":
    main()
