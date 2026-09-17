"""Bot B v2.5 – "Zweite Welle": kleine Solana-Tokens, die Woche 1–2 ueberlebt haben und deren
Volumen und Kaeuferueberhang anhaltend wachsen. Handelt Spot ueber Jupiter (Paper-Simulation).

Ablauf pro Lauf (stuendlich):
  1. Beobachtungsliste pflegen: Kandidaten aus Bot A's Log + DexScreener, Alter 21–56 Tage.
  2. Fuer alle beobachteten Tokens einen Tages-Snapshot speichern (Preis, Volumen, Liquiditaet, Kaeufe/Verkaeufe).
  3. Offene Positionen verwalten (Teilverkauf, Trailing, Stop, These gebrochen, Zeitstop, Nachkauf).
  4. Einstiege pruefen – nur mit mind. 3 Tagen eigener Snapshot-Historie.
Alles ist in data/bot_b_history.json und data/bot_b_state.json nachvollziehbar."""
import time, os
from common import *

EXCLUDE_SYM = {"USDC", "USDT", "USDS", "PYUSD", "USD1", "DAI", "FDUSD", "USDE", "EURC", "USDG", "USDY", "OTC"}
EXCLUDE_SUB = ("USD", "EUR", "GBP", "CHF", "JPY", "XAU")

def tradeable_sym(sym):
    sym = sym.upper()
    if sym in EXCLUDE_SYM: return False
    return not any(s in sym for s in EXCLUDE_SUB)

# ---------- Universum ----------
MIN_AGE_D, MAX_AGE_D   = 7, 56                # v2.5: ab 7 Tagen (mehr Datenpunkte; 3 Tage eigene Historie bleiben Pflicht)
MIN_MCAP, MAX_MCAP     = 10_000, 800_000       # v2.2: aufgeweitet (300k war zu eng, siehe log)
# v2.4: Liquiditaet proportional statt fix. Die alte 50k-Schwelle schloss das gesamte untere Mcap-Fenster
# aus (0 von 5 beobachteten Tokens unter 100k Cap kamen durch) - und haette dort nur Tokens durchgelassen,
# deren Liquiditaet ein Vielfaches ihrer Marktkapitalisierung betraegt, also genau das Fake-Muster aus Bot C.
# Beobachtetes Verhaeltnis in der Watchlist: Median 0,28 / p10 0,17 / p90 0,71.
MIN_LIQ = 10_000                       # absolute Untergrenze, nur damit 60 $ ohne grossen Impact handelbar sind
MIN_LIQ_RATIO, MAX_LIQ_RATIO = 0.12, 1.2
MIN_VOL24              = 30_000
# ---------- Einstieg ----------
MIN_HISTORY_DAYS = 3          # Tage Historie (eigene Snapshots oder GeckoTerminal-Kerzen), bevor gekauft wird
VOL_TREND_MIN    = 1.30       # 3d-Volumen vs. 3d davor (wenn >= 6 Tage Historie), sonst gegen Vortag
VOL_TREND_MAX    = 4.00       # v2.5: >4x = Blow-off-Spike (PERIHEL kam mit 24x rein und lief sofort -16 %). Wir wollen
                              # anhaltend wachsendes Interesse, nicht den einen Pump-Tag.
MIN_BUY_RATIO    = 0.56       # v2.5: 0,52-0,55 ist Rauschen (alle 5 offenen Verlierer lagen dort)
MAX_ABOVE_AVG    = 0.25       # v2.5: nicht kaufen, wenn Preis > 25 % ueber 7-Tage-Schnitt (60 % hiess: Spitze kaufen)
# ---------- Position ----------
ENTRY_USD, ADD_USD, MAX_POS = 40.0, 25.0, 8   # v2.5
ADD_AT_X   = 1.25             # Nachkauf bei +25 %, wenn Volumen weiter steigt
TP1_X, TP1_FRAC = 1.5, 0.30   # v2.1: +50 % -> 30 % raus (vorher 2x/50 %); Rest laeuft ueber Trailing
TRAIL, HARD_STOP, MAX_HOLD_D = -0.25, -0.25, 30   # v2.5: -35 % Stop bei 60 $ + 3-4 % Slippage je Seite war zu teuer
LIQ_DROP_EXIT, VOL_DROP_EXIT = -0.40, 0.50
# ---------- Schutz ----------
COOLDOWN_D, STREAK_HALVE = 14, 3

DAY = 86400

def day_key(): return int(time.time() // DAY)

# ---------- Datenbeschaffung ----------
def batch_pairs(addrs):
    """DexScreener liefert bis zu 30 Tokens je Aufruf. Gibt {addr: bester Solana-Pool} zurueck."""
    out = {}
    for k in range(0, len(addrs), 30):
        d = get(f"{DS}/latest/dex/tokens/{','.join(addrs[k:k+30])}") or {}
        for p in (d.get("pairs") or []):
            if p.get("chainId") != "solana": continue
            a = p["baseToken"]["address"]
            if a not in out or (p.get("liquidity") or {}).get("usd", 0) > (out[a].get("liquidity") or {}).get("usd", 0):
                out[a] = p
        time.sleep(1.1)
    return out

def snapshot(p):
    tx = (p.get("txns") or {}).get("h24") or {}
    b, s = tx.get("buys", 0), tx.get("sells", 0)
    return {"d": day_key(), "t": now_iso(), "px": float(p.get("priceUsd") or 0),
            "vol": (p.get("volume") or {}).get("h24") or 0, "vol6": (p.get("volume") or {}).get("h6") or 0,
            "liq": (p.get("liquidity") or {}).get("usd") or 0, "mcap": p.get("marketCap") or p.get("fdv") or 0,
            "buy_ratio": b / (b + s) if b + s else 0,
            "age_d": (time.time() * 1000 - (p.get("pairCreatedAt") or time.time() * 1000)) / 86400000}

SOLANA_NET = 1399811149
CODEX_KEY = os.environ.get("CODEX_KEY")
CODEX_URL = "https://graph.codex.io/graphql"
DISCOVER_EVERY_S = 30 * 60   # Codex-Discovery hoechstens alle 30 Minuten (Budget-Schonung)

def codex(query, variables=None):
    if not CODEX_KEY: return None
    try:
        r = requests.post(CODEX_URL, json={"query": query, "variables": variables or {}},
                          headers={"Authorization": CODEX_KEY, "Content-Type": "application/json", **UA}, timeout=30)
        r.raise_for_status(); out = r.json()
        if out.get("errors"): print("codex:", str(out["errors"])[:200]); return None
        return out.get("data")
    except Exception as e:
        print("codex fehler:", e); return None

Q_AGED = """
query($net: [Int!]) {
  filterTokens(
    filters: { network: $net, volume24: { gte: %s },
               marketCap: { gte: %s, lte: %s }, liquidity: { gte: %s } }
    rankings: [{ attribute: volume24, direction: DESC }]
    limit: 200
  ) { results { token { address symbol } } }
}""" % (MIN_VOL24, MIN_MCAP, MAX_MCAP, MIN_LIQ)

def codex_aged_candidates():
    """Kandidaten nach MCap/Liquiditaet/Volumen - exakt das Abfragemuster, das bei Bot E funktioniert.
    KEIN Alter in der Abfrage: weder als Filter noch als Ausgabefeld (beides liess die Antwort dauerhaft
    leer zurueckkommen, 'last_fresh_n: 0'). Das Altersfenster MIN_AGE_D..MAX_AGE_D wird ohnehin weiter
    unten in der Signal-Logik geprueft - dort steht 'age_d' aus DexScreeners pairCreatedAt zur Verfuegung,
    das ist die verlaesslichere Quelle. Die Discovery liefert hier also nur den Rohtopf."""
    d = codex(Q_AGED, {"net": [SOLANA_NET]})
    if not d: return set()
    return {r["token"]["address"] for r in (d.get("filterTokens") or {}).get("results") or []}

GT = "https://api.geckoterminal.com/api/v2/networks/solana"

def gt_history(pool_addr):
    """Tageskerzen eines Pools (Solana-Pooladresse = DexScreener pairAddress) fuers Backfill der eigenen Historie."""
    d = get(f"{GT}/pools/{pool_addr}/ohlcv/day", {"limit": 30})
    rows = []
    for ts, o, h, l, c, v in sorted((d or {}).get("data", {}).get("attributes", {}).get("ohlcv_list", [])):
        rows.append({"d": int(ts // DAY), "t": "", "px": float(c), "vol": float(v), "vol6": None, "liq": None, "mcap": None, "buy_ratio": None, "age_d": None})
    return rows[:-1]

def discover(hist):
    """Kandidaten NUR im Zielalterfenster (14-56 Tage): Codex filtert bereits serverseitig nach Alter,
    MCap, Liquiditaet und Volumen (alle 30 Min neu) - das deckt Bs Universum vollstaendig und sauber ab.
    Bot A's Kandidatenlog wird NICHT mehr gemischt: das filtert nur nach Alter, nicht nach MCap, und
    fuellte die Watchlist zuletzt mit ~130 Tokens, die laengst ausserhalb von Bs 10k-300k-Fenster lagen.
    Fallback: falls Codex mal ausfaellt/kein Key gesetzt ist, bleiben die bereits beobachteten Tokens erhalten,
    damit laufende Snapshots/Positionen nicht abreissen - es kommen nur einfach keine neuen hinzu bis Codex wieder geht."""
    addrs = set(hist.keys())
    st_meta = load("bot_b_meta.json", {})
    if time.time() - st_meta.get("last_discover", 0) >= DISCOVER_EVERY_S:
        fresh = codex_aged_candidates()
        addrs |= fresh
        st_meta["last_discover"] = time.time(); st_meta["last_fresh_n"] = len(fresh)
        save("bot_b_meta.json", st_meta)
    return list(addrs)[:400]

def resolve(sym):
    """(fuer Bot C) liquidester Solana-Pool eines Symbols."""
    cache = load("bot_b_tokens.json", {})
    if sym in cache: return cache[sym]
    q = {"BTC": "cbBTC", "ETH": "WETH"}.get(sym, sym)
    d = get(f"{DS}/latest/dex/search", {"q": q}) or {}
    pairs = [p for p in (d.get("pairs") or []) if p.get("chainId") == "solana"
             and p["baseToken"]["symbol"].upper() == q.upper() and (p.get("liquidity") or {}).get("usd", 0) > 200_000]
    if not pairs: return None
    best = max(pairs, key=lambda p: p["liquidity"]["usd"])
    cache[sym] = best["baseToken"]["address"]; save("bot_b_tokens.json", cache)
    return cache[sym]

# ---------- Signal-Logik ----------
def daily(rows):
    """Ein Snapshot je Tag (der letzte des Tages)."""
    by = {}
    for r in rows: by[r["d"]] = r
    return [by[k] for k in sorted(by)]

def merged_history(own, gt):
    """Eigene Snapshots haben Vorrang; GeckoTerminal fuellt fehlende Tage auf."""
    by = {r["d"]: r for r in gt}
    for r in own: by[r["d"]] = r
    return [by[k] for k in sorted(by)]

def entry_check(rows, cur):
    """Gibt (ok, grund) zurueck. rows = Tages-Historie (aelteste zuerst), cur = aktueller Snapshot."""
    if not (MIN_AGE_D <= cur["age_d"] <= MAX_AGE_D): return False, "alter"
    if len(rows) < MIN_HISTORY_DAYS: return False, f"historie {len(rows)}d"
    if not (MIN_MCAP <= cur["mcap"] <= MAX_MCAP): return False, "mcap"
    ratio = cur["liq"] / max(cur["mcap"], 1)
    if cur["liq"] < MIN_LIQ: return False, f"liq {cur['liq']:.0f}"
    if ratio < MIN_LIQ_RATIO: return False, f"liq/mcap {ratio:.2f} zu duenn"
    if ratio > MAX_LIQ_RATIO: return False, f"liq/mcap {ratio:.2f} (kuenstlich?)"
    if cur["vol"] < MIN_VOL24: return False, "volumen"
    # 1 Sicherheit: Liquiditaet faellt nicht (nur pruefbar mit eigenen Snapshots)
    liqs = [r["liq"] for r in rows[-3:] if r.get("liq")]
    if liqs and cur["liq"] < 0.85 * max(liqs): return False, "liq faellt"
    # 2 Substanz: Volumen-Trend aus eigenen Snapshots
    vols = [r["vol"] for r in rows] + [cur["vol"]]
    if len(vols) >= 6: trend = sum(vols[-3:]) / 3 / max(sum(vols[-6:-3]) / 3, 1)
    else: trend = vols[-1] / max(vols[-2], 1)
    if trend < VOL_TREND_MIN: return False, f"vol-trend {trend:.2f}"
    if trend > VOL_TREND_MAX: return False, f"vol-trend {trend:.2f} = spike"
    # 3 Nachfrage: Kaeuferueberhang, kein Einbruch in den letzten 6h
    if cur["buy_ratio"] < MIN_BUY_RATIO: return False, f"buy-ratio {cur['buy_ratio']:.2f}"
    if cur["vol6"] * 4 < 0.5 * cur["vol"]: return False, "6h-volumen eingebrochen"
    # 4 Struktur: ueber dem Schnitt, aber nicht dem Spike hinterher
    avg = sum(r["px"] for r in rows[-7:]) / len(rows[-7:])
    if cur["px"] <= avg: return False, "unter 7d-schnitt"
    if cur["px"] > avg * (1 + MAX_ABOVE_AVG): return False, "zu weit ueber schnitt"
    return True, f"ok vol-trend {trend:.2f} buy {cur['buy_ratio']:.2f}"

def main():
    pf = Paper("bot_b")
    st = pf.s; st.setdefault("cooldown", {}); st.setdefault("streak", 0); st.setdefault("half_left", 0)
    hist = load("bot_b_history.json", {})
    addrs = discover(hist)
    pairs = batch_pairs(addrs)
    prices = {}
    today = day_key()
    # 2. Snapshots
    for a, p in pairs.items():
        sym = p["baseToken"]["symbol"]
        if not tradeable_sym(sym): continue
        s = snapshot(p)
        if s["px"] <= 0: continue
        rows = hist.setdefault(a, {"sym": p["baseToken"]["symbol"], "rows": []})["rows"]
        if rows and rows[-1]["d"] == today: rows[-1] = s
        else: rows.append(s)
        hist[a]["rows"] = rows[-70:]
        prices[a] = s["px"]
    # tote/zu alte/nie-passende Tokens aus der Beobachtung nehmen (nicht aus Positionen/Cooldown)
    # "nie passend": Codex liefert nur noch mcap/liq-gefilterte Kandidaten (v2.2) - Reste aus der alten
    # ungefilterten Quelle (Bot A's Log) werden hier sofort ausgemustert statt langsam auszulaufen.
    for a in list(hist):
        r = hist[a]["rows"]
        if a in st["positions"] or st["cooldown"].get(a, 0) > today: continue
        last = r[-1] if r else None
        stale = not last or last["age_d"] > MAX_AGE_D + 5 or (len(r) >= 3 and all(x["liq"] < 20_000 for x in r[-3:]))
        never_fits = last and not (MIN_MCAP <= last["mcap"] <= MAX_MCAP)
        if stale or never_fits: del hist[a]
    # 3. Positionen verwalten
    for a, pos in list(st["positions"].items()):
        p = pairs.get(a)
        if not p: continue
        cur = snapshot(p); px = cur["px"]; liq = cur["liq"]
        pos["peak"] = max(pos.get("peak", px), px)
        x = px / pos["entry"]
        held = held_seconds(pos) / DAY
        why = None
        if x <= 1 + HARD_STOP: why = "stop"
        elif liq < pos.get("entry_liq", liq) * (1 + LIQ_DROP_EXIT): why = "these: liq"
        elif held >= 3 and cur["vol"] < pos.get("entry_vol", cur["vol"]) * VOL_DROP_EXIT: why = "these: vol"
        elif x >= TP1_X and not pos.get("tp1"):
            pos["tp1"] = True; pf.sell(a, px, TP1_FRAC, liq, "tp1"); continue
        elif px / pos["peak"] - 1 <= TRAIL and x > 1: why = "trail"
        elif held >= MAX_HOLD_D: why = "time"
        if why:
            pnl_neg = px < pos["entry"]
            pf.sell(a, px, 1.0, liq, why)
            if why in ("stop", "these: liq", "these: vol") or pnl_neg:
                st["cooldown"][a] = today + COOLDOWN_D; st["streak"] += 1
                if st["streak"] >= STREAK_HALVE: st["half_left"] = 5; st["streak"] = 0
            else: st["streak"] = 0
            continue
        # Nachkauf: nur Gewinner, nur einmal, nur wenn Volumen weiter waechst
        if x >= ADD_AT_X and not pos.get("added") and cur["vol"] > pos.get("entry_vol", 0) and st["cash"] >= ADD_USD + 5:
            pos["added"] = True; pf.buy(pos["sym"], a, px, ADD_USD, liq, "add")
    # 4. Einstiege
    checks = []; gt_cache = load("bot_b_gt_cache.json", {})
    grob = {"alter": 0, "mcap": 0, "liq": 0, "vol": 0}
    for a, p in pairs.items():
        if a in st["positions"] or len(st["positions"]) >= MAX_POS: continue
        if st["cooldown"].get(a, 0) > today: continue
        rows = daily(hist.get(a, {}).get("rows", []))
        cur = rows[-1] if rows else None
        if not cur: continue
        # Grobfilter zuerst (spart GeckoTerminal-Aufrufe) – Gruende zaehlen
        if not (MIN_AGE_D <= cur["age_d"] <= MAX_AGE_D): grob["alter"] += 1; continue
        if not (MIN_MCAP <= cur["mcap"] <= MAX_MCAP): grob["mcap"] += 1; continue
        if cur["liq"] < MIN_LIQ: grob["liq"] += 1; continue
        if cur["vol"] < MIN_VOL24: grob["vol"] += 1; continue
        own = rows[:-1]
        pool_addr = p.get("pairAddress")
        if len(own) < 7 and pool_addr:
            c = gt_cache.get(a)
            if not c or c["d"] != today:
                c = {"d": today, "rows": gt_history(pool_addr)}; gt_cache[a] = c; time.sleep(2.1)
            own = merged_history(own, c["rows"])
        ok, why = entry_check(own, cur)
        checks.append((p["baseToken"]["symbol"], why))
        if not ok: continue
        safe, risks = rug_ok(a); time.sleep(1.1)
        if not safe:
            if risks != ["rugcheck unavailable"]: st["cooldown"][a] = today + 30
            continue
        size = ENTRY_USD * (0.5 if st["half_left"] > 0 else 1.0)
        if st["cash"] < size + 5: continue
        if pf.buy(p["baseToken"]["symbol"], a, cur["px"], size, cur["liq"], why):
            pos = st["positions"][a]; pos["entry_liq"] = cur["liq"]; pos["entry_vol"] = cur["vol"]
            if st["half_left"] > 0: st["half_left"] -= 1
            append_jsonl("bot_b_signals.jsonl", {"t": now_iso(), "addr": a, "sym": pos["sym"], "why": why, "risks": risks, **cur})
    save("bot_b_history.json", hist); save("bot_b_gt_cache.json", {k: v for k, v in gt_cache.items() if v["d"] >= today - 1})
    st["watchlist"] = len(hist); st["strategy"] = "second_wave"
    v = pf.mark(prices); pf.commit()
    passed = [c for c in checks if c[1].startswith("ok")]
    print(f"Bot B [zweite Welle]: equity {v:.2f} | cash {st['cash']:.2f} | positions {len(st['positions'])} | beobachtet {len(hist)} | signale {len(passed)}")
    print(f"   grobfilter: {grob['alter']} alter, {grob['mcap']} mcap, {grob['liq']} liq, {grob['vol']} vol -> {len(checks)} feingeprueft")
    for sym, why in checks[:15]: print(f"   {sym:<10} {why}")

if __name__ == "__main__":
    main()
