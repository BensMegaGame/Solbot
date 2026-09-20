"""Bot F v3 – "Rueckschlag-Lotterie": dieselbe Mechanik wie Bot C, aber bewusst riskant:
kleinere Coins (Rang 150-400 statt 30-200), nur 3 Positionen zu je einem Drittel des Kapitals,
kein Stop. Hoch gewinnen oder deutlich verlieren - genau das ist die Aufgabe dieses Bots.

WARUM DIESE TAKTIK UND KEINE ANDERE: Von allen in diesem Projekt gemessenen Ansaetzen ist der
Rueckschlagkauf der einzige, der in zwei getrennten Zeitraeumen positiv blieb. Momentum, Rang-
Aufsteiger, Pump.fun-Graduation, Umsatzschub allein und alle Micro-Cap-Varianten (rund 40 getestete
Regeln) sind in mindestens einem Zeitraum klar negativ. Riskanter wird hier deshalb ueber die
DOSIS (kleinere Coins, weniger Positionen, kein Stop), nicht ueber eine andere Idee.

GEMESSEN (665 Solana-Coins, 365 Tage, getrennt nach Halbjahren, Rang 200-600 als Naeherung
dieses Bereichs): +3,9 %/Woche im ersten, +6,5 %/Woche im zweiten Halbjahr, im Jahr +237 %.

DREI EHRLICHE WARNUNGEN, die zu dieser Zahl gehoeren:
 1) Der Marktfilter laesst nur 19 von 38 Wochen ueberhaupt zu. Die Auswertung steht also auf
    19 Wochen, nicht auf einem Jahr. Das ist WENIG.
 2) Der Schnitt haengt an wenigen Wochen: ohne die zwei besten Wochen bleibt im ersten Halbjahr
    -19,7 %/Woche. Die Trefferquote liegt bei 40 %. Dieser Bot lebt von Ausreissern.
 3) CoinGecko kennt nur Coins, die es HEUTE noch gibt. Im Bereich Rang 150-400 sind im letzten
    Jahr viele Coins verschwunden; sie fehlen in der Messung. Das Ergebnis ist dadurch zu gut.
Deshalb gilt hier eine harte Abbruchregel: faellt das Kapital unter 300 $ (-40 %), stoppt der
Bot den Handel von selbst und verwaltet nur noch offene Positionen.

Regeln im Einzelnen (identisch zu Bot C, nur anders dosiert):
 - Kauf: am tiefsten unter dem 90-Tage-Hoch, mindestens 15 % darunter
 - 24h-Umsatz mindestens 1,3x des eigenen 30-Tage-Schnitts (jemand kauft wieder)
 - letzte 7 Tage besser als -25 % (kein freier Fall)
 - nur wenn SOL ueber seinem 20-Tage-Durchschnitt steht, sonst Cash
 - Verkauf nach 7 Tagen, KEIN Stop (ein Stop verschlechtert das Ergebnis messbar)
 - Bot C und Bot F halten nie denselben Coin: die Rangbereiche ueberschneiden sich nicht, und
   Bot F ueberspringt zusaetzlich jeden Coin, der in Bot Cs Universum oder Bestand steht.

Datenquellen: Codex (Rangliste), DexScreener (Kurse ueber feste Paar-Adressen), GeckoTerminal
(Tageskerzen), Jupiter (Fill-Preise ueber common.Paper).
"""
import os, time, statistics
from common import *

CODEX_KEY = os.environ.get("CODEX_KEY")
CODEX_URL = "https://graph.codex.io/graphql"
SOLANA, SOL_MINT = 1399811149, "So11111111111111111111111111111111111111112"
GT = "https://api.geckoterminal.com/api/v2/networks/solana"

# ---------- Universum: Rang 30-200 nach MCap ----------
RANG_VON, RANG_BIS = 150, 400
U_LIMIT = 420                  # so viele holt Codex; daraus wird die Rangliste gebildet
U_MIN_LIQ = 100_000            # kleinere Coins, aber ein 160-$-Kauf muss ohne grossen Impact durchgehen
U_MIN_VOL24 = 50_000
DISCOVER_EVERY_S = 12 * 3600   # 2 Codex-Calls/Tag
EXCLUDE_SYM = {"USDC", "USDT", "USDS", "PYUSD", "USD1", "DAI", "FDUSD", "USDE", "EURC", "USDG", "USDY", "CASH",
               "SOL", "WSOL", "ETH", "WETH", "BTC", "WBTC", "CBBTC", "TBTC", "WBNB", "BNB",
               "JITOSOL", "MSOL", "BSOL", "JUPSOL", "INF", "BNSOL", "HSOL", "DSOL", "VSOL", "JLP"}
EXCLUDE_SUB = ("USD", "EUR", "GBP", "CHF", "JPY", "XAU", "SOL")

# ---------- Einstieg ----------
SCHUB_MIN = 1.3                # 24h-Umsatz >= 1,3x des eigenen 30-Tage-Schnitts
MOM7_MIN = -0.25               # letzte 7 Tage besser als -25 %
HOCH_TAGE = 90                 # Bezugshoch
MAX_VOM_HOCH = -0.15           # mindestens 15 % unter dem 90-Tage-Hoch, sonst ist es kein Rueckschlag
SOL_MA_TAGE = 20               # Marktfilter
MAX_POS = 3                    # 3 Positionen zu je einem Drittel des Gesamtkapitals
POS_FRAC = 0.33
MAX_BUYS_PER_RUN = 1           # gestaffelt einsteigen statt alles in einer Minute
COOLDOWN_D = 7                 # ein verkaufter Coin ist 7 Tage gesperrt
STOP_UNTER = 300.0             # Abbruchregel: faellt das Gesamtkapital darunter (-40 %), keine neuen
                               # Kaeufe mehr. Offene Positionen laufen regulaer aus. Der Bot schaltet
                               # sich selbst ab, statt eine widerlegte Regel weiter zu bezahlen.

# ---------- Ausstieg ----------
HALTE_D = 7                    # getestete Haltedauer
LIQ_EXIT_DROP = -0.50          # Notbremse: Liquiditaet halbiert -> raus (kam im Test nie vor, schuetzt aber)
DEAD_PRICE_H = 24              # 24 h ohne Kurs -> abschreiben

# ---------- Laufzeitschutz (run_paper.sh bricht nach 240 s ab) ----------
GT_PER_RUN = 12                # so viele Tageskerzen-Abrufe je Lauf (je ~2,1 s)
PAIR_LOOKUPS_PER_RUN = 25      # so viele Paar-Suchen je Lauf (je ~0,4 s)
DAILY_MAX_AGE_S = 20 * 3600    # Tageskerzen hoechstens 20 h alt verwenden


def tradeable(sym):
    sym = (sym or "").upper()
    if sym in EXCLUDE_SYM: return False
    return not any(s in sym for s in EXCLUDE_SUB)


Q_C = """
query($net:[Int!]) {
  filterTokens(filters:{ network:$net, liquidity:{gte:%d}, volume24:{gte:%d} },
               rankings:[{attribute:marketCap, direction:DESC}], limit:%d)
  { results { marketCap token { address symbol } } }
}""" % (U_MIN_LIQ, U_MIN_VOL24, U_LIMIT)


def codex_universe():
    """Liste [(addr, sym)] in MCap-Reihenfolge, bereits auf Rang RANG_VON..RANG_BIS geschnitten.
    None = Codex-Fehler (altes Universum weiterverwenden)."""
    if not CODEX_KEY: return None
    try:
        r = requests.post(CODEX_URL, headers={"Authorization": CODEX_KEY, "Content-Type": "application/json", **UA},
                          json={"query": Q_C, "variables": {"net": [SOLANA]}}, timeout=30)
        r.raise_for_status(); d = r.json()
        if d.get("errors"): print("Bot F codex:", str(d["errors"])[:200]); return None
        reihe = []
        for x in ((d.get("data") or {}).get("filterTokens") or {}).get("results") or []:
            t = x.get("token") or {}
            if not t.get("address"): continue
            reihe.append((t["address"], (t.get("symbol") or "?").upper()))
        # Die Rangliste zaehlt ALLE Tokens (auch Stablecoins/LSTs), damit "Rang 30" dasselbe bedeutet
        # wie in der Auswertung. Erst danach wird aussortiert, was nicht handelbar ist.
        aus = [(a, s) for i, (a, s) in enumerate(reihe, 1) if RANG_VON <= i <= RANG_BIS and tradeable(s)]
        return aus
    except Exception as e:
        print("Bot F codex fehler:", e); return None


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
    pf = Paper("bot_f"); st = pf.s
    st.setdefault("cooldown", {})
    now = time.time(); today = int(now // 86400)
    meta = load("bot_f_meta.json", {"universe": [], "last_discover": 0})
    cache = load("bot_f_daily.json", {})

    # 1) Universum (Rang 30-200), alle 12 h
    if now - meta.get("last_discover", 0) >= DISCOVER_EVERY_S or not meta.get("universe"):
        u = codex_universe()
        if u:
            meta["universe"], meta["last_discover"] = u, now
            gueltig = {a for a, _ in u} | set(st["positions"])
            meta["pair_of"] = {k: v for k, v in (meta.get("pair_of") or {}).items() if k in gueltig}
        elif meta.get("universe"):
            print("Bot F: Codex nicht erreichbar, nutze altes Universum")
    universe = meta.get("universe", [])
    syms = {a: s for a, s in universe}
    addrs = list(dict.fromkeys([a for a, _ in universe] + list(st["positions"])))
    if not addrs:
        print("Bot F: kein Universum (CODEX_KEY?)"); pf.mark({}); pf.commit(); return

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
    equity_jetzt = st["cash"] + sum(q["qty"] * (q.get("mark") or q.get("cur_price") or q["entry"])
                                    for q in st["positions"].values())
    if equity_jetzt < STOP_UNTER:
        log.append(("ABBRUCH", f"Kapital {equity_jetzt:.0f} $ unter {STOP_UNTER:.0f} $ - keine neuen Kaeufe mehr"))
    elif gruen is None:
        log.append(("MARKT", "SOL-Durchschnitt noch unbekannt -> kein Kauf"))
    elif not gruen:
        log.append(("MARKT", "SOL unter 20-Tage-Schnitt -> kein Kauf, Cash halten"))
    else:
        c_meta = load("bot_c_meta.json", {})
        c_state = load("bot_c_state.json", {})
        tabu = {a for a, _ in (c_meta.get("universe") or [])} | set((c_state.get("positions") or {}))
        kand = []
        for a, s in universe:
            if a in st["positions"] or st["cooldown"].get(a, 0) > today: continue
            if a in tabu: continue                     # nie denselben Coin wie Bot C halten
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
                append_jsonl("bot_f_signals.jsonl", {"t": now_iso(), "addr": a, "sym": s, "px": px_of(p),
                                                     "vom_hoch": round(vom_hoch, 3), "schub": round(schub, 2),
                                                     "liq": round(liq), "usd": round(usd, 2)})
        if not kand: log.append(("MARKT", "gruen, aber kein Coin erfuellt Rueckschlag + Umsatzschub"))

    st["cooldown"] = {k: v for k, v in st["cooldown"].items() if v > today}
    st["strategy"] = "rueckschlag_lotterie"
    st["coverage"] = {"t": now_iso(), "kurse": sum(1 for a in addrs if a in pairs), "von": len(addrs),
                      "kerzen": sum(1 for a in addrs if (cache.get(a) or {}).get("k")), "offen": offen_daily,
                      "markt": gruen, "gestoppt": equity_jetzt < STOP_UNTER}
    save("bot_f_meta.json", meta); save("bot_f_daily.json", cache)
    v = pf.mark(prices); pf.commit()
    print(f"Bot F [rueckschlag-lotterie]: equity {v:.2f} | cash {st['cash']:.2f} | positionen {len(st['positions'])}/{MAX_POS}"
          f" | universum {len(universe)} | kurse {st['coverage']['kurse']}/{len(addrs)}"
          f" | kerzen {st['coverage']['kerzen']} (offen {offen_daily}) | markt {gruen}")
    for sym, txt in log[:12]: print(f"   {sym:<12} {txt}")


if __name__ == "__main__":
    main()
