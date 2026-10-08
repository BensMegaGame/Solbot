"""Bot D v4 – "Datensammler": kauft NICHTS mehr, sammelt nur noch Daten fuer die Fruehkaeufer-Analyse.

WARUM: Als Handels-Bot ist D gescheitert (500 $ -> ~96 $, 295 Positionen, 11 % Gewinner, 78 % per Stop).
Wertvoll waren seine Daten. Ab v4 sammelt er gezielt das, was fuer die Frage "gibt es Wallets, die immer
wieder frueh in spaetere Gewinner einsteigen?" noetig ist - fuer Gewinner UND Verlierer, sonst ist die
Auswertung wertlos (Bots, die ueberall frueh kaufen, saehen sonst wie Genies aus).

Pro Kandidat (Codex, 12 h - 7 Tage alt, MCap 15-150k, wie bisher):
  1) FRUEHKAEUFER (einmal je Coin): die ersten 100 Transaktionen ab Start ueber Helius
     getTransactionsForAddress (aelteste zuerst, ~10 Credits je Coin). Aus den Token-Kontostaenden
     vorher/nachher: welche Wallet kaufte, wie viele Sekunden und Slots nach dem Start, wie viel.
     Kaeufe im Start-Slot oder dem naechsten gelten als Sniper/Insider und werden spaeter ausgeklammert.
     Falls die Methode im Helius-Plan fehlt: Ausweichweg ueber getSignaturesForAddress + getTransaction.
  2) ERGEBNIS: Kurs ab Entdeckung 7 Tage lang (bis 72 h alle 5 Min, danach stuendlich). Gewinner = mind. 2x
     ueber dem ersten gesehenen Kurs innerhalb von 7 Tagen.
  3) WALLET-RANGLISTE (data/bot_d_top_wallets.json): je Wallet die Coins, in denen sie frueh (nicht als
     Sniper) gekauft hat, und wie viele davon Gewinner wurden - verglichen mit der Gewinnerquote aller Coins.
     Das ist eine BEOBACHTUNG, noch keine Strategie: ob eine Wallet wirklich besser ist, wird erst mit
     zeitlich getrennten Daten geprueft.

Offene Positionen aus v3 werden noch nach den alten Regeln verkauft. Die teuren Helius-Abfragen von v3
(Enhanced Transactions, 100 Credits je Abfrage, und die Wallet-Echtheitspruefung) entfallen.
"""
import os, time, statistics
from common import *

HELIUS = os.environ.get("HELIUS_KEY")
CODEX_KEY = os.environ.get("CODEX_KEY")
CODEX_URL = "https://graph.codex.io/graphql"
SOLANA = 1399811149
RC = "https://api.rugcheck.xyz/v1/tokens"
DISCOVER_EVERY_S = 30 * 60             # Codex hoechstens alle 30 Min (~1.440 Calls/Monat)
PATH_HOURS = 7 * 24                    # v4: 7 Tage Kursverlauf je Kandidat (Gewinner/Verlierer sicher bestimmen)
DICHT_H = 72                           # bis 72 h alle 5 Min, danach stuendlich
HANDEL = False                         # v4: keine Kaeufe mehr - reiner Datensammler
FRUEH_PRO_LAUF = 8                     # so viele Coins je Lauf auf Fruehkaeufer pruefen (Laufzeit + Credits)
FRUEH_TX = 100                         # die ersten 100 Transaktionen ab Start
FRUEH_MAX_KAEUFER = 40                 # hoechstens 40 verschiedene Kaeufer je Coin speichern
SNIPER_SLOTS = 1                       # Kauf im Start-Slot oder dem naechsten = Sniper/Insider
GEWINNER_X = 2.0                       # Gewinner = mind. 2x ueber dem ersten gesehenen Kurs (7 Tage)
MIN_COINS_WALLET = 3                   # Wallet erst ab 3 auswertbaren Coins in die Rangliste
FALLBACK_SEITEN = 25                   # Ausweichweg: hoechstens 25 x 1000 Signaturen zurueckblaettern

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
def q_cand(now):
    """Die Zeitstempel stehen als ZAHLEN in der Abfrage, nicht als GraphQL-Variablen.
    Am 18.09. gemessen: createdAt mit einer Variablen ($after) laesst Codex mit
    DOWNSTREAM_SERVICE_ERROR abbrechen - dieselbe Abfrage mit einer festen Zahl an derselben
    Stelle antwortet sauber (20 Treffer). Ein Fehler auf Codex-Seite, den die Zahl umgeht."""
    return """
query($net: [Int!]) {
  filterTokens(
    filters: { network: $net, createdAt: { gte: %d, lte: %d }, volume24: { gte: %s },
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
}""" % (int(now - MAX_AGE_H * 3600), int(now - MIN_AGE_H * 3600), MIN_VOL24, MIN_MCAP, MAX_MCAP, MIN_LIQ, MAX_LIQ)


def fnum(x, default=0.0):
    try: return float(x) if x is not None else default
    except Exception: return default

def codex_candidates():
    """None = Codex-Fehler (bald erneut versuchen). Liste = Ergebnis (ggf. leer)."""
    if not CODEX_KEY: return None
    now = time.time()
    try:
        r = requests.post(CODEX_URL, headers={"Authorization": CODEX_KEY, "Content-Type": "application/json", **UA},
                          json={"query": q_cand(now), "variables": {"net": [SOLANA]}}, timeout=30)
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
        aeltestes = {}
        for p in (d.get("pairs") or []):
            if p.get("chainId") != "solana": continue
            a = p["baseToken"]["address"]
            c = p.get("pairCreatedAt")
            if c and (a not in aeltestes or c < aeltestes[a]): aeltestes[a] = c
            if a not in out or (p.get("liquidity") or {}).get("usd", 0) > (out[a].get("liquidity") or {}).get("usd", 0):
                out[a] = p
        # Das Alter des liquidesten Pools unterschaetzt das Token-Alter, sobald ein Token einen neuen Pool
        # bekommt (Migration, zweites DEX-Listing). Das AELTESTE bekannte Paar ist die bessere Naeherung.
        for a, c in aeltestes.items():
            if a in out: out[a]["_aeltestes_paar_ms"] = c
        time.sleep(1.1)
    return out

# ---------- v4: Fruehkaeufer ----------
def _kaeufer_aus_tx(tx, mint):
    """(wallet, menge) wenn der Gebuehrenzahler dieser Transaktion den Token GEKAUFT hat (Kontostand gestiegen)."""
    meta = tx.get("meta") or {}
    if meta.get("err") is not None: return None
    keys = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    if not keys: return None
    payer = keys[0] if isinstance(keys[0], str) else (keys[0] or {}).get("pubkey")
    vor = sum(float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0) for b in meta.get("preTokenBalances") or []
              if b.get("mint") == mint and b.get("owner") == payer)
    nach = sum(float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0) for b in meta.get("postTokenBalances") or []
               if b.get("mint") == mint and b.get("owner") == payer)
    return (payer, round(nach - vor, 4)) if nach - vor > 0 else None


def erste_transaktionen(mint, credits):
    """Die aeltesten ~FRUEH_TX Transaktionen des Mints, aelteste zuerst. (liste, weg) oder (None, fehler)."""
    try:
        r = helius_rpc("getTransactionsForAddress", [mint, {"transactionDetails": "full", "sortOrder": "asc",
                       "limit": FRUEH_TX, "encoding": "json", "maxSupportedTransactionVersion": 0}])
        credits["n"] += 10
        data = (r or {}).get("data") if isinstance(r, dict) else None
        if data is not None: return data, "gtfa"
    except Exception as e:
        fehler = str(e)[:80]
    else:
        fehler = "leere antwort"
    # Ausweichweg: Signaturen bis zum Anfang zurueckblaettern (1 Credit je 1000), dann die aeltesten einzeln holen
    try:
        vor, aelteste = None, []
        for _ in range(FALLBACK_SEITEN):
            opt = {"limit": 1000, **({"before": vor} if vor else {})}
            sigs = helius_rpc("getSignaturesForAddress", [mint, opt]) or []; credits["n"] += 1
            if not sigs: break
            aelteste = sigs; vor = sigs[-1]["signature"]
            if len(sigs) < 1000: break
        else:
            return None, f"zu viele transaktionen ({fehler})"
        ziel = list(reversed(aelteste))[:min(FRUEH_TX, 60)]
        out = []
        for sg in ziel:
            tx = helius_rpc("getTransaction", [sg["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0}])
            credits["n"] += 1
            if tx: out.append(tx)
        return out, "fallback"
    except Exception as e:
        return None, f"fehler {fehler} / {str(e)[:60]}"


def fruehkaeufer(mint, credits):
    """{t0, slot0, via, n_tx, kaeufer: [[wallet, sek_nach_start, slots_nach_start, menge], ...]} oder None."""
    txs, via = erste_transaktionen(mint, credits)
    if not txs: return {"fehler": via}
    txs = [t for t in txs if t.get("slot") is not None]
    if not txs: return {"fehler": "keine slots"}
    t0 = min((t.get("blockTime") or 0) for t in txs) or None
    slot0 = min(t["slot"] for t in txs)
    gesehen, kaeufer = set(), []
    for t in sorted(txs, key=lambda x: (x["slot"], x.get("transactionIndex") or 0)):
        k = _kaeufer_aus_tx(t, mint)
        if not k or k[0] in gesehen: continue
        gesehen.add(k[0])
        kaeufer.append([k[0], int((t.get("blockTime") or t0 or 0) - (t0 or 0)), int(t["slot"] - slot0), k[1]])
        if len(kaeufer) >= FRUEH_MAX_KAEUFER: break
    return {"t0": t0, "slot0": slot0, "via": via, "n_tx": len(txs), "kaeufer": kaeufer}


def ergebnis(e, now):
    """'gewinner' | 'verlierer' | None (noch offen). Gewinner = mind. GEWINNER_X ueber dem ersten Kurs in 7 Tagen."""
    if not e.get("px0"): return None
    if e.get("peak", 0) / e["px0"] >= GEWINNER_X: return "gewinner"
    if now - e.get("seit", now) >= PATH_HOURS * 3600: return "verlierer"
    return None


def rangliste(early, now):
    """Top-Wallets: Coins, in denen die Wallet frueh (kein Sniper) kaufte, und wie viele davon Gewinner wurden."""
    w = {}
    fertig = gew = 0
    for mint, e in early.items():
        erg = ergebnis(e, now)
        if not erg or not e.get("kaeufer"): continue
        fertig += 1; gew += erg == "gewinner"
        x = e.get("peak", 0) / e["px0"]
        for wallet, dt, dslot, _ in e["kaeufer"]:
            if dslot <= SNIPER_SLOTS: continue
            r = w.setdefault(wallet, {"coins": 0, "gewinner": 0, "x": []})
            r["coins"] += 1; r["gewinner"] += erg == "gewinner"; r["x"].append(x)
    basis = gew / fertig if fertig else None
    kand = [(r["gewinner"], r["gewinner"] / r["coins"], r["coins"], sum(r["x"]) / len(r["x"]))
            for r in w.values() if r["coins"] >= MIN_COINS_WALLET]
    kand.sort(key=lambda k: (-k[0], -k[1], -k[3]))
    top = [{"name": f"Wallet {chr(65 + i)}", "coins": c, "gewinner": g, "verlierer": c - g,
            "quote": round(q, 3), "avg_peak_x": round(x, 2)} for i, (g, q, c, x) in enumerate(kand[:3])]
    return {"t": now_iso(), "coins_ausgewertet": fertig, "coins_gewinner": gew,
            "basisquote": round(basis, 3) if basis is not None else None,
            "wallets_ab_3_coins": len(kand), "wallets_gesamt": len(w), "top": top,
            "hinweis": "Beobachtung, keine Strategie: noch nicht zeitlich getrennt geprueft."}


def main():
    pf = Paper("bot_d", rebuy_sperre_h=24); st = pf.s
    st["strategy"] = "datensammler"; st["helius"] = bool(HELIUS); st["codex"] = bool(CODEX_KEY)
    now = time.time(); today = int(now // 86400)
    cooldown = load("bot_d_cooldown.json", {})
    paths = load("bot_d_paths.json", {}); early = load("bot_d_early.json", {})
    meta = load("bot_d_meta.json", {"cands": [], "last_discover": 0})
    tag = time.strftime("%Y-%m-%d", time.gmtime(now))
    if meta.get("credits_tag") != tag: meta["credits_tag"], meta["credits_heute"] = tag, 0
    credits = {"n": 0}

    # 1) Kandidaten (alle 30 Min neu)
    if now - meta.get("last_discover", 0) >= DISCOVER_EVERY_S:
        fresh = codex_candidates()
        if fresh is None:
            alter_h = (now - meta.get("last_discover", now)) / 3600
            print(f"Bot D: Codex-Abfrage fehlgeschlagen. Kandidatenliste ist {alter_h:.1f} h alt")
        else:
            meta["last_discover"] = now
            if fresh: meta["cands"] = fresh
            else: print("Bot D: Codex meldet 0 Kandidaten im Fenster")
    cands = {c["addr"]: c for c in meta.get("cands", [])}
    for a, c in cands.items():
        paths.setdefault(a, {"sym": c["sym"], "first": now, "pts": [],
                             "q": {k: c[k] for k in ("liq", "mcap", "holders", "top10", "bundler", "sniper", "insider")}})

    # 2) Kurse + Pfade (bis 72 h alle 5 Min, danach stuendlich; insgesamt 7 Tage)
    def faellig(p):
        alter = now - p["first"]
        if alter > PATH_HOURS * 3600: return False
        if alter <= DICHT_H * 3600 or not p["pts"]: return True
        return (alter / 60 - p["pts"][-1][0]) >= 55
    watch = [a for a, p in paths.items() if faellig(p)]
    pairs = batch_pairs(list(set(watch) | set(st["positions"].keys())))
    prices = {}
    for a, p in pairs.items():
        px = float(p.get("priceUsd") or 0); liq = (p.get("liquidity") or {}).get("usd") or 0
        if px > 0: prices[a] = px
        if a in watch:
            paths[a]["pts"].append([round((now - paths[a]["first"]) / 60, 1), px, liq])
        e = early.get(a)
        if e is not None and px > 0 and now - e.get("seit", now) <= PATH_HOURS * 3600:
            e.setdefault("px0", px); e["peak"] = max(e.get("peak", px), px); e["last"] = px

    # 3) offene Positionen aus v3 noch abwickeln (keine neuen Kaeufe)
    for a, pos in list(st["positions"].items()):
        px = prices.get(a)
        if not px:
            pos.setdefault("no_px_since", now_iso())
            if (now - parse_iso(pos["no_px_since"])) / 3600 >= ORPHAN_HOURS:
                qty = pos["qty"]; entry = pos["entry"]
                st["trades"].append({"t": now_iso(), "side": "sell", "sym": pos["sym"], "addr": a,
                                     "price": 0.0, "usd": 0.0, "pnl": round(-qty * entry, 2), "peak_x":
                                     round(pos.get("peak", entry) / entry, 3), "held_h": round(held_seconds(pos) / 3600, 1),
                                     "frac": 1.0, "quote": "none", "reason": "abgeschrieben"})
                del st["positions"][a]
            continue
        pos.pop("no_px_since", None)
        liq = (pairs[a].get("liquidity") or {}).get("usd") or 0
        pos["peak"] = max(pos.get("peak", px), px)
        x = px / pos["entry"]; held_d = held_seconds(pos) / 86400
        liq0 = pos.get("liq0") or liq; pos.setdefault("liq0", liq0)
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
        if why: pf.sell(a, px, 1.0, liq, why)

    # 4) Fruehkaeufer: jeder Kandidat genau einmal (Gewinner UND Verlierer)
    neu, wege, fehler = 0, {}, 0
    if HELIUS:
        offen = [a for a in cands if a not in early][:FRUEH_PRO_LAUF]
        for a in offen:
            r = fruehkaeufer(a, credits)
            if r.get("fehler"):
                fehler += 1
                early[a] = {"sym": cands[a]["sym"], "seit": paths.get(a, {}).get("first", now), "fehler": r["fehler"]}
                print(f"  {cands[a]['sym']}: Fruehkaeufer nicht ermittelbar ({r['fehler']})")
                continue
            early[a] = {"sym": cands[a]["sym"], "seit": paths.get(a, {}).get("first", now), **r}
            if a in prices: early[a].setdefault("px0", prices[a]); early[a]["peak"] = prices[a]
            neu += 1; wege[r["via"]] = wege.get(r["via"], 0) + 1
            time.sleep(0.3)
    meta["credits_heute"] = meta.get("credits_heute", 0) + credits["n"]

    # 5) Rangliste fuers Dashboard
    top = rangliste(early, now)
    save("bot_d_top_wallets.json", top)

    # 6) Pfade aufraeumen (nach 7 Tagen + 1 Tag ins Archiv)
    for a in list(paths):
        if now - paths[a]["first"] > PATH_HOURS * 3600 + 86400:
            append_jsonl("bot_d_paths_archive.jsonl", {"addr": a, **paths[a]}); del paths[a]

    save("bot_d_paths.json", paths); save("bot_d_meta.json", meta); save("bot_d_early.json", early)
    save("bot_d_cooldown.json", {k: v for k, v in cooldown.items() if v > today})
    st["coverage"] = {"t": now_iso(), "kandidaten": len(cands), "pfade": len(paths),
                      "kurse": sum(1 for a in watch if a in pairs), "von": len(watch),
                      "fruehkaeufer_neu": neu, "fruehkaeufer_fehler": fehler, "weg": wege,
                      "coins_mit_fruehkaeufern": sum(1 for e in early.values() if e.get("kaeufer")),
                      "coins_ausgewertet": top["coins_ausgewertet"], "credits_heute": meta["credits_heute"]}
    v = pf.mark(prices); pf.commit()
    print(f"Bot D [datensammler]: kandidaten {len(cands)} | pfade {len(paths)} | kurse {st['coverage']['kurse']}/{len(watch)}"
          f" | fruehkaeufer neu {neu} {wege} fehler {fehler} | coins mit fruehkaeufern {st['coverage']['coins_mit_fruehkaeufern']}"
          f" | ausgewertet {top['coins_ausgewertet']} | credits heute ~{meta['credits_heute']} | offene alt-positionen {len(st['positions'])}")

if __name__ == "__main__":
    main()
