"""Bot E v4.2 – "Large-Cap Dip": EINE konzentrierte Position (90 % des Cash) in einem der ~50 groessten
Solana-Tokens, nachdem dieser in 24 h deutlich staerker gefallen ist als der Gesamtmarkt und der
Verkaufsdruck sichtbar nachgelassen hat. Harter Stop -10 %, Teilgewinn +15 %, Trailing fuer den Rest.

Warum so: Das alte "Overreaction Fade" (v1-v3) hat auf 60-300k-MCap-Tokens 18 von 29 Trades per Stop
beendet (-435 $) - dort ist ein -30 % Tag kein Ausrutscher, sondern oft der Anfang vom Ende. Bei
Tokens mit >30 M MCap und >1 M Liquiditaet gibt es Rugpulls praktisch nicht, Slippage ist ~0 und ein
Uebertreibungs-Tag wird statistisch deutlich oefter wieder aufgeholt.

Datenquellen: Codex (Universum = Top-Tokens nach MCap, alle 6 h), DexScreener (Kurs/Change/Liq/Vol,
jeder Lauf), Jupiter (Fill-Preise wie bei allen Bots). Kein Rugcheck noetig (Universum ist etabliert).
"""
import os, time
from common import *

CODEX_KEY = os.environ.get("CODEX_KEY")
CODEX_URL = "https://graph.codex.io/graphql"
SOLANA, SOL_MINT = 1399811149, "So11111111111111111111111111111111111111112"

# ---- Universum ----
U_MIN_MCAP, U_MIN_LIQ, U_MIN_VOL24 = 10_000_000, 300_000, 200_000   # Testphase: ~Top 100 statt Top 50
U_LIMIT = 120
DISCOVER_EVERY_S = 12 * 3600      # Top-100 nach MCap aendert sich kaum; 2 Codex-Calls/Tag (~60/Monat)
EXCLUDE_SYM = {"USDC", "USDT", "USDS", "PYUSD", "USD1", "DAI", "FDUSD", "USDE", "EURC", "USDG", "USDY", "CASH",
               "SOL", "WSOL", "ETH", "WETH", "BTC", "WBTC", "CBBTC", "TBTC", "WBNB", "BNB",       # Majors (gewrappt)
               "JITOSOL", "MSOL", "BSOL", "JUPSOL", "INF", "BNSOL", "HSOL", "DSOL", "VSOL", "JLP"}  # LSTs/LP = SOL-Derivate
EXCLUDE_SUB = ("USD", "EUR", "GBP", "CHF", "JPY", "XAU", "SOL")   # "...SOL" faengt restliche LSTs

# ---- Einstieg (alle Bedingungen muessen gleichzeitig gelten) ----
DROP_24H = (-30.0, -10.0)   # Token-24h-Aenderung in %: deutlicher Ausverkauf, aber kein Kollaps
REL_TO_SOL_PP = 6.0         # Token muss mind. 8 Prozentpunkte schlechter als SOL sein (= tokenspezifische Panik,
                            # nicht einfach "alles faellt")
SOL_24H_MIN = -12.0         # bei breitem Markt-Crash (SOL < -12 %) gar nicht kaufen - Messer faellt
STAB_1H_MIN = -1.5          # letzte Stunde >= -1,5 %: Verkaufsdruck laesst nach (kein Fallen-Messer-Kauf)
BOUNCE_6H = (1.5, 6.0)      # Kurs 1,5-6 % UEBER dem 6h-Tief: die Erholung hat begonnen (Bestaetigung), ist aber
                            # noch nicht gelaufen. Am Tief selbst wird nicht gekauft (das ist Messer-Fangen).
MAX_DROP_7D = -35.0         # 7-Tage-Aenderung (GeckoTerminal-Tageskerzen): ein -15 %-Tag in einer -50 %-Woche ist
                            # Abwaertstrend, keine Uebertreibung -> nicht kaufen
VOL_TO_MCAP_MIN = 0.03      # 24h-Volumen >= 3 % der MCap: echter Umsatz, keine Duennluft-Drift
COOLDOWN_D = 5              # gestoppter Token 5 Tage sperren
MAX_CONSEC_STOPS, PAUSE_D = 3, 3   # 3 Stops in Folge -> 3 Tage Pause (Regime passt nicht)

# ---- Ausstieg ----
MAX_POS = 2                 # Testphase: 2 Positionen statt 1 -> doppelt so viele Datenpunkte, halbe Varianz
POS_FRAC = 0.45             # 45 % des GESAMTKAPITALS (Cash + offene Positionen) je Position, nicht des freien Cash.
                            # So ist Position 2 genauso gross wie Position 1, und die Groesse waechst/schrumpft
                            # mit dem Depot statt mit dem zufaelligen Cash-Rest.
STOP = -0.10                # harter Stop ab Einstand
TP1_GAIN, TP1_FRAC = 0.15, 0.5   # bei +15 % die Haelfte verkaufen
TRAIL_ARM, TRAIL_GIVEBACK = 0.08, -0.06   # ab +8 % Hoch: Rueckgabe 6 % vom Hoch -> raus
BREAKEVEN_AFTER_TP1 = True  # nach TP1 Stop auf Einstand ziehen: der Rest kann nichts mehr kosten
MAX_HOLD_D, FLAT_EXIT_GAIN = 7, 0.03      # nach 7 Tagen raus, wenn unter +3 %
HIST_KEEP_S = 7 * 3600

def tradeable(sym):
    sym = (sym or "").upper()
    if sym in EXCLUDE_SYM: return False
    return not any(s in sym for s in EXCLUDE_SUB)

Q_E = """
query($net:[Int!]) {
  filterTokens(filters:{ network:$net, marketCap:{gte:%d}, liquidity:{gte:%d}, volume24:{gte:%d} },
               rankings:[{attribute:marketCap, direction:DESC}], limit:%d)
  { results { token { address symbol } } }
}""" % (U_MIN_MCAP, U_MIN_LIQ, U_MIN_VOL24, U_LIMIT)

def codex_universe():
    if not CODEX_KEY: return None
    try:
        r = requests.post(CODEX_URL, headers={"Authorization": CODEX_KEY, "Content-Type": "application/json", **UA},
                          json={"query": Q_E, "variables": {"net": [SOLANA]}}, timeout=30)
        r.raise_for_status(); d = r.json()
        if d.get("errors"): print("codex:", str(d["errors"])[:200]); return None
        return [x["token"]["address"] for x in d["data"]["filterTokens"]["results"] if tradeable(x["token"]["symbol"])]
    except Exception as e:
        print("codex fehler:", e); return None

# v4.1 - Kurse ueber feste PAAR-Adressen statt ueber Token-Adressen.
# Bis 20.09. fragte Bot E DexScreener mit /latest/dex/tokens/<30 Tokens> ab. Diese Antwort enthaelt aber
# hoechstens 30 PAARE insgesamt - und Large Caps haben Dutzende Paare je Token. Folge (gemessen an 40 Laeufen):
# nur 41-47 der 72 Tokens bekamen einen Kurs, 14 nie, und SOL selbst nur in 2 von 40 Laeufen. Ohne SOL-Kurs
# gilt "SOL-Referenz fehlt -> kein Kauf" - Bot E war deshalb seit dem 18.09. praktisch blind.
# Jetzt: je Token einmal das liquideste Paar suchen (/token-pairs, 1 Token je Abfrage, alle 12 h neu),
# danach jeden Lauf genau diese Paare abfragen (/latest/dex/pairs, 30 Paare je Abfrage = 30 Tokens).
SOL_REF_PAIRS = ["58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2",   # Raydium SOL/USDC
                 "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"]   # Orca SOL/USDC (Reserve)

def _liq(p): return float(((p or {}).get("liquidity") or {}).get("usd") or 0)

def best_pair(token):
    """Liquidestes Solana-Paar, in dem der Token BASIS ist (sonst waere priceUsd der Kurs des Gegenstuecks)."""
    d = get(f"{DS}/token-pairs/v1/solana/{token}")
    time.sleep(0.3)                                   # Limit 300/Min
    if isinstance(d, dict): d = d.get("pairs")          # Format-Absicherung (Liste laut Doku)
    pairs = [p for p in (d or []) if p.get("chainId") == "solana"
             and (p.get("baseToken") or {}).get("address") == token and p.get("pairAddress")]
    return max(pairs, key=_liq) if pairs else None

def fetch_pairs(pair_addrs):
    out = []
    for k in range(0, len(pair_addrs), 30):
        d = get(f"{DS}/latest/dex/pairs/solana/{','.join(pair_addrs[k:k+30])}") or {}
        out += [p for p in (d.get("pairs") or []) if p and p.get("chainId") == "solana"]
        time.sleep(1.1)
    return out

def batch_pairs(addrs, meta):
    """{token: paar} fuer alle addrs, plus SOL unter SOL_MINT. Paar-Adressen werden in meta["pair_of"] gemerkt."""
    pair_of = meta.setdefault("pair_of", {})
    out = {}
    known = [pair_of[a] for a in addrs if pair_of.get(a)]
    for p in fetch_pairs(known) + fetch_pairs(SOL_REF_PAIRS):   # getrennt: ein fehlerhaftes Paar stoert nicht die anderen
        a = (p.get("baseToken") or {}).get("address")
        if a == SOL_MINT:
            if _liq(p) > _liq(out.get(SOL_MINT)): out[SOL_MINT] = p
        elif a in addrs and pair_of.get(a) == p.get("pairAddress"):
            out[a] = p
    for a in addrs:                                   # neu im Universum oder Paar verschwunden -> einmal suchen
        if a in out: continue
        p = best_pair(a)
        if p: pair_of[a] = p["pairAddress"]; out[a] = p
    if SOL_MINT not in out:                           # Reserve fuer die Referenz: liquidestes SOL-Paar suchen
        p = best_pair(SOL_MINT)
        if p: out[SOL_MINT] = p
    return out

GT = "https://api.geckoterminal.com/api/v2/networks/solana"

def change_7d(pool, px):
    """7-Tage-Aenderung in % aus GeckoTerminal-Tageskerzen (kostenlos). Nur fuer echte Kandidaten aufgerufen."""
    if not pool: return None
    d = get(f"{GT}/pools/{pool}/ohlcv/day", {"limit": 9}); time.sleep(2.1)
    rows = ((d or {}).get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
    rows = sorted(rows)
    if len(rows) < 8: return None
    ref = rows[-8][4]                                # Schlusskurs vor 7 Tagen
    return (px / ref - 1) * 100 if ref else None

def f(p, *keys):
    v = p
    for k in keys: v = (v or {}).get(k)
    return float(v) if v is not None else None

def main():
    pf = Paper("bot_e"); st = pf.s
    now = time.time(); today = int(now // 86400)
    meta = load("bot_e_meta.json", {}); hist = load("bot_e_hist.json", {}); cooldown = load("bot_e_cooldown.json", {})
    if now - meta.get("last_discover", 0) >= DISCOVER_EVERY_S or not meta.get("universe"):
        u = codex_universe()
        if u: meta["universe"], meta["last_discover"], meta["pair_of"] = u, now, {}   # Paare neu suchen (Liquiditaet wandert)
        elif meta.get("universe"): print("Bot E: Codex nicht erreichbar, nutze altes Universum")
    universe = list(set(meta.get("universe", [])) | set(st["positions"].keys()))
    if not universe: print("Bot E: kein Universum (CODEX_KEY?)"); pf.mark({}); pf.commit(); return
    pairs = batch_pairs(universe, meta)
    sol = pairs.get(SOL_MINT); sol24 = f(sol, "priceChange", "h24") if sol else None
    prices, checks = {}, []

    # 1) eigene Kurs-Historie (fuer 6h-Tief) fortschreiben
    for a, p in pairs.items():
        px = f(p, "priceUsd") or 0
        if px > 0:
            pts = hist.setdefault(a, []); pts.append([now, px])
            hist[a] = [x for x in pts if now - x[0] <= HIST_KEEP_S][-90:]   # 60 Punkte waren nur 5 h statt 6 h
    for a in list(hist):
        if a not in universe and a != SOL_MINT: del hist[a]   # nur bei Austritt aus dem Universum loeschen

    # 2) offene Position verwalten
    for a, pos in list(st["positions"].items()):
        p = pairs.get(a); px = f(p, "priceUsd") if p else None
        if not px or px <= 0: continue
        prices[a] = px; liq = f(p, "liquidity", "usd") or 0
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
            pf.sell(a, px, 1.0, liq, why); meta["last_sell"] = now
            if why == "stop":
                cooldown[a] = today + COOLDOWN_D; meta["consec_stops"] = meta.get("consec_stops", 0) + 1
                if meta["consec_stops"] >= MAX_CONSEC_STOPS:
                    meta["paused_until"] = today + PAUSE_D; meta["consec_stops"] = 0
                    print(f"Bot E: {MAX_CONSEC_STOPS} Stops in Folge -> Pause bis Tag {meta['paused_until']}")
            else: meta["consec_stops"] = 0

    # 3) Einstieg pruefen. Kandidaten werden seit v4.2 auch bei vollem Depot ermittelt und fuer Bot F
    #    gespeichert (data/signale_e.json); GEKAUFT wird genau wie vorher nur, wenn can_buy gilt.
    can_buy = len(st["positions"]) < MAX_POS and meta.get("paused_until", 0) <= today
    markt_ok = True
    if sol24 is None: print("Bot E: SOL-Referenz fehlt -> kein Kauf"); can_buy = markt_ok = False
    elif sol24 < SOL_24H_MIN: checks.append(("SOL", f"markt {sol24:+.1f}% -> kein Kauf")); can_buy = markt_ok = False
    cands = []
    if markt_ok:
        for a, p in pairs.items():
            if a == SOL_MINT or a in st["positions"] or cooldown.get(a, 0) > today: continue
            sym = p["baseToken"]["symbol"]
            if not tradeable(sym): continue
            c24, c1, c6 = f(p, "priceChange", "h24"), f(p, "priceChange", "h1"), f(p, "priceChange", "h6")
            mcap = f(p, "marketCap") or f(p, "fdv") or 0; liq = f(p, "liquidity", "usd") or 0
            vol = f(p, "volume", "h24") or 0; px = f(p, "priceUsd") or 0
            if None in (c24, c1) or px <= 0: continue
            if not (DROP_24H[0] <= c24 <= DROP_24H[1]): continue          # normaler Tag - nichts loggen
            why = None
            if mcap < U_MIN_MCAP or liq < U_MIN_LIQ: why = f"mcap/liq {mcap/1e6:.0f}M/{liq/1e6:.1f}M"
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
            if why: checks.append((sym, f"24h {c24:+.0f}% | {why}")); continue
            cands.append((c24 - sol24, a, sym, px, liq, c24, c1, mcap))
        cands.sort()                                                        # staerkste relative Uebertreibung zuerst
    save("signale_e.json", {"t": now, "bot": "E", "kand": [{"addr": a, "sym": sym, "px": px, "liq": round(liq),
                                                            "pair": (pairs.get(a) or {}).get("pairAddress")}
                                                           for _, a, sym, px, liq, _, _, _ in cands]})
    if can_buy:
        equity = st["cash"] + sum(p["qty"] * (p.get("mark") or p.get("cur_price") or p["entry"]) for p in st["positions"].values())
        for rel, a, sym, px, liq, c24, c1, mcap in cands[:MAX_POS - len(st["positions"])]:
            usd = min(st["cash"] - 1, equity * POS_FRAC)
            if usd < 20: break
            if pf.buy(sym, a, px, usd, liq, f"dip 24h {c24:+.0f}% (SOL {sol24:+.0f}%) 1h {c1:+.1f}%"):
                checks.append((sym, f"GEKAUFT {usd:.0f}$ | 24h {c24:+.0f}% vs SOL {sol24:+.0f}%"))
                append_jsonl("bot_e_signals.jsonl", {"t": now_iso(), "addr": a, "sym": sym, "c24": c24, "c1": c1,
                                                     "sol24": sol24, "mcap": mcap, "usd": round(usd, 2)})

    save("bot_e_meta.json", meta); save("bot_e_hist.json", hist)
    save("bot_e_cooldown.json", {k: v for k, v in cooldown.items() if v > today})
    st["strategy"] = "largecap_dip"; st["universe_n"] = len(universe)
    st["coverage"] = {"t": now_iso(), "kurse": sum(1 for a in universe if a in pairs), "von": len(universe),
                      "sol": sol24 is not None}
    v = pf.mark(prices); pf.commit()
    print(f"Bot E [largecap dip]: equity {v:.2f} | cash {st['cash']:.2f} | positions {len(st['positions'])} "
          f"| kurse {st['coverage']['kurse']}/{len(universe)} | SOL 24h {sol24 if sol24 is None else round(sol24, 1)}")
    for sym, why in checks[:12]: print(f"   {sym:<10} {why}")

if __name__ == "__main__":
    main()
