"""Bot E LIVE – handelt Bot E's Regeln mit ECHTEM Geld (USDC) auf einer eigenen Wallet.

WICHTIG ZUR SICHERHEIT:
  - Laeuft aus einer ZWEITEN Kopie des Repos (z.B. /root/solbot_live), die sich NIE automatisch aktualisiert.
    Code aendert sich hier nur, wenn du dort selbst "git pull" eingibst.
  - Der Wallet-Schluessel liegt in einer Datei ausserhalb des Repos (LIVE_KEY_FILE, Standard /root/.solbot_live_key).
  - Gestartet von run_live.sh (Cron alle 2 Min), mit Sperre gegen ueberlappende Laeufe.

REGELN: exakt die von Bot E (bot_e.py wird importiert, alle Grenzwerte kommen von dort):
  Universum ~Top 100 Solana-Tokens, Kauf nach Dip (24h -10 bis -30 %, mind. 6 Prozentpunkte schlechter als SOL,
  letzte Stunde stabil, 1,5-6 % ueber dem 6h-Tief, 7 Tage nicht schlechter als -35 %), hoechstens 2 Positionen,
  je 45 % des Kapitals (USDC + Positionen). Stop -10 %, Haelfte bei +15 %, Trailing, danach Stop auf Einstand,
  nach 7 Tagen raus. 3 Stops in Folge -> 3 Tage Pause, gestoppter Coin 5 Tage gesperrt.

UNTERSCHIEDE ZU E PAPER (bewusst):
  - Entscheidungen bei offenen Positionen mit dem Jupiter-Verkaufskurs (= was man wirklich bekommt), nicht mit dem
    DexScreener-Kurs. Schutz gegen kaputte Kurse (z.B. ANTFUN am 17.11.: DexScreener -100 %, echter Kurs +8 %).
  - Kontostaende kommen IMMER von der Blockchain. Es ist immer nur EINE Transaktion gleichzeitig unterwegs.
  - SOL-Reserve fuer Gebuehren: faellt sie unter 1 $, wird mit USDC auf 5 $ aufgefuellt (zaehlt nicht zum Kapital).

MODI (.env):  LIVE_MODUS=trocken  -> rechnet alles, baut und simuliert Transaktionen, sendet NICHTS
              LIVE_MODUS=live     -> echte Trades
              LIVE_MAX_KAUF_USD=20 -> Obergrenze je Kauf (Mini-Test). 0 = keine Grenze (45 % des Kapitals).
NOTAUS:       Datei STOP in diesem Ordner anlegen -> der Bot macht gar nichts mehr (offene Positionen bleiben!).
              alles_verkaufen.py verkauft alles in USDC.
"""
import os, sys, time, json, traceback, calendar

HIER = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HIER)
sys.path.insert(0, REPO); sys.path.insert(0, HIER)
import common
STATE_DIR = os.environ.get("LIVE_STATE_DIR") or os.path.join(os.path.dirname(REPO), "live_state")
os.makedirs(STATE_DIR, exist_ok=True)
common.DATA = STATE_DIR                       # alle load/save/append_jsonl landen ausserhalb des Repos
import bot_e as E
from chain import Chain, lade_wallet, USDC, SOL_MINT
import telegram as tg

MODUS = (os.environ.get("LIVE_MODUS") or "trocken").strip().lower()
KEY_FILE = os.environ.get("LIVE_KEY_FILE") or "/root/.solbot_live_key"
MAX_KAUF_USD = float(os.environ.get("LIVE_MAX_KAUF_USD") or 20)
TROCKEN_KAPITAL = 200.0                        # Trockenlauf mit leerer Wallet: so tun, als waeren 200 $ da
MIN_KAUF_USD = 10.0
BUY_BPS, SELL_BPS = 100, 300                   # erlaubte Slippage: Kauf 1 %, Verkauf 3 % (raus kommen ist wichtiger)
MAX_IMPACT = 0.10                              # wie Paper
BUY_DEVIATION = 1.25                           # wie Paper: Jupiter-Fill vs. DexScreener-Kurs
SOL_MIN_USD, SOL_ZIEL_USD = 1.0, 5.0
WARTEN_S = 30                                  # so lange in einem Lauf auf Bestaetigung warten
STAUB_USD = 0.50                               # Rest unter 0,50 $ gilt als verkauft
WIEDERKAUF_SPERRE_H = 6                        # 20.11.: denselben Coin erst 6 h nach einem kompletten Verkauf wieder kaufen
STATE = "live_state.json"


def heute(): return int(time.time() // 86400)

def usd_fmt(x): return f"{x:,.2f} $".replace(",", "'")


class Bot:
    def __init__(self):
        self.st = common.load(STATE, {"positions": {}, "pending": {}, "meta": {}, "cooldown": {}, "hist": {},
                                      "trocken_signale": {}})
        self.meta = self.st["meta"]
        self.kp = lade_wallet(KEY_FILE)
        self.ch = Chain(self.kp, os.environ["HELIUS_KEY"], os.environ["JUP_KEY"])
        self.now = time.time(); self.toks = {}; self.lam = 0; self.werte = {}
        if "wiederkauf_sperre" not in self.st:   # einmalig: Verkaeufe der letzten 6 h aus dem Trade-Log uebernehmen
            sp = self.st["wiederkauf_sperre"] = {}
            try:
                for z in open(os.path.join(STATE_DIR, "live_trades.jsonl")):
                    t = json.loads(z)
                    if t.get("side") != "sell": continue
                    bis = calendar.timegm(time.strptime(t["t"][:19], "%Y-%m-%dT%H:%M:%S")) + WIEDERKAUF_SPERRE_H * 3600
                    if bis > self.now: sp[t["mint"]] = bis
            except (OSError, ValueError, KeyError): pass
        self.aktion_gesperrt = False

    # ---------- Hilfen ----------
    def speichern(self): common.save(STATE, self.st)

    def einmal(self, key, text, alle_s=3600):
        g = self.meta.setdefault("gemeldet", {})
        if self.now - g.get(key, 0) >= alle_s:
            g[key] = self.now; tg.senden(text)

    def salden(self):
        self.lam = self.ch.sol_lamports(); self.toks = self.ch.token_konten()

    def raw(self, mint): return (self.toks.get(mint) or {}).get("raw", 0)

    def usdc(self): return self.raw(USDC) / 1e6

    def kapital(self):
        """USDC + Wert der Positionen (Jupiter-Verkaufswert dieses Laufs, sonst Einstand)."""
        return self.usdc() + sum(self.werte.get(m, p["cost"]) for m, p in self.st["positions"].items())

    def konto_text(self):
        return f"Konto: {usd_fmt(self.usdc())} USDC + {usd_fmt(self.kapital() - self.usdc())} in Positionen = {usd_fmt(self.kapital())}"

    # ---------- offene Transaktionen ----------
    def offene_aufloesen(self):
        """Bestimmt fuer jede offene Transaktion, ob sie endgueltig ist, und verbucht sie anhand der KONTOSTAENDE."""
        fertig = []
        for key, pd in self.st["pending"].items():
            res = self.ch.endgueltig(pd["sig"], pd["last_valid"]) if pd.get("sig") else \
                ("abgelaufen" if self.ch.blockhoehe() > pd["last_valid"] + 5 else None)
            if res is None:
                if self.now - pd["t"] > 900:
                    self.einmal("pending_" + key, f"⚠️ Transaktion fuer {pd.get('sym')} seit {int((self.now - pd['t']) / 60)} Min "
                                                  f"unbestaetigt. Signatur: {pd.get('sig')}", 3600)
                self.aktion_gesperrt = True
                continue
            fertig.append((key, pd, res))
        if not fertig: return
        self.salden()
        for key, pd, res in fertig:
            del self.st["pending"][key]
            getattr(self, "_fertig_" + pd["art"])(pd, res)
        self.speichern()

    def _fertig_kauf(self, pd, res):
        m = pd["mint"]; neu = self.raw(m) - pd["raw_vorher"]
        if neu <= 0:
            tg.senden(f"❌ Kauf {pd['sym']} fehlgeschlagen ({res}). Nichts gekauft.\n{self.konto_text()}"); return
        dec = self.toks[m]["dec"]
        bezahlt = pd["usdc_vorher"] - self.usdc()
        if not (0 < bezahlt <= pd["usd"] * 1.05): bezahlt = pd["usd"]
        entry = bezahlt / (neu / 10 ** dec)
        self.st["positions"][m] = {"sym": pd["sym"], "raw": self.raw(m), "dec": dec, "entry": entry, "cost": bezahlt,
                                   "opened": common.now_iso(), "peak": entry, "tp1": False}
        common.append_jsonl("live_trades.jsonl", {"t": common.now_iso(), "side": "buy", "sym": pd["sym"], "mint": m,
                                                  "usd": round(bezahlt, 2), "preis": entry, "sig": pd.get("sig"), "grund": pd["grund"]})
        self.werte[m] = bezahlt
        tg.senden(f"🟢 GEKAUFT {pd['sym']} fuer {usd_fmt(bezahlt)}\nPreis {entry:.6g} $ | {pd['grund']}\n{self.konto_text()}")

    def _fertig_verkauf(self, pd, res):
        m = pd["mint"]; pos = self.st["positions"].get(m)
        verkauft = pd["raw_vorher"] - self.raw(m)
        if not pos or verkauft <= 0:
            tg.senden(f"❌ Verkauf {pd['sym']} ({pd['grund']}) fehlgeschlagen ({res}). Position bleibt, neuer Versuch "
                      f"beim naechsten Lauf, falls die Bedingung noch gilt."); return
        erhalten = max(0.0, self.usdc() - pd["usdc_vorher"])
        anteil = min(1.0, verkauft / pd["raw_vorher"])
        kosten = pos["cost"] * anteil; pnl = erhalten - kosten
        pos["cost"] -= kosten; pos["raw"] = self.raw(m)
        rest_wert = (pos["raw"] / 10 ** pos["dec"]) * (erhalten / (verkauft / 10 ** pos["dec"]))
        common.append_jsonl("live_trades.jsonl", {"t": common.now_iso(), "side": "sell", "sym": pd["sym"], "mint": m,
                                                  "usd": round(erhalten, 2), "pnl": round(pnl, 2), "anteil": round(anteil, 3),
                                                  "sig": pd.get("sig"), "grund": pd["grund"]})
        ganz = pos["raw"] == 0 or rest_wert < STAUB_USD
        if pd["grund"] == "tp1" and not ganz:
            pos["tp1"] = True; self.werte[m] = rest_wert
        if ganz:
            del self.st["positions"][m]; self.werte.pop(m, None); self.meta["last_sell"] = self.now
            self.st.setdefault("wiederkauf_sperre", {})[m] = self.now + WIEDERKAUF_SPERRE_H * 3600
            if pd["grund"] == "stop":
                self.st["cooldown"][m] = heute() + E.COOLDOWN_D
                self.meta["consec_stops"] = self.meta.get("consec_stops", 0) + 1
                if self.meta["consec_stops"] >= E.MAX_CONSEC_STOPS:
                    self.meta["paused_until"] = heute() + E.PAUSE_D; self.meta["consec_stops"] = 0
                    tg.senden(f"⏸ {E.MAX_CONSEC_STOPS} Stops in Folge -> {E.PAUSE_D} Tage keine neuen Kaeufe (Regel von Bot E).")
            else:
                self.meta["consec_stops"] = 0
        emoji = "🟢" if pnl >= 0 else "🔴"
        tg.senden(f"{emoji} VERKAUFT {pd['sym']} ({pd['grund']}, {anteil:.0%} der Position) fuer {usd_fmt(erhalten)}\n"
                  f"Gewinn/Verlust: {pnl:+.2f} $\n{self.konto_text()}")

    def _fertig_sol(self, pd, res):
        tg.senden(f"⛽ SOL-Reserve aufgefuellt: jetzt {self.lam / 1e9:.4f} SOL ({res})." if self.lam > pd["lam_vorher"]
                  else f"⚠️ SOL auffuellen fehlgeschlagen ({res}).")

    # ---------- Ausfuehren ----------
    def ausfuehren(self, art, mint, sym, q, grund, **extra):
        """trocken: bauen + simulieren. live: senden und bis WARTEN_S auf Bestaetigung warten."""
        try: tx, last_valid = self.ch.swap_tx(q)
        except Exception as ex: return ("trocken" if MODUS != "live" else "fehler"), f"Jupiter: {str(ex)[:180]}"
        if MODUS != "live":
            return "trocken", self.ch.simulieren(tx)
        pd = {"art": art, "mint": mint, "sym": sym, "grund": grund, "t": self.now, "sig": None, "last_valid": last_valid,
              "usdc_vorher": self.usdc(), "raw_vorher": self.raw(mint), "lam_vorher": self.lam, **extra}
        self.st["pending"][mint] = pd; self.speichern()        # VOR dem Senden speichern (Absturz-Schutz)
        try:
            pd["sig"] = self.ch.senden(tx)
        except Exception as ex:
            del self.st["pending"][mint]; self.speichern()
            return "fehler", str(ex)[:200]
        self.speichern()
        self.aktion_gesperrt = True
        if self.ch.warten(pd["sig"], last_valid, WARTEN_S) is not None:
            self.aktion_gesperrt = False
            self.offene_aufloesen()
        return "gesendet", None

    # ---------- Lauf ----------
    def lauf(self):
        if not self.meta.get("gestartet"):
            self.salden()
            self.meta["gestartet"] = common.now_iso()
            tg.senden(f"🚀 Bot E LIVE gestartet. Modus: {MODUS.upper()}"
                      + (f", max. {MAX_KAUF_USD:.0f} $ je Kauf" if MAX_KAUF_USD > 0 else ", 45 % des Kapitals je Kauf")
                      + f"\nWallet: {self.ch.pub}\nUSDC: {usd_fmt(self.usdc())} | SOL: {self.lam / 1e9:.4f}")
        if self.meta.get("modus") != MODUS or self.meta.get("max_kauf") != MAX_KAUF_USD:
            if self.meta.get("modus"):
                tg.senden(f"🔧 Einstellung geaendert: Modus {MODUS.upper()}, "
                          + (f"max. {MAX_KAUF_USD:.0f} $ je Kauf" if MAX_KAUF_USD > 0 else "45 % des Kapitals je Kauf"))
            self.meta["modus"], self.meta["max_kauf"] = MODUS, MAX_KAUF_USD

        self.salden()
        self.offene_aufloesen()
        self.abgleich()

        # Universum und Kurse (Regeln und Funktionen von Bot E)
        m = self.meta
        if self.now - m.get("last_discover", 0) >= E.DISCOVER_EVERY_S or not m.get("universe"):
            u = E.codex_universe()
            if u: m["universe"], m["last_discover"], m["pair_of"] = u, self.now, {}
            elif not m.get("universe"):
                p = os.path.join(os.path.dirname(REPO), "solbot", "data", "bot_e_meta.json")
                if os.path.exists(p):
                    m["universe"] = json.load(open(p)).get("universe") or []; m["last_discover"] = self.now - E.DISCOVER_EVERY_S + 3600
                    self.einmal("universe_paper", f"ℹ️ Codex nicht erreichbar - Universum ({len(m['universe'])} Coins) "
                                                  f"vorlaeufig von E Paper uebernommen.", 86400)
        universe = list(set(m.get("universe") or []) | set(self.st["positions"]))
        if not universe:
            self.einmal("kein_universum", "⚠️ Kein Coin-Universum (Codex nicht erreichbar). Es wird nichts gekauft.", 6 * 3600)
        pairs = E.batch_pairs(universe, m) if universe else {}
        sol = pairs.get(SOL_MINT)
        sol24 = E.f(sol, "priceChange", "h24") if sol else None
        sol_px = E.f(sol, "priceUsd") if sol else None
        self.historie(pairs, universe)

        self.sol_reserve(sol_px)
        self.positionen(pairs)
        self.einstiege(pairs, sol24)
        self.konten_aufraeumen()
        self.tagesbericht(sol_px)
        self.st["cooldown"] = {k: v for k, v in self.st["cooldown"].items() if v > heute()}
        self.st["wiederkauf_sperre"] = {k: v for k, v in self.st.get("wiederkauf_sperre", {}).items() if v > self.now}
        self.st["letzter_lauf"] = common.now_iso()
        self.speichern()
        print(f"Bot E LIVE [{MODUS}] {self.konto_text()} | SOL {self.lam / 1e9:.4f} | positionen {len(self.st['positions'])} "
              f"| offen {len(self.st['pending'])} | kurse {len(pairs)}/{len(universe)} | SOL 24h {sol24}")

    def abgleich(self):
        """Positionen mit der Wallet abgleichen (z.B. wenn von Hand etwas verkauft wurde)."""
        for m, pos in list(self.st["positions"].items()):
            if m in self.st["pending"]: continue
            r = self.raw(m)
            if r == 0:
                del self.st["positions"][m]
                tg.senden(f"⚠️ {pos['sym']} ist nicht mehr in der Wallet - aus der Positionsliste entfernt. "
                          f"(Von Hand verkauft oder verschoben?)")
            elif r != pos["raw"]:
                if r < pos["raw"]: pos["cost"] *= r / pos["raw"]
                pos["raw"] = r

    def historie(self, pairs, universe):
        """Eigene Kurs-Historie fuer das 6h-Tief. Nur alle ~5 Min ein Punkt (wie Paper), damit 90 Punkte = 7,5 h."""
        hist = self.st["hist"]
        for a, p in pairs.items():
            px = E.f(p, "priceUsd") or 0
            if px <= 0: continue
            pts = hist.setdefault(a, [])
            if pts and self.now - pts[-1][0] < 270: continue
            pts.append([self.now, px])
            hist[a] = [x for x in pts if self.now - x[0] <= E.HIST_KEEP_S][-90:]
        for a in list(hist):
            if a not in universe and a != SOL_MINT: del hist[a]

    def sol_reserve(self, sol_px):
        if not sol_px or self.st["pending"] or self.aktion_gesperrt: return
        if MODUS != "live" and self.usdc() < 1: return          # Trockenlauf mit leerer Wallet: keine Warnungen
        sol_usd = self.lam / 1e9 * sol_px
        if sol_usd >= SOL_MIN_USD: return
        bedarf = SOL_ZIEL_USD - sol_usd
        if self.lam < 15_000:
            self.einmal("sol_leer", f"⚠️ Fast kein SOL mehr ({self.lam / 1e9:.6f}) - nicht einmal genug fuer die Gebuehr zum "
                                    f"Auffuellen. Bitte etwas SOL an {self.ch.pub} senden.", 6 * 3600); return
        if self.usdc() < bedarf + 1:
            self.einmal("sol_kein_usdc", f"⚠️ SOL-Reserve nur {sol_usd:.2f} $, aber zu wenig freies USDC zum Auffuellen.", 6 * 3600); return
        q = self.ch.quote(USDC, SOL_MINT, int(bedarf * 1e6), BUY_BPS)
        if not q: return
        res, err = self.ausfuehren("sol", SOL_MINT, "SOL", q, "reserve")
        if res == "trocken": self.einmal("sol_trocken", f"[TROCKEN] wuerde SOL-Reserve um {bedarf:.2f} $ auffuellen. "
                                                        f"Simulation: {'ok' if not err else err}", 6 * 3600)
        elif res == "fehler": tg.senden(f"⚠️ SOL auffuellen: Senden fehlgeschlagen: {err}")

    def positionen(self, pairs):
        for m, pos in list(self.st["positions"].items()):
            if m in self.st["pending"] or self.aktion_gesperrt: continue
            q = self.ch.quote(m, USDC, pos["raw"], SELL_BPS)
            if not q:
                pos.setdefault("ohne_kurs_seit", self.now)
                if self.now - pos["ohne_kurs_seit"] > 1800:
                    self.einmal("ohne_kurs_" + m, f"⚠️ {pos['sym']}: seit {int((self.now - pos['ohne_kurs_seit']) / 60)} Min "
                                                  f"keine Jupiter-Route zum Verkaufen.", 3600)
                continue
            pos.pop("ohne_kurs_seit", None)
            wert = int(q["outAmount"]) / 1e6
            px = wert / (pos["raw"] / 10 ** pos["dec"])
            ds = E.f(pairs.get(m), "priceUsd") if pairs.get(m) else None
            if ds and px < ds * 0.75:
                self.einmal("verdacht_" + m, f"⚠️ {pos['sym']}: Jupiter-Kurs {px:.6g} liegt >25 % unter DexScreener {ds:.6g}. "
                                             f"Keine Entscheidung in diesem Lauf (Schutz gegen kaputte Route).", 3600)
                continue
            self.werte[m] = wert
            pos["peak"] = max(pos.get("peak", px), px)
            x = px / pos["entry"] - 1; peak_x = pos["peak"] / pos["entry"] - 1; vom_hoch = px / pos["peak"] - 1
            tage = common.held_seconds(pos) / 86400
            why = None; frac = 1.0
            if not pos.get("tp1"):
                if x <= E.STOP: why = "stop"
                elif x >= E.TP1_GAIN: why, frac = "tp1", E.TP1_FRAC
                elif peak_x >= E.TRAIL_ARM and vom_hoch <= E.TRAIL_GIVEBACK: why = "trail"
                elif tage >= E.MAX_HOLD_D and x < E.FLAT_EXIT_GAIN: why = "time"
            else:
                if E.BREAKEVEN_AFTER_TP1 and x <= 0.0: why = "breakeven"
                elif vom_hoch <= E.TRAIL_GIVEBACK: why = "trail"
                elif tage >= E.MAX_HOLD_D: why = "time"
            if not why: continue
            raw = pos["raw"] if frac >= 1 else int(pos["raw"] * frac)
            if frac < 1: q = self.ch.quote(m, USDC, raw, SELL_BPS)
            if not q: continue
            res, err = self.ausfuehren("verkauf", m, pos["sym"], q, why, frac=frac)
            if res == "trocken":
                self.einmal(f"trocken_v_{m}_{why}", f"[TROCKEN] wuerde {pos['sym']} verkaufen ({why}, {frac:.0%}) bei {x:+.1%}. "
                                                    f"Simulation: {'ok' if not err else err}", 3600)
            elif res == "fehler":
                self.einmal("sendefehler_" + m, f"⚠️ Verkauf {pos['sym']} ({why}): Senden fehlgeschlagen: {err}", 600)

    def einstiege(self, pairs, sol24):
        st, m = self.st, self.meta
        markt_ok = sol24 is not None and sol24 >= E.SOL_24H_MIN
        if not markt_ok or not pairs: return
        can_buy = (len(st["positions"]) < E.MAX_POS and m.get("paused_until", 0) <= heute()
                   and not st["pending"] and not self.aktion_gesperrt)
        if not can_buy: return
        cands = []
        for a, p in pairs.items():
            if a == SOL_MINT or a in st["positions"] or st["cooldown"].get(a, 0) > heute(): continue
            if st.get("wiederkauf_sperre", {}).get(a, 0) > self.now: continue   # 6 h nach Verkauf gesperrt
            sym = p["baseToken"]["symbol"]
            if not E.tradeable(sym): continue
            c24, c1 = E.f(p, "priceChange", "h24"), E.f(p, "priceChange", "h1")
            mcap = E.f(p, "marketCap") or E.f(p, "fdv") or 0; liq = E.f(p, "liquidity", "usd") or 0
            vol = E.f(p, "volume", "h24") or 0; px = E.f(p, "priceUsd") or 0
            if None in (c24, c1) or px <= 0: continue
            if not (E.DROP_24H[0] <= c24 <= E.DROP_24H[1]): continue
            if mcap < E.U_MIN_MCAP or liq < E.U_MIN_LIQ: continue
            if c24 > sol24 - E.REL_TO_SOL_PP or c1 < E.STAB_1H_MIN or vol / mcap < E.VOL_TO_MCAP_MIN: continue
            pts = [x[1] for x in st["hist"].get(a, []) if self.now - x[0] <= 6 * 3600]
            if len(pts) < 6 or not (E.BOUNCE_6H[0] / 100 <= px / min(pts) - 1 <= E.BOUNCE_6H[1] / 100): continue
            c7 = E.change_7d(p.get("pairAddress"), px)
            if c7 is None or c7 < E.MAX_DROP_7D: continue
            cands.append((c24 - sol24, a, sym, px, c24, c1))
        cands.sort()
        for rel, a, sym, px, c24, c1 in cands[:E.MAX_POS - len(st["positions"])]:
            if self.aktion_gesperrt or st["pending"]: break
            kapital = self.kapital(); frei = self.usdc()
            if MODUS != "live" and kapital < 20: kapital = frei = TROCKEN_KAPITAL
            usd = min(frei - 1, kapital * E.POS_FRAC)
            if MAX_KAUF_USD > 0: usd = min(usd, MAX_KAUF_USD)
            if usd < MIN_KAUF_USD: break
            grund = f"dip 24h {c24:+.0f}% (SOL {sol24:+.0f}%) 1h {c1:+.1f}%"
            dec = common.token_decimals(a)
            q = self.ch.quote(USDC, a, int(usd * 1e6), BUY_BPS)
            if dec is None or not q: continue
            impact = abs(float(q.get("priceImpactPct") or 0)); fill = usd / (int(q["outAmount"]) / 10 ** dec)
            if impact > MAX_IMPACT or not (1 / BUY_DEVIATION <= fill / px <= BUY_DEVIATION):
                self.einmal("kauf_abgelehnt_" + a, f"ℹ️ {sym}: Kaufsignal, aber Jupiter passt nicht (Impact {impact:.1%}, "
                                                   f"Fill {fill:.6g} vs. Kurs {px:.6g}). Nicht gekauft.", 6 * 3600)
                continue
            if MODUS != "live":
                if self.now - st["trocken_signale"].get(a, 0) < 6 * 3600: continue
                res, err = self.ausfuehren("kauf", a, sym, q, grund, usd=usd)
                st["trocken_signale"][a] = self.now
                tg.senden(f"[TROCKEN] wuerde {sym} fuer {usd_fmt(usd)} kaufen | {grund}\nSimulation: "
                          + ("ok" if not err else f"{err}" + (" (erwartet, solange die Wallet leer ist)" if self.usdc() < usd else "")))
                common.append_jsonl("live_signale.jsonl", {"t": common.now_iso(), "modus": MODUS, "sym": sym, "mint": a,
                                                           "usd": round(usd, 2), "grund": grund, "sim": err})
                continue
            res, err = self.ausfuehren("kauf", a, sym, q, grund, usd=usd)
            common.append_jsonl("live_signale.jsonl", {"t": common.now_iso(), "modus": MODUS, "sym": sym, "mint": a,
                                                       "usd": round(usd, 2), "grund": grund, "res": res, "err": err})
            if res == "fehler": tg.senden(f"⚠️ Kauf {sym}: Senden fehlgeschlagen: {err}")

    def konten_aufraeumen(self):
        """Leere Token-Konten schliessen (je Lauf hoechstens eines), damit die Miete (~0,002 SOL) zurueckkommt."""
        if MODUS != "live" or self.st["pending"] or self.aktion_gesperrt: return
        for mint, e in self.toks.items():
            if mint == USDC or mint in self.st["positions"]: continue
            for konto, prog, raw in e["konten"]:
                if raw == 0:
                    try: self.ch.konto_schliessen(konto, prog)
                    except Exception as ex: print("konto schliessen:", ex)
                    return

    def tagesbericht(self, sol_px):
        if time.gmtime(self.now).tm_hour < 19 or self.meta.get("bericht_tag") == heute(): return
        self.meta["bericht_tag"] = heute()
        zeilen = []
        for m, p in self.st["positions"].items():
            w = self.werte.get(m)
            zeilen.append(f"  {p['sym']}: {usd_fmt(w) if w else '?'} ({(w / p['cost'] - 1):+.1%})" if w else f"  {p['sym']}")
        sol_usd = self.lam / 1e9 * (sol_px or 0)
        tg.senden(f"📊 Tagesbericht Bot E LIVE ({MODUS})\n{self.konto_text()}\nSOL-Reserve: {self.lam / 1e9:.4f} SOL "
                  f"(~{sol_usd:.2f} $)\nPositionen: {len(self.st['positions'])}\n" + "\n".join(zeilen)
                  + (f"\nPause bis Tag {self.meta['paused_until']}" if self.meta.get("paused_until", 0) > heute() else ""))


def main():
    if os.path.exists(os.path.join(HIER, "STOP")) or os.path.exists(os.path.join(STATE_DIR, "STOP")):
        st = common.load(STATE, {"meta": {}}); g = st.setdefault("meta", {}).setdefault("gemeldet", {})
        if time.time() - g.get("stop", 0) > 86400:
            g["stop"] = time.time(); common.save(STATE, st)
            tg.senden("⛔ STOP-Datei gefunden: Bot E LIVE macht nichts. Offene Positionen bleiben unveraendert. "
                      "Zum Weitermachen die Datei STOP loeschen.")
        print("STOP-Datei vorhanden - nichts zu tun"); return
    fehlt = [k for k in ("HELIUS_KEY", "JUP_KEY") if not os.environ.get(k)]
    if fehlt or not os.path.exists(KEY_FILE):
        tg.senden(f"⚠️ Bot E LIVE kann nicht starten: fehlt {', '.join(fehlt) or ''} {'' if os.path.exists(KEY_FILE) else 'Schluesseldatei ' + KEY_FILE}")
        return
    if MODUS not in ("trocken", "live"):
        tg.senden(f"⚠️ LIVE_MODUS '{MODUS}' unbekannt - erlaubt sind trocken oder live. Nichts gemacht."); return
    try: bot = Bot()
    except Exception as ex:
        tg.senden(f"⚠️ Bot E LIVE kann nicht starten: {str(ex)[:300]}"); raise
    try:
        bot.lauf()
    except Exception as ex:
        bot.speichern()
        traceback.print_exc()
        bot.einmal("fehler_" + type(ex).__name__, f"⚠️ Fehler im Lauf von Bot E LIVE: {str(ex)[:300]}", 3600)
        bot.speichern()


if __name__ == "__main__":
    main()
