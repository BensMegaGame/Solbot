"""Zeigt den Stand von Bot E LIVE (nur lesen).   Aufruf:  python status.py"""
import os, sys, json
HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
from einrichten import env_lesen, ROOT
e = env_lesen()
for k, v in e.items(): os.environ.setdefault(k, v)
from chain import Chain, lade_wallet, USDC

ch = Chain(lade_wallet(e.get("LIVE_KEY_FILE") or os.path.join(ROOT, ".solbot_live_key")), e["HELIUS_KEY"], e["JUP_KEY"])
st_pfad = os.path.join(os.path.dirname(os.path.dirname(HIER)), "live_state", "live_state.json")
st = json.load(open(st_pfad)) if os.path.exists(st_pfad) else {}
toks = ch.token_konten()
print(f"Wallet:   {ch.pub}")
print(f"Modus:    {e.get('LIVE_MODUS', 'trocken')} | max. je Kauf {e.get('LIVE_MAX_KAUF_USD', '20')} $ | STOP-Datei: "
      f"{'JA' if os.path.exists(os.path.join(HIER, 'STOP')) else 'nein'}")
print(f"SOL:      {ch.sol_lamports() / 1e9:.4f}")
print(f"USDC:     {(toks.get(USDC) or {}).get('raw', 0) / 1e6:.2f}")
print(f"Letzter Lauf: {st.get('letzter_lauf')}")
for m, p in (st.get("positions") or {}).items():
    print(f"Position: {p['sym']} | Einstand {p['entry']:.6g} $ | Kosten {p['cost']:.2f} $ | seit {p['opened']} | Teilverkauf {'ja' if p.get('tp1') else 'nein'}")
for m, p in (st.get("pending") or {}).items():
    print(f"OFFEN:    {p['art']} {p['sym']} | Signatur {p.get('sig')}")
andere = [m for m, t in toks.items() if t["raw"] > 0 and m != USDC and m not in (st.get("positions") or {})]
if andere: print("Andere Tokens in der Wallet (vom Bot nicht verwaltet):", ", ".join(andere))
