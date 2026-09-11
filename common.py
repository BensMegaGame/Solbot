"""Gemeinsame Bausteine: Konfiguration, Daten, Fee-Modell, Paper-Executor."""
import json, os, time, requests
from datetime import datetime, timezone

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA, exist_ok=True)

START_CAPITAL = 500.0          # USD pro Bot
SWAP_FEE      = 0.003          # 0,3 % Jupiter/Raydium
GAS_USD       = 0.02           # pro Transaktion inkl. Priority Fee
DS = "https://api.dexscreener.com"
UA = {"User-Agent": "solbot-paper/1.0"}

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

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
    def __init__(self, bot):
        self.bot = bot
        self.file = f"{bot}_state.json"
        self.s = load(self.file, {"cash": START_CAPITAL, "positions": {}, "trades": [],
                                   "equity": [], "started": now_iso()})

    def buy(self, sym, addr, price, usd, liquidity, reason):
        if usd > self.s["cash"] or usd <= 0: return False
        slip = slippage_pct(usd, liquidity)
        cost = usd * (1 + SWAP_FEE + slip) + GAS_USD
        if cost > self.s["cash"]: usd = (self.s["cash"] - GAS_USD) / (1 + SWAP_FEE + slip); cost = self.s["cash"]
        qty = usd / (price * (1 + slip))
        self.s["cash"] -= cost
        pos = self.s["positions"].get(addr, {"sym": sym, "qty": 0, "cost": 0, "opened": now_iso(), "peak": price})
        pos["qty"] += qty; pos["cost"] += cost; pos["entry"] = pos["cost"] / pos["qty"]
        self.s["positions"][addr] = pos
        self.s["trades"].append({"t": now_iso(), "side": "buy", "sym": sym, "addr": addr,
                                 "price": price, "usd": round(cost, 2), "slip": round(slip, 4), "reason": reason})
        return True

    def sell(self, addr, price, frac, liquidity, reason):
        pos = self.s["positions"].get(addr)
        if not pos: return False
        qty = pos["qty"] * frac
        gross = qty * price
        slip = slippage_pct(gross, liquidity)
        net = gross * (1 - SWAP_FEE - slip) - GAS_USD
        self.s["cash"] += max(net, 0)
        cost_part = pos["cost"] * frac
        pos["qty"] -= qty; pos["cost"] -= cost_part
        pnl = net - cost_part
        self.s["trades"].append({"t": now_iso(), "side": "sell", "sym": pos["sym"], "addr": addr,
                                 "price": price, "usd": round(net, 2), "pnl": round(pnl, 2),
                                 "frac": frac, "reason": reason})
        if pos["qty"] <= 1e-9 or frac >= 0.999:
            del self.s["positions"][addr]
        return True

    def mark(self, prices):
        val = self.s["cash"] + sum(p["qty"] * prices.get(a, p["entry"]) for a, p in self.s["positions"].items())
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
        val = self.s["cash"] + sum(p["margin"] + self.pnl(p, prices.get(k, p["entry"])) for k, p in self.s["positions"].items())
        self.s["equity"].append({"t": now_iso(), "v": round(val, 2)}); self.s["equity"] = self.s["equity"][-5000:]
        return val

    def commit(self): save(self.file, self.s)
