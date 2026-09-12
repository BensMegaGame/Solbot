"""Bot D – konzentrierte Micro-Cap-Wetten mit Holder-Qualitaet, Bot-Cluster-Filter und Longshot-Kategorie.
Kein Nachkaufen. Positionsgroesse in % des Cash sobald Portfolio > 500 $ (Deckel 120 $), sonst 60 $ fix.
Holder-/Timing-Pruefung braucht HELIUS_KEY (Free-Tier reicht); ohne Key werden diese Checks uebersprungen."""
import os, time, statistics
from common import *
from bot_a import candidates, metrics, passes, rug_ok

# ---- Positionsgroesse ----
BASE_USD, PCT_CASH, CAP_USD, START_EQ = 60.0, 0.15, 120.0, 500.0
MAX_POS = 5
# ---- Holder-Qualitaet / Timing ----
N_BUYERS, MIN_REAL_SHARE = 20, 0.40
WALLET_MIN_AGE_D, WALLET_MIN_TX, WALLET_MIN_TOKENS = 7, 20, 3
MIN_GAP_CV, MAX_SAME_AMOUNT = 0.30, 0.50
# ---- Exits (Standard) ----
TP1_X, TP1_FRAC, TP2_X, STOP, TRAIL, MAX_HOLD_D = 3.0, 0.5, 10.0, -0.5, -0.35, 10
COOLDOWN_D = 14
# ---- Longshot ----
LS_USD, LS_MAX_POS = 15.0, 3
LS_MIN_AGE_H, LS_MAX_AGE_H = 0.5, 6
LS_MIN_LIQ, LS_MIN_MCAP, LS_MAX_MCAP = 20_000, 30_000, 500_000
LS_TP1_X, LS_TP1_FRAC, LS_TP2_X, LS_STOP, LS_TRAIL, LS_MAX_HOLD_D = 5.0, 0.34, 20.0, -0.6, -0.40, 5

HELIUS = os.environ.get("HELIUS_KEY")
RC = "https://api.rugcheck.xyz/v1/tokens"

# ---------- Helius ----------
def helius_rpc(method, params):
    r = requests.post(f"https://mainnet.helius-rpc.com/?api-key={HELIUS}", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers=UA, timeout=20)
    r.raise_for_status(); return r.json().get("result")

def recent_buyers(mint):
    """Letzte Kaeufer (feePayer von SWAPs, bei denen der Token an sie ging) + Zeitpunkte + Betraege."""
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
        # 1000 Signaturen = Limit erreicht -> sehr aktive Wallet, zaehlt als real wenn alt genug
        if n >= 1000 and age_d >= WALLET_MIN_AGE_D: real = True
    except Exception as e:
        real = None
    cache[w] = real; return real

def holder_quality(mint, cache):
    """(ok, grund). ok=None wenn Pruefung nicht moeglich."""
    if not HELIUS: return None, "helius fehlt"
    try: buys = recent_buyers(mint)
    except Exception as e: return None, f"helius: {e}"
    if len(buys) < 8: return False, f"nur {len(buys)} kaeufer"
    # Timing-Varianz (Bot-Cluster kaufen in gleichmaessigen Abstaenden)
    ts = sorted(b["t"] for b in buys); gaps = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    if len(gaps) >= 5:
        cv = statistics.pstdev(gaps) / max(statistics.mean(gaps), 1)
        if cv < MIN_GAP_CV: return False, f"bot-timing cv {cv:.2f}"
    # Identische Betraege
    amts = [b["amt"] for b in buys]
    if amts and max(amts.count(a) for a in set(amts)) / len(amts) > MAX_SAME_AMOUNT: return False, "identische betraege"
    # Wallet-Qualitaet (dedupliziert)
    wallets = list(dict.fromkeys(b["wallet"] for b in buys))
    flags = [wallet_is_real(w, cache) for w in wallets]; time.sleep(0.2)
    known = [f for f in flags if f is not None]
    if len(known) < 5: return None, "wallets nicht pruefbar"
    share = sum(known) / len(known)
    return share >= MIN_REAL_SHARE, f"echte wallets {share:.0%} ({len(known)})"

def rug_strict(addr):
    s = get(f"{RC}/{addr}/report/summary")
    if not s: return False
    risks = [r.get("name", "").lower() for r in (s.get("risks") or [])]
    bad = ("mint", "freeze", "unlocked", "top 10", "single holder", "copycat", "low liquidity", "high holder")
    if any(k in r for r in risks for k in bad): return False
    sc = s.get("score_normalised")
    return sc is None or sc <= 30

def size_for(pf, prices):
    eq = pf.s["cash"] + sum(p["qty"] * prices.get(a, p["entry"]) for a, p in pf.s["positions"].items())
    if eq <= START_EQ: return BASE_USD
    return max(BASE_USD, min(CAP_USD, PCT_CASH * pf.s["cash"]))

def manage(pf, addr, pos, m, cooldown, today):
    px = m["price"]; pos["peak"] = max(pos.get("peak", px), px)
    x = px / pos["entry"]; held = (time.time() - time.mktime(time.strptime(pos["opened"], "%Y-%m-%dT%H:%M:%SZ"))) / 86400
    ls = pos.get("kind") == "longshot"
    tp1, f1, tp2, stop, trail, maxd = (LS_TP1_X, LS_TP1_FRAC, LS_TP2_X, LS_STOP, LS_TRAIL, LS_MAX_HOLD_D) if ls else (TP1_X, TP1_FRAC, TP2_X, STOP, TRAIL, MAX_HOLD_D)
    why = None
    if x <= 1 + stop: why = "stop"
    elif x >= tp2: why = "tp2"
    elif x >= tp1 and not pos.get("tp1"): pos["tp1"] = True; pf.sell(addr, px, f1, m["liq"], "tp1"); return
    elif pos.get("tp1") and px / pos["peak"] - 1 <= trail: why = "trail"
    elif held >= maxd: why = "time"
    if why:
        pf.sell(addr, px, 1.0, m["liq"], why)
        if px < pos["entry"]: cooldown[addr] = today + COOLDOWN_D

def main():
    pf = Paper("bot_d"); st = pf.s
    today = int(time.time() // 86400)
    cooldown = load("bot_d_cooldown.json", {}); wcache = load("bot_d_wallets.json", {})
    prices = {}; checks = []
    # 1) Positionen
    for addr, pos in list(st["positions"].items()):
        p = solana_pair(addr)
        if not p: continue
        m = metrics(p); prices[addr] = m["price"]
        manage(pf, addr, pos, m, cooldown, today); time.sleep(1.1)
    # 2) Kandidaten
    n_std = sum(1 for p in st["positions"].values() if p.get("kind") != "longshot")
    n_ls = sum(1 for p in st["positions"].values() if p.get("kind") == "longshot")
    for addr in candidates():
        if addr in st["positions"] or cooldown.get(addr, 0) > today: continue
        p = solana_pair(addr)
        if not p: continue
        m = metrics(p); sym = p["baseToken"]["symbol"]
        # --- Longshot: sehr frueh, sehr streng, klein ---
        if LS_MIN_AGE_H <= m["age_h"] <= LS_MAX_AGE_H and n_ls < LS_MAX_POS:
            if m["liq"] >= LS_MIN_LIQ and LS_MIN_MCAP <= m["mcap"] <= LS_MAX_MCAP and m["buy_ratio"] >= 0.55 and m["price"] > 0:
                if rug_strict(addr) and st["cash"] >= LS_USD + 2:
                    if pf.buy(sym, addr, m["price"], LS_USD, m["liq"], "longshot"):
                        st["positions"][addr]["kind"] = "longshot"; n_ls += 1; prices[addr] = m["price"]
                        checks.append((sym, "LONGSHOT gekauft"))
                    time.sleep(1.1); continue
        # --- Standard: Bot-A-Filter + Holder-Qualitaet ---
        if n_std >= MAX_POS or not passes(m): continue
        ok, risks = rug_ok(addr); time.sleep(1.1)
        if not ok: checks.append((sym, "rugcheck")); continue
        hq, why = holder_quality(addr, wcache)
        checks.append((sym, why))
        if hq is False: cooldown[addr] = today + 3; continue      # kurz sperren, nicht jede 5 Min neu pruefen
        usd = size_for(pf, prices)
        if st["cash"] >= usd + 2 and pf.buy(sym, addr, m["price"], usd, m["liq"], f"std {why}"):
            st["positions"][addr]["kind"] = "standard"; n_std += 1; prices[addr] = m["price"]
            append_jsonl("bot_d_signals.jsonl", {"t": now_iso(), "addr": addr, "sym": sym, "usd": usd, "why": why, **{k: round(v, 4) for k, v in m.items() if isinstance(v, (int, float))}})
        time.sleep(1.1)
    save("bot_d_cooldown.json", {k: v for k, v in cooldown.items() if v > today})
    save("bot_d_wallets.json", dict(list(wcache.items())[-3000:]))
    st["strategy"] = "concentrated"; st["helius"] = bool(HELIUS)
    v = pf.mark(prices); pf.commit()
    print(f"Bot D [konzentriert{'' if HELIUS else ', ohne Helius'}]: equity {v:.2f} | cash {st['cash']:.2f} | positions {len(st['positions'])} (longshot {n_ls}) | geprueft {len(checks)}")
    for sym, why in checks[:12]: print(f"   {sym:<10} {why}")

if __name__ == "__main__":
    main()
