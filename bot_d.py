"""Bot D v3 – "Small-Cap Longshots" (alle Positionen klein, viele Tickets, Paper via echte Jupiter-Quotes).

Warum der Umbau: v2 handelte 60-$-Standardpositionen auf 2-14 Tage alte Micro-Caps und lag klar im Minus.
v3 uebernimmt die Lehren aus Bot C's 574 geloggten Pfaden:
  - LIQUIDITAETS-OBERGRENZE ist der wichtigste Filter. Alle gekauften Rugs dort hatten > 189.000 $
    Startliquiditaet - kuenstlich aufgeblasen, um Mindest-Liquiditaetsfilter zu triggern.
  - Zu viele Holder sind ein WARNSIGNAL, kein Guetesiegel (Rugs: Median 1.674, Ueberlebende: 277).
  - Rugger optimieren top10/bundler nach unten, um Filter zu bestehen. Deshalb Plausibilitaetsfenster
    (min UND max) statt nur Obergrenzen.
Alle Positionen laufen in Longshot-Groesse: kleiner Einsatz, viele Tickets, hohe Ziele.

Datensammlung fuer spaeter:
  - bot_d_paths.json: Preis-/Liquiditaetspfad jedes Kandidaten (fuer analyze_d.py, wie bei Bot C)
  - bot_d_smart.json: welche Wallet fruehzeitig in welchen Token kaufte + wie der Token lief
    -> Grundlage fuer die spaetere "Smart Money"-Strategie. Sammelt ab sofort, aendert das Handeln NICHT.
"""
import os, time, statistics
from common import *

HELIUS = os.environ.get("HELIUS_KEY")
CODEX_KEY = os.environ.get("CODEX_KEY")
CODEX_URL = "https://graph.codex.io/graphql"
SOLANA = 1399811149
RC = "https://api.rugcheck.xyz/v1/tokens"
DISCOVER_EVERY_S = 30 * 60             # Codex hoechstens alle 30 Min (~1.440 Calls/Monat)
PATH_HOURS = 72                        # Pfad-Logging je Kandidat (Tage-Strategie, daher laenger als bei C)

# ---- Universum: ganz kleine Caps ----
MIN_AGE_H, MAX_AGE_H = 12, 7 * 24      # 12 Stunden bis 7 Tage
MIN_MCAP, MAX_MCAP = 15_000, 150_000
MIN_LIQ, MAX_LIQ = 10_000, 50_000      # Obergrenze: Lehre aus Bot C
MIN_VOL24 = 25_000

# ---- Qualitaet (Plausibilitaetsfenster statt reiner Obergrenzen) ----
# v3.2 - Holder-Grenze neu hergeleitet. Die 600 stammten aus Bot C (Tokens MINUTEN nach Migration; dort
# sind 1.600 Holder unmoeglich organisch). Bot D's Universum ist 12 h bis 7 Tage alt - Median 631 Holder,
# die 600er-Grenze warf also die Haelfte aller Kandidaten raus. 3.500 war umgekehrt zu lax: bei Bot C
# steigt die Rug-Quote von 21 % (<=600) auf 38 % (<=3.500).
MIN_HOLDERS, MAX_HOLDERS = 30, 2000
# Zusaetzlich ein Plausibilitaetsmass gegen Airdrop-/Fake-Holder: Liquiditaet je Holder. Echte Kaeufer
# bringen Kapital mit, per Airdrop verteilte Wallets nicht. In D's Universum liegt der Median bei 19 $;
# die Ausreisser darunter sind eindeutig (LOTTO: 18.868 Holder bei 0,60 $ je Holder, ALLCAT: 1,30 $).
# Kein liq/holder-Filter mehr: er fing nur Faelle, die MAX_HOLDERS ohnehin faengt, und produzierte
# Fehlalarme bei voellig normalen Tokens. Die Holderzahl allein ist das trennschaerfere Mass.
MAX_LIQ_OVER_MCAP = 1.0        # Sicherheitsnetz: Liquiditaet ueber der Marktkapitalisierung ist kuenstlich
                               # (kommt im aktuellen Universum nicht vor, faengt aber kuenftige Ausreisser)
MAX_PRE_BUY_DROP = -0.25       # nicht ins fallende Messer: Preis in der letzten Stunde um mehr als 25 % gefallen
MAX_PRE_BUY_LIQ_DROP = -0.15   # Liquiditaet faellt bereits vor dem Kauf -> Rug-Vorlauf, Finger weg
ORPHAN_HOURS = 12              # Position ohne jeden Kurs seit 12 h = faktisch tot -> abschreiben, Slot frei
MAX_TOP10, MAX_BUNDLER, MAX_SNIPER, MAX_INSIDER = 45.0, 10.0, 15.0, 3.0
MIN_BUYS_24H, MAX_SELL_RATIO = 30, 0.9

# ---- Helius-Holderpruefung (uebernommen aus v2) ----
N_BUYERS = 20
# Die Wallet-Alters-Heuristik ist unbelegt: bei Memecoins kauft ein Grossteil mit frischen Wallets, das
# sind keine Bots. Statt hart zu filtern wird die Quote gespeichert (bot_d_smart.json) und blockiert nur
# bei eindeutigen Sybil-Clustern. Nach einigen Wochen laesst sich an den Trades messen, ob sie taugt.
MIN_REAL_SHARE = 0.15
N_WALLET_CHECK = 8            # Stichprobe statt aller 20 Wallets - 2,5x schneller, gleiche Aussage
WALLET_MIN_AGE_D, WALLET_MIN_TX, WALLET_MIN_TOKENS = 7, 20, 3
MIN_GAP_CV, MAX_SAME_AMOUNT = 0.30, 0.50

# ---- Position / Exits (Longshot-Struktur fuer alle) ----
POS_USD, MAX_POS = 15.0, 20            # 20 x 15 $ = 300 $ im Markt, 200 $ Puffer
MAX_BUYS_PER_RUN = 6                   # Laufzeitschutz: Helius-Pruefung dauert ~10-20 s je Kandidat,
                                       # run_paper.sh bricht nach 240 s ab. Lieber ueber mehrere Laeufe fuellen.
# Gestaffelt statt "alles oder nichts": 20x wird so selten erreicht, dass die Restposition meist
# ueber Trailing oder Zeitstop endet statt am Ziel. Drei Stufen sichern unterwegs ab.
TP1_X, TP1_FRAC = 5.0, 0.34            # ein Drittel raus - Einsatz zurueck plus Gewinn
TP2_X, TP2_FRAC = 8.0, 0.5             # die Haelfte des Rests = wieder ein Drittel der Ausgangsposition
TP3_X = 15.0                           # Rest
STOP, TRAIL, MAX_HOLD_D = -0.40, -0.40, 3
# Statt "12 h ohne Anstieg raus" (haette laut Trade-Historie fast alle Gewinner gekillt - Noiz brauchte
# 31 h fuer 7,8x) der aussagekraeftigere Ausstieg: LIQUIDITAET. Sie verlaesst den Pool, bevor der Preis faellt.
LIQ_EXIT_DROP = -0.35                  # Liquiditaet 35 % unter Einstiegsstand -> raus
DEAD_AFTER_H, DEAD_BELOW = 36, -0.20   # nach 36 h immer noch >20 % im Minus -> Kapital freigeben
COOLDOWN_D = 14

# v2.4: $after/$before sind Float!, NICHT Int!. Codex meldete bei Int! den Typfehler
# "Variable $after of type Int! used in position expecting type Float" und brach damit die GESAMTE
# Abfrage ab - codex_candidates() lieferte still eine leere Liste. Bot D hat deshalb vom 16.09. 14:50
# bis zum 18.09. mit einer 42 Stunden alten Kandidatenliste gearbeitet, ohne dass es auffiel.
Q_CAND = """
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

def fnum(x, default=0.0):
    try: return float(x) if x is not None else default
    except Exception: return default

def codex_candidates():
    """None = Codex-Fehler (bald erneut versuchen). Liste = Ergebnis (ggf. leer)."""
    if not CODEX_KEY: return None
    now = time.time()
    try:
        r = requests.post(CODEX_URL, headers={"Authorization": CODEX_KEY, "Content-Type": "application/json", **UA},
                          json={"query": Q_CAND, "variables": {"net": [SOLANA], "after": float(now - MAX_AGE_H * 3600),
                                                                "before": float(now - MIN_AGE_H * 3600)}}, timeout=30)
        r.raise_for_status(); d = r.json()
        if d.get("errors"): print("codex:", str(d["errors"])[:200]); return None
        out = []
        for x in d["data"]["filterTokens"]["results"]:
            t = x["token"]
            out.append({"addr": t["address"], "sym": t.get("symbol") or "?",
                        "liq": fnum(x.get("liquidity")), "mcap": fnum(x.get("marketCap")), "vol": fnum(x.get("volume24")),
                        "buys": int(fnum(x.get("buyCount24"))), "sells": int(fnum(x.get("sellCount24"))),
                        "holders": int(fnum(x.get("holders"))),
                        "top10": fnum(x.get("top10HoldersPercent"), None), "bundler": fnum(x.get("bundlerHeldPercentage"), None),
                        "sniper": fnum(x.get("sniperHeldPercentage"), None), "insider": fnum(x.get("insiderHeldPercentage"), None)})
        return out
    except Exception as e:
        print("codex fehler:", e); return None

def quality(c):
    """Grund fuer Ablehnung, oder None wenn ok."""
    if not (MIN_LIQ <= c["liq"] <= MAX_LIQ): return f"liq {c['liq']:.0f}"
    if c["mcap"] > 0 and c["liq"] / c["mcap"] > MAX_LIQ_OVER_MCAP: return f"liq/mcap {c['liq']/c['mcap']:.1f} (kuenstlich?)"
    if not (MIN_MCAP <= c["mcap"] <= MAX_MCAP): return f"mcap {c['mcap']:.0f}"
    if not (MIN_HOLDERS <= c["holders"] <= MAX_HOLDERS): return f"holders {c['holders']}"
    if c["buys"] < MIN_BUYS_24H: return f"buys24 {c['buys']}"
    if c["buys"] and c["sells"] / c["buys"] > MAX_SELL_RATIO: return f"sell-ratio {c['sells']/c['buys']:.2f}"
    if c["top10"] is not None and c["top10"] > MAX_TOP10: return f"top10 {c['top10']:.0f}%"
    if c["bundler"] is not None and c["bundler"] > MAX_BUNDLER: return f"bundler {c['bundler']:.0f}%"
    if c["sniper"] is not None and c["sniper"] > MAX_SNIPER: return f"sniper {c['sniper']:.0f}%"
    if c["insider"] is not None and c["insider"] > MAX_INSIDER: return f"insider {c['insider']:.1f}%"
    return None

# ---------- Helius ----------
def helius_rpc(method, params):
    r = requests.post(f"https://mainnet.helius-rpc.com/?api-key={HELIUS}", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers=UA, timeout=20)
    r.raise_for_status(); return r.json().get("result")

def recent_buyers(mint):
    r = requests.get(f"https://api.helius.xyz/v0/addresses/{mint}/transactions", params={"api-key": HELIUS, "type": "SWAP", "limit": 60}, headers=UA, timeout=25)
    if r.status_code != 200: raise RuntimeError(f"helius tx {r.status_code}")
    buys = []
    for tx in r.json():
        payer = tx.get("feePayer")
        for t in tx.get("tokenTransfers", []):
            if t.get("mint") == mint and t.get("toUserAccount") == payer and (t.get("tokenAmount") or 0) > 0:
                buys.append({"wallet": payer, "t": tx.get("timestamp", 0), "amt": round(float(t["tokenAmount"]), 4)}); break
    buys.sort(key=lambda b: -b["t"])
    return buys[:N_BUYERS]

def wallet_is_real(w, cache):
    if w in cache: return cache[w]
    try:
        sigs = helius_rpc("getSignaturesForAddress", [w, {"limit": 1000}]) or []
        n = len(sigs); oldest = min((s.get("blockTime") or time.time()) for s in sigs) if sigs else time.time()
        age_d = (time.time() - oldest) / 86400
        accts = helius_rpc("getTokenAccountsByOwner", [w, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"}, {"encoding": "jsonParsed"}]) or {}
        ntok = sum(1 for a in accts.get("value", []) if float(a["account"]["data"]["parsed"]["info"]["tokenAmount"].get("uiAmount") or 0) > 0)
        real = age_d >= WALLET_MIN_AGE_D and n >= WALLET_MIN_TX and ntok >= WALLET_MIN_TOKENS
        if n >= 1000 and age_d >= WALLET_MIN_AGE_D: real = True
    except Exception:
        real = None
    cache[w] = real; return real

def collect_buyers(mint, sym, smart):
    """Nur Datensammlung fuer die spaetere Smart-Money-Strategie: 1 Helius-Call, keine Kaufentscheidung.
    Laeuft fuer jeden Kandidaten mit brauchbaren Kennzahlen, nicht nur fuer die, die gekauft werden -
    sonst haengt die gesamte Datenbasis an der Strenge der Filter und waechst praktisch nicht."""
    if not HELIUS: return None
    try: buys = recent_buyers(mint)
    except Exception: return None
    for b in buys:
        w = smart.setdefault(b["wallet"], {"tokens": {}})
        w["tokens"].setdefault(mint, {"sym": sym, "first_seen": now_iso(), "t": b["t"]})
    return buys

def holder_quality(mint, cache, smart, sym, buys=None):
    """(ok, grund). Harte Ablehnung nur bei eindeutigen Bot-Signaturen."""
    if not HELIUS: return True, "helius fehlt (kein Ausschluss)"
    if buys is None: buys = collect_buyers(mint, sym, smart)
    if buys is None: return True, "helius nicht erreichbar (kein Ausschluss)"
    if len(buys) < 8: return False, f"nur {len(buys)} kaeufer"
    ts = sorted(b["t"] for b in buys); gaps = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    if len(gaps) >= 5:
        cv = statistics.pstdev(gaps) / max(statistics.mean(gaps), 1)
        if cv < MIN_GAP_CV: return False, f"bot-timing cv {cv:.2f}"
    amts = [b["amt"] for b in buys]
    if amts and max(amts.count(a) for a in set(amts)) / len(amts) > MAX_SAME_AMOUNT: return False, "identische betraege"
    wallets = list(dict.fromkeys(b["wallet"] for b in buys))[:N_WALLET_CHECK]
    flags = [wallet_is_real(w, cache) for w in wallets]; time.sleep(0.2)
    known = [f for f in flags if f is not None]
    if len(known) < 3: return True, "wallets nicht pruefbar (kein Ausschluss)"
    share = sum(known) / len(known)
    smart.setdefault("_stats", {})[mint] = {"real_share": round(share, 2), "t": now_iso(), "sym": sym}
    return share >= MIN_REAL_SHARE, f"echte wallets {share:.0%} ({len(known)})"

def rug_strict(addr):
    s = get(f"{RC}/{addr}/report/summary")
    if not s: return False
    risks = [r.get("name", "").lower() for r in (s.get("risks") or [])]
    bad = ("mint", "freeze", "unlocked", "top 10", "single holder", "copycat", "low liquidity", "high holder")
    if any(k in r for r in risks for k in bad): return False
    sc = s.get("score_normalised")
    return sc is None or sc <= 30

def batch_pairs(addrs):
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

def main():
    pf = Paper("bot_d"); st = pf.s
    st["strategy"] = "smallcap_longshots"; st["helius"] = bool(HELIUS); st["codex"] = bool(CODEX_KEY)
    now = time.time(); today = int(now // 86400)
    cooldown = load("bot_d_cooldown.json", {}); wcache = load("bot_d_wallets.json", {})
    smart = load("bot_d_smart.json", {}); paths = load("bot_d_paths.json", {})
    meta = load("bot_d_meta.json", {"cands": [], "last_discover": 0})

    # 1) Kandidaten (alle 30 Min neu)
    if now - meta.get("last_discover", 0) >= DISCOVER_EVERY_S:
        fresh = codex_candidates()
        if fresh is None:
            alter_h = (now - meta.get("last_discover", now)) / 3600
            print(f"Bot D: Codex-Abfrage fehlgeschlagen. Kandidatenliste ist {alter_h:.1f} h alt "
                  f"({len(meta.get('cands', []))} Tokens) - ab ~2 h nicht mehr aussagekraeftig.")
        else:
            meta["last_discover"] = now
            if fresh: meta["cands"] = fresh
            else: print("Bot D: Codex meldet 0 Kandidaten im Fenster")
    cands = {c["addr"]: c for c in meta.get("cands", [])}
    for a, c in cands.items():
        paths.setdefault(a, {"sym": c["sym"], "first": now, "pts": [],
                             "q": {k: c[k] for k in ("liq", "mcap", "holders", "top10", "bundler", "sniper", "insider")}})

    # 2) Kurse + Pfade fortschreiben
    watch = [a for a, p in paths.items() if now - p["first"] <= PATH_HOURS * 3600]
    pairs = batch_pairs(list(set(watch) | set(st["positions"].keys())))
    prices = {}
    for a, p in pairs.items():
        px = float(p.get("priceUsd") or 0); liq = (p.get("liquidity") or {}).get("usd") or 0
        if px > 0: prices[a] = px
        if a in paths and now - paths[a]["first"] <= PATH_HOURS * 3600:
            paths[a]["pts"].append([round((now - paths[a]["first"]) / 60, 1), px, liq])

    # 3) Positionen verwalten
    for a, pos in list(st["positions"].items()):
        px = prices.get(a)
        if not px:
            # Kein Kurs: merken, wann das begann. Bleibt es dabei, ist der Token tot (typisch nach einem Rug)
            # und die Position wuerde sonst dauerhaft einen der MAX_POS Plaetze blockieren.
            pos.setdefault("no_px_since", now_iso())
            if (now - parse_iso(pos["no_px_since"])) / 3600 >= ORPHAN_HOURS:
                qty = pos["qty"]; entry = pos["entry"]
                st["trades"].append({"t": now_iso(), "side": "sell", "sym": pos["sym"], "addr": a,
                                     "price": 0.0, "usd": 0.0, "pnl": round(-qty * entry, 2), "peak_x":
                                     round(pos.get("peak", entry) / entry, 3), "held_h": round(held_seconds(pos) / 3600, 1),
                                     "frac": 1.0, "quote": "none", "reason": "abgeschrieben"})
                del st["positions"][a]; cooldown[a] = today + COOLDOWN_D
                print(f"  {pos['sym']}: seit {ORPHAN_HOURS} h kein Kurs -> als Totalverlust abgeschrieben")
            continue
        pos.pop("no_px_since", None)
        liq = (pairs[a].get("liquidity") or {}).get("usd") or 0
        pos["peak"] = max(pos.get("peak", px), px)
        x = px / pos["entry"]; held_d = held_seconds(pos) / 86400
        liq0 = pos.get("liq0") or liq
        pos.setdefault("liq0", liq0)
        why = None
        if x <= 1 + STOP: why = "stop"
        elif liq0 and liq / liq0 - 1 <= LIQ_EXIT_DROP: why = "liq-drop"
        elif held_d * 24 >= DEAD_AFTER_H and x - 1 <= DEAD_BELOW: why = "tot"
        elif x >= TP3_X: why = "tp3"
        elif x >= TP2_X and not pos.get("tp2"):
            pos["tp2"] = True; pf.sell(a, px, TP2_FRAC, liq, "tp2"); continue
        elif x >= TP1_X and not pos.get("tp1"):
            pos["tp1"] = True; pf.sell(a, px, TP1_FRAC, liq, "tp1"); continue
        elif pos.get("tp1") and px / pos["peak"] - 1 <= TRAIL: why = "trail"
        elif held_d >= MAX_HOLD_D: why = "time"
        if why:
            pf.sell(a, px, 1.0, liq, why)
            if px < pos["entry"]: cooldown[a] = today + COOLDOWN_D

    # 4) Einstiege
    checks = []; bought = 0
    for a, c in cands.items():
        if a in st["positions"] or cooldown.get(a, 0) > today: continue
        if len(st["positions"]) >= MAX_POS: break
        why = quality(c)
        if why: checks.append((c["sym"], why)); continue
        px = prices.get(a); liq = (pairs.get(a, {}).get("liquidity") or {}).get("usd") or 0
        if not px or not (MIN_LIQ <= liq <= MAX_LIQ): checks.append((c["sym"], f"ds-liq {liq:.0f}")); continue
        # Eigener Verlauf der letzten ~1 h: weder in einen Preissturz noch in abfliessende Liquiditaet kaufen.
        pts = [x for x in paths.get(a, {}).get("pts", []) if x[1] > 0 and x[2] > 0][-13:]
        if len(pts) >= 4:
            px_chg = pts[-1][1] / pts[0][1] - 1
            liq_chg = pts[-1][2] / pts[0][2] - 1
            if px_chg <= MAX_PRE_BUY_DROP: checks.append((c["sym"], f"preis {px_chg:+.0%} in 1h")); continue
            if liq_chg <= MAX_PRE_BUY_LIQ_DROP:
                checks.append((c["sym"], f"liq {liq_chg:+.0%} in 1h (rug-vorlauf)")); cooldown[a] = today + 2; continue
        # Datensammlung laeuft VOR der Kaufentscheidung und unabhaengig von ihr
        buys = collect_buyers(a, c["sym"], smart)
        if not rug_strict(a):
            checks.append((c["sym"], "rugcheck")); cooldown[a] = today + COOLDOWN_D; time.sleep(1.1); continue
        time.sleep(1.1)
        hq, reason = holder_quality(a, wcache, smart, c["sym"], buys)
        if hq is False: cooldown[a] = today + 3; checks.append((c["sym"], reason)); continue
        if st["cash"] < POS_USD + 2: break
        if bought >= MAX_BUYS_PER_RUN:
            checks.append((c["sym"], "ok, aber Kauflimit dieses Laufs erreicht")); break
        if pf.buy(c["sym"], a, px, POS_USD, liq, "longshot"):
            bought += 1
            st["positions"][a]["liq0"] = liq
            checks.append((c["sym"], f"GEKAUFT liq {liq:.0f} mcap {c['mcap']:.0f} holders {c['holders']}"))
            append_jsonl("bot_d_signals.jsonl", {"t": now_iso(), "addr": a, "sym": c["sym"], "liq": liq,
                                                 **{k: c[k] for k in ("mcap", "vol", "holders", "top10", "bundler", "sniper", "insider")}})

    # 5) Smart-Money-Performance nachtragen (wie liefen die Tokens, die eine Wallet gekauft hat?)
    for w, rec in smart.items():
        if w == "_stats" or "tokens" not in rec: continue
        for mint, info in rec["tokens"].items():
            if mint in prices and prices[mint] > 0:
                info.setdefault("px0", prices[mint])
                info["px_last"] = prices[mint]
                if info.get("px0"): info["x"] = round(prices[mint] / info["px0"], 3)
    if len(smart) > 6000:     # ausduennen: Wallets behalten, die in den meisten Tokens auftauchen
        stats = smart.get("_stats")
        keep = dict(sorted(((k, v) for k, v in smart.items() if k != "_stats"),
                           key=lambda kv: -len(kv[1].get("tokens", {})))[:4500])
        if stats: keep["_stats"] = stats
        smart = keep

    # 6) Pfade aufraeumen
    for a in list(paths):
        if now - paths[a]["first"] > PATH_HOURS * 3600 + 86400:
            append_jsonl("bot_d_paths_archive.jsonl", {"addr": a, **paths[a]}); del paths[a]

    save("bot_d_paths.json", paths); save("bot_d_meta.json", meta); save("bot_d_smart.json", smart)
    save("bot_d_wallets.json", wcache); save("bot_d_cooldown.json", {k: v for k, v in cooldown.items() if v > today})
    v = pf.mark(prices); pf.commit()
    print(f"Bot D [smallcap-longshots]: equity {v:.2f} | cash {st['cash']:.2f} | positions {len(st['positions'])}/{MAX_POS} | kandidaten {len(cands)} | pfade {len(paths)} | wallets {len(smart)}")
    for sym, why in checks[:12]: print(f"   {sym:<12} {why}")

if __name__ == "__main__":
    main()
