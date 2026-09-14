"""Auswertung der von Bot C gesammelten Post-Migration-Pfade.
Fuer jede Kombination aus Einstiegs-Minute und Haltezeit wird simuliert: Kauf zum Kurs bei Minute E,
Stop -30 %, sonst Verkauf nach H Minuten. Ausgabe: Median-/Mittelwert-Rendite, Trefferquote, Anzahl.
Aufruf:  python analyze_c.py            (alle Tokens)
         python analyze_c.py --clean    (nur Tokens, die Bot C's Qualitaetsfilter bestanden haetten)"""
import json, os, sys, statistics
from common import DATA, load

ENTRIES = [5, 10, 15, 20, 30, 45, 60, 90]
HOLDS   = [15, 30, 60, 120, 170]
STOP    = -0.30
CLEAN   = "--clean" in sys.argv

def all_paths():
    out = list(load("bot_c_paths.json", {}).values())
    arch = os.path.join(DATA, "bot_c_paths_archive.jsonl")
    if os.path.exists(arch):
        out += [json.loads(l) for l in open(arch) if l.strip()]
    return out

def clean_ok(q):
    if not q: return False
    lim = {"top10": 35, "bundler": 10, "sniper": 15, "insider": 10}
    return all(q.get(k) is None or q[k] <= v for k, v in lim.items())

def price_at(pts, minute):
    """Erster Punkt ab 'minute' (max. 6 Min spaeter), sonst None."""
    for m, px, liq in pts:
        if m >= minute and m <= minute + 6 and px > 0: return px
    return None

def simulate(pts, e, h):
    p0 = price_at(pts, e)
    if not p0 or pts[-1][0] < e + h: return None      # Pfad muss das Fenster komplett abdecken (sonst Bias)
    for m, px, liq in pts:
        if m <= e or px <= 0: continue
        if m > e + h: break
        if px / p0 - 1 <= STOP: return STOP
    p1 = price_at(pts, e + h)
    return p1 / p0 - 1 if p1 else None

def main():
    paths = [p for p in all_paths() if len(p.get("pts", [])) >= 3]
    if CLEAN: paths = [p for p in paths if clean_ok(p.get("q"))]
    print(f"Tokens: {len(paths)} ({'nur saubere' if CLEAN else 'alle'})\n")
    print(f"{'Entry':>6} {'Hold':>5} {'n':>4} {'Median':>8} {'Mittel':>8} {'>0':>5} {'>+50%':>6}")
    best = []
    for e in ENTRIES:
        for h in HOLDS:
            rs = [r for r in (simulate(p["pts"], e, h) for p in paths) if r is not None]
            if len(rs) < 5: continue
            med, mean = statistics.median(rs), statistics.mean(rs)
            win = sum(r > 0 for r in rs) / len(rs); big = sum(r >= 0.5 for r in rs) / len(rs)
            best.append((mean, e, h, len(rs)))
            print(f"{e:>6} {h:>5} {len(rs):>4} {med:>+8.1%} {mean:>+8.1%} {win:>5.0%} {big:>6.0%}")
    if best:
        best.sort(reverse=True)
        print("\nBeste Kombinationen (nach Mittelwert, inkl. Stop -30 %):")
        for mean, e, h, n in best[:5]: print(f"  Entry {e} min, Hold {h} min: {mean:+.1%} (n={n})")
        print("\nHinweis: erst ab ~100 Tokens aussagekraeftig. Mittelwert zaehlt (Lotterie-Verteilung), Median zeigt den Normalfall.")

if __name__ == "__main__":
    main()
