"""Bot F v5 – "Nach dem Stop": kauft erst, wenn ein virtueller Kauf von Bot C oder Bot E ausgestoppt wurde.

IDEE: Jeder Kandidat, den Bot C oder Bot E in einem Lauf fuer kaufenswert halten, wird von Bot F VIRTUELL
mitgekauft - egal ob C/E selbst gerade Cash oder freie Plaetze haben. Faellt der Kurs danach bis zum Stop
dieser Bots (-10 % vom virtuellen Einstieg), kauft Bot F dort WIRKLICH. Steigt der Kurs vorher auf +15 %
(dort haetten C/E Gewinn genommen) oder vergehen 7 Tage, wird der virtuelle Kauf verworfen.

ECHTER HANDEL: je Kauf 20 % des Gesamtkapitals (Cash + offene Positionen). Verkauf komplett bei +20 %
oder bei -15 % vom echten Einstand. Keine Haltedauer-Grenze. Ein gehandelter Coin ist danach 5 Tage gesperrt.

WAS WIR DARUEBER WISSEN (ehrlich): Getestet an 811 Kursverlaeufen aus Bot D (Micro Caps, 5-Min-Kurse, bis 72 h):
nach einem virtuellen Stop zu kaufen ist deutlich besser als sofort oder zufaellig zu kaufen (ohne die 5 besten
Trades rund -2 % statt -8 % je Trade, mehr Gewinner), wurde aber in keiner Variante in beiden Zeithaelften
positiv. Fuer die groesseren Coins von C und E gibt es keine solchen Daten - genau das misst Bot F jetzt live.
Es ist ein Experiment, keine belegte Strategie.

ABBRUCHREGEL (vorab festgelegt): faellt das Gesamtkapital unter 300 $ (-40 %), kauft Bot F nichts Neues mehr
und verwaltet nur noch offene Positionen.

Laeuft NACH Bot C und Bot E (run_paper.sh arbeitet bot_*.py alphabetisch ab) und liest deren
data/signale_c.json und data/signale_e.json aus demselben Lauf.
"""
import os, time
from common import *

BOT = "Bot F"
VERSION = "f5"

# ---------- virtuelle Kaeufe (Regeln von Bot C / Bot E) ----------
V_STOP = -0.10              # Stop von C und E -> hier wird Bot F aktiv
V_TP = 0.15                 # dort haetten C/E Gewinn genommen -> virtueller Kauf verworfen
V_MAX_D = 7                 # Haltedauer-Grenze von C/E -> danach verworfen
SIGNAL_MAX_ALT_S = 20 * 60  # nur Signale aus dem aktuellen Lauf verwenden

# ---------- echter Handel (Vorgabe) ----------
POS_FRAC = 0.20             # 20 % des Gesamtkapitals je Kauf
TP = 0.20                   # +20 % -> alles verkaufen
SL = -0.15                  # -15 % -> alles verkaufen
COOLDOWN_D = 5
MIN_LIQ = 100_000           # Mindest-Liquiditaet des Pools beim echten Kauf
STOP_UNTER = 300.0          # Abbruchregel
DEAD_PRICE_H = 24           # 24 h ohne Kurs -> abschreiben


def _liq(p): return float(((p or {}).get("liquidity") or {}).get("usd") or 0)

def px_of(p):
    try: return float((p or {}).get("priceUsd") or 0)
    except (TypeError, ValueError): return 0.0

def fetch_pairs(pair_addrs):
    out = {}
    pair_addrs = list(dict.fromkeys(a for a in pair_addrs if a))
    for k in range(0, len(pair_addrs), 30):
        d = get(f"{DS}/latest/dex/pairs/solana/{','.join(pair_addrs[k:k+30])}") or {}
        for p in (d.get("pairs") or []):
            if p and p.get("chainId") == "solana" and p.get("pairAddress"): out[p["pairAddress"]] = p
        time.sleep(1.1)
    return out


def versionswechsel():
    alt = load("bot_f_state.json", None)
    if not alt or alt.get("version") == VERSION: return
    os.makedirs(os.path.join(DATA, "archive"), exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M", time.gmtime())
    for name in ("bot_f_state.json", "bot_f_meta.json", "bot_f_daily.json"):
        p = os.path.join(DATA, name)
        if os.path.exists(p): os.replace(p, os.path.join(DATA, "archive", f"{name[:-5]}_{alt.get('version', 'v4')}_{stamp}.json"))
    print(f"{BOT}: Strategiewechsel auf {VERSION} - alter Stand archiviert, Neustart mit {START_CAPITAL:.0f} $")


def main():
    versionswechsel()
    pf = Paper("bot_f"); st = pf.s; st["version"] = VERSION
    st.setdefault("cooldown", {})
    now = time.time(); today = int(now // 86400)
    meta = load("bot_f_meta.json", {}); virt = meta.setdefault("virtuell", {})
    log = []

    # 1) neue Signale von C und E -> virtuelle Kaeufe eroeffnen
    neu = 0
    for datei in ("signale_c.json", "signale_e.json"):
        s = load(datei, {})
        if not s or now - s.get("t", 0) > SIGNAL_MAX_ALT_S: continue
        for k in s.get("kand") or []:
            a = k.get("addr")
            if not a or not k.get("px") or not k.get("pair"): continue
            if a in virt or a in st["positions"] or st["cooldown"].get(a, 0) > today: continue
            virt[a] = {"sym": k.get("sym"), "px0": float(k["px"]), "pair": k["pair"], "t0": now, "von": s.get("bot")}
            neu += 1
            log.append((k.get("sym"), f"virtuell gekauft ({s.get('bot')}) bei {float(k['px']):.6g}"))

    # 2) Kurse fuer virtuelle Kaeufe und echte Positionen
    paare = fetch_pairs([v["pair"] for v in virt.values()] + [p.get("pair") for p in st["positions"].values()])
    kurs = lambda pair: px_of(paare.get(pair))
    prices = {a: kurs(p.get("pair")) for a, p in st["positions"].items() if kurs(p.get("pair")) > 0}

    # 3) echte Positionen: +20 % oder -15 %
    for a, pos in list(st["positions"].items()):
        px = prices.get(a); liq = _liq(paare.get(pos.get("pair")))
        if not px:
            seit = pos.setdefault("no_px_since", now_iso())
            if (now - parse_iso(seit)) / 3600 >= DEAD_PRICE_H and pos.get("qty", 0) > 0:
                verlust = round(pos.get("cost", 0.0), 2)
                st["trades"].append({"t": now_iso(), "side": "sell", "sym": pos["sym"], "addr": a, "price": 0.0,
                                     "usd": 0.0, "pnl": -verlust, "frac": 1.0, "reason": "abgeschrieben (kein kurs)"})
                del st["positions"][a]; st["cooldown"][a] = today + COOLDOWN_D
                log.append((pos["sym"], f"abgeschrieben - {DEAD_PRICE_H} h ohne Kurs"))
            continue
        pos.pop("no_px_since", None)
        pos["peak"] = max(pos.get("peak", px), px)
        x = px / pos["entry"] - 1
        warum = "tp" if x >= TP else "stop" if x <= SL else None
        if warum and pf.sell(a, px, 1.0, liq, warum):
            st["cooldown"][a] = today + COOLDOWN_D
            log.append((pos["sym"], f"verkauft ({warum}) bei {x:+.1%}"))

    # 4) virtuelle Kaeufe pruefen: Stop getroffen -> echt kaufen
    equity = st["cash"] + sum(q["qty"] * (q.get("mark") or q.get("cur_price") or q["entry"]) for q in st["positions"].values())
    gestoppt = equity < STOP_UNTER
    if gestoppt: log.append(("ABBRUCH", f"Kapital {equity:.0f} $ unter {STOP_UNTER:.0f} $ - keine neuen Kaeufe"))
    for a, v in list(virt.items()):
        p = paare.get(v["pair"]); px = px_of(p)
        if px <= 0:
            if now - v["t0"] > V_MAX_D * 86400: del virt[a]
            continue
        x = px / v["px0"] - 1
        v["tief"] = min(v.get("tief", x), x)
        if x >= V_TP:
            append_jsonl("bot_f_virtuell.jsonl", {"t": now_iso(), "sym": v["sym"], "von": v["von"], "ende": "tp", "x": round(x, 4)})
            del virt[a]; continue
        if now - v["t0"] > V_MAX_D * 86400:
            append_jsonl("bot_f_virtuell.jsonl", {"t": now_iso(), "sym": v["sym"], "von": v["von"], "ende": "zeit", "x": round(x, 4)})
            del virt[a]; continue
        if x > V_STOP: continue
        # virtueller Stop getroffen
        append_jsonl("bot_f_virtuell.jsonl", {"t": now_iso(), "sym": v["sym"], "von": v["von"], "ende": "stop", "x": round(x, 4)})
        del virt[a]
        liq = _liq(p)
        if gestoppt: continue
        if a in st["positions"] or st["cooldown"].get(a, 0) > today: continue
        if liq < MIN_LIQ: log.append((v["sym"], f"stop getroffen, aber liq {liq/1e3:.0f}k zu klein")); continue
        usd = min(st["cash"] - 1, equity * POS_FRAC)
        if usd < 20: log.append((v["sym"], "stop getroffen, aber kein Cash")); continue
        if pf.buy(v["sym"], a, px, usd, liq, f"nach virtuellem stop ({v['von']}) {x:+.1%}"):
            st["positions"][a]["pair"] = v["pair"]; prices[a] = px
            log.append((v["sym"], f"GEKAUFT {usd:.0f} $ nach virtuellem Stop {x:+.1%} (Signal von {v['von']})"))
            append_jsonl("bot_f_signals.jsonl", {"t": now_iso(), "addr": a, "sym": v["sym"], "von": v["von"],
                                                 "px0": v["px0"], "px": px, "x": round(x, 4), "liq": round(liq), "usd": round(usd, 2)})

    st["cooldown"] = {k: t for k, t in st["cooldown"].items() if t > today}
    st["strategy"] = "nach_dem_stop"
    st["coverage"] = {"t": now_iso(), "virtuell": len(virt), "neu": neu, "gestoppt": gestoppt}
    save("bot_f_meta.json", meta)
    val = pf.mark(prices); pf.commit()
    print(f"{BOT} [nach dem stop]: equity {val:.2f} | cash {st['cash']:.2f} | positionen {len(st['positions'])}"
          f" | virtuell {len(virt)} (neu {neu}) | gestoppt {gestoppt}")
    for sym, txt in log[:12]: print(f"   {str(sym):<12} {txt}")


if __name__ == "__main__":
    main()
