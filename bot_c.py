"""Bot C v5 – "Rueckschlag": kauft die drei Solana-Coins aus Rang 30-200, die am weitesten unter
ihrem 90-Tage-Hoch stehen und gerade wieder Umsatz ziehen - aber nur, solange SOL ueber seinem
10-Tage-Durchschnitt liegt. Sonst Cash. Haltedauer 14 Tage, kein Stop.

GEMESSEN an 665 Solana-Coins ueber 365 Tage (data/top_hist.json), getrennt nach Halbjahren:
    diese Einstellung:            +6,7 %/Woche (1. Hj)   +6,8 %/Woche (2. Hj)
    Median der aktiven Wochen:    +6,7 %                 +8,5 %   (also nicht von Ausreissern getragen)
    schlimmste Woche: -29,6 %  |  Kosten verdoppelt (2 % je Seite): immer noch positiv
    zufaellige Coins, gleicher Marktfilter: -29 % im Jahr | Top-200 einfach halten: -57 %

WAS SICH GEGENUEBER v4 GEAENDERT HAT (alles an denselben Daten gemessen, beide Halbjahren getrennt):
 1) NUR NOCH DIE DREI TIEFSTEN. Der Vorteil steckt ausschliesslich dort: Plaetze 1-3 der nach Tiefe
    sortierten Liste ergeben +6,7 %/+6,8 % je Woche, Plaetze 4-6 nur -0,9 %/+1,8 %, Plaetze 9-11
    sogar -2,2 %/-0,7 %. Mehr Positionen verwaessern also genau das, was funktioniert.
 2) HALTEDAUER 14 STATT 7 TAGE. Der Rueckschlag braucht laenger als eine Woche: 7 Tage ergaben
    +1,8 %/+0,9 %, 14 Tage +2,1 %/+3,5 % (jeweils in der 8-Positionen-Variante gemessen).
 3) MARKTFILTER AUF 10 STATT 20 TAGE. Ueber alle 648 getesteten Kombinationen gemittelt der mit
    Abstand staerkste einzelne Hebel: MA10 +2,6 %/+3,8 % je Woche, MA20 -1,3 %/+3,4 %, MA30 -1,1 %/+2,6 %.
 4) RANGLISTE VON COINGECKO STATT CODEX. Die Regel wurde an der CoinGecko-Rangliste gemessen; live
    nach einer anderen Liste zu handeln waere ein anderer Test. Zudem lieferte die Codex-Abfrage
    mit hohem Limit live nichts ("universum 0").

WARUM DIE EINZELNEN BEDINGUNGEN:
 - TIEF UNTER DEM 90-TAGE-HOCH: Rueckschlag schlaegt Momentum deutlich. Die am weitesten gefallenen
   Coins brachten +9,7 %/Woche (1. Hj) bzw. +0,8 % (2. Hj) MEHR als der Markt, die staerksten -0,8 %/+0,5 %.
 - UMSATZSCHUB (24h-Umsatz >= 1,3x des eigenen 30-Tage-Schnitts) als Bestaetigung, dass wieder
   gekauft wird. Umsatzschub allein, ohne Rueckschlag: -59 % im Jahr.
 - KEIN ABSTURZ (letzte 7 Tage besser als -25 %): nicht ins fallende Messer greifen.
 - MARKTFILTER: ohne ihn -58 % im Jahr statt +445 %. Der wichtigste Schalter ueberhaupt.
 - KEIN STOP: ein Stop bei -20 % verschlechtert das Ergebnis messbar, weil diese Coins typischerweise
   kurz nach dem Tief drehen. Das Risiko begrenzt die feste Haltedauer.

EHRLICHE GRENZEN: Der Marktfilter laesst nur 19 der 38 Wochen zu, die Auswertung steht also auf
19 aktiven Wochen. CoinGecko kennt nur heute noch existierende Coins; verschwundene fehlen, das
Ergebnis faellt dadurch eher zu gut aus.

Datenquellen: CoinGecko (Rangliste, 2x taeglich), DexScreener (Kurse/Umsatz je Lauf ueber feste
Paar-Adressen), GeckoTerminal (Tageskerzen fuer Hoch, Umsatzschnitt, SOL-Durchschnitt),
Jupiter (Fill-Preise ueber common.Paper).
"""
import os, time, statistics
from common import *

SOLANA, SOL_MINT = 1399811149, "So11111111111111111111111111111111111111112"
GT = "https://api.geckoterminal.com/api/v2/networks/solana"

# ---------- Universum: Rang 30-200 nach MCap ----------
RANG_VON, RANG_BIS = 30, 200
U_MIN_LIQ = 150_000            # unter dieser Pool-Liquiditaet ist ein 100-$-Kauf nicht sauber handelbar
U_MIN_VOL24 = 50_000
DISCOVER_EVERY_S = 12 * 3600   # Rangliste 2x taeglich
EXCLUDE_SYM = {"USDC", "USDT", "USDS", "PYUSD", "USD1", "DAI", "FDUSD", "USDE", "EURC", "USDG", "USDY", "CASH",
               "SOL", "WSOL", "ETH", "WETH", "BTC", "WBTC", "CBBTC", "TBTC", "WBNB", "BNB",
               "JITOSOL", "MSOL", "BSOL", "JUPSOL", "INF", "BNSOL", "HSOL", "DSOL", "VSOL", "JLP"}
EXCLUDE_SUB = ("USD", "EUR", "GBP", "CHF", "JPY", "XAU", "SOL")

# ---------- Einstieg ----------
SCHUB_MIN = 1.3                # 24h-Umsatz >= 1,3x des eigenen 30-Tage-Schnitts
MOM7_MIN = -0.25               # letzte 7 Tage besser als -25 %
HOCH_TAGE = 90                 # Bezugshoch
MAX_VOM_HOCH = -0.15           # mindestens 15 % unter dem 90-Tage-Hoch, sonst ist es kein Rueckschlag
SOL_MA_TAGE = 10               # Marktfilter
MAX_POS = 3                    # nur die 3 am tiefsten gefallenen Coins, je ein Drittel des Kapitals
POS_FRAC = 0.33
MAX_BUYS_PER_RUN = 1           # gestaffelt einsteigen statt alles in einer Minute
COOLDOWN_D = 7                 # ein verkaufter Coin ist 7 Tage gesperrt

# ---------- Ausstieg ----------
HALTE_D = 14                   # getestete Haltedauer
LIQ_EXIT_DROP = -0.50          # Notbremse: Liquiditaet halbiert -> raus (kam im Test nie vor, schuetzt aber)
DEAD_PRICE_H = 24              # 24 h ohne Kurs -> abschreiben

# ---------- Laufzeitschutz (run_paper.sh bricht nach 240 s ab) ----------
GT_PER_RUN = 10                # so viele Tageskerzen-Abrufe je Lauf (je ~2,1 s)
PAIR_LOOKUPS_PER_RUN = 25      # so viele Paar-Suchen je Lauf (je ~0,4 s)
DAILY_MAX_AGE_S = 20 * 3600    # Tageskerzen hoechstens 20 h alt verwenden


def tradeable(sym):
    sym = (sym or "").upper()
    if sym in EXCLUDE_SYM: return False
    return not any(s in sym for s in EXCLUDE_SUB)


BOT = "Bot C"
# ---------------------------------------------------------------- Universum ueber CoinGecko
# Bis 20.09. kam die Rangliste von Codex. Ergebnis live: "universum 0" - die Abfrage mit hohem
# Limit lieferte nichts. Wichtiger noch: die Backtest-Rangliste stammte aus CoinGecko. Wenn der Bot
# live nach einer anderen Rangliste handelt als die, an der die Regel gemessen wurde, misst man
# zwei verschiedene Dinge. Deshalb jetzt dieselbe Quelle wie im Backtest. Codex wird hier nicht mehr gebraucht.
CG = "https://api.coingecko.com/api/v3"
CG_KEY = os.environ.get("COINGECKO_KEY")
CG_HDR = {**UA, **({"x-cg-demo-api-key": CG_KEY} if CG_KEY else {})}
ADDR_CACHE_S = 24 * 3600


def cg_get(pfad, params):
    try:
        r = requests.get(f"{CG}{pfad}", params=params, headers=CG_HDR, timeout=40)
        if r.status_code != 200:
            print(f"{BOT}: CoinGecko {r.status_code} bei {pfad}"); return None
        return r.json()
    except Exception as e:
        print(f"{BOT}: CoinGecko Fehler: {e}"); return None


def solana_adressen(meta, now):
    """{coingecko_id: solana-mint}. Wird hoechstens einmal taeglich neu geholt."""
    c = load("cg_solana_addr.json", {})
    if c.get("t", 0) and now - c["t"] < ADDR_CACHE_S and c.get("a"): return c["a"]
    lst = cg_get("/coins/list", {"include_platform": "true"})
    if not lst: return c.get("a") or {}
    a = {x["id"]: (x.get("platforms") or {}).get("solana") for x in lst}
    a = {k: v for k, v in a.items() if v}
    save("cg_solana_addr.json", {"t": now, "a": a})
    return a


def universum_laden(meta, now):
    """[(addr, sym)] fuer Rang RANG_VON..RANG_BIS. Rang = Platz unter den handelbaren Solana-Coins
    nach Marktkapitalisierung - genau wie in der Auswertung. None = Fehler."""
    adr = solana_adressen(meta, now)
    if not adr: return None
    reihe = []
    for seite in range(1, 4):
        res = cg_get("/coins/markets", {"vs_currency": "usd", "category": "solana-ecosystem",
                                        "order": "market_cap_desc", "per_page": 250, "page": seite})
        if res is None: break
        for x in res:
            a = adr.get(x["id"]); sym = (x.get("symbol") or "").upper()
            if a and tradeable(sym) and (x.get("total_volume") or 0) >= U_MIN_VOL24:
                reihe.append((a, sym))
        if len(reihe) >= RANG_BIS or len(res) < 250: break
        time.sleep(2.2)
    if len(reihe) < RANG_VON:
        print(f"{BOT}: CoinGecko lieferte nur {len(reihe)} Coins - zu wenig fuer Rang {RANG_VON}")
        return None
    return reihe[RANG_VON - 1:RANG_BIS]


# ---------------- DexScreener ueber feste Paar-Adressen ----------------
# Die Abfrage /latest/dex/tokens/<30 Tokens> liefert insgesamt nur ~30 PAARE. Grosse Coins haben
# Dutzende Paare und verdraengen dabei die anderen Tokens (bei Bot E bekam so nur die Haelfte des
# Universums einen Kurs). Deshalb: je Token einmal das liquideste Paar suchen, danach die Paare
# direkt abfragen - dort liefert jede Adresse genau ein Ergebnis.
SOL_REF_PAIRS = ["58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2",   # Raydium SOL/USDC
                 "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"]   # Orca SOL/USDC (Reserve)


def _liq(p): return float(((p or {}).get("liquidity") or {}).get("usd") or 0)
def px_of(p):
    try: return float((p or {}).get("priceUsd") or 0)
    except (TypeError, ValueError): return 0.0
def vol24_of(p):
    try: return float(((p or {}).get("volume") or {}).get("h24") or 0)
    except (TypeError, ValueError): return 0.0


def best_pair(token):
    """Liquidestes Solana-Paar, in dem der Token BASIS ist (sonst waere priceUsd der Kurs des Gegenstuecks)."""
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


def kurse(addrs, meta):
    """{token: paar} plus SOL unter SOL_MINT. Unbekannte Paare werden gedrosselt nachgeschlagen."""
    pair_of = meta.setdefault("pair_of", {})
    out = {}
    bekannt = [pair_of[a] for a in addrs if pair_of.get(a)]
    for p in fetch_pairs(bekannt) + fetch_pairs(SOL_REF_PAIRS):
        a = (p.get("baseToken") or {}).get("address")
        if a == SOL_MINT:
            if _liq(p) > _liq(out.get(SOL_MINT)): out[SOL_MINT] = p
        elif a in addrs and pair_of.get(a) == p.get("pairAddress"):
            out[a] = p
    offen = [a for a in addrs if a not in out][:PAIR_LOOKUPS_PER_RUN]
    for a in offen:
        p = best_pair(a)
        if p: pair_of[a] = p["pairAddress"]; out[a] = p
    if SOL_MINT not in out:
        p = best_pair(SOL_MINT)
        if p: out[SOL_MINT] = p
    return out


# ---------------- GeckoTerminal: Tageskerzen (Hoch, Umsatzschnitt, SOL-Durchschnitt) ----------------
def tageskerzen(pool, limit=100):
    """[(zeit, schlusskurs, umsatz_usd), ...] aufsteigend. None bei Fehler."""
    d = get(f"{GT}/pools/{pool}/ohlcv/day", {"limit": limit}); time.sleep(2.1)
    rows = ((d or {}).get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
    if not rows: return None
    return [(int(r[0]), float(r[4]), float(r[5])) for r in sorted(rows)]


def daily_pflegen(addrs, pairs, cache, now):
    """Aktualisiert hoechstens GT_PER_RUN Eintraege je Lauf. Aelteste zuerst."""
    faellig = []
    for a in addrs:
        p = pairs.get(a)
        if not p or not p.get("pairAddress"): continue
        c = cache.get(a)
        if not c or now - c.get("t", 0) >= DAILY_MAX_AGE_S: faellig.append((c.get("t", 0) if c else 0, a, p["pairAddress"]))
    faellig.sort()
    for _, a, pool in faellig[:GT_PER_RUN]:
        k = tageskerzen(pool)
        if k: cache[a] = {"t": now, "k": [[x[0], x[1], x[2]] for x in k[-HOCH_TAGE:]]}
        else: cache[a] = {"t": now, "k": (cache.get(a) or {}).get("k") or []}
    return len(faellig)


def markt_ok(cache, px_sol, now):
    """SOL ueber seinem 20-Tage-Durchschnitt? None = unbekannt (dann wird nicht gekauft)."""
    c = cache.get(SOL_MINT)
    if not c or not c.get("k") or not px_sol: return None
    schluss = [x[1] for x in c["k"]][-SOL_MA_TAGE:]
    if len(schluss) < SOL_MA_TAGE * 0.8: return None
    return px_sol > statistics.mean(schluss)


def signal(a, p, cache, now):
    """(vom_hoch, schub) wenn der Coin alle Bedingungen erfuellt, sonst (None, grund)."""
    k = (cache.get(a) or {}).get("k") or []
    if len(k) < 35: return None, "zu wenig historie"
    px = px_of(p)
    if px <= 0: return None, "kein kurs"
    schluss = [x[1] for x in k]
    hoch = max(max(schluss), px)
    vom_hoch = px / hoch - 1
    if vom_hoch > MAX_VOM_HOCH: return None, f"nur {vom_hoch:+.0%} unter Hoch"
    if len(schluss) >= 8 and schluss[-8] > 0:
        mom7 = px / schluss[-8] - 1
        if mom7 <= MOM7_MIN: return None, f"7d {mom7:+.0%} (Absturz)"
    umsaetze = [x[2] for x in k[-30:] if x[2] > 0]
    if len(umsaetze) < 20: return None, "zu wenig umsatzdaten"
    schnitt = statistics.mean(umsaetze)
    schub = (vol24_of(p) / schnitt) if schnitt > 0 else 0
    if schub < SCHUB_MIN: return None, f"umsatz {schub:.1f}x"
    return (vom_hoch, schub), "ok"


def main():
    pf = Paper("bot_c"); st = pf.s
    st.setdefault("cooldown", {})
    now = time.time(); today = int(now // 86400)
    meta = load("bot_c_meta.json", {"universe": [], "last_discover": 0})
    cache = load("bot_c_daily.json", {})

    # 1) Universum (Rang 30-200), alle 12 h
    if now - meta.get("last_discover", 0) >= DISCOVER_EVERY_S or not meta.get("universe"):
        u = universum_laden(meta, now)
        if u:
            meta["universe"], meta["last_discover"] = u, now
            gueltig = {a for a, _ in u} | set(st["positions"])
            meta["pair_of"] = {k: v for k, v in (meta.get("pair_of") or {}).items() if k in gueltig}
        elif meta.get("universe"):
            print("Bot C: Rangliste nicht erreichbar, nutze alte Liste")
    universe = meta.get("universe", [])
    syms = {a: s for a, s in universe}
    addrs = list(dict.fromkeys([a for a, _ in universe] + list(st["positions"])))
    if not addrs:
        print("Bot C: kein Universum (COINGECKO_KEY?)"); pf.mark({}); pf.commit(); return

    # 2) Kurse + Tageskerzen
    pairs = kurse(addrs + [SOL_MINT], meta)
    prices = {a: px_of(p) for a, p in pairs.items() if px_of(p) > 0}
    offen_daily = daily_pflegen([SOL_MINT] + addrs, pairs, cache, now)

    log = []
    # 3) Positionen: Zeitausstieg, Notbremse, Totalausfall
    for a, pos in list(st["positions"].items()):
        px = prices.get(a)
        if not px:
            seit = pos.get("no_px_since") or pos["opened"]
            pos.setdefault("no_px_since", now_iso())
            if (now - parse_iso(seit)) / 3600 >= DEAD_PRICE_H and pos.get("qty", 0) > 0:
                verlust = round(pos.get("cost", 0.0), 2)
                pos["qty"] = 0.0; pos["cost"] = 0.0
                st["trades"].append({"t": now_iso(), "side": "sell", "sym": pos["sym"], "addr": a, "price": 0.0,
                                     "usd": 0.0, "pnl": -verlust, "frac": 1.0, "reason": "abgeschrieben (kein kurs)"})
                del st["positions"][a]; st["cooldown"][a] = today + COOLDOWN_D
                log.append((pos["sym"], f"abgeschrieben - {DEAD_PRICE_H} h ohne Kurs"))
            continue
        pos.pop("no_px_since", None)
        liq = _liq(pairs.get(a))
        gehalten_d = held_seconds(pos) / 86400
        warum = None
        if gehalten_d >= HALTE_D: warum = "haltedauer"
        elif liq and pos.get("entry_liq") and liq / pos["entry_liq"] - 1 <= LIQ_EXIT_DROP: warum = "liq-einbruch"
        if warum and pf.sell(a, px, 1.0, liq, warum):
            st["cooldown"][a] = today + COOLDOWN_D
            log.append((pos["sym"], f"{warum} bei {px/pos['entry']-1:+.1%} nach {gehalten_d:.1f} Tagen"))

    # 4) Einstieg
    gruen = markt_ok(cache, prices.get(SOL_MINT), now)
    kaeufe = 0
    if gruen is None:
        log.append(("MARKT", f"SOL-{SOL_MA_TAGE}-Tage-Schnitt noch unbekannt -> kein Kauf"))
    elif not gruen:
        log.append(("MARKT", f"SOL unter {SOL_MA_TAGE}-Tage-Schnitt -> kein Kauf, Cash halten"))
    else:
        kand = []
        for a, s in universe:
            if a in st["positions"] or st["cooldown"].get(a, 0) > today: continue
            p = pairs.get(a)
            if not p: continue
            sig, grund = signal(a, p, cache, now)
            if sig: kand.append((sig[0], sig[1], a, s, p))
        kand.sort()                                   # am tiefsten unter dem Hoch zuerst
        equity = st["cash"] + sum(q["qty"] * (q.get("mark") or q.get("cur_price") or q["entry"])
                                  for q in st["positions"].values())
        for vom_hoch, schub, a, s, p in kand:
            if kaeufe >= MAX_BUYS_PER_RUN or len(st["positions"]) >= MAX_POS: break
            usd = min(st["cash"] - 1, equity * POS_FRAC)
            if usd < 20: break
            liq = _liq(p)
            if pf.buy(s, a, px_of(p), usd, liq, f"rueckschlag {vom_hoch:+.0%} vom 90d-Hoch, umsatz {schub:.1f}x"):
                st["positions"][a]["entry_liq"] = liq
                kaeufe += 1
                log.append((s, f"GEKAUFT {usd:.0f} $ | {vom_hoch:+.0%} unter Hoch, Umsatz {schub:.1f}x"))
                append_jsonl("bot_c_signals.jsonl", {"t": now_iso(), "addr": a, "sym": s, "px": px_of(p),
                                                     "vom_hoch": round(vom_hoch, 3), "schub": round(schub, 2),
                                                     "liq": round(liq), "usd": round(usd, 2)})
        if not kand: log.append(("MARKT", "gruen, aber kein Coin erfuellt Rueckschlag + Umsatzschub"))

    st["cooldown"] = {k: v for k, v in st["cooldown"].items() if v > today}
    st["strategy"] = "rueckschlag_umsatzschub"
    st["coverage"] = {"t": now_iso(), "kurse": sum(1 for a in addrs if a in pairs), "von": len(addrs),
                      "kerzen": sum(1 for a in addrs if (cache.get(a) or {}).get("k")), "offen": offen_daily,
                      "markt": gruen}
    save("bot_c_meta.json", meta); save("bot_c_daily.json", cache)
    v = pf.mark(prices); pf.commit()
    print(f"Bot C [rueckschlag]: equity {v:.2f} | cash {st['cash']:.2f} | positionen {len(st['positions'])}/{MAX_POS}"
          f" | universum {len(universe)} | kurse {st['coverage']['kurse']}/{len(addrs)}"
          f" | kerzen {st['coverage']['kerzen']} (offen {offen_daily}) | markt {gruen}")
    for sym, txt in log[:12]: print(f"   {sym:<12} {txt}")


if __name__ == "__main__":
    main()
