"""Test fuer Bot D v5 (nur Lesen, kauft nichts). Prueft, ob die neuen Datenquellen so antworten, wie Bot D es erwartet.

Aufruf (keine Sonderzeichen noetig): im Solbot-Ordner
    cd data
    python3 testwallet.py
Das Skript findet den Solbot-Ordner selbst und laedt die Schluessel selbst aus der .env-Datei.
Kosten: ~6 Codex-Abfragen, ~30 Helius-Credits, ein paar kostenlose GeckoTerminal-/Jupiter-Abfragen.
Ergebnis auf dem Bildschirm und in data/wallet_test.json (landet beim naechsten Bot-Lauf auf GitHub).
"""
import json, os, sys, time, traceback

HIER = os.path.dirname(os.path.abspath(__file__))
SOLBOT = os.path.dirname(HIER) if os.path.basename(HIER) == "data" else HIER
os.chdir(SOLBOT); sys.path.insert(0, SOLBOT)
for env in (os.path.join(os.path.dirname(SOLBOT), ".env"), os.path.join(SOLBOT, ".env"), os.path.expanduser("~/.env")):
    if os.path.exists(env):
        for zeile in open(env):
            zeile = zeile.strip()
            if zeile.startswith("export "): zeile = zeile[7:]
            if "=" in zeile and not zeile.startswith("#"):
                k, v = zeile.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import requests
import bot_d as D
import walletpruef as wp
from common import load, get, jup_quote, USDC

erg = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "schluessel": {k: bool(os.environ.get(k)) for k in ("CODEX_KEY", "HELIUS_KEY", "JUP_KEY")}}
print("Schluessel gefunden:", erg["schluessel"])

def schritt(name, f):
    print(f"\n=== {name}")
    try:
        r = f(); erg[name] = r; print(json.dumps(r, indent=1)[:1500])
    except Exception as ex:
        erg[name] = {"FEHLER": str(ex)[:300]}; print("FEHLER:", ex); traceback.print_exc(limit=1)

early = load("bot_d_early.json", {})
coin = next((a for a, e in sorted(early.items(), key=lambda kv: -(kv[1].get("seit") or 0))
             if e.get("pool") and e.get("t0") and time.time() - e.get("seit", 0) > 86400), None)
e = early.get(coin) or {}
print("Test-Coin:", e.get("sym"), coin)

def codex_fenster():
    out = {}
    for name, filt in (("mit_filter", ", eventDisplayType: [Buy]"), ("ohne_filter", "")):
        q = ('{ getTokenEvents(query: {address: "%s", networkId: 1399811149, timestamp: {from: %d, to: %d}%s}, direction: ASC, limit: 100) '
             '{ items { timestamp maker eventDisplayType } } }' % (e["pool"], e["t0"] + 60, e["t0"] + 900, filt))
        try:
            items = (D._codex(q).get("getTokenEvents") or {}).get("items") or []
            ts = [x["timestamp"] - e["t0"] for x in items]
            out[name] = {"n": len(items), "kaeufe": sum(x.get("eventDisplayType") == "Buy" for x in items),
                         "sek_nach_start_von_bis": [min(ts), max(ts)] if ts else None}
        except Exception as ex:
            out[name] = {"FEHLER": str(ex)[:200]}
        time.sleep(1.2)
    q = ('{ getTokenEvents(query: {address: "%s", networkId: 1399811149, timestamp: {from: %d, to: %d}}, direction: ASC, limit: 100) '
         '{ items { timestamp maker eventDisplayType } } }' % (coin, e["t0"] + 900, e["t0"] + 7200))
    try:
        items = (D._codex(q).get("getTokenEvents") or {}).get("items") or []
        ts = [x["timestamp"] - e["t0"] for x in items]
        out["fenster2_token"] = {"n": len(items), "sek_nach_start_von_bis": [min(ts), max(ts)] if ts else None}
    except Exception as ex:
        out["fenster2_token"] = {"FEHLER": str(ex)[:200]}
    return out

def codex_bars():
    k = wp.codex_kerzen(D._codex, e["pool"], e["t0"], e["t0"] + 3600)
    return {"n": len(k), "erste": k[:2], "sek_nach_start_erste": (k[0][0] - e["t0"]) if k else None}

def gt():
    t = e["t0"] + 600
    pool = wp.gt_pool(coin, t); time.sleep(2.1)
    k = wp.gt_kerzen(pool, e["t0"], e["t0"] + 16 * 3600) if pool else []
    return {"pool": pool, "gleich_wie_startpool": pool == e.get("pool"), "kerzen": len(k),
            "min_nach_start_erste_letzte": [round((k[0][0] - e["t0"]) / 60), round((k[-1][0] - e["t0"]) / 60)] if k else None,
            "beispiel": k[:2]}

def helius():
    info, kand = D.stufe1(early, time.time())
    w = kand[0] if kand else None
    if not w: return {"hinweis": "noch kein Kandidat"}
    cr = {"n": 0}
    sigs, voll = wp.signaturen(os.environ["HELIUS_KEY"], w, cr)
    txs = wp.transaktionen(os.environ["HELIUS_KEY"], [s["signature"] for s in sigs[:25]], cr)
    sw = [wp.swap_aus_tx(t, w) for t in txs]
    arten = {}
    for s in sw: arten[(s or {}).get("art", "kein_token")] = arten.get((s or {}).get("art", "kein_token"), 0) + 1
    return {"signaturen_14_tage": len(sigs), "seitengrenze": voll, "tx_geholt": len(txs), "credits": cr["n"],
            "arten": arten, "beispiel": [s for s in sw if s and s.get("art") in ("kauf", "verkauf")][:3]}

def jupiter():
    meta = load("bot_d_meta.json", {})
    c = (meta.get("cands") or [{}])[0]
    out = {"d_kandidat": {"sym": c.get("sym"), "liq": c.get("liq"), "kosten": wp.kosten_messen(c["addr"]) if c.get("addr") else None}}
    neu = get("https://api.dexscreener.com/token-profiles/latest/v1") or []
    for t in [x for x in neu if x.get("chainId") == "solana"][:15]:
        pairs = get(f"https://api.dexscreener.com/token-pairs/v1/solana/{t['tokenAddress']}") or []
        if any((p.get("dexId") or "").lower() in ("pumpfun", "pump.fun", "pump") for p in pairs):
            q = jup_quote(USDC, t["tokenAddress"], 5_000_000)
            out["pump_kurve"] = {"token": t["tokenAddress"], "jupiter_quote": bool(q)}
            break
        time.sleep(0.5)
    return out

if coin and os.environ.get("CODEX_KEY"):
    schritt("codex_fenster", codex_fenster)
    schritt("codex_kerzen", codex_bars)
if coin: schritt("geckoterminal", gt)
if os.environ.get("HELIUS_KEY"): schritt("helius", helius)
if os.environ.get("JUP_KEY"): schritt("jupiter", jupiter)

json.dump(erg, open("data/wallet_test.json", "w"), indent=1)
print("\nFertig. Ergebnis in data/wallet_test.json - kommt mit dem naechsten Bot-Lauf auf GitHub, ich lese es dort.")
