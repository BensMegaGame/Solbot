"""4h-Kerzen und Funding-Rates fuer SOL/ETH/BTC. Quellen: Binance, Fallback Bybit (beide kostenlos, ohne Key).
Format Kerze: {"t": ms, "o","h","l","c","v"}"""
import time, requests
from common import UA

PAIRS = {"SOL": "SOLUSDT", "ETH": "ETHUSDT", "BTC": "BTCUSDT"}
BARS_PER_DAY = 6

def _binance_klines(sym, days):
    end = int(time.time() * 1000); start = end - days * 86400000; out = []
    while start < end:
        r = requests.get("https://api.binance.com/api/v3/klines", params={"symbol": PAIRS[sym], "interval": "4h", "startTime": start, "limit": 1000}, headers=UA, timeout=20)
        if r.status_code != 200: raise RuntimeError(f"binance {r.status_code}")
        rows = r.json()
        if not rows: break
        out += [{"t": k[0], "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4]), "v": float(k[7])} for k in rows]
        start = rows[-1][0] + 1
        if len(rows) < 1000: break
        time.sleep(0.3)
    return out

def _bybit_klines(sym, days):
    end = int(time.time() * 1000); start = end - days * 86400000; out = []
    while start < end:
        r = requests.get("https://api.bybit.com/v5/market/kline", params={"category": "linear", "symbol": PAIRS[sym], "interval": "240", "start": start, "limit": 1000}, headers=UA, timeout=20)
        if r.status_code != 200: raise RuntimeError(f"bybit {r.status_code}")
        rows = sorted(r.json()["result"]["list"], key=lambda k: int(k[0]))
        if not rows: break
        out += [{"t": int(k[0]), "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4]), "v": float(k[6])} for k in rows]
        start = int(rows[-1][0]) + 1
        if len(rows) < 1000: break
        time.sleep(0.3)
    return out

def klines(sym, days=365):
    for fn in (_binance_klines, _bybit_klines):
        try:
            k = fn(sym, days)
            if len(k) > 100: return k
        except Exception as e: print("klines", sym, e)
    return None

def _binance_funding(sym, days):
    end = int(time.time() * 1000); start = end - days * 86400000; out = []
    while start < end:
        r = requests.get("https://fapi.binance.com/fapi/v1/fundingRate", params={"symbol": PAIRS[sym], "startTime": start, "limit": 1000}, headers=UA, timeout=20)
        if r.status_code != 200: raise RuntimeError(f"binance funding {r.status_code}")
        rows = r.json()
        if not rows: break
        out += [{"t": x["fundingTime"], "r": float(x["fundingRate"])} for x in rows]
        start = rows[-1]["fundingTime"] + 1
        if len(rows) < 1000: break
        time.sleep(0.3)
    return out

def _bybit_funding(sym, days):
    end = int(time.time() * 1000); start = end - days * 86400000; out = []
    while start < end:
        r = requests.get("https://api.bybit.com/v5/market/funding/history", params={"category": "linear", "symbol": PAIRS[sym], "startTime": start, "limit": 200}, headers=UA, timeout=20)
        if r.status_code != 200: raise RuntimeError(f"bybit funding {r.status_code}")
        rows = sorted(r.json()["result"]["list"], key=lambda x: int(x["fundingRateTimestamp"]))
        if not rows: break
        out += [{"t": int(x["fundingRateTimestamp"]), "r": float(x["fundingRate"])} for x in rows]
        start = int(rows[-1]["fundingRateTimestamp"]) + 1
        if len(rows) < 200: break
        time.sleep(0.3)
    return out

def funding(sym, days=365):
    for fn in (_binance_funding, _bybit_funding):
        try:
            f = fn(sym, days)
            if len(f) > 10: return f
        except Exception as e: print("funding", sym, e)
    return []

def align_funding(bars, fund):
    """Zu jeder Kerze die zuletzt bekannte 8h-Funding-Rate."""
    out, j, cur = [], 0, 0.0
    for b in bars:
        while j < len(fund) and fund[j]["t"] <= b["t"]: cur = fund[j]["r"]; j += 1
        out.append(cur)
    return out
