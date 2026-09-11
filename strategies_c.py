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

# ======== Runde 2: Regime-Filter + kreative Strategien ========
CONTEXT = {}   # backtest/bot setzen hier {"data": {sym: {"bars":[...]}}, "sym": aktuelles Symbol}

def _efficiency(c, n=30):
    """Kaufman Efficiency Ratio: 1 = glatter Trend, 0 = Gezappel."""
    net = abs(c[-1] - c[-1-n]); path = sum(abs(a - b) for a, b in zip(c[-n-1:-1], c[-n:]))
    return net / path if path else 0

def c6r_pullback_regime(bars, fund, i):
    """C6, aber nur wenn der Markt tatsaechlich trendet (Efficiency Ratio > 0,3 ueber 30 Kerzen)."""
    if i < 130: return None
    c = [b["c"] for b in bars[:i+1]]
    if _efficiency(c) < 0.30: return None
    return c6_trend_pullback(bars, fund, i)

def c8_pair_relative(bars, fund, i):
    """Marktneutral: SOL vs. BTC. Wer in 5 Tagen (30 Kerzen) relativ staerker war, wird long, der andere short.
    Nur bei klarem Abstand (> 6 %). ETH bleibt aussen vor."""
    d = CONTEXT.get("data"); sym = CONTEXT.get("sym")
    if not d or sym not in ("SOL", "BTC") or "SOL" not in d or "BTC" not in d or i < 40: return None
    rs = d["SOL"]["bars"][i]["c"] / d["SOL"]["bars"][i-30]["c"] - d["BTC"]["bars"][i]["c"] / d["BTC"]["bars"][i-30]["c"]
    if abs(rs) < 0.06: return None
    c = [b["c"] for b in bars[:i+1]]; atr = _atr(bars[:i+1]) / c[-1]; stop = max(0.02, 1.5 * atr)
    if sym == "SOL": return ("long", stop) if rs > 0 else ("short", stop)
    return ("short", stop) if rs > 0 else ("long", stop)

def c9_cascade_fade(bars, fund, i):
    """Liquidations-Kaskade: eine 4h-Kerze mit Spanne > 2,5 ATR und Volumen > 2x Schnitt uebertreibt meist.
    Gegen die Kerze handeln, kurz halten."""
    if i < 60: return None
    b = bars[i]; c = [x["c"] for x in bars[:i+1]]; atr = _atr(bars[:i]) 
    vol_avg = sum(x["v"] for x in bars[i-30:i]) / 30
    if (b["h"] - b["l"]) < 2.5 * atr or b["v"] < 2 * vol_avg: return None
    stop = max(0.015, 1.0 * atr / c[-1])
    if b["c"] < b["o"] and (b["o"] - b["c"]) / b["o"] > 0.03: return ("long", stop)
    if b["c"] > b["o"] and (b["c"] - b["o"]) / b["o"] > 0.03: return ("short", stop)
    return None

def c10_weekend_fade(bars, fund, i):
    """Wochenend-Bewegungen entstehen auf duenner Liquiditaet. Montag 00:00 UTC: wenn Sa+So > 2,5 % bewegt, dagegen."""
    import datetime as dt
    b = bars[i]; t = dt.datetime.utcfromtimestamp(b["t"] / 1000)
    if not (t.weekday() == 0 and t.hour == 0) or i < 20: return None    # erste Montagskerze
    move = bars[i-1]["c"] / bars[i-13]["c"] - 1                          # Freitag 24:00 -> Sonntag 24:00 (12 Kerzen)
    if abs(move) < 0.025: return None
    c = [x["c"] for x in bars[:i+1]]; stop = max(0.015, 1.2 * _atr(bars[:i+1]) / c[-1])
    return ("short", stop) if move > 0 else ("long", stop)

STRATEGIES.update({"C6r_pullback_regime": c6r_pullback_regime, "C8_pair_SOL_BTC": c8_pair_relative,
                   "C9_cascade_fade": c9_cascade_fade, "C10_weekend_fade": c10_weekend_fade})
EXITS.update({"C6r_pullback_regime": (2.5, 90, 2.0), "C8_pair_SOL_BTC": (2.0, 60, None),
              "C9_cascade_fade": (1.5, 12, None), "C10_weekend_fade": (1.5, 18, None)})
# Runde 1 zum Vergleich auf die zwei Besten reduzieren
for k in ("C5_squeeze", "C7_funding"): STRATEGIES.pop(k, None)
