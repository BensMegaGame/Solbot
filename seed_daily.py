"""Befuellt den Tageskerzen-Cache von Bot C und Bot F direkt aus den schon vorhandenen
bt_top.py-Backtestdaten (data/top_hist.json), statt tagelang auf GeckoTerminal zu warten
(dort kommen nur GT_PER_RUN=10 Coins pro 5-Min-Lauf nach).

Aufruf im Repo-Ordner (einmalig, dauert < 1 Sekunde, kein Netzwerk noetig):
    python3 seed_daily.py

Schreibt data/bot_c_daily.json und data/bot_f_daily.json. Bestehende, bereits aktuelle
Eintraege (juenger als 6 h) werden nicht angefasst. GeckoTerminal uebernimmt danach ganz
normal die taegliche Auffrischung (DAILY_MAX_AGE_S), das hier ist nur der Kaltstart.
"""
import json, os, time

import bot_c

HIST = "data/top_hist.json"
HOCH_TAGE = bot_c.HOCH_TAGE          # 90, gleich fuer C und F
SOL_MINT = bot_c.SOL_MINT
FRESH_S = 6 * 3600                    # Cache-Eintraege juenger als das lassen wir in Ruhe


def build_index(hist):
    """solana-adresse -> [[t_sek, schlusskurs, umsatz], ...] aufsteigend, aus top_hist.json."""
    idx = {}
    for cid, h in hist.items():
        d = h.get("d")
        if not d:
            continue
        addr = h.get("addr") or (SOL_MINT if h.get("sym") == "_SOL" else None)
        if not addr:
            continue
        rows = sorted((int(tag), v[0], v[2]) for tag, v in d.items() if v and v[0])
        k = [[t * 86400, preis, umsatz] for t, preis, umsatz in rows][-HOCH_TAGE:]
        if len(k) >= 35:
            idx[addr] = k
    return idx


def seed(bot, idx):
    daily_path = f"bot_{bot}_daily.json"
    meta = json.load(open(f"data/bot_{bot}_meta.json"))
    try:
        st = json.load(open(f"data/bot_{bot}_state.json"))
        positionen = list(st.get("positions", {}))
    except FileNotFoundError:
        positionen = []
    universe_addrs = [a for a, _ in meta.get("universe", [])]
    addrs = set(universe_addrs) | set(positionen) | {SOL_MINT}

    cache = {}
    p = os.path.join("data", daily_path)
    if os.path.exists(p):
        cache = json.load(open(p))

    now = time.time()
    neu, uebersprungen, fehlend = 0, 0, 0
    for a in addrs:
        vorhanden = cache.get(a)
        if vorhanden and now - vorhanden.get("t", 0) < FRESH_S:
            uebersprungen += 1
            continue
        k = idx.get(a)
        if not k:
            fehlend += 1
            continue
        cache[a] = {"t": now, "k": k}
        neu += 1

    with open(p, "w") as f:
        json.dump(cache, f, indent=1)
    print(f"Bot {bot.upper()}: {neu} Coins befuellt, {uebersprungen} bereits aktuell, "
          f"{fehlend} ohne Backtest-Historie (bleiben GeckoTerminal ueberlassen) "
          f"-> {len(addrs)} im Universum gesamt")


if __name__ == "__main__":
    if not os.path.isdir("data"):
        raise SystemExit("Bitte im Solbot-Ordner starten (dort, wo data/ liegt).")
    if not os.path.exists(HIST):
        raise SystemExit(f"{HIST} fehlt - bt_top.py muss vorher gelaufen sein.")
    hist = json.load(open(HIST))
    idx = build_index(hist)
    print(f"{len(idx)} Coins mit ausreichender Historie in top_hist.json gefunden.\n")
    seed("c", idx)
    seed("f", idx)
    print("\nFertig. Naechster Bot-C/F-Lauf sollte deutlich mehr Coins mit vollstaendigem Signal zeigen.")
