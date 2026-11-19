"""NOTFALL: verkauft ALLE Tokens der Bot-Wallet in USDC (SOL fuer Gebuehren bleibt) und stoppt den Bot.

    python alles_verkaufen.py
Legt zuerst die Datei STOP an (der Bot kauft danach nichts mehr), fragt dann nach Bestaetigung.
Zum spaeteren Weitermachen: Datei STOP loeschen.
"""
import os, sys, json, time
HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
from einrichten import env_lesen, ROOT
e = env_lesen()
for k, v in e.items(): os.environ.setdefault(k, v)
from chain import Chain, lade_wallet, USDC, SOL_MINT
import telegram as tg

open(os.path.join(HIER, "STOP"), "w").write("angelegt von alles_verkaufen.py\n")
print("STOP-Datei angelegt - der Bot handelt nicht mehr.")
ch = Chain(lade_wallet(e.get("LIVE_KEY_FILE") or os.path.join(ROOT, ".solbot_live_key")), e["HELIUS_KEY"], e["JUP_KEY"])
toks = {m: t for m, t in ch.token_konten().items() if t["raw"] > 0 and m not in (USDC, SOL_MINT)}
if not toks: sys.exit("Keine Tokens ausser USDC in der Wallet. Fertig.")
for m, t in toks.items():
    q = ch.quote(m, USDC, t["raw"], 500)
    print(f"{m}: {t['raw'] / 10 ** t['dec']:.6g} Stueck -> ca. {int(q['outAmount']) / 1e6:.2f} USDC" if q else f"{m}: keine Route!")
if input("\nAlles verkaufen? Tippe JA: ").strip() != "JA": sys.exit("Abgebrochen (STOP-Datei bleibt).")
for m, t in toks.items():
    q = ch.quote(m, USDC, t["raw"], 500)
    if not q: print(f"{m}: keine Route - bitte in Phantom verkaufen."); continue
    try:
        tx, lv = ch.swap_tx(q); sig = ch.senden(tx); res = ch.warten(sig, lv, 60)
        print(f"{m}: {res} | {sig}")
    except Exception as ex:
        print(f"{m}: FEHLER {ex}")
st_pfad = os.path.join(os.path.dirname(os.path.dirname(HIER)), "live_state", "live_state.json")
if os.path.exists(st_pfad):
    st = json.load(open(st_pfad)); st["positions"] = {}; st["pending"] = {}
    json.dump(st, open(st_pfad, "w"), indent=1)
usdc = (ch.token_konten().get(USDC) or {}).get("raw", 0) / 1e6
tg.senden(f"🛑 NOTFALL-VERKAUF ausgefuehrt. USDC jetzt: {usdc:.2f} $. Bot gestoppt (STOP-Datei).")
print(f"Fertig. USDC: {usdc:.2f}")
