"""Stufe 2 der Copy-Trading-Pruefung (wird von bot_d.py benutzt, laeuft nicht allein).

FRAGE: Haette man mit einer Kandidaten-Wallet Geld verdient, wenn man sie KOPIERT haette - also mit unserer
Verzoegerung gekauft und verkauft, nach Kosten? Dafuer reicht der Blick auf unsere ~40 Coins am Tag nicht
(wir saehen nur einen kleinen Ausschnitt ihrer Trades). Deshalb hier pro Wallet ihre KOMPLETTE Handelshistorie.

ABLAUF je Wallet:
  1) Helius getSignaturesForAddress: Transaktionen der letzten 14 Tage (1 Credit je 1000). Mehr als 50 je Tag
     -> Bot, sofort verworfen (kostet fast nichts).
  2) Helius getTransaction fuer die neuesten bis zu 300 (1 Credit je Stueck): aus den Kontostaenden vorher/nachher
     jeden Swap gegen SOL erkennen (Kauf: Token rein, SOL raus; Verkauf umgekehrt). Je Coin ein "Trade" vom ersten
     Kauf bis die Menge wieder verkauft ist. Coins mit Token-Transfers ohne SOL (Airdrop, Umbuchung) fallen raus.
  3) Vorfilter: >= 20 Trades, typische Zeit bis zum ersten Verkauf >= 5 Minuten, letzter Trade <= 3 Tage alt.
  4) KOPIERER-SIMULATION der neuesten bis zu 30 Trades mit Minutenkerzen (GeckoTerminal, kostenlos; Ausweichweg
     Codex): Einstieg 1 Minute nach dem Kauf der Wallet, Ausstieg 1 Minute nach ihrem ersten Verkauf, spaetestens
     nach 16 h, eigener Stop -50 %. Davon die Kosten abgezogen, die wir mit Jupiter messen (Kauf + sofortiger
     Verkauf ueber 50 $ bei aehnlichen Coins); bis genug Messungen da sind pauschal 3 %.
  5) BESTANDEN nur wenn: >= 20 simulierte Trades, Schnitt > 0, Schnitt ohne die 2 besten > 0, und in BEIDEN
     zeitlichen Haelften Schnitt > 0. Alle Grenzen sind VORAB festgelegt (siehe unten), nicht nachjustiert.
"""
import time, statistics, calendar
import requests
from common import UA, get, jup_quote, token_decimals, USDC

WSOL = "So11111111111111111111111111111111111111112"
STABLE = {USDC, "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
GT = "https://api.geckoterminal.com/api/v2/networks/solana"
GT_H = {"Accept": "application/json;version=20230302", **UA}

TAGE = 14                 # Pruefzeitraum
MAX_TX_TAG = 50           # mehr Transaktionen je Tag = Bot
SIG_SEITEN = 5            # hoechstens 5 x 1000 Signaturen
MAX_TX_HOLEN = 300        # hoechstens so viele Transaktionen einzeln holen (~300 Credits je Wallet)
MIN_TRADES = 20
MIN_HALTE_MIN = 5         # typische Zeit bis zum ersten Verkauf
AKTIV_D = 3
DELAY_S = 60              # unsere Verzoegerung beim Kopieren
SIM_MAX_H = 16            # spaetestens nach 16 h raus (1000 Minutenkerzen = 1 Abfrage)
SIM_STOP = -0.50
SIM_TRADES = 30
KOSTEN_STANDARD = 0.03    # bis genug Jupiter-Messungen da sind
KOSTEN_MIN_MESSUNGEN = 10


# ---------------- Helius ----------------
def _url(key): return f"https://mainnet.helius-rpc.com/?api-key={key}"

def _rpc(key, method, params):
    r = requests.post(_url(key), json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers=UA, timeout=30)
    r.raise_for_status(); j = r.json()
    if j.get("error"): raise RuntimeError(str(j["error"])[:120])
    return j.get("result")

def signaturen(key, wallet, credits):
    """Signaturen der letzten TAGE (neueste zuerst) und ob die Seitengrenze erreicht wurde."""
    grenze = time.time() - TAGE * 86400
    out, vor = [], None
    for _ in range(SIG_SEITEN):
        opt = {"limit": 1000, **({"before": vor} if vor else {})}
        s = _rpc(key, "getSignaturesForAddress", [wallet, opt]) or []; credits["n"] += 1
        if not s: return out, False
        for x in s:
            if (x.get("blockTime") or 0) < grenze: return out, False
            if x.get("err") is None: out.append(x)
        vor = s[-1]["signature"]
        if len(s) < 1000: return out, False
        time.sleep(0.2)
    return out, True

def transaktionen(key, sigs, credits, deadline=None):
    """getTransaction fuer alle sigs - erst als Sammelabfrage (25 je Aufruf), sonst einzeln."""
    opt = {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}
    out = []
    for k in range(0, len(sigs), 25):
        if deadline and time.time() > deadline: raise TimeoutError("Laufzeit - naechster Lauf versucht es erneut")
        teil = sigs[k:k + 25]
        try:
            body = [{"jsonrpc": "2.0", "id": i, "method": "getTransaction", "params": [s, opt]} for i, s in enumerate(teil)]
            r = requests.post(_url(key), json=body, headers=UA, timeout=45); credits["n"] += len(teil)
            j = r.json()
            if isinstance(j, list) and all("result" in x for x in j):
                out += [x["result"] for x in sorted(j, key=lambda x: x.get("id", 0)) if x.get("result")]
                time.sleep(0.3); continue
        except Exception:
            pass
        for s in teil:
            try:
                tx = _rpc(key, "getTransaction", [s, opt]); credits["n"] += 1
                if tx: out.append(tx)
            except Exception:
                time.sleep(1)
            time.sleep(0.12)
    return out

def _menge(b):
    a = b.get("uiTokenAmount") or {}
    try: return float(a.get("uiAmountString") or a.get("uiAmount") or 0)
    except (TypeError, ValueError): return 0.0

def swap_aus_tx(tx, wallet):
    """{"art": kauf|verkauf|transfer|unklar, ...} oder None (nichts mit Tokens)."""
    meta = tx.get("meta") or {}
    if meta.get("err") is not None: return None
    msg = (tx.get("transaction") or {}).get("message") or {}
    keys = [k if isinstance(k, str) else (k or {}).get("pubkey") for k in msg.get("accountKeys") or []]
    la = meta.get("loadedAddresses") or {}
    keys += (la.get("writable") or []) + (la.get("readonly") or [])
    if wallet not in keys: return None
    i = keys.index(wallet)
    try: sol = (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9
    except (KeyError, IndexError): return None
    d = {}
    for b in meta.get("preTokenBalances") or []:
        if b.get("owner") == wallet: d[b["mint"]] = d.get(b["mint"], 0) - _menge(b)
    for b in meta.get("postTokenBalances") or []:
        if b.get("owner") == wallet: d[b["mint"]] = d.get(b["mint"], 0) + _menge(b)
    sol += d.pop(WSOL, 0)
    toks = {m: v for m, v in d.items() if abs(v) > 1e-9}
    if not toks: return None
    t = tx.get("blockTime") or 0
    if len(toks) != 1 or any(m in STABLE for m in toks): return {"art": "unklar", "mints": list(toks), "t": t}
    m, v = next(iter(toks.items()))
    if abs(sol) < 0.001: return {"art": "transfer", "mint": m, "t": t}
    if v > 0 and sol < 0: return {"art": "kauf", "mint": m, "menge": v, "sol": -sol, "t": t}
    if v < 0 and sol > 0: return {"art": "verkauf", "mint": m, "menge": -v, "sol": sol, "t": t}
    return {"art": "unklar", "mints": [m], "t": t}

def trades_bilden(swaps):
    """Je Coin ein Trade: erster Kauf ... bis >= 98 % der gekauften Menge verkauft sind."""
    swaps = sorted((s for s in swaps if s), key=lambda s: s["t"])
    schmutzig = {s["mint"] for s in swaps if s["art"] == "transfer"} | {m for s in swaps if s["art"] == "unklar" for m in s.get("mints", [])}
    offen, fertig = {}, []
    for s in swaps:
        m = s.get("mint")
        if not m or m in schmutzig or s["art"] not in ("kauf", "verkauf"): continue
        p = offen.get(m)
        if s["art"] == "kauf":
            if not p: p = offen[m] = {"mint": m, "t_kauf": s["t"], "sol_in": 0.0, "tok_in": 0.0, "sol_out": 0.0, "tok_out": 0.0}
            p["sol_in"] += s["sol"]; p["tok_in"] += s["menge"]
        else:
            if not p: continue                        # Kauf lag vor dem Pruefzeitraum
            p["sol_out"] += s["sol"]; p["tok_out"] += s["menge"]; p.setdefault("t_verkauf", s["t"])
            if p["tok_out"] >= 0.98 * p["tok_in"]:
                p["t_zu"] = s["t"]; fertig.append(p); del offen[m]
    for p in offen.values(): p["offen"] = True; fertig.append(p)
    return sorted(fertig, key=lambda p: p["t_kauf"])


# ---------------- Minutenkerzen ----------------
def _iso(s):
    try: return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception: return 0

def gt_pool(mint, t):
    """Pool, in dem zur Zeit t gehandelt wurde: der zuletzt VOR t eroeffnete (pump.fun-Kurve -> nach Umzug PumpSwap)."""
    d = get(f"{GT}/tokens/{mint}/pools", params={"page": 1}) or {}
    pools = [(_iso((p.get("attributes") or {}).get("pool_created_at") or ""), (p.get("attributes") or {}).get("address"))
             for p in d.get("data") or []]
    pools = [p for p in pools if p[1]]
    if not pools: return None
    vorher = [p for p in pools if p[0] and p[0] <= t]
    return (max(vorher) if vorher else min(pools))[1]

def gt_kerzen(pool, von, bis):
    d = get(f"{GT}/pools/{pool}/ohlcv/minute", params={"aggregate": 1, "before_timestamp": int(bis), "limit": 1000,
                                                       "currency": "usd"}) or {}
    rows = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    return sorted([r[:5] for r in rows if r and r[0] >= von - 120])

def codex_kerzen(codex, pool, von, bis):
    """Ausweichweg. codex = Funktion(query) -> data (zaehlt selbst das Budget)."""
    d = codex('{ getBars(symbol: "%s:1399811149", from: %d, to: %d, resolution: "1", removeEmptyBars: true) { t o h l c } }'
              % (pool, int(von), int(bis))).get("getBars") or {}
    t = d.get("t") or []
    return [[t[i], d["o"][i], d["h"][i], d["l"][i], d["c"][i]] for i in range(len(t)) if d["c"][i]]

def kopie_detail(tr, kerzen, kosten):
    """Wie kopie_simulieren, aber mit allen Einzelheiten (v5.2, zum Nachpruefen):
    {"kopie", "entry", "exit", "grund", "luecke_rein_s", "luecke_raus_s", "kerzen"} oder None (keine Kurse)."""
    te = tr["t_kauf"] + DELAY_S
    ende = te + SIM_MAX_H * 3600
    raus = min(tr["t_verkauf"] + DELAY_S, ende) if tr.get("t_verkauf") else ende
    k = [r for r in kerzen if r[4]]
    rein = next((r for r in k if r[0] <= te < r[0] + 60), None) or next((r for r in k if r[0] > te), None)
    if not rein or rein[0] > te + 15 * 60: return None          # kein Kurs in der Naehe des Einstiegs
    entry = float(rein[4]); exit_px, t_exit, grund = None, None, None
    for r in k:
        if r[0] <= rein[0]: continue
        if r[0] > raus: break
        if float(r[3]) <= entry * (1 + SIM_STOP): exit_px, t_exit, grund = entry * (1 + SIM_STOP), r[0], "stop"; break
        exit_px, t_exit = float(r[4]), r[0]
    if exit_px is None: exit_px, t_exit = entry, rein[0]          # gleiche Minute raus
    if not grund: grund = "nach_verkauf" if tr.get("t_verkauf") and tr["t_verkauf"] + DELAY_S < ende else "max_16h"
    return {"kopie": round(exit_px / entry - 1 - kosten, 4), "entry": entry, "exit": exit_px, "grund": grund,
            "luecke_rein_s": int(rein[0] - te),                  # Kerze des Einstiegs: Abstand zum Soll-Zeitpunkt
            "luecke_raus_s": int(raus - t_exit) if grund != "stop" else 0,   # wie alt war der letzte Kurs beim Ausstieg
            "kerzen": len(k)}

def kopie_simulieren(tr, kerzen, kosten):
    """Ergebnis eines Kopierers in Prozent nach Kosten, oder None (keine Kurse)."""
    d = kopie_detail(tr, kerzen, kosten)
    return d["kopie"] if d else None


# ---------------- Kosten mit Jupiter messen ----------------
def kosten_messen(mint, usd=50.0):
    """Verlust eines Kaufs ueber usd und sofortigen Verkaufs, laut Jupiter-Quotes (+ 2 x Netzwerkgebuehr)."""
    hin = jup_quote(USDC, mint, int(usd * 1e6))
    if not hin or not hin[0]: return None
    zurueck = jup_quote(mint, USDC, hin[0])
    if not zurueck or not zurueck[0]: return None
    return round(1 - zurueck[0] / (usd * 1e6) + 2 * 0.02 / usd, 4)

def kosten_aktuell(messungen):
    w = [m["k"] for m in messungen if m.get("k") is not None]
    if len(w) < KOSTEN_MIN_MESSUNGEN: return KOSTEN_STANDARD, "pauschal"
    return max(0.005, statistics.median(w)), "jupiter"


# ---------------- Bewertung ----------------
def bewerten(sim):
    """sim = [[t_kauf, ergebnis], ...] -> (bestanden, kennzahlen)."""
    sim = sorted(sim)
    x = [s[1] for s in sim]
    if len(x) < MIN_TRADES: return False, {"n": len(x), "grund": f"nur {len(x)} Trades mit Kursen"}
    h = len(x) // 2
    ohne = sorted(x)[:-2]
    kz = {"n": len(x), "schnitt": round(statistics.mean(x), 4), "ohne_top2": round(statistics.mean(ohne), 4),
          "haelfte1": round(statistics.mean(x[:h]), 4), "haelfte2": round(statistics.mean(x[h:]), 4),
          "median": round(statistics.median(x), 4), "trefferquote": round(sum(v > 0 for v in x) / len(x), 3)}
    ok = kz["schnitt"] > 0 and kz["ohne_top2"] > 0 and kz["haelfte1"] > 0 and kz["haelfte2"] > 0
    if not ok:
        kz["grund"] = "Kopierer-Ergebnis nicht in allen Punkten positiv"
    return ok, kz


def vorab(key, wallet, credits):
    """Schnelltest mit EINER Abfrage (1 Credit): die letzten 1.000 Signaturen. Liegen alle in den letzten 14 Tagen,
    sind das > 70 je Tag -> Bot. Status: bot | inaktiv | vorab_ok (dann volle Pruefung mit historie_pruefen)."""
    now = time.time()
    s = [x for x in (_rpc(key, "getSignaturesForAddress", [wallet, {"limit": 1000}]) or []) if x.get("blockTime")]
    credits["n"] += 1
    rec = {"t": now}
    if not s: rec.update(status="inaktiv", grund="keine Transaktionen"); return rec
    if now - max(x["blockTime"] for x in s) > AKTIV_D * 86400:
        rec.update(status="inaktiv", grund="letzte Transaktion aelter als 3 Tage"); return rec
    in14 = [x for x in s if x["blockTime"] >= now - TAGE * 86400]
    spanne_d = max(1.0, (now - min(x["blockTime"] for x in in14)) / 86400)
    rec["tx_tag"] = round(len(in14) / spanne_d, 1)
    if rec["tx_tag"] > MAX_TX_TAG:
        rec.update(status="bot", grund=f"{rec['tx_tag']} Transaktionen je Tag (Schnelltest)"); return rec
    rec["status"] = "vorab_ok"
    return rec


def historie_pruefen(key, wallet, credits, deadline=None):
    """Schritte 1-3. Gibt den Pruef-Datensatz zurueck (status + Kennzahlen + offene Simulation)."""
    now = time.time()
    rec = {"t": now}
    sigs, voll = signaturen(key, wallet, credits)
    if voll:
        rec.update(status="bot", grund=f"> {SIG_SEITEN * 1000} Transaktionen in {TAGE} Tagen"); return rec
    if not sigs:
        rec.update(status="inaktiv", grund="keine Transaktionen in 14 Tagen"); return rec
    spanne_d = max(1.0, (now - min(s.get("blockTime") or now for s in sigs)) / 86400)
    rec["tx_tag"] = round(len(sigs) / spanne_d, 1)
    rec["letzte"] = max(s.get("blockTime") or 0 for s in sigs)
    if rec["tx_tag"] > MAX_TX_TAG:
        rec.update(status="bot", grund=f"{rec['tx_tag']} Transaktionen je Tag"); return rec
    if now - rec["letzte"] > AKTIV_D * 86400:
        rec.update(status="inaktiv", grund="letzte Transaktion aelter als 3 Tage"); return rec
    txs = transaktionen(key, [s["signature"] for s in sigs[:MAX_TX_HOLEN]], credits, deadline)
    trades = trades_bilden([swap_aus_tx(t, wallet) for t in txs])
    zu = [t for t in trades if not t.get("offen")]
    rec["trades"] = len(trades); rec["geschlossen"] = len(zu)
    rec["liegen_gelassen"] = sum(1 for t in trades if t.get("offen") and now - t["t_kauf"] > 3 * 86400)
    if zu:
        rec["eigen_schnitt"] = round(statistics.mean(t["sol_out"] / t["sol_in"] - 1 for t in zu if t["sol_in"] > 0), 4)
    halte = [(t["t_verkauf"] - t["t_kauf"]) / 60 for t in trades if t.get("t_verkauf")]
    rec["halte_min"] = round(statistics.median(halte), 1) if halte else None
    # simulierbar: verkauft, oder Einstieg lang genug her
    sim = [t for t in trades if t.get("t_verkauf") or now - t["t_kauf"] > SIM_MAX_H * 3600 + DELAY_S]
    if len(sim) < MIN_TRADES:
        rec.update(status="zu_wenig", grund=f"nur {len(sim)} Trades in {TAGE} Tagen"); return rec
    if rec["halte_min"] is None or rec["halte_min"] < MIN_HALTE_MIN:
        rec.update(status="zu_schnell", grund=f"verkauft typisch nach {rec['halte_min']} min"); return rec
    if not trades or now - max(t["t_kauf"] for t in trades) > AKTIV_D * 86400:
        rec.update(status="inaktiv", grund="kein Kauf in den letzten 3 Tagen"); return rec
    rec["status"] = "sim"
    rec["offen_sim"] = [{"mint": t["mint"], "t_kauf": t["t_kauf"], "t_verkauf": t.get("t_verkauf"),
                         "sol_in": round(t["sol_in"], 4),
                         # v5.2: Ergebnis der Wallet SELBST fuer diesen Trade (nur wenn ganz verkauft) - zum Vergleich mit der Kopie
                         "eigen": round(t["sol_out"] / t["sol_in"] - 1, 4) if not t.get("offen") and t["sol_in"] > 0 else None}
                        for t in sim[-SIM_TRADES:]]
    rec["sim"] = []; rec["details"] = []; rec["ohne_kurs"] = 0
    return rec
