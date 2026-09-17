"""Auswertung des Bot-B-Shadow-Logs. Aufruf im Repo-Ordner:  python3 check.py"""
import json, statistics, collections, os

P = "data/bot_b_shadow.jsonl"
if not os.path.exists(P):
    raise SystemExit("Datei fehlt: " + os.path.abspath(P))

rows = [json.loads(l) for l in open(P) if l.strip()]
ev = [r for r in rows if r.get("grund") is not None]
print(f"Zeilen gesamt: {len(rows)} | mit Grund: {len(ev)} | bestanden: {sum(1 for r in ev if r.get('ok'))}")

print("\nAblehnungsgruende:")
for g, n in collections.Counter(r["grund"].split()[0] for r in ev if not r.get("ok")).most_common():
    print(f"  {g:<14} {n}")

vt = []
for r in ev:
    g = r["grund"]
    if g.startswith("vol-trend"):
        try: vt.append(float(g.split()[1]))
        except (IndexError, ValueError): pass
if vt:
    print(f"\nVolumen-Trend der abgelehnten Tokens (n={len(vt)}):")
    print(f"  median {statistics.median(vt):.2f} | min {min(vt):.2f} | max {max(vt):.2f}")
    for s in (1.0, 1.1, 1.2, 1.3, 2, 4, 6):
        print(f"  ueber {s}: {sum(1 for x in vt if x > s)}")

br = [r["buy_ratio"] for r in ev if r.get("buy_ratio")]
if br:
    print(f"\nKaeuferanteil: median {statistics.median(br):.3f} | >=0.56: {sum(1 for x in br if x >= 0.56)} von {len(br)}")

syms = collections.Counter(r["sym"] for r in ev if r.get("sym"))
print(f"\nBeobachtete Tokens: {len(syms)}")
