"""Gemeinsame Bausteine: Konfiguration, Daten, Fee-Modell, Paper-Executor."""
import json, os, time, calendar, requests
from datetime import datetime, timezone

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA, exist_ok=True)

START_CAPITAL = 500.0          # USD pro Bot
SWAP_FEE      = 0.003          # 0,3 % Jupiter/Raydium
GAS_USD       = 0.02           # pro Transaktion inkl. Priority Fee
DS = "https://api.dexscreener.com"
UA = {"User-Agent": "solbot-paper/1.0"}

# ---- Jupiter (echte Fill-Preise, auch im Paper-Modus) ----
JUP_KEY    = os.environ.get("JUP_KEY")                     # kostenlos: https://portal.jup.ag
HELIUS_KEY = os.environ.get("HELIUS_KEY")
JUP_QUOTE  = "https://api.jup.ag/swap/v1/quote"
USDC       = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
MAX_IMPACT = 0.10          # Kauf ablehnen, wenn Jupiter > 10 % Price-Impact meldet (macht ein echter Bot auch)
BUY_DEVIATION = 1.25       # v2.8: KAUF-Sicherung. Jupiter ist die Wahrheit (dort wird gefuellt), DexScreener kann
                           # bei frisch migrierten Tokens stark hinterherhinken. Weicht der Fill um mehr als 25 %
                           # vom Referenzkurs ab, passen die beiden Quellen nicht zusammen: der Einstand waere
                           # dann gegen einen falschen Kurs gemessen und jede spaetere Prozentangabe verfaelscht
                           # (SOF: Fill 0,000245 vs. Kurs 0,00054 -> Position stand sofort "bei 2x", ohne dass
                           # sich der Markt bewegt hatte, und der 3x-Ausstieg loeste bei echten 2x aus).
                           # Beim Kauf ist Aussetzen gratis - es gibt immer einen naechsten Kandidaten.
MAX_DEVIATION = 6          # Sicherung gegen kaputte Quotes: weicht der Jupiter-Fill um mehr als das 6-fache vom
                           # zuletzt bekannten Kurs ab (z.B. Route ueber einen fast leeren Pool), wird NICHT gehandelt.
STALE_ZERO_H = 6           # Position ohne Kurs seit > 6 h -> mit 0 bewerten (tot/gerugt), nicht mit Einstand

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def parse_iso(s):
    """ISO-UTC-String -> Unix-Zeit. (time.mktime waere lokale Zeit -> auf CET-Server 1-2 h falsch.)"""
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))

def held_seconds(pos):
    return time.time() - parse_iso(pos["opened"])

RC = "https://api.rugcheck.xyz/v1/tokens"
HARD_RISKS = ("mint", "freeze", "unlocked", "top 10", "single holder")

def rug_ok(addr):
    """(ok, risks). Fail-CLOSED: wenn Rugcheck nicht antwortet -> (False, ['rugcheck unavailable'])."""
    s = get(f"{RC}/{addr}/report/summary")
    if not s: return False, ["rugcheck unavailable"]
    risks = [r.get("name", "") for r in (s.get("risks") or [])]
    return not any(k in r.lower() for r in risks for k in HARD_RISKS), risks

def get(url, params=None, retries=3):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=20)
            if r.status_code == 429:
                time.sleep(15 * (i + 1)); continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if i == retries - 1:
                print("fetch failed:", url, e)
                return None
            time.sleep(3)

def load(name, default):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        with open(p) as f: return json.load(f)
    return default

def save(name, obj):
    with open(os.path.join(DATA, name), "w") as f:
        json.dump(obj, f, indent=1)

def append_jsonl(name, obj):
    with open(os.path.join(DATA, name), "a") as f:
        f.write(json.dumps(obj) + "\n")

def rpc(method, params):
    url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}" if HELIUS_KEY else "https://api.mainnet-beta.solana.com"
    r = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers=UA, timeout=20)
    r.raise_for_status(); return r.json().get("result")

def token_decimals(mint):
    """Dezimalstellen eines Mints (fuer Jupiter-Rohbetraege). Gecacht in data/decimals.json."""
    cache = load("decimals.json", {})
    if mint in cache: return cache[mint]
    try: dec = int(rpc("getTokenSupply", [mint])["value"]["decimals"])
    except Exception as e:
        print("decimals failed:", mint, e); return None
    cache[mint] = dec; save("decimals.json", cache); return dec

def jup_quote(input_mint, output_mint, amount_raw):
    """Echte Jupiter-Quote. Gibt (out_raw, impact) oder None (kein Key / keine Route / Fehler)."""
    if not JUP_KEY or amount_raw <= 0: return None
    try:
        r = requests.get(JUP_QUOTE, params={"inputMint": input_mint, "outputMint": output_mint, "amount": int(amount_raw),
                                            "slippageBps": 100, "restrictIntermediateTokens": "true"},
                         headers={"x-api-key": JUP_KEY, **UA}, timeout=15)
        if r.status_code != 200:
            print("jup quote", r.status_code, r.text[:120]); return None
        q = r.json()
        return int(q["outAmount"]), abs(float(q.get("priceImpactPct") or 0))
    except Exception as e:
        print("jup quote failed:", e); return None

def slippage_pct(order_usd, liquidity_usd):
    """Grobe Schätzung: Preisimpact ~ Order / Liquidität, mindestens 0,2 %."""
    if not liquidity_usd:
        return 0.05
    return max(0.002, min(0.15, order_usd / liquidity_usd))

def solana_pair(addr):
    d = get(f"{DS}/latest/dex/tokens/{addr}")
    pairs = [p for p in ((d or {}).get("pairs") or []) if p.get("chainId") == "solana"]
    if not pairs: return None
    return max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0))

class Paper:
    """Simuliertes Portfolio. buy()/sell() sind die einzigen Stellen, die
    fuer echte Trades ausgetauscht werden muessten (Jupiter Swap API)."""
    def __init__(self, bot, quotes=True):
        self.bot = bot; self.quotes = quotes      # quotes=False: Token ohne Jupiter-Route (z.B. Bonding Curve)
        self.file = f"{bot}_state.json"
        self.s = load(self.file, {"cash": START_CAPITAL, "positions": {}, "trades": [],
                                   "equity": [], "started": now_iso()})

    def buy(self, sym, addr, price, usd, liquidity, reason):
        if usd > self.s["cash"] or usd <= 0: return False
        src = "est"
        q = None
        if self.quotes and JUP_KEY:
            dec = token_decimals(addr)
            q = jup_quote(USDC, addr, usd * 1e6) if dec is not None else None
            if q is None:
                print(f"  {sym}: keine Jupiter-Route/Quote -> kein Kauf"); return False
            out_raw, impact = q
            if impact > MAX_IMPACT or out_raw <= 0:
                print(f"  {sym}: impact {impact:.1%} > {MAX_IMPACT:.0%} -> kein Kauf"); return False
            qty = out_raw / 10 ** dec
            fill_px = usd / qty if qty else 0
            if price and fill_px and not (price / BUY_DEVIATION <= fill_px <= price * BUY_DEVIATION):
                print(f"  {sym}: Jupiter-Fill {fill_px:.8g} weicht {fill_px/price-1:+.0%} von Kurs {price:.8g} ab -> kein Kauf")
                return False
            cost = usd + GAS_USD                       # Jupiter-Out enthaelt schon DEX-Fees + Impact
            if cost > self.s["cash"]: return False
            slip = impact; src = "jup"
        else:
            slip = slippage_pct(usd, liquidity)
            cost = usd * (1 + SWAP_FEE + slip) + GAS_USD
            if cost > self.s["cash"]: usd = (self.s["cash"] - GAS_USD) / (1 + SWAP_FEE + slip); cost = self.s["cash"]
            qty = usd / (price * (1 + slip))
        self.s["cash"] -= cost
        pos = self.s["positions"].get(addr, {"sym": sym, "qty": 0, "cost": 0, "opened": now_iso(), "peak": price})
        pos["qty"] += qty; pos["cost"] += cost; pos["entry"] = pos["cost"] / pos["qty"]
        self.s["positions"][addr] = pos
        self.s["trades"].append({"t": now_iso(), "side": "buy", "sym": sym, "addr": addr,
                                 "price": price, "fill": round(cost / qty, 12), "usd": round(cost, 2), "slip": round(slip, 4),
                                 "quote": src, "reason": reason})
        return True

    def sell(self, addr, price, frac, liquidity, reason):
        pos = self.s["positions"].get(addr)
        if not pos: return False
        qty = pos["qty"] * frac
        src = "est"
        if self.quotes and JUP_KEY:
            dec = token_decimals(addr)
            q = jup_quote(addr, USDC, qty * 10 ** dec) if dec is not None else None
            if q is None:
                pos["no_route"] = now_iso(); print(f"  {pos['sym']}: keine Jupiter-Route -> Verkauf nicht moeglich")
                return False
            out_raw, slip = q
            fill_px = (out_raw / 1e6) / qty if qty else 0
            ref = pos.get("cur_price") or price
            # v2.8: Beim VERKAUF wird eine Abweichung nur protokolliert, nicht blockiert. Ein Stop, der wegen einer
            # Kursabweichung nicht ausgeloest wird, laesst die Position ungeschuetzt weiterlaufen - genau das Risiko,
            # gegen das der Stop existiert. Nur voellig absurde Quotes (>6x) werden weiter verworfen, denn die sind
            # nachweislich kaputt und nicht bloss ungenau.
            if ref and fill_px and not (ref / BUY_DEVIATION <= fill_px <= ref * BUY_DEVIATION):
                print(f"  {pos['sym']}: Verkauf-Fill {fill_px:.8g} weicht {fill_px/ref-1:+.0%} von Kurs {ref:.8g} ab - wird trotzdem ausgefuehrt")
            if ref and fill_px and not (ref / MAX_DEVIATION <= fill_px <= ref * MAX_DEVIATION):
                print(f"  {pos['sym']}: Jupiter-Fill {fill_px:.8g} weicht >{MAX_DEVIATION}x von Kurs {ref:.8g} ab -> verworfen (kaputte Quote?)")
                return False
            net = out_raw / 1e6 - GAS_USD; src = "jup"
            pos.pop("no_route", None)
        else:
            gross = qty * price
            slip = slippage_pct(gross, liquidity)
            net = gross * (1 - SWAP_FEE - slip) - GAS_USD
        self.s["cash"] += max(net, 0)
        cost_part = pos["cost"] * frac
        pos["qty"] -= qty; pos["cost"] -= cost_part
        pnl = net - cost_part
        peak_x = round(max(pos.get("peak", price), price) / pos["entry"], 3) if pos.get("entry") else None
        self.s["trades"].append({"t": now_iso(), "side": "sell", "sym": pos["sym"], "addr": addr,
                                 "price": price, "usd": round(net, 2), "pnl": round(pnl, 2), "peak_x": peak_x,
                                 "held_h": round(held_seconds(pos) / 3600, 1),
                                 "frac": frac, "slip": round(slip, 4), "quote": src, "reason": reason})
        if pos["qty"] <= 1e-9 or frac >= 0.999:
            del self.s["positions"][addr]
        return True

    def mark(self, prices):
        """Equity. Ohne aktuellen Kurs: letzter bekannter Kurs; nach STALE_ZERO_H ohne Kurs -> 0 (tot).
        Nie der Einstandspreis - der wuerde einen Rug als Break-even tarnen."""
        now = time.time(); val = self.s["cash"]
        for a, p in self.s["positions"].items():
            if a in prices and prices[a] > 0:
                p["cur_price"] = prices[a]; p.pop("no_quote_since", None)
            else:
                p.setdefault("no_quote_since", now_iso())
            stale_h = (now - parse_iso(p["no_quote_since"])) / 3600 if p.get("no_quote_since") else 0
            px = 0.0 if stale_h > STALE_ZERO_H else p.get("cur_price", 0.0)
            p["mark"] = px; val += p["qty"] * px
        self.s["equity"].append({"t": now_iso(), "v": round(val, 2)})
        self.s["equity"] = self.s["equity"][-5000:]
        return val

    def commit(self):
        save(self.file, self.s)


# ---------------- Perpetuals (Bot C) ----------------
PERP_FEE     = 0.0006    # Jupiter Perps: 0,06 % oeffnen + 0,06 % schliessen
PERP_SLIP    = 0.0005    # JLP-Pool: praktisch kein Slippage auf SOL/ETH/BTC, kleiner Puffer
FUNDING_DAY  = 0.0024    # Jupiter Borrow-Fee ~0,01 %/Stunde auf die volle Positionsgroesse (beide Seiten zahlen)
MAINT_MARGIN = 0.05      # Liquidation, wenn Verlust >= Margin * (1 - 5 %)

def liq_price(entry, side, lev):
    move = (1 - MAINT_MARGIN) / lev
    return entry * (1 - move) if side == "long" else entry * (1 + move)

class PaperPerp:
    """Simulierte Perp-Positionen: long/short, isolierte Margin, Liquidation, Funding.
    open()/close() sind die Stellen, die fuer echte Trades (Drift SDK) ersetzt wuerden."""
    def __init__(self, bot):
        self.bot = bot; self.file = f"{bot}_state.json"
        self.s = load(self.file, {"cash": START_CAPITAL, "positions": {}, "trades": [],
                                   "equity": [], "started": now_iso(), "liquidations": 0})

    def open(self, sym, key, side, price, margin, lev, reason):
        if margin > self.s["cash"] or margin <= 0 or key in self.s["positions"]: return False
        fill = price * (1 + PERP_SLIP) if side == "long" else price * (1 - PERP_SLIP)
        size = margin * lev; fee = size * PERP_FEE
        self.s["cash"] -= margin + fee
        self.s["positions"][key] = {"sym": sym, "side": side, "lev": lev, "entry": fill, "size": size,
                                     "qty": size / fill, "margin": margin, "liq": liq_price(fill, side, lev),
                                     "opened": now_iso(), "peak": fill, "funding": 0.0, "last_mark": time.time()}
        self.s["trades"].append({"t": now_iso(), "side": "open_" + side, "sym": sym, "addr": key, "price": fill,
                                 "usd": round(margin, 2), "lev": lev, "reason": reason})
        return True

    def pnl(self, p, price):
        d = (price - p["entry"]) / p["entry"]
        return p["size"] * (d if p["side"] == "long" else -d) - p["funding"]

    def close(self, key, price, reason):
        p = self.s["positions"].get(key)
        if not p: return False
        fill = price * (1 - PERP_SLIP) if p["side"] == "long" else price * (1 + PERP_SLIP)
        if reason == "liquidation":
            net = 0.0; self.s["liquidations"] += 1
        else:
            net = max(p["margin"] + self.pnl(p, fill) - p["size"] * PERP_FEE, 0.0)
        self.s["cash"] += net
        self.s["trades"].append({"t": now_iso(), "side": "close_" + p["side"], "sym": p["sym"], "addr": key, "price": fill,
                                 "usd": round(net, 2), "pnl": round(net - p["margin"], 2), "lev": p["lev"], "reason": reason})
        del self.s["positions"][key]
        return True

    def mark(self, prices):
        """Funding abrechnen, Liquidationen pruefen, Equity schreiben."""
        now = time.time()
        for key, p in list(self.s["positions"].items()):
            px = prices.get(key)
            if not px: continue
            days = (now - p.get("last_mark", now)) / 86400
            p["funding"] += p["size"] * FUNDING_DAY * days   # Borrow-Fee, zahlen long und short
            p["last_mark"] = now
            p["peak"] = max(p["peak"], px) if p["side"] == "long" else min(p["peak"], px)
            hit = px <= p["liq"] if p["side"] == "long" else px >= p["liq"]
            if hit or p["margin"] + self.pnl(p, px) <= 0: self.close(key, px, "liquidation")
        for k, p in self.s["positions"].items():
            if k in prices: p["cur_price"] = prices[k]
        val = self.s["cash"] + sum(p["margin"] + self.pnl(p, prices.get(k, p["entry"])) for k, p in self.s["positions"].items())
        self.s["equity"].append({"t": now_iso(), "v": round(val, 2)}); self.s["equity"] = self.s["equity"][-5000:]
        return val

    def commit(self): save(self.file, self.s)
