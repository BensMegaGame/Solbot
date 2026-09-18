"""Bot C v3 – "Wiederanlauf": kauft Tokens, die bereits einen Lauf von mindestens 2x HINTER sich haben,
danach deutlich zurueckgesetzt sind und sich gerade stabilisieren. Ziel ist das alte Hoch, nicht der Mond.

WARUM DIE ALTE STRATEGIE WEG IST. Bot C hat bis zum 18.09. Pump.fun-Tokens 8-60 Minuten nach der
Migration gekauft. An 2.085 eigenen Pfaden gemessen ist die Graduation aber ein DUMP-Ereignis:
Median-Kursaenderung ab 12 Min nach Migration +30 Min -1,2 % | +1 h -9,1 % | +2 h -47,1 % | +3 h -61,3 %.
47,4 % verlieren binnen 3 h ueber 70 %, nur 20,2 % stehen nach 3 h ueber Wasser. Bilanz: 21 von 23
Trades im Verlust. Das war kein Ausfuehrungs-, sondern ein Thesenproblem - kein Stop dreht -61 % Median.

WARUM DIESE STRATEGIE. Die Idee "zweite Welle" ist richtig, nur nicht bei graduierten Tokens, sondern
bei Tokens, die ihren Lauf bewiesen haben. An 120 eigenen 60-Stunden-Pfaden gemessen (bot_d_paths.json):

                                        Wiederanlauf      gleiche Population, Einstieg am Anfang
   Median-Hoch nach Einstieg                2,30x                      1,39x
   erreicht >= 2x                             59 %                       38 %
   Ende ueber Einstieg                        76 %                       51 %
   erreicht das alte Hoch wieder              79 %                          -

Backtest mit den Exits dieser Datei: +37,8 % ROI je eingesetztem Dollar, ohne die drei besten Tokens
+22,3 %, 23 von 34 Positionen im Gewinn. Ich habe vier Ausloeser- und fuenf Exit-Varianten gerechnet -
alle 20 Kombinationen positiv (+20 % bis +47 %). Das ist ein Plateau, keine einzelne gefundene Zelle.

DAS PROFIL IST BEWUSST DAS GEGENTEIL VON BOT F: hier 68 % Trefferquote mit kleinen Vielfachen, dort
22 % mit grossen. Die beiden Bots ergaenzen sich, sie konkurrieren nicht.

Drei gemessene Detailentscheidungen:
 - Stabilisierung verlangen (Kurs >= Kurs vor 30 Min): +41,3 % statt +37,8 %. Kein Griff ins Messer.
 - Pump.fun-Herkunft NICHT ausschliessen: +37,3 % gegen +37,8 %, also neutral. Sobald ein Token einen
   2x-Lauf bewiesen hat, spielt seine Herkunft keine Rolle mehr - der Lauf selbst ist der Filter.
   (Bot F schliesst sie aus, weil er UNBEWIESENE Tokens kauft - dort sind sie messbar schlechter.)
 - Bei mehr Signalen als Plaetzen den FLACHSTEN Ruecksetzer zuerst: mit 6 Plaetzen +44,6 %, waehrend
   "staerkster vorheriger Lauf zuerst" nur +0,8 % und "tiefster Ruecksetzer zuerst" +9,0 % ergibt.
   Ein flacher Ruecksetzer heisst: der Token haelt sich.

KEIN CODEX-AUFRUF. Die Beobachtungsliste kommt aus den Kandidatenlisten von Bot D und Bot F, die
ohnehin erzeugt werden, plus der eigenen Kurshistorie. Nur DexScreener (kostenlos) und Jupiter (Fills).

WARMLAUF: Der Ausloeser braucht einen selbst beobachteten 2x-Lauf. Die Historie wird beim ersten Lauf
einmalig aus bot_d_paths.json vorbefuellt (echte eigene Beobachtungen), der Ruecksetzer und die
Stabilisierung muessen aber im Live-Kurs vorliegen. Trotzdem: die ersten Tage bleibt es hier ruhig.
"""
import os, time
from common import *

# ---------- Beobachtungsliste ----------
WATCH_SOURCES = ("bot_f_meta.json", "bot_d_meta.json")   # deren Codex-Kandidaten mitbenutzen
MAX_WATCH = 300
HIST_KEEP_D = 10                 # Tage, die ein Token ohne Kurs in der Historie bleibt
PTS_KEEP_H = 8                   # Stunden rollierende Kurspunkte (fuer die Stabilisierungspruefung)
SEED_FILE = "bot_d_paths.json"   # einmaliges Vorbefuellen der Historie

# ---------- Ausloeser (alle Bedingungen gleichzeitig) ----------
RUN_X = 2.0                      # beobachteter Lauf: Hoch >= 2x ueber dem ersten beobachteten Kurs
PULLBACK = -0.35                 # aktueller Kurs >= 35 % unter diesem Hoch
PULLBACK_FLOOR = -0.60           # ... aber nicht tiefer als 60 %. Gemessen ist das neutral (identisches
                                 # Ergebnis bei -60 %, +43,9 % statt +41,2 % bei -50 %, also eine einzige
                                 # Position Unterschied = Rauschen). Die Grenze steht als Vernunftschranke
                                 # gegen einen Rug im Gange, nicht als gemessener Vorteil.
STAB_MIN = 30                    # Kurs >= Kurs vor 30 Minuten (Stabilisierung)
LIQ_KEEP = 0.60                  # Liquiditaet noch >= 60 % vom Stand beim Hoch (Rug-Schutz)
MIN_LIQ = 8_000
MIN_MCAP, MAX_MCAP = 10_000, 1_000_000   # Die gemessene Population startete bei MCap <= 150k und lief
                                         # 2x-10x; nach dem Ruecksetzer liegen die Einstiegs-MCaps damit
                                         # bei etwa 30k-750k. 1 Mio. deckt das ab, 2 Mio. waere Extrapolation.
REENTRY_PEAK_X = 1.05            # nach einem Verkauf erst wieder kaufen, wenn ein NEUES Hoch >5 % darueber
COOLDOWN_D = 3

# ---------- Position ----------
POS_USD, MAX_POS = 60.0, 6       # 6 x 60 $ = 360 $ im Markt, 140 $ Puffer
MAX_BUYS_PER_RUN = 2

# ---------- Ausstieg (Variante A aus dem Backtest) ----------
STOP = -0.30                     # auf den Einstand
TP_FRAC = 0.5                    # Haelfte am alten Hoch - dort war die Nachfrage zuletzt erschoepft
TRAIL_ARM_X, TRAIL_GIVE = 2.0, -0.25   # ab 2x ab Einstand: 25 % Rueckgabe vom Hoch beendet die Position
DEAD_AFTER_H, DEAD_PEAK_X = 6, 1.10    # nach 6 h ohne 1,1x ist der Platz mehr wert als die Hoffnung
MAX_HOLD_H = 48
LIQ_DROP_EXIT = -0.35
DEAD_PRICE_H = 12                # 12 h ohne Kurs -> abschreiben, Platz freigeben


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


def num(v, default=0.0):
    try: return float(v) if v is not None else default
    except (TypeError, ValueError): return default


def px_of(p): return num((p or {}).get("priceUsd"))
def liq_of(p): return num(((p or {}).get("liquidity") or {}).get("usd"))
def mcap_of(p): return num((p or {}).get("marketCap")) or num((p or {}).get("fdv"))


def watchlist(hist, positions):
    """Kandidaten der anderen Bots + alles, was wir schon beobachten. Kein eigener Codex-Aufruf."""
    addrs = set(positions)
    # Tokens mit beobachtetem Lauf haben Vorrang - sie sind der eigentliche Grund fuer diesen Bot
    laeufer = [a for a, h in hist.items() if h.get("peak") and h.get("p0") and h["peak"] / h["p0"] >= RUN_X]
    addrs |= set(laeufer)
    for f in WATCH_SOURCES:
        for c in (load(f, {}).get("cands") or []):
            a = c.get("addr") if isinstance(c, dict) else None
            if a: addrs.add(a)
    addrs |= set(hist)
    # Reihenfolge: Positionen, dann Laeufer, dann der Rest - damit die Kappung nie einen Laeufer trifft
    ordered = list(positions) + [a for a in laeufer if a not in positions]
    ordered += [a for a in addrs if a not in set(ordered)]
    return ordered[:MAX_WATCH]


def seed_history(hist):
    """Einmalig: echte eigene Beobachtungen aus Bot Ds Pfadlog als Startpunkt (p0/peak)."""
    src = load(SEED_FILE, {})
    if not src: return 0
    n = 0
    for a, v in src.items():
        if a in hist: continue
        pts = [p for p in (v.get("pts") or []) if p[1] and p[1] > 0]
        if len(pts) < 10: continue
        peak = max(pts, key=lambda p: p[1])
        hist[a] = {"sym": v.get("sym") or "?", "p0": pts[0][1], "t0": time.time(),
                   "peak": peak[1], "liq_at_peak": peak[2] or 0, "pts": [], "seeded": True}
        n += 1
    return n


def main():
    pf = Paper("bot_c"); st = pf.s
    st.setdefault("cooldown", {})
    now = time.time(); today = int(now // 86400)
    hist = load("bot_c_hist.json", {})
    meta = load("bot_c_meta.json", {})

    if not meta.get("seeded"):
        n = seed_history(hist)
        meta["seeded"] = True; meta["seed_n"] = n
        print(f"Bot C: Historie einmalig mit {n} beobachteten Tokens aus {SEED_FILE} vorbefuellt")

    addrs = watchlist(hist, st["positions"])
    if len(addrs) < 5:
        print("Bot C: WARNUNG - Beobachtungsliste fast leer. Laufen bot_d.py und bot_f.py? "
              "Bot C bezieht seine Kandidaten aus data/bot_f_meta.json und data/bot_d_meta.json.")
    pairs = batch_pairs(addrs) if addrs else {}
    prices = {a: px_of(p) for a, p in pairs.items() if px_of(p) > 0}
    log = []

    # 1) Historie fortschreiben: p0 und peak sind bleibend, die Punktreihe rollt
    for a, p in pairs.items():
        px = px_of(p)
        if px <= 0: continue
        liq = liq_of(p)
        h = hist.setdefault(a, {"sym": (p.get("baseToken") or {}).get("symbol") or "?",
                                "p0": px, "t0": now, "peak": px, "liq_at_peak": liq, "pts": []})
        h["sym"] = (p.get("baseToken") or {}).get("symbol") or h.get("sym") or "?"
        h.setdefault("p0", px); h.setdefault("t0", now)
        if px > h.get("peak", 0):
            h["peak"] = px; h["liq_at_peak"] = liq; h["peak_t"] = now
        pts = h.setdefault("pts", []); pts.append([now, px])
        h["pts"] = [x for x in pts if now - x[0] <= PTS_KEEP_H * 3600][-120:]
        h["last"] = now
    for a in list(hist):
        if a in st["positions"]: continue
        if now - hist[a].get("last", hist[a].get("t0", now)) > HIST_KEEP_D * 86400: del hist[a]

    # 2) Offene Positionen: Ausstieg hat Vorrang
    for a, pos in list(st["positions"].items()):
        px = prices.get(a)
        if not px:
            seit = pos.get("no_quote_since") or pos["opened"]
            if (now - parse_iso(seit)) / 3600 >= DEAD_PRICE_H and pos.get("qty", 0) > 0:
                verlust = round(pos.get("cost", 0.0), 2)
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
        x = px / pos["entry"] - 1
        peak_x = pos["peak"] / pos["entry"]
        from_peak = px / pos["peak"] - 1
        held_h = held_seconds(pos) / 3600
        ziel = pos.get("target")            # das alte Hoch
        why = None
        if x <= STOP: why = "stop"
        elif liq and pos.get("entry_liq") and liq / pos["entry_liq"] - 1 <= LIQ_DROP_EXIT: why = "liq-drop"
        elif held_h >= DEAD_AFTER_H and peak_x < DEAD_PEAK_X: why = "totes kapital"
        elif peak_x >= TRAIL_ARM_X and from_peak <= TRAIL_GIVE: why = "trail"
        elif held_h >= MAX_HOLD_H: why = "zeit"
        if not why and ziel and not pos.get("tp1") and px >= ziel:
            pos["tp1"] = True
            if pf.sell(a, px, TP_FRAC, liq, "tp1 (altes hoch)"):
                log.append((pos["sym"], f"altes Hoch erreicht bei {x+1:.2f}x - Haelfte raus"))
            continue
        if why:
            if pf.sell(a, px, 1.0, liq, why):
                st["cooldown"][a] = today + COOLDOWN_D
                h = hist.get(a)
                if h: h["peak_at_sell"] = h.get("peak")
                log.append((pos["sym"], f"{why} bei {x+1:.2f}x (Hoch {peak_x:.2f}x)"))

    # 3) Signale sammeln
    kand = []
    for a, p in pairs.items():
        if a in st["positions"] or st["cooldown"].get(a, 0) > today: continue
        if len(st["positions"]) >= MAX_POS: break
        h = hist.get(a)
        if not h or not h.get("p0") or not h.get("peak"): continue
        run_x = h["peak"] / h["p0"]
        if run_x < RUN_X: continue
        px = px_of(p); liq = liq_of(p); mcap = mcap_of(p)
        if px <= 0: continue
        pull = px / h["peak"] - 1
        if pull > PULLBACK: continue                                 # noch nicht genug zurueckgesetzt
        if pull < PULLBACK_FLOOR: continue                           # Absturz statt Ruecksetzer
        if h.get("peak_at_sell") and h["peak"] < h["peak_at_sell"] * REENTRY_PEAK_X: continue
        if liq < MIN_LIQ: continue
        if not (MIN_MCAP <= mcap <= MAX_MCAP): continue
        if h.get("liq_at_peak") and liq < LIQ_KEEP * h["liq_at_peak"]: continue
        pts = h.get("pts") or []
        ref = None
        for t, q in pts:                                             # Kurs vor >= STAB_MIN Minuten
            if now - t >= STAB_MIN * 60: ref = q
        if ref is None: continue                                     # noch keine 30 Min eigene Punkte
        if px < ref: continue                                        # faellt noch -> kein Griff ins Messer
        kand.append({"addr": a, "sym": h["sym"], "px": px, "liq": liq, "pull": pull,
                     "run_x": run_x, "target": h["peak"]})

    # flachster Ruecksetzer zuerst (gemessen die beste Reihenfolge bei begrenzten Plaetzen)
    kand.sort(key=lambda c: -c["pull"])
    kaeufe = 0
    for c in kand:
        if kaeufe >= MAX_BUYS_PER_RUN or len(st["positions"]) >= MAX_POS: break
        if st["cash"] < POS_USD + 2: break
        ok, risks = rug_ok(c["addr"]); time.sleep(1.1)
        if not ok:
            log.append((c["sym"], "rugcheck")); st["cooldown"][c["addr"]] = today + COOLDOWN_D; continue
        grund = f"wiederanlauf: lauf {c['run_x']:.1f}x, jetzt {c['pull']:+.0%} unter hoch"
        if pf.buy(c["sym"], c["addr"], c["px"], POS_USD, c["liq"], grund):
            pos = st["positions"][c["addr"]]
            pos["target"] = c["target"]; pos["entry_liq"] = c["liq"]
            kaeufe += 1
            log.append((c["sym"], f"GEKAUFT {POS_USD:.0f} $ | Lauf {c['run_x']:.1f}x, {c['pull']:+.0%} unter Hoch, "
                                  f"Ziel {c['target']:.6g}"))
            append_jsonl("bot_c_signals.jsonl", {"t": now_iso(), "addr": c["addr"], "sym": c["sym"],
                                                 "px": c["px"], "run_x": round(c["run_x"], 2),
                                                 "pull": round(c["pull"], 3), "target": c["target"],
                                                 "liq": c["liq"]})

    st["cooldown"] = {k: v for k, v in st["cooldown"].items() if v > today}
    st["strategy"] = "wiederanlauf"; st["watchlist"] = len(hist)
    save("bot_c_hist.json", hist); save("bot_c_meta.json", meta)
    v = pf.mark(prices); pf.commit()
    laeufer = sum(1 for h in hist.values() if h.get("peak") and h.get("p0") and h["peak"] / h["p0"] >= RUN_X)
    print(f"Bot C [wiederanlauf]: equity {v:.2f} | cash {st['cash']:.2f} | positionen {len(st['positions'])}"
          f" | beobachtet {len(hist)} (davon {laeufer} mit >={RUN_X}x-Lauf) | signale {len(kand)}")
    for sym, txt in log[:12]: print(f"   {sym:<12} {txt}")


if __name__ == "__main__":
    main()
