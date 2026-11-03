"""Paper-Copy-Trading fuer Bot D (v6, ab 03.11.) - wird von bot_d.py benutzt, laeuft nicht allein.

IDEE: Die Wallets der Copy-Liste (data/bot_d_copy_liste.json) werden bei JEDEM Lauf (alle 5 Min) abgefragt.
Kauft eine Wallet einen Coin, kauft Bot D ihn auf Papier nach - zum echten Jupiter-Kurs in diesem Moment.
Verkauft die Wallet, verkauft Bot D alles. Gemessen wird damit genau die Frage, die die Simulation nicht
beantworten kann: Was bleibt mit UNSERER echten Verzoegerung (bis ~5 Min, weil nur alle 5 Min geschaut wird)
und echten Fills uebrig?

REGELN (bewusst einfach, zum Testen):
  - 50 $ je Kopie, hoechstens 8 Positionen gleichzeitig, kein zweiter Kauf desselben Coins
  - Kauf nur, wenn wir ihn hoechstens 15 Min nach der Wallet sehen (sonst ist der Zug abgefahren)
  - Verkauf komplett, sobald die Quell-Wallet (irgendeinen Teil) verkauft
  - eigener Schutz: Stop -50 %, spaetestens nach 24 h raus
  - die Verzoegerung (Sekunden zwischen Wallet-Kauf und unserem Kauf) wird je Trade mitgeschrieben
    (data/bot_d_copy_trades.jsonl)
Kosten: je Wallet und Lauf 1 Helius-Credit + 1 je neuer Transaktion (~1.000-1.500 am Tag bei 3 Wallets).
"""
import time
from common import *
import walletpruef as wp

POS_USD = 50.0
MAX_POS = 8
MAX_VERZ_S = 15 * 60
STOP = -0.50
MAX_HALTE_H = 24
MAX_NEU_TX = 15
HELIUS_COPY_MAX_TAG = 1500


def neue_swaps(key, wallet, sw, credits):
    """Kaeufe/Verkaeufe der Wallet seit der zuletzt gesehenen Signatur (aelteste zuerst)."""
    opt = {"limit": 25, **({"until": sw["bis_sig"]} if sw.get("bis_sig") else {})}
    sigs = [x for x in (wp._rpc(key, "getSignaturesForAddress", [wallet, opt]) or []) if x.get("err") is None]
    credits["n"] += 1
    if not sigs: return []
    erstes_mal = not sw.get("bis_sig")
    sw["bis_sig"] = sigs[0]["signature"]
    if erstes_mal: return []              # beim ersten Mal nur den Startpunkt merken, keine alten Trades nachkaufen
    neu = list(reversed(sigs[:MAX_NEU_TX]))
    txs = wp.transaktionen(key, [x["signature"] for x in neu], credits)
    out = [s for s in (wp.swap_aus_tx(t, wallet) for t in txs) if s and s["art"] in ("kauf", "verkauf")]
    return sorted(out, key=lambda s: s["t"])


def _kurs(mint):
    p = solana_pair(mint) or {}
    try: px = float(p.get("priceUsd") or 0)
    except (TypeError, ValueError): px = 0.0
    return px, float((p.get("liquidity") or {}).get("usd") or 0), p.get("baseToken", {}).get("symbol") or mint[:4]


def lauf(pf, liste, key, meta):
    """liste = [{"name", "wallet"}, ...]. Gibt (preise, log) zurueck."""
    st = pf.s; now = time.time(); log = []
    cp = st.setdefault("copy", {"wallets": {}})
    tag = time.strftime("%Y-%m-%d", time.gmtime(now))
    if meta.get("copy_tag") != tag: meta["copy_tag"], meta["copy_credits_heute"] = tag, 0
    namen = {x["wallet"]: x["name"] for x in liste}
    # beobachten: Copy-Liste + Wallets, von denen wir noch Positionen halten
    beob = list(namen) + [p["quelle"] for p in st["positions"].values() if p.get("quelle") and p["quelle"] not in namen]
    preise = {}
    for w in dict.fromkeys(beob):
        if not key or meta["copy_credits_heute"] >= HELIUS_COPY_MAX_TAG: break
        sw = cp["wallets"].setdefault(w, {}); credits = {"n": 0}
        try: swaps = neue_swaps(key, w, sw, credits)
        except Exception as ex:
            log.append(f"{namen.get(w, w[:4])}: Abfrage fehlgeschlagen ({str(ex)[:60]})"); swaps = []
        meta["copy_credits_heute"] += credits["n"]; meta["credits_heute"] = meta.get("credits_heute", 0) + credits["n"]
        for s in swaps:
            m = s["mint"]; verz = now - s["t"]; name = namen.get(w) or (st["positions"].get(m) or {}).get("quelle_name") or w[:4]
            if s["art"] == "kauf":
                sw["kaeufe_gesehen"] = sw.get("kaeufe_gesehen", 0) + 1
                if w not in namen or m in st["positions"]: continue
                if len(st["positions"]) >= MAX_POS: log.append(f"{name} kauft {m[:4]}.. - alle {MAX_POS} Plaetze belegt"); continue
                if verz > MAX_VERZ_S: log.append(f"{name} kauft {m[:4]}.. - erst nach {verz/60:.0f} Min gesehen, zu spaet"); continue
                px, liq, sym = _kurs(m)
                if px <= 0: log.append(f"{name} kauft {sym} - kein Kurs"); continue
                usd = min(POS_USD, st["cash"] - 1)
                if usd < 10: log.append(f"{name} kauft {sym} - kein Cash"); continue
                if pf.buy(sym, m, px, usd, liq, f"kopie {name} (+{verz:.0f} s)"):
                    pos = st["positions"][m]
                    pos.update(quelle=w, quelle_name=name, t_wallet=s["t"], wallet_sol=round(s["sol"], 4))
                    preise[m] = px
                    append_jsonl("bot_d_copy_trades.jsonl", {"t": now_iso(), "art": "kauf", "wallet": name, "sym": sym, "mint": m,
                                                             "verzoegerung_s": round(verz), "px": px, "liq": round(liq),
                                                             "wallet_sol": round(s["sol"], 4), "usd": usd})
                    log.append(f"KOPIE {name}: {sym} gekauft {usd:.0f} $ ({verz:.0f} s nach der Wallet)")
            else:
                pos = st["positions"].get(m)
                if not pos or pos.get("quelle") != w: continue
                px, liq, sym = _kurs(m)
                if px > 0 and pf.sell(m, px, 1.0, liq, f"wallet verkauft (+{verz:.0f} s)"):
                    x = px / pos["entry"] - 1 if pos.get("entry") else None
                    append_jsonl("bot_d_copy_trades.jsonl", {"t": now_iso(), "art": "verkauf", "wallet": name, "sym": pos["sym"],
                                                             "mint": m, "verzoegerung_s": round(verz), "px": px,
                                                             "x": round(x, 4) if x is not None else None, "grund": "wallet verkauft"})
                    log.append(f"KOPIE {name}: {pos['sym']} verkauft ({x:+.0%}, Wallet hat verkauft)")
    # eigener Schutz: Stop und Zeitgrenze
    for m, pos in list(st["positions"].items()):
        if not pos.get("quelle"): continue
        px, liq, sym = _kurs(m)
        if px <= 0: continue
        preise[m] = px
        pos["peak"] = max(pos.get("peak", px), px)
        x = px / pos["entry"] - 1
        warum = "stop" if x <= STOP else "zeit 24h" if held_seconds(pos) >= MAX_HALTE_H * 3600 else None
        if warum and pf.sell(m, px, 1.0, liq, warum):
            append_jsonl("bot_d_copy_trades.jsonl", {"t": now_iso(), "art": "verkauf", "wallet": pos.get("quelle_name"),
                                                     "sym": pos["sym"], "mint": m, "px": px, "x": round(x, 4), "grund": warum})
            log.append(f"KOPIE: {pos['sym']} verkauft ({warum}, {x:+.0%})")
    return preise, log
