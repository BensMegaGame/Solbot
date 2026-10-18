"""Bot D v5 – "Wallet-Suche fuer Copy-Trading": kauft NICHTS, sammelt und prueft Wallets in drei Stufen.

WARUM v5: v4 fragte "wer war bei unseren Coins frueh dabei, und wurden die Coins Gewinner?". Das reicht nicht:
  - die "Fruehkaeufer" waren fast nur Sniper-Bots (typisch 6 s nach Start, der 40. nach 15 s) - nicht kopierbar;
  - wir sehen nur ~40 Coins am Tag, eine Wallet handelt viel mehr - eine, die alles kauft, sieht gut aus;
  - die Gewinnerquote war verzerrt (Gewinner zaehlten sofort, Verlierer erst nach 7 Tagen: 54 % statt ~33 %).
Die Daten von v4.1 liegen unveraendert in data/archive/ (Kopie beim ersten Lauf von v5).

STUFE 1 - SAMMELN (je Coin einmal, Codex): wie bisher die ersten 100 Trades des pump.fun-Startpools, NEU dazu die
  Kaeufer aus Minute 1-15 und 15-120 nach Start. Kaeufe in den ersten 30 s = Sniper (nicht kopierbar). Wallets bei
  mehr als 5 % aller Coins = Bot. Ergebnis je Coin: hoechster Kurs in 72 h ab Entdeckung, Gewinner = mind. 2x,
  gewertet erst nach 72 h. Kandidaten: Wallets mit >= 3 Coins (ohne Sniper-Kaeufe) und mind. 1 Gewinner.
STUFE 2 - PRUEFEN (walletpruef.py, je Lauf hoechstens 1 neue Wallet): komplette Handelshistorie der letzten 14 Tage
  ueber Helius, dann Kopierer-Simulation mit 1 Minute Verzoegerung und mit Jupiter gemessenen Kosten. Bestanden nur
  mit >= 20 Trades, Schnitt > 0, ohne die 2 besten > 0 und in beiden Zeithaelften > 0.
STUFE 3 - COPY-LISTE (data/bot_d_copy_liste.json, hoechstens 3 Wallets): eine bestandene Wallet kommt sofort auf
  einen freien Platz; einmal je Woche wird die Liste komplett neu bestimmt (nicht staendig, sonst jagt man der
  Wallet mit der zufaellig besten Woche hinterher). Gehandelt wird hier NICHT - die Liste ist die Grundlage fuer
  einen spaeteren Paper-Copy-Dienst.

Offene Positionen aus v3 werden noch nach den alten Regeln verkauft.
"""
import os, time, statistics, shutil, json
from collections import Counter
from common import *
import walletpruef as wp

VERSION = "d5"

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
CODEX_MAX_TAG = 200                    # v5: hoechstens so viele Codex-Abfragen je Tag fuer Fruehkaeufer, spaete Kaeufer und
                                       # Kerzen (+ ~48 fuer die Kandidatensuche, + ~2 Bot E -> ~7.500 von 10.000 im Monat)
CODEX_EVENTS = 100                     # die ersten 100 Trades des Startpools
NEU_VERSUCH_D = 3                      # v4.1: Coins, bei denen Helius scheiterte, werden erneut versucht (solange < 3 Tage alt)
FRUEH_TX = 100                         # die ersten 100 Transaktionen ab Start
FRUEH_MAX_KAEUFER = 40                 # hoechstens 40 verschiedene Kaeufer je Coin speichern
SNIPER_S = 30                          # v5: Kauf in den ersten 30 Sekunden = Sniper/Bot/Insider (nicht kopierbar).
                                       # Gemessen 18.10.: typischer Fruehkaeufer kauft 6 s nach Start, der 40. nach 15 s.
GEWINNER_X = 2.0                       # Gewinner = mind. 2x ueber dem ersten gesehenen Kurs ...
FENSTER_H = 72                         # v5: ... innerhalb von 72 h. Gewertet wird ERST nach 72 h - Gewinner und Verlierer
                                       # gleich lang beobachtet (vorher zaehlten Gewinner sofort, Verlierer erst nach 7 Tagen
                                       # -> angezeigte Gewinnerquote 54-92 % statt sauber gerechneter ~33 %)
MIN_COINS_WALLET = 3                   # Wallet erst ab 3 Coins Kandidat
SPAET_FENSTER = [(60, 900), (900, 7200)]   # v5: Kaeufer aus Minute 1-15 und 15-120 nach Start (je 1 Codex-Abfrage)
SPAET_MAX = 40                         # hoechstens 40 neue Kaeufer je Fenster speichern
BOT_ANTEIL = 0.05                      # Wallet bei mehr als 5 % aller Coins dabei = Bot (erst ab 100 Coins angewandt)
KAND_MAX = 60                          # so viele Kandidaten fuer Stufe 2
NEU_PRUEFEN_D = 7                      # Wallet hoechstens alle 7 Tage neu pruefen
HELIUS_WALLET_MAX_TAG = 4000           # Helius-Credits je Tag fuer Stufe 2 (~13 Wallets, ~120k im Monat)
MAX_SIM_PARALLEL = 3                   # neue Wallet erst pruefen, wenn weniger als 3 Simulationen offen sind
KERZEN_PRO_LAUF = 12                   # GeckoTerminal-Abfragen je Lauf (2 je simuliertem Trade)
KOSTEN_EVERY_S = 30 * 60               # alle 30 Min eine Jupiter-Kostenmessung
ZEIT_NEUE_WALLET_S, ZEIT_ENDE_S = 100, 195  # Laufzeitschutz (run_paper.sh bricht nach 240 s ab)
COPY_MAX = 3
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



# ---------- v4.1: Fruehkaeufer ueber Codex aus dem STARTPOOL ----------
# Test am 10.10. (testcodex.py): Codex nimmt bei einer Token-Adresse den groessten Pool (bei pump.fun-Coins der Pool
# NACH dem Umzug) - erster Trade 13-55 min nach Start, 0 von 40 Wallets wie Helius. Fragt man dagegen den
# pump.fun-Pool (Bonding Curve) direkt ab: erster Trade 0 min nach Start, 30 von 40 Wallets wie Helius - und das
# auch bei Coins, an denen Helius scheiterte (zu viele Transaktionen). Kosten: 1 Codex-Abfrage je Coin, plus 1,
# falls DexScreener den Startpool nicht kennt. Nicht-pump.fun-Coins: weiter ueber Helius.
def _codex(query):
    r = requests.post(CODEX_URL, json={"query": query}, timeout=40,
                      headers={"Authorization": CODEX_KEY, "Content-Type": "application/json", **UA})
    d = r.json()
    if d.get("errors"): raise RuntimeError(json.dumps(d["errors"])[:160])
    return d.get("data") or {}

def startpool(mint, codex_n):
    """Adresse des pump.fun-Startpools (Bonding Curve) oder None. Erst DexScreener (kostenlos), dann Codex."""
    d = get(f"{DS}/token-pairs/v1/solana/{mint}")
    if isinstance(d, dict): d = d.get("pairs")
    for p in d or []:
        if (p.get("dexId") or "").lower() in ("pumpfun", "pump.fun", "pump") and p.get("pairAddress"):
            return p["pairAddress"]
    if not CODEX_KEY: return None
    codex_n["n"] += 1
    res = (_codex('{ listPairsWithMetadataForToken(tokenAddress: "%s", networkId: %d) { results { pair { address } exchange { name } } } }'
                  % (mint, SOLANA)).get("listPairsWithMetadataForToken") or {}).get("results") or []
    for x in res:
        if ((x.get("exchange") or {}).get("name") or "").lower() in ("pump", "pump.fun"):
            return (x.get("pair") or {}).get("address")
    return None

def fruehkaeufer_codex(mint, pool, codex_n):
    codex_n["n"] += 1
    items = ((_codex('{ getTokenEvents(query: {address: "%s", networkId: %d}, direction: ASC, limit: %d) '
                     '{ items { timestamp blockNumber maker eventDisplayType token0SwapValueUsd token1SwapValueUsd } } }'
                     % (pool, SOLANA, CODEX_EVENTS)).get("getTokenEvents") or {}).get("items") or [])
    if not items: return {"fehler": "codex: keine trades im startpool"}
    t0 = items[0].get("timestamp"); slot0 = items[0].get("blockNumber") or 0
    gesehen, kaeufer = set(), []
    for x in items:
        w = x.get("maker")
        if x.get("eventDisplayType") != "Buy" or not w or w in gesehen: continue
        gesehen.add(w)
        usd = max(float(x.get("token0SwapValueUsd") or 0), float(x.get("token1SwapValueUsd") or 0))
        kaeufer.append([w, int((x.get("timestamp") or t0) - t0), int((x.get("blockNumber") or slot0) - slot0), round(usd, 2)])
        if len(kaeufer) >= FRUEH_MAX_KAEUFER: break
    return {"t0": t0, "slot0": slot0, "via": "codex", "pool": pool, "n_tx": len(items), "kaeufer": kaeufer}

class BudgetLeer(Exception):
    pass

def codex_budget(meta, codex_n, n=1):
    if not CODEX_KEY or meta.get("codex_heute", 0) + codex_n["n"] + n > CODEX_MAX_TAG: raise BudgetLeer()


# ---------- v5: spaetere Kaeufer (Minute 1-15 und 15-120 nach Start) ----------
def spaete_kaeufer(e, mint, meta, codex_n):
    """[[wallet, sek_nach_start, usd], ...] aus den Codex-Ereignissen der SPAET_FENSTER. Fenster 1 im Startpool
    (Bonding Curve), Fenster 2 ueber die Token-Adresse (= groesster Pool, nach dem Umzug)."""
    t0 = int(e["t0"]); schon = {k[0] for k in e.get("kaeufer") or []}
    out, gesehen = [], set()
    for i, (a, b) in enumerate(SPAET_FENSTER):
        adr = e.get("pool") if i == 0 and e.get("pool") else mint
        for versuch in (0, 1):
            filt = "" if meta.get("codex_ohne_filter") else ", eventDisplayType: [Buy]"
            codex_budget(meta, codex_n); codex_n["n"] += 1
            try:
                items = (_codex('{ getTokenEvents(query: {address: "%s", networkId: %d, timestamp: {from: %d, to: %d}%s}, '
                                'direction: ASC, limit: 100) { items { timestamp maker eventDisplayType token0SwapValueUsd '
                                'token1SwapValueUsd } } }' % (adr, SOLANA, t0 + a, t0 + b, filt)).get("getTokenEvents") or {}).get("items") or []
                break
            except RuntimeError as ex:
                if versuch == 0 and filt and "eventDisplayType" in str(ex):
                    meta["codex_ohne_filter"] = True; continue    # Filter nicht erlaubt -> ohne Filter, Kaeufe selbst auslesen
                raise
        n = 0
        for x in items:
            w = x.get("maker")
            if x.get("eventDisplayType") != "Buy" or not w or w in schon or w in gesehen: continue
            gesehen.add(w); n += 1
            usd = max(float(x.get("token0SwapValueUsd") or 0), float(x.get("token1SwapValueUsd") or 0))
            out.append([w, int((x.get("timestamp") or t0) - t0), round(usd, 2)])
            if n >= SPAET_MAX: break
    return out


# ---------- v5: Ergebnis je Coin - erst nach 72 h, Gewinner und Verlierer gleich lang beobachtet ----------
def ergebnis(e, now):
    """'gewinner' | 'verlierer' | None (noch keine 72 h oder kein Kurs)."""
    if now - e.get("seit", now) < FENSTER_H * 3600: return None
    if not e.get("p0") or not e.get("p72"): return None
    return "gewinner" if e["p72"] / e["p0"] >= GEWINNER_X else "verlierer"


def versionswechsel(early, paths, meta):
    """Beim ersten Lauf von v5: v4.1-Daten nach data/archive/ KOPIEREN und p0/p72 aus den Kursverlaeufen nachrechnen."""
    if meta.get("version") == VERSION: return
    os.makedirs(os.path.join(DATA, "archive"), exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M", time.gmtime())
    for name in ("bot_d_early.json", "bot_d_top_wallets.json", "bot_d_meta.json", "bot_d_state.json"):
        p = os.path.join(DATA, name)
        if os.path.exists(p): shutil.copy(p, os.path.join(DATA, "archive", f"{name[:-5]}_v41_{stamp}.json"))
    pfade = dict(paths)
    p_arch = os.path.join(DATA, "bot_d_paths_archive.jsonl")
    if os.path.exists(p_arch):
        for zeile in open(p_arch):
            try: r = json.loads(zeile)
            except Exception: continue
            if r.get("addr") in early and r["addr"] not in pfade: pfade[r["addr"]] = r
    n = 0
    for a, e in early.items():
        p = pfade.get(a)
        if not p or not p.get("pts"): continue
        seit = e.get("seit") or p["first"]
        pts = [(p["first"] + x[0] * 60, x[1]) for x in p["pts"] if x[1] and x[1] > 0]
        pts = [x for x in pts if seit - 600 <= x[0] <= seit + FENSTER_H * 3600]
        if pts: e["p0"], e["p72"] = pts[0][1], max(x[1] for x in pts); n += 1
    meta["version"] = VERSION
    print(f"Bot D: Wechsel auf {VERSION} - v4.1-Daten nach data/archive/ kopiert, 72h-Ergebnis fuer {n} Coins nachgerechnet")


# ---------- Stufe 1: Kandidaten ----------
def stufe1(early, now):
    mit = [(a, e) for a, e in early.items() if e.get("kaeufer") or e.get("spaet")]
    N = len(mit)
    auftritt, w = Counter(), {}
    fertig = gew = 0
    for a, e in mit:
        erg = ergebnis(e, now)
        if erg: fertig += 1; gew += erg == "gewinner"
        alle = {k[0] for k in e.get("kaeufer") or []} | {k[0] for k in e.get("spaet") or []}
        auftritt.update(alle)
        brauchbar = {k[0] for k in e.get("kaeufer") or [] if k[1] >= SNIPER_S} | {k[0] for k in e.get("spaet") or [] if k[1] >= SNIPER_S}
        for x in brauchbar:
            r = w.setdefault(x, [0, 0, 0, []])          # coins, ausgewertet, gewinner, hoechststaende
            r[0] += 1
            if erg: r[1] += 1; r[2] += erg == "gewinner"; r[3].append(e["p72"] / e["p0"])
    grenze = BOT_ANTEIL * N if N >= 100 else float("inf")
    bots = {x for x, c in auftritt.items() if c > grenze}
    kand = [(r[2], r[1], r[0], x) for x, r in w.items() if x not in bots and r[0] >= MIN_COINS_WALLET and r[2] >= 1]
    kand.sort(key=lambda k: (-k[0], -(k[0] / k[1] if k[1] else 0), -k[2]))
    top = [{"name": f"Wallet {chr(65 + i)}", "coins": c, "ausgewertet": au, "gewinner": g, "verlierer": au - g,
            "quote": round(g / au, 3) if au else None,
            "avg_peak_x": round(sum(w[x][3]) / len(w[x][3]), 2) if w[x][3] else None}
           for i, (g, au, c, x) in enumerate(kand[:3])]
    info = {"coins_mit_daten": N, "coins_ausgewertet": fertig, "coins_gewinner": gew,
            "basisquote": round(gew / fertig, 3) if fertig else None, "wallets_gesamt": len(auftritt),
            "bots": len(bots), "kandidaten": len(kand), "top": top}
    return info, [k[3] for k in kand[:KAND_MAX]]


# ---------- Stufe 2: Wallets pruefen ----------
def stufe2(kand, checks, meta, t_start, codex_n):
    now = time.time(); tag = time.strftime("%Y-%m-%d", time.gmtime(now))
    if meta.get("wallet_tag") != tag: meta["wallet_tag"], meta["helius_wallet_heute"] = tag, 0
    kosten, kosten_art = wp.kosten_aktuell(meta.get("kosten_mess", []))
    log = []
    # a) offene Kopierer-Simulationen weiterfuehren
    abfragen = 0
    for w, rec in checks.items():
        if rec.get("status") != "sim": continue
        while rec.get("offen_sim") and abfragen < KERZEN_PRO_LAUF and time.time() - t_start < ZEIT_ENDE_S:
            tr = rec["offen_sim"].pop(0); k = []
            try:
                pool = wp.gt_pool(tr["mint"], tr["t_kauf"]); abfragen += 1; time.sleep(2.1)
                bis = tr["t_kauf"] + wp.DELAY_S + wp.SIM_MAX_H * 3600 + 120
                if pool: k = wp.gt_kerzen(pool, tr["t_kauf"], bis); abfragen += 1; time.sleep(2.1)
                if pool and not k:
                    try:
                        codex_budget(meta, codex_n); codex_n["n"] += 1
                        k = wp.codex_kerzen(_codex, pool, tr["t_kauf"] - 120, bis)
                    except BudgetLeer: pass
            except Exception as ex:
                rec["kerzen_fehler"] = str(ex)[:80]
            r = wp.kopie_simulieren(tr, k, kosten) if k else None
            if r is None: rec["ohne_kurs"] = rec.get("ohne_kurs", 0) + 1
            else: rec.setdefault("sim", []).append([tr["t_kauf"], round(r, 4)])
        if not rec.get("offen_sim"):
            ok, kz = wp.bewerten(rec.get("sim", []))
            rec.update(kz); rec["status"] = "bestanden" if ok else "durchgefallen"
            rec["kosten"], rec["kosten_art"] = kosten, kosten_art; rec.pop("offen_sim", None)
            log.append(f"Wallet {w[:4]}..: {rec['status']} ({kz.get('n')} Trades, Schnitt {kz.get('schnitt')})")
        if abfragen >= KERZEN_PRO_LAUF or time.time() - t_start >= ZEIT_ENDE_S: break
    # b) hoechstens eine neue Wallet je Lauf
    offen = sum(1 for r in checks.values() if r.get("status") == "sim")
    if (HELIUS and offen < MAX_SIM_PARALLEL and meta["helius_wallet_heute"] + 350 <= HELIUS_WALLET_MAX_TAG
            and time.time() - t_start < ZEIT_NEUE_WALLET_S):
        neu = next((x for x in kand if now - (checks.get(x) or {}).get("t", 0) > NEU_PRUEFEN_D * 86400), None)
        if neu:
            credits = {"n": 0}
            try: rec = wp.historie_pruefen(HELIUS, neu, credits, deadline=t_start + ZEIT_ENDE_S)
            except Exception as ex:
                fv = (checks.get(neu) or {}).get("fehlversuche", 0) + 1     # bis zu 2x im naechsten Lauf erneut
                rec = {"t": now if fv >= 3 else 0, "status": "fehler", "grund": str(ex)[:100], "fehlversuche": fv}
            meta["helius_wallet_heute"] += credits["n"]; meta["credits_heute"] = meta.get("credits_heute", 0) + credits["n"]
            checks[neu] = rec
            log.append(f"Wallet {neu[:4]}.. geprueft: {rec['status']} {rec.get('grund', '')} (~{credits['n']} Credits)")
    return log


# ---------- Stufe 3: Copy-Liste ----------
def stufe3(checks, now):
    alt = load("bot_d_copy_liste.json", {"wallets": []})
    woche = time.strftime("%G-W%V", time.gmtime(now))
    ok = sorted(((r.get("ohne_top2", 0), w) for w, r in checks.items()
                 if r.get("status") == "bestanden" and now - r.get("t", 0) < 14 * 86400), reverse=True)
    felder = ("n", "schnitt", "ohne_top2", "haelfte1", "haelfte2", "trefferquote", "halte_min", "tx_tag", "kosten", "kosten_art")
    if alt.get("woche") != woche:                       # woechentlich komplett neu
        liste = [w for _, w in ok[:COPY_MAX]]
    else:                                               # unter der Woche nur freie Plaetze auffuellen
        liste = [x["wallet"] for x in alt.get("wallets", []) if (checks.get(x["wallet"]) or {}).get("status") == "bestanden"]
        liste += [w for _, w in ok if w not in liste][:COPY_MAX - len(liste)]
    seit = {x["wallet"]: x.get("seit") for x in alt.get("wallets", [])}
    neu = {"woche": woche, "t": now_iso(),
           "wallets": [{"name": f"Wallet {chr(65 + i)}", "wallet": w, "seit": seit.get(w) or now_iso(),
                        **{k: checks[w].get(k) for k in felder}} for i, w in enumerate(liste)]}
    save("bot_d_copy_liste.json", neu)
    return neu


def main():
    t_start = time.time()
    pf = Paper("bot_d", rebuy_sperre_h=24); st = pf.s; st["version"] = VERSION
    st["strategy"] = "datensammler"; st["helius"] = bool(HELIUS); st["codex"] = bool(CODEX_KEY)
    now = time.time(); today = int(now // 86400)
    cooldown = load("bot_d_cooldown.json", {})
    paths = load("bot_d_paths.json", {}); early = load("bot_d_early.json", {})
    meta = load("bot_d_meta.json", {"cands": [], "last_discover": 0})
    versionswechsel(early, paths, meta)
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
        if e is not None and px > 0 and now - e.get("seit", now) <= FENSTER_H * 3600:
            e.setdefault("p0", px); e["p72"] = max(e.get("p72", px), px)       # v5: Hoechststand nur in 72 h

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
    if meta.get("codex_tag") != tag: meta["codex_tag"], meta["codex_heute"] = tag, 0
    codex_n = {"n": 0}
    # neue Kandidaten zuerst, danach Coins, bei denen es frueher scheiterte (noch keine 3 Tage alt)
    nochmal = [a for a, e in early.items() if not e.get("kaeufer") and not e.get("codex_versucht")
               and now - e.get("seit", now) < NEU_VERSUCH_D * 86400]
    offen = ([a for a in cands if a not in early] + nochmal)[:FRUEH_PRO_LAUF]
    spaet_neu, spaet_fehler = 0, None
    for a in offen:
        sym = (early.get(a) or {}).get("sym") or (cands.get(a) or {}).get("sym") or "?"
        alt = early.get(a) or {"sym": sym, "seit": paths.get(a, {}).get("first", now)}
        r, pool = None, None
        if CODEX_KEY and meta["codex_heute"] + codex_n["n"] + 2 <= CODEX_MAX_TAG:
            try:
                pool = startpool(a, codex_n)
                if pool: r = fruehkaeufer_codex(a, pool, codex_n)
            except Exception as ex:
                r = {"fehler": f"codex: {str(ex)[:80]}"}
            alt["codex_versucht"] = True
        if (r is None or r.get("fehler")) and HELIUS and not pool and not alt.get("fehler"):
            r = fruehkaeufer(a, credits)               # kein pump.fun-Startpool -> Helius wie bisher
        if r is None: continue                         # Codex-Tagesbudget erschoepft -> spaeter
        if r.get("fehler"):
            fehler += 1; alt["fehler"] = r["fehler"]; early[a] = alt
            print(f"  {sym}: Fruehkaeufer nicht ermittelbar ({r['fehler']})")
            continue
        alt.pop("fehler", None); alt.update(r); early[a] = alt
        if a in prices: alt.setdefault("px0", prices[a]); alt.setdefault("peak", prices[a])
        neu += 1; wege[r["via"]] = wege.get(r["via"], 0) + 1
        time.sleep(0.3)

    # 4b) v5: spaetere Kaeufer (Minute 1-15, 15-120) - neue Coins zuerst, dann die alten nachholen (neueste zuerst)
    if meta.get("spaet_tag") != tag: meta["spaet_tag"], meta["spaet_fehler_heute"] = tag, 0
    nachholen = sorted([a for a, e in early.items() if e.get("t0") and not e.get("spaet_versucht")],
                       key=lambda a: -(early[a].get("seit") or 0))
    for a in nachholen[:FRUEH_PRO_LAUF]:
        if meta["spaet_fehler_heute"] >= 5 or time.time() - t_start > 100: break
        e = early[a]
        try:
            e["spaet"] = spaete_kaeufer(e, a, meta, codex_n); e["spaet_versucht"] = True; spaet_neu += 1
        except BudgetLeer:
            break
        except Exception as ex:
            meta["spaet_fehler_heute"] += 1; spaet_fehler = str(ex)[:160]; meta["spaet_letzter_fehler"] = spaet_fehler
            e["spaet_fehlversuche"] = e.get("spaet_fehlversuche", 0) + 1
            if e["spaet_fehlversuche"] >= 2: e["spaet_versucht"] = True
        time.sleep(0.3)

    # 4c) v5: Kosten eines 50-$-Kaufs + sofortigen Verkaufs mit Jupiter messen (fuer die Kopierer-Simulation)
    if now - meta.get("kosten_t", 0) >= KOSTEN_EVERY_S and cands:
        meta["kosten_t"] = now
        c = sorted(cands.values(), key=lambda c: -c.get("vol", 0))[int(now // KOSTEN_EVERY_S) % min(len(cands), 20)]
        k = wp.kosten_messen(c["addr"])
        if k is not None:
            meta.setdefault("kosten_mess", []).append({"t": int(now), "sym": c["sym"], "liq": round(c.get("liq", 0)), "k": k})
            meta["kosten_mess"] = meta["kosten_mess"][-300:]
    meta["credits_heute"] = meta.get("credits_heute", 0) + credits["n"]
    meta["codex_heute"] = meta.get("codex_heute", 0) + codex_n["n"]; codex_n["n"] = 0

    # 5) Stufe 1-3
    info, kand = stufe1(early, now)
    checks = load("bot_d_wallet_check.json", {})
    w_log = stufe2(kand, checks, meta, t_start, codex_n)
    meta["codex_heute"] = meta.get("codex_heute", 0) + codex_n["n"]; codex_n["n"] = 0
    save("bot_d_wallet_check.json", checks)
    copy = stufe3(checks, now)
    stat = Counter(r.get("status") for r in checks.values())
    kosten, kosten_art = wp.kosten_aktuell(meta.get("kosten_mess", []))
    top = {"t": now_iso(), "version": VERSION, **info,
           "stufe2": {"geprueft": len(checks), **dict(stat)},
           "copy_liste": [{k: v for k, v in x.items() if k != "wallet"} for x in copy["wallets"]],
           "copy_woche": copy["woche"],
           "kosten": {"wert": kosten, "art": kosten_art, "messungen": len(meta.get("kosten_mess", []))},
           "hinweis": "Stufe 1 ist Beobachtung. Erst Stufe 2 (Kopierer-Simulation, Zeitsplit) entscheidet."}
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
                      "coins_ausgewertet": top["coins_ausgewertet"], "credits_heute": meta["credits_heute"],
                      "codex_heute": meta["codex_heute"], "spaet_neu": spaet_neu,
                      "coins_mit_spaeten": sum(1 for e in early.values() if e.get("spaet")),
                      "spaet_fehler": spaet_fehler or (meta.get("spaet_letzter_fehler") if meta.get("spaet_fehler_heute") else None),
                      "codex_ohne_filter": bool(meta.get("codex_ohne_filter")),
                      "kandidaten_wallets": info["kandidaten"], "wallets_geprueft": len(checks),
                      "wallets_bestanden": stat.get("bestanden", 0), "copy_liste": len(copy["wallets"]),
                      "helius_wallet_heute": meta.get("helius_wallet_heute", 0)}
    v = pf.mark(prices); pf.commit()
    print(f"Bot D [datensammler]: kandidaten {len(cands)} | pfade {len(paths)} | kurse {st['coverage']['kurse']}/{len(watch)}"
          f" | fruehkaeufer neu {neu} {wege} fehler {fehler} | coins mit fruehkaeufern {st['coverage']['coins_mit_fruehkaeufern']}"
          f" | ausgewertet {top['coins_ausgewertet']} (basis {top['basisquote']}) | spaete kaeufer neu {spaet_neu}"
          f" | kandidaten {info['kandidaten']} | geprueft {len(checks)} {dict(stat)} | copy-liste {len(copy['wallets'])}"
          f" | credits heute ~{meta['credits_heute']} | codex heute {meta['codex_heute']} | {time.time() - t_start:.0f} s")
    for z in w_log: print("  ", z)

if __name__ == "__main__":
    main()
