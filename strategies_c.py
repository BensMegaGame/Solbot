"""Bot C – Long/Short mit Hebel auf Jupiter Perps (SOL, ETH, BTC). 4h-Kerzen.
Jede Strategie bekommt Kerzen (dicts), Funding-Liste (8h-Rate je Kerze) und den Index i der aktuellen Kerze.
Rueckgabe: ("long"|"short", stop_pct) oder None. stop_pct = Abstand des Stops in % (ATR-basiert)."""

def _sma(c, n): return sum(c[-n:]) / n
def _atr(bars, n=14):
    tr = [max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"])) for p, b in zip(bars[-n-1:-1], bars[-n:])]
    return sum(tr) / len(tr)
def _rsi(c, n=14):
    g = l = 0.0
    for a, b in zip(c[-n-1:-1], c[-n:]):
        d = b - a; g += max(d, 0); l += max(-d, 0)
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)

def c3_trend_vol(bars, fund, i):
    """Referenz aus v1: 7-Tage-Trend (42 Kerzen) > +8 %/< -8 % mit Volumen > 1,3x 30d-Schnitt."""
    if i < 181: return None
    c = [b["c"] for b in bars[:i+1]]; v = [b["v"] for b in bars[:i+1]]
    conf = sum(v[-42:]) / 42 / max(sum(v[-180:]) / 180, 1) > 1.3
    r = c[-1] / c[-43] - 1
    if conf and r > 0.08: return ("long", 0.05)
    if conf and r < -0.08: return ("short", 0.05)
    return None

def c5_squeeze(bars, fund, i):
    """Volatilitaets-Ausbruch: Bollinger-Bandbreite (20) auf 90-Kerzen-Tief, dann Schluss ausserhalb des Bands."""
    if i < 120: return None
    c = [b["c"] for b in bars[:i+1]]
    def width(k):
        w = c[k-19:k+1]; m = sum(w) / 20; sd = (sum((x - m) ** 2 for x in w) / 20) ** 0.5
        return 4 * sd / m, m, sd
    ws = [width(k)[0] for k in range(i - 90, i)]
    w, m, sd = width(i)
    if min(ws) < w * 0.9: return None            # Bandbreite muss gerade erst aus dem Tief kommen
    atr = _atr(bars[:i+1]) / c[-1]
    if c[-1] > m + 2 * sd: return ("long", max(0.02, 1.5 * atr))
    if c[-1] < m - 2 * sd: return ("short", max(0.02, 1.5 * atr))
    return None

def c6_trend_pullback(bars, fund, i):
    """Nur in Trendrichtung (SMA 120 = 20 Tage), Einstieg beim Ruecksetzer (RSI 14 < 35 bzw. > 65)."""
    if i < 130: return None
    c = [b["c"] for b in bars[:i+1]]
    sma = _sma(c, 120); rsi = _rsi(c); atr = _atr(bars[:i+1]) / c[-1]
    if c[-1] > sma * 1.02 and rsi < 35: return ("long", max(0.02, 1.5 * atr))
    if c[-1] < sma * 0.98 and rsi > 65: return ("short", max(0.02, 1.5 * atr))
    return None

def c7_funding_contrarian(bars, fund, i):
    """Gegen die Masse: 8h-Funding im Schnitt der letzten 3 Zahlungen > +0,05 % -> short, < -0,02 % -> long.
    Zusaetzlich muss der Preis in den letzten 24h in Funding-Richtung gelaufen sein (Ueberhitzung)."""
    if i < 30 or not fund: return None
    f = sum(fund[i-5:i+1]) / 6
    c = [b["c"] for b in bars[:i+1]]; r24 = c[-1] / c[-7] - 1
    atr = _atr(bars[:i+1]) / c[-1]
    if f > 0.0005 and r24 > 0.03: return ("short", max(0.02, 1.5 * atr))
    if f < -0.0002 and r24 < -0.03: return ("long", max(0.02, 1.5 * atr))
    return None

STRATEGIES = {"C3_trend_vol": c3_trend_vol, "C5_squeeze": c5_squeeze,
              "C6_trend_pullback": c6_trend_pullback, "C7_funding": c7_funding_contrarian}

# (take_profit als Vielfaches des Stop-Abstands, max_hold in Kerzen (4h), trailing als Vielfaches des Stop-Abstands)
EXITS = {"C3_trend_vol": (None, 84, 1.4), "C5_squeeze": (None, 60, 1.5),
         "C6_trend_pullback": (2.5, 90, 2.0), "C7_funding": (2.0, 18, None)}

def exit_signal(strategy, side, entry, price, peak, held_bars, stop_pct):
    tp_mult, max_bars, trail_mult = EXITS[strategy]
    x = (price / entry - 1) * (1 if side == "long" else -1)
    if x <= -stop_pct: return "stop"
    if tp_mult and x >= tp_mult * stop_pct: return "tp"
    if trail_mult and peak:
        off = (price / peak - 1) * (1 if side == "long" else -1)
        if off <= -trail_mult * stop_pct and x > 0: return "trail"
    if held_bars >= max_bars: return "time"
    return None

# ---- Dynamische Positionsgroesse ----
MAX_MARGIN_PCT, MIN_MARGIN = 0.50, 20.0

def margin_for(risk_pct, lev, equity, stop_pct):
    """Verlust am Stop = risk_pct * equity. margin = risk / (lev * stop)."""
    m = risk_pct * equity / (lev * stop_pct)
    return max(MIN_MARGIN, min(m, MAX_MARGIN_PCT * equity))
