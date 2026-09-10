"""Die drei Kandidaten-Strategien fuer Bot B. Arbeiten auf Tageskerzen.
Jede Funktion bekommt Preis- und Volumenlisten (aelteste zuerst) und gibt
'buy', 'hold' oder None zurueck fuer den letzten Tag."""

def _chg(p, n):  return p[-1] / p[-1 - n] - 1 if len(p) > n else 0
def _avg(x):     return sum(x) / len(x) if x else 0

def v1_divergenz(prices, vols):
    """Fundamentals steigen, Preis faellt: Volumen 7d vs. Vor-7d +20 %, Preis 14d < -5 %."""
    if len(prices) < 15: return None
    vol_trend = _avg(vols[-7:]) / max(_avg(vols[-14:-7]), 1) - 1
    if vol_trend > 0.20 and _chg(prices, 14) < -0.05: return "buy"
    return None

def v2_meanrev(prices, vols):
    """Ueberverkauft: 7-Tage-Drop groesser 20 %."""
    if len(prices) < 8: return None
    return "buy" if _chg(prices, 7) < -0.20 else None

def v3_momentum(prices, vols):
    """Ausbruch: Schluss ueber 30-Tage-Hoch."""
    if len(prices) < 31: return None
    return "buy" if prices[-1] > max(prices[-31:-1]) else None

def v4_meanrev_vol(prices, vols):
    """Panikverkauf: 7-Tage-Drop groesser 20 % UND Volumen 3d mind. 1,5x des 30-Tage-Schnitts."""
    if len(prices) < 31: return None
    spike = _avg(vols[-3:]) / max(_avg(vols[-30:]), 1)
    return "buy" if _chg(prices, 7) < -0.20 and spike >= 1.5 else None

def v5_meanrev_soft(prices, vols):
    """Sanfter: 7-Tage-Drop groesser 12 %."""
    if len(prices) < 8: return None
    return "buy" if _chg(prices, 7) < -0.12 else None

STRATEGIES = {"V1_divergenz": v1_divergenz, "V2_meanrev": v2_meanrev, "V3_momentum": v3_momentum,
              "V4_meanrev_vol": v4_meanrev_vol, "V5_meanrev_soft": v5_meanrev_soft}

# Ausstiegsregeln je Strategie: (take_profit, stop, max_hold_days, trailing)
EXITS = {"V1_divergenz": (0.20, -0.15, 14, None),
         "V2_meanrev":   (0.15, -0.15, 14, None),
         "V3_momentum":  (None, -0.15, 45, -0.15),
         "V4_meanrev_vol": (0.15, -0.15, 14, None),
         "V5_meanrev_soft": (0.10, -0.12, 10, None)}

def exit_signal(strategy, entry, price, peak, held_days):
    tp, stop, max_d, trail = EXITS[strategy]
    x = price / entry - 1
    if stop is not None and x <= stop: return "stop"
    if tp is not None and x >= tp: return "tp"
    if trail is not None and peak and price / peak - 1 <= trail and x > 0: return "trail"
    if held_days >= max_d: return "time"
    return None
