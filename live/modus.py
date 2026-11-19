"""Modus von Bot E LIVE umstellen, ohne die .env von Hand zu bearbeiten.

    python modus.py              -> zeigt die aktuellen Einstellungen
    python modus.py trocken      -> nur simulieren, nichts senden
    python modus.py live         -> echte Trades
    python modus.py grenze 20    -> hoechstens 20 $ je Kauf (Mini-Test)
    python modus.py grenze 0     -> keine Grenze, 45 % des Kapitals je Kauf
Gilt ab dem naechsten Lauf (spaetestens 2 Minuten). Telegram meldet die Aenderung.
"""
import sys
from einrichten import env_lesen, env_setzen

a = sys.argv[1:]
if a and a[0] in ("trocken", "live"):
    if a[0] == "live" and input("Wirklich ECHTE Trades einschalten? Tippe JA: ").strip() != "JA": sys.exit("Abgebrochen.")
    env_setzen("LIVE_MODUS", a[0])
elif len(a) == 2 and a[0] == "grenze":
    v = float(a[1]); env_setzen("LIVE_MAX_KAUF_USD", str(int(v)) if v == int(v) else str(v))
elif a:
    sys.exit(__doc__)
e = env_lesen()
print(f"Modus: {e.get('LIVE_MODUS', 'trocken')} | max. je Kauf: {e.get('LIVE_MAX_KAUF_USD', '20')} $ (0 = keine Grenze)")
