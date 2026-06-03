"""
Structure Sanity Check — Layer 4.

Role: Reject only if Kronos direction is in CLEAR violent opposition to market.
Does NOT generate trade direction. Direction comes from Kronos.

Definition of "violent opposition":
  Kronos says LONG  AND 1h has clear LL+LH sequence with NO CHoCH toward bullish
  Kronos says SHORT AND 1h has clear HH+HL sequence with NO CHoCH toward bearish

Everything else is ALLOWED:
  - HTF trending with Kronos        (best case)
  - HTF ranging or consolidating    (Kronos saw the breakout coming)
  - HTF in early CHoCH              (Kronos caught the reversal early)
  - HTF pullback within main trend  (THIS fixes the HYPEUSDT case)
  - HTF slightly against Kronos     (Kronos has more data + AI — trust it)

Swing detection is still used to find OB/FVG/liquidity levels for entry.
"""
from typing import List
from backend.mexc.models import Candle, SwingPoint
from backend.config import SWING_LOOKBACK


def detect_swing_points(candles: List[Candle], n=SWING_LOOKBACK) -> List[SwingPoint]:
    """Detect swing highs and lows. Used for SMC component detection."""
    if len(candles) < n * 2 + 5:
        return []
    raw_h, raw_l = [], []
    for i in range(n, len(candles) - n):
        c = candles[i]
        if all(c.high >= candles[j].high for j in range(i-n, i+n+1) if j != i):
            raw_h.append((i, c.high, c.timestamp))
        if all(c.low  <= candles[j].low  for j in range(i-n, i+n+1) if j != i):
            raw_l.append((i, c.low,  c.timestamp))

    swings, prev = [], None
    for _, price, ts in raw_h:
        kind = "HH" if (prev is None or price > prev) else "LH"
        swings.append(SwingPoint(price=price, timestamp=ts, kind=kind))
        prev = price
    prev = None
    for _, price, ts in raw_l:
        kind = "LL" if (prev is None or price < prev) else "HL"
        swings.append(SwingPoint(price=price, timestamp=ts, kind=kind))
        prev = price
    return sorted(swings, key=lambda s: s.timestamp)



