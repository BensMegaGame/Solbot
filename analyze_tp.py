"""Wie hoch standen Positionen, bevor sie per Stop/Trail/Zeit verkauft wurden?
Liest alle bot_*_state.json und wertet das Feld peak_x (Hoechststand / Einstieg) der Verkaeufe aus.
Damit laesst sich pruefen, ob ein fruehes Zwischenziel (TP0 bei 1.8x, Bot B TP1 bei 1.5x) etwas gebracht haette.
Aufruf: python analyze_tp.py"""
import json, os
from common import DATA

LEVELS = [1.2, 1.5, 1.8, 2.0, 3.0, 5.0]

def main():
    for bot in ("a", "b", "c", "d", "e"):
        p = os.path.join(DATA, f"bot_{bot}_state.json")
        if not os.path.exists(p): continue
        tr = json.load(open(p))["trades"]
        sells = [t for t in tr if t.get("side") == "sell" and t.get("peak_x") is not None]
        if not sells: print(f"Bot {bot.upper()}: noch keine Verkaeufe mit peak_x (Feld gibt es erst seit v2.1)\n"); continue
        final = [t for t in sells if t.get("frac", 1) >= 0.999]           # nur Komplett-Exits
        losers = [t for t in final if t["reason"] in ("stop", "time", "trail", "these: liq", "these: vol") and t["pnl"] < 0]
        print(f"Bot {bot.upper()}: {len(final)} Komplett-Exits, davon {len(losers)} mit Verlust")
        print(f"  {'Hoechststand >=':<16}" + "".join(f"{l:>7}x" for l in LEVELS))
        print(f"  {'alle Exits':<16}" + "".join(f"{sum(t['peak_x']>=l for t in final):>8}" for l in LEVELS))
        print(f"  {'Verlust-Exits':<16}" + "".join(f"{sum(t['peak_x']>=l for t in losers):>8}" for l in LEVELS))
        if losers:
            avg_peak = sum(t["peak_x"] for t in losers) / len(losers)
            print(f"  Verlust-Exits: mittlerer Hoechststand {avg_peak:.2f}x, mittlere Haltezeit {sum(t.get('held_h',0) for t in losers)/len(losers):.1f} h")
        tp0 = [t for t in tr if t.get("reason") == "tp0"]
        if tp0: print(f"  TP0 ausgeloest: {len(tp0)}x, realisiert {sum(t['pnl'] for t in tp0):+.2f} $")
        print()
    print("Lesart: Steht bei 'Verlust-Exits' unter 1.8x eine hohe Zahl, haetten diese Positionen vor dem Stop")
    print("        ein Viertel mit Gewinn realisieren koennen -> TP0 lohnt. Steht dort ~0, kostet TP0 nur Gebuehren.")
    print("Hinweis: peak_x wird nur alle 5 Min gemessen; kurze Spikes dazwischen fehlen (live waere es aehnlich).")

if __name__ == "__main__":
    main()
