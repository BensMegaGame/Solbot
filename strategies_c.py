"""Bot C – Long/Short mit Hebel auf Jupiter Perps (SOL, ETH, BTC). Tageskerzen.
Rueckgabe: "long", "short" oder None. Stops sind fuer Majors eng gesetzt, damit sie
auch bei 10x (Liquidation bei ~9,5 %) deutlich vor der Liquidation greifen."""

def _chg(p, n): return p[-1] / p[-1 - n] - 1 if len(p) > n else 0
def _avg(x):    return sum(x) / len(x) if x else 0

def c1_breakout(prices, vols):
    """Ausbruch: Schluss ueber 20-Tage-Hoch -> long, unter 20-Tage-Tief -> short."""
    if len(prices) < 22: return None
    w = prices[-21:-1]
    if prices[-1] > max(w): return "long"
    if prices[-1] < min(w): return "short"
    return None

def c2_meanrev(prices, vols):
    """Gegenbewegung: +15 % in 3 Tagen -> short, -12 % in 3 Tagen -> long."""
    if len(prices) < 5: return None
    c = _chg(prices, 3)
    if c > 0.15: return "short"
    if c < -0.12: return "long"
    return None

def c3_trend_vol(prices, vols):
    """7d-Trend > +8 % / < -8 % mit Volumen > 1,3x 30d-Schnitt."""
    if len(prices) < 31: return None
    conf = _avg(vols[-7:]) / max(_avg(vols[-30:]), 1) > 1.3
    c = _chg(prices, 7)
    if conf and c > 0.08: return "long"
    if conf and c < -0.08: return "short"
    return None

def c4_ma_cross(prices, vols):
    """Gleitende Durchschnitte: 10d kreuzt 30d nach oben -> long, nach unten -> short."""
    if len(prices) < 32: return None
    f0, s0 = _avg(prices[-11:-1]), _avg(prices[-31:-1])
    f1, s1 = _avg(prices[-10:]), _avg(prices[-30:])
    if f0 <= s0 and f1 > s1: return "long"
    if f0 >= s0 and f1 < s1: return "short"
    return None

STRATEGIES = {"C1_breakout": c1_breakout, "C2_meanrev": c2_meanrev,
              "C3_trend_vol": c3_trend_vol, "C4_ma_cross": c4_ma_cross}

# (take_profit, stop, max_hold_days, trailing) – in % der Preisbewegung (ohne Hebel).
EXITS = {"C1_breakout": (None, -0.05, 20, -0.06),
         "C2_meanrev":  (0.06, -0.04, 5, None),
         "C3_trend_vol": (None, -0.05, 14, -0.07),
         "C4_ma_cross": (None, -0.06, 30, -0.08)}

def exit_signal(strategy, side, entry, price, peak, held_days):
    tp, stop, max_d, trail = EXITS[strategy]
    x = (price / entry - 1) * (1 if side == "long" else -1)
    if stop is not None and x <= stop: return "stop"
    if tp is not None and x >= tp: return "tp"
    if trail is not None and peak:
        off = (price / peak - 1) * (1 if side == "long" else -1)
        if off <= trail and x > 0: return "trail"
    if held_days >= max_d: return "time"
    return None

# ---- Dynamische Positionsgroesse ----
RISK_PCT, MAX_MARGIN_PCT, MIN_MARGIN = 0.04, 0.50, 20.0

def margin_for(strategy, lev, equity):
    """Riskiert RISK_PCT des Kapitals pro Trade: Verlust am Stop = margin * lev * |stop|.
    Enger Stop / niedriger Hebel -> groessere Margin, und umgekehrt."""
    stop = abs(EXITS[strategy][1])
    m = RISK_PCT * equity / (lev * stop)
    return max(MIN_MARGIN, min(m, MAX_MARGIN_PCT * equity))
