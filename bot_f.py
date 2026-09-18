"""Bot F – "Momentum Runner" v2: kleine Sonde in viele Kandidaten, EINE Nachlage in die, die schon
laufen, und Gewinner erst spaet abgeben. Kalibriert per Backtest auf 120 eigenen 60-Stunden-Kursverlaeufen
(data/bot_d_paths.json, bereinigt, ohne Pump.fun-Herkunft).

BACKTEST-ERGEBNIS dieser Einstellungen gegen die Alternativen (ROI je eingesetztem Dollar):
   Bot D Regeln (60 $ einmalig, Trail -40 % ab 5x):  +13,3 %   ohne die 5 besten Tokens: -4,8 %
   Bot F v1 (Trail ab 2x, zwei Nachlagen):           -6,4 %    ohne die 5 besten Tokens: -17,3 %
   DIESE Einstellung:                                +35,5 %   ohne die 5 besten Tokens: +11,0 %
   Holdout (Adressen per Hash in zwei Haelften):     +36,3 % / +35,0 %  -> nicht auf Ausreisser gefittet
Profil: 26 von 120 Positionen im Gewinn (22 %), Durchschnittsgewinn +74 $ gegen -6,80 $ Durchschnitts-
verlust, groesster Einzelverlust -30 $. Die Trefferquote ist NIEDRIG und soll das auch sein.

DIE VIER ENTSCHEIDUNGEN, DIE DEN UNTERSCHIED MACHEN (alle am Pfaddatensatz gemessen):

1) KEINE PUMP.FUN-HERKUNFT. Graduierte Pump.fun-Tokens sind auch Tage spaeter die schlechteste
   Teilmenge: Median-Endkurs 0,45x gegen 1,02x beim Rest, nur 20 % ueber Wasser gegen 55 %.
   Auf dieser Teilmenge allein ergibt selbst die beste Regel -5,8 % (ohne Top-3: -37,8 %).
   Der Ausschluss ueber die Adress-Endung "pump" hebt den ROI von +16,7 % auf +24,5 % - gratis,
   ohne zusaetzliche Abfrage.

2) TRAILING ERST AB 3x. Das war der Fehler in v1. Ein Token, das 2x erreicht, laeuft in 69 % der
   Faelle weiter auf 3x - ein Trailing ab 2x verkauft genau in diese Fortsetzung hinein.
   Gemessen: Trail ab 2,0x = +3,9 %, ab 2,5x = +10,2 %, ab 3,0x = +22,9 %, ab 4,0x = +19,7 %.
   3x ist die Mitte des Plateaus, kein Randwert.

3) EINE Nachlage, nicht zwei, und bei 1,5x statt 2x. Genau hier lag meine Fehlannahme: ich hielt
   "spaeter = sicherer" fuer besser. Gemessen ist das Gegenteil richtig, weil die Bestaetigung bei 2x
   zu teuer bezahlt wird: eine Nachlage bei 1,5x = +27,8 %, bei 2,0x = +18,3 %, zwei Nachlagen
   (1,5x + 3x) = +22,9 %, keine Nachlage = +20,9 % - aber mit halbem Gewinn in Dollar (+455 statt +895).
   Die Nachlage loest bei 46 % aller Positionen aus.

4) TOTES KAPITAL SCHNELL FREIGEBEN. Nach 4 h ohne 1,15x raus. Gemessen +27,5 % gegen +19,9 %
   bei 4 h/1,25x und +22,7 % ohne die Regel. Nicht der Verlust ist das Problem, sondern der
   blockierte Platz: 57 % aller Tokens kommen nie ueber 1,1x, und sie zeigen das binnen Stunden.

Datenquellen: Codex (Kandidaten + Holder/Sniper/Bundler/Insider), DexScreener (Kurse, jeder Lauf),
RugCheck (harte Risiken), Jupiter (Fills ueber common.Paper). Keine zusaetzliche API noetig.
"""
import os, time
from common import *

CODEX_KEY = os.environ.get("CODEX_KEY")
CODEX_URL = "https://graph.codex.io/graphql"
SOLANA = 1399811149

# ---------- Universum (identisch zu Bot D: dort 22 Verkaeufe, KEIN Totalverlust, schlimmster -8,50 $) ----------
MIN_AGE_H, MAX_AGE_H = 12, 7 * 24     # unter 12 h halten Stops nicht (Bot C: Stop -15 %, realisiert -48 %)
MIN_MCAP, MAX_MCAP = 15_000, 150_000   # NICHT weiter oeffnen: alle 158 Backtest-Pfade lagen bei MCap
MIN_LIQ, MAX_LIQ = 10_000, 50_000      # 25,6k-149,3k und Liquiditaet 10,0k-49,5k. Oberhalb davon gibt es
                                       # keine Messung, die +35,5 % stuetzt - das waere stille Extrapolation.
MAX_LIQ_OVER_MCAP = 1.0               # Liquiditaet ueber der MCap = kuenstlich aufgeblasen
MIN_HOLDERS, MAX_HOLDERS = 30, 2000
MIN_BUYS_24H, MAX_SELL_RATIO = 30, 0.90
MAX_TOP10, MAX_BUNDLER, MAX_SNIPER, MAX_INSIDER = 45.0, 10.0, 15.0, 3.0
MIN_VOL24 = 25_000
SKIP_PUMPFUN = True                   # Entscheidung 1: Adressen auf "pump" ausschliessen
MAX_DROP_1H = -12.0                   # faellt gerade hart -> nicht ins Messer greifen.
                                      # Vernunftschranke, nicht gemessen (die Pfade beginnen erst beim Kauf).
DISCOVER_EVERY_S = 15 * 60            # 2.880 Codex-Calls/Monat. 60 Min wuerden fachlich genauso
                                      # reichen (ein Token bleibt im 12h-7d-Fenster ueber sechs Tage kaufbar),
                                      # aber das Budget ist nach Bot Cs Umbau frei - also genutzt.

# ---------- Position: Sonde + EINE Nachlage ----------
PROBE_USD = 20.0
ADD_X, ADD_USD = 1.5, 40.0            # Entscheidung 3
MAX_POS = 10                          # 10 Sonden = 200 $; der Rest ist Reserve fuer Nachlagen
MAX_BUYS_PER_RUN = 4
ADD_RESERVE = 120.0                   # so viel Cash bleibt fuer Nachlagen reserviert und wird nicht
                                      # in neue Sonden gesteckt - eine Nachlage ist wertvoller als
                                      # eine weitere Sonde (46 % Ausloesequote, 11:1 Auszahlung)

# ---------- Ausstieg ----------
STOP = -0.45                          # auf den gemischten Einstand; Plateau reicht bis -60 %, -45 % ist innen
DEAD_AFTER_H, DEAD_PEAK_X = 4, 1.15   # Entscheidung 4
TP_X, TP_FRAC = 5.0, 0.5              # bei 5x die Haelfte: Einsatz mehrfach zurueck, Rest laeuft frei
TRAIL_ARM_X, TRAIL_GIVE = 3.0, -0.28  # Entscheidung 2
LIQ_DROP_EXIT = -0.35
MAX_HOLD_H = 72
DEAD_PRICE_H = 12                     # 12 h ohne Kurs -> abschreiben, damit der Platz frei wird
COOLDOWN_D = 14

Q_F = """
query($net: [Int!], $after: Float!, $before: Float!) {
  filterTokens(
    filters: { network: $net, createdAt: { gte: $after, lte: $before }, volume24: { gte: %s },
               marketCap: { gte: %s, lte: %s }, liquidity: { gte: %s, lte: %s } }
    rankings: [{ attribute: volume24, direction: DESC }]
    limit: 150
  ) {
    results {
      liquidity marketCap volume24 buyCount24 sellCount24 holders
      top10HoldersPercent bundlerHeldPercentage sniperHeldPercentage insiderHeldPercentage
      token { address symbol }
    }
  }
}""" % (MIN_VOL24, MIN_MCAP, MAX_MCAP, MIN_LIQ, MAX_LIQ)
# $after/$before sind Float!, nicht Int! - mit Int! bricht die Abfrage komplett ab und liefert still
# eine leere Liste. Genau daran hingen Bot C und Bot D am 17.09. ueber 24 h mit eingefrorenen Kandidaten.


def fnum(x, default=0.0):
    try: return float(x) if x is not None else default
    except (TypeError, ValueError): return default


def codex_candidates():
    """None = Fehler (spaeter erneut versuchen), Liste = Ergebnis (ggf. leer)."""
    if not CODEX_KEY: return None
    now = time.time()
    try:
        r = requests.post(CODEX_URL, headers={"Authorization": CODEX_KEY, "Content-Type": "application/json", **UA},
                          json={"query": Q_F, "variables": {"net": [SOLANA],
                                                            "after": float(now - MAX_AGE_H * 3600),
                                                            "before": float(now - MIN_AGE_H * 3600)}}, timeout=30)
        r.raise_for_status(); d = r.json()
        if d.get("errors"):
            print("Bot F codex:", str(d["errors"])[:200]); return None
        out = []
        for x in ((d.get("data") or {}).get("filterTokens") or {}).get("results") or []:
            t = x.get("token") or {}
            if not t.get("address"): continue
            out.append({"addr": t["address"], "sym": t.get("symbol") or "?",
                        "liq": fnum(x.get("liquidity")), "mcap": fnum(x.get("marketCap")),
                        "vol": fnum(x.get("volume24")),
                        "buys": int(fnum(x.get("buyCount24"))), "sells": int(fnum(x.get("sellCount24"))),
                        "holders": int(fnum(x.get("holders"))),
                        "top10": fnum(x.get("top10HoldersPercent"), None),
                        "bundler": fnum(x.get("bundlerHeldPercentage"), None),
                        "sniper": fnum(x.get("sniperHeldPercentage"), None),
                        "insider": fnum(x.get("insiderHeldPercentage"), None)})
        return out
    except Exception as e:
        print("Bot F codex fehler:", e); return None


def quality(c):
    """Grund fuer Ablehnung, oder None wenn sauber."""
    if SKIP_PUMPFUN and c["addr"].endswith("pump"): return "pump.fun-herkunft"
    if not (MIN_LIQ <= c["liq"] <= MAX_LIQ): return f"liq {c['liq']:.0f}"
    if c["mcap"] > 0 and c["liq"] / c["mcap"] > MAX_LIQ_OVER_MCAP: return "liq>mcap"
    if not (MIN_MCAP <= c["mcap"] <= MAX_MCAP): return f"mcap {c['mcap']:.0f}"
    if not (MIN_HOLDERS <= c["holders"] <= MAX_HOLDERS): return f"holders {c['holders']}"
    if c["buys"] < MIN_BUYS_24H: return f"buys24 {c['buys']}"
    if c["buys"] and c["sells"] / c["buys"] > MAX_SELL_RATIO: return f"sell-ratio {c['sells']/c['buys']:.2f}"
    if c["top10"] is not None and c["top10"] > MAX_TOP10: return f"top10 {c['top10']:.0f}%"
    if c["bundler"] is not None and c["bundler"] > MAX_BUNDLER: return f"bundler {c['bundler']:.0f}%"
    if c["sniper"] is not None and c["sniper"] > MAX_SNIPER: return f"sniper {c['sniper']:.0f}%"
    if c["insider"] is not None and c["insider"] > MAX_INSIDER: return f"insider {c['insider']:.1f}%"
    return None


def batch_pairs(addrs):
    """DexScreener-Kurse fuer bis zu 30 Adressen je Aufruf; je Token das liquideste Paar."""
    out = {}
    for k in range(0, len(addrs), 30):
        chunk = addrs[k:k + 30]
        if not chunk: continue
        d = get(f"{DS}/latest/dex/tokens/{','.join(chunk)}") or {}
        for p in (d.get("pairs") or []):
            if p.get("chainId") != "solana": continue
            a = (p.get("baseToken") or {}).get("address")
            if not a: continue
            liq = ((p.get("liquidity") or {}).get("usd") or 0)
            if a not in out or liq > ((out[a].get("liquidity") or {}).get("usd") or 0):
                out[a] = p
        time.sleep(1.1)
    return out


def px_of(p):
    try: return float((p or {}).get("priceUsd") or 0)
    except (TypeError, ValueError): return 0.0


def liq_of(p):
    try: return float(((p or {}).get("liquidity") or {}).get("usd") or 0)
    except (TypeError, ValueError): return 0.0


def chg_1h(p):
    try: return float(((p or {}).get("priceChange") or {}).get("h1"))
    except (TypeError, ValueError): return None


def main():
    pf = Paper("bot_f"); st = pf.s
    st.setdefault("cooldown", {})
    now = time.time(); today = int(now // 86400)
    meta = load("bot_f_meta.json", {"cands": [], "last_discover": 0})

    # 1) Kandidaten. Zeitstempel IMMER setzen, damit ein stiller Ausfall im Log sichtbar wird.
    if now - meta.get("last_discover", 0) >= DISCOVER_EVERY_S:
        fresh = codex_candidates()
        meta["last_discover"] = now
        if fresh is None:
            print("Bot F: Codex-Fehler, nutze vorherige Kandidatenliste")
        else:
            meta["cands"] = fresh
            if not fresh: print("Bot F: WARNUNG - Codex lieferte 0 Kandidaten")
    cands = {c["addr"]: c for c in meta.get("cands", []) if c.get("addr")}

    # 2) Kurse fuer Kandidaten UND offene Positionen
    addrs = list(dict.fromkeys(list(cands) + list(st["positions"])))
    pairs = batch_pairs(addrs) if addrs else {}
    prices = {a: px_of(p) for a, p in pairs.items() if px_of(p) > 0}

    # 3) Offene Positionen: Ausstieg hat immer Vorrang vor Einstieg
    log = []
    for a, pos in list(st["positions"].items()):
        px = prices.get(a)
        if not px:
            seit = pos.get("no_quote_since") or pos["opened"]
            if (now - parse_iso(seit)) / 3600 >= DEAD_PRICE_H and pos.get("qty", 0) > 0:
                verlust = round(pos.get("cost", 0.0), 2)      # Einstand zuerst sichern
                pos["qty"] = 0.0; pos["cost"] = 0.0
                st["trades"].append({"t": now_iso(), "side": "sell", "sym": pos["sym"], "addr": a,
                                     "price": 0.0, "usd": 0.0, "pnl": -verlust, "frac": 1.0,
                                     "reason": "abgeschrieben (kein kurs)"})
                del st["positions"][a]
                st["cooldown"][a] = today + COOLDOWN_D
                log.append((pos["sym"], f"abgeschrieben - {DEAD_PRICE_H} h ohne Kurs, -{verlust:.0f} $"))
            continue
        liq = liq_of(pairs.get(a))
        pos["peak"] = max(pos.get("peak", px), px)
        base = pos.get("base_entry") or pos["entry"]     # Erstkurs: Bezug fuer Nachlage und Ziele
        x_entry = px / pos["entry"] - 1                  # gegen gemischten Einstand -> schuetzt echtes Kapital
        x_base = px / base                               # gegen Erstkurs -> misst den Lauf
        peak_x = pos["peak"] / base
        from_peak = px / pos["peak"] - 1
        held_h = held_seconds(pos) / 3600
        why = None
        if x_entry <= STOP: why = "stop"
        elif liq and pos.get("entry_liq") and liq / pos["entry_liq"] - 1 <= LIQ_DROP_EXIT: why = "liq-drop"
        elif held_h >= DEAD_AFTER_H and peak_x < DEAD_PEAK_X: why = "totes kapital"
        elif peak_x >= TRAIL_ARM_X and from_peak <= TRAIL_GIVE: why = "trail"
        elif held_h >= MAX_HOLD_H: why = "zeit"
        if not why and x_base >= TP_X and not pos.get("tp1"):
            pos["tp1"] = True
            if pf.sell(a, px, TP_FRAC, liq, "tp1"):
                log.append((pos["sym"], f"TP bei {x_base:.1f}x - Haelfte raus"))
            continue
        if why:
            if pf.sell(a, px, 1.0, liq, why):
                st["cooldown"][a] = today + COOLDOWN_D
                log.append((pos["sym"], f"{why} bei {x_base:.2f}x (Hoch {peak_x:.2f}x)"))
            continue
        # Nachlage: genau einmal, nur in einen Token, der den Lauf bereits gezeigt hat
        if not pos.get("added") and x_base >= ADD_X:
            if st["cash"] >= ADD_USD:
                if pf.buy(pos["sym"], a, px, ADD_USD, liq, f"nachlage bei {x_base:.2f}x"):
                    pos["added"] = True; pos["base_entry"] = base
                    log.append((pos["sym"], f"NACHGELEGT {ADD_USD:.0f} $ bei {x_base:.2f}x"))
            else:
                log.append((pos["sym"], f"Nachlage bei {x_base:.2f}x faellt aus - Cash {st['cash']:.0f} $"))

    # 4) Neue Sonden
    kaeufe = 0
    for a, c in cands.items():
        if kaeufe >= MAX_BUYS_PER_RUN or len(st["positions"]) >= MAX_POS: break
        if a in st["positions"] or st["cooldown"].get(a, 0) > today: continue
        if st["cash"] - PROBE_USD < ADD_RESERVE: break     # Reserve gehoert den Nachlagen
        if quality(c): continue
        px = prices.get(a)
        if not px: continue
        p = pairs.get(a)
        c1 = chg_1h(p)
        if c1 is not None and c1 < MAX_DROP_1H: continue
        ok, risks = rug_ok(a); time.sleep(1.1)
        if not ok: continue
        liq = liq_of(p) or c["liq"]
        if pf.buy(c["sym"], a, px, PROBE_USD, liq, "sonde"):
            pos = st["positions"][a]
            pos["base_entry"] = pos["entry"]; pos["entry_liq"] = liq
            kaeufe += 1
            log.append((c["sym"], f"SONDE {PROBE_USD:.0f} $ | mcap {c['mcap']/1000:.0f}k liq {liq/1000:.0f}k"))
            append_jsonl("bot_f_signals.jsonl", {"t": now_iso(), "addr": a, "sym": c["sym"], "px": px,
                                                 "mcap": c["mcap"], "liq": liq, "holders": c["holders"],
                                                 "buys": c["buys"], "sells": c["sells"]})

    st["cooldown"] = {k: v for k, v in st["cooldown"].items() if v > today}
    st["strategy"] = "momentum_runner"
    save("bot_f_meta.json", meta)
    v = pf.mark(prices); pf.commit()
    nachgelegt = sum(1 for p in st["positions"].values() if p.get("added"))
    print(f"Bot F [momentum runner]: equity {v:.2f} | cash {st['cash']:.2f} | positionen {len(st['positions'])}"
          f" (davon nachgelegt {nachgelegt}) | kandidaten {len(cands)}")
    for sym, txt in log[:15]: print(f"   {sym:<12} {txt}")


if __name__ == "__main__":
    main()
