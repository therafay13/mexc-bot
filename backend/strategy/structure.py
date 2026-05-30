"""
Market Structure Shift (MSS) detection on OHLCV candle data.

CONCEPTS EXPLAINED IN CODE TERMS:

Swing High at index i:
  candles[i].high is the highest value in candles[i-N .. i+N]
  (N = SWING_LOOKBACK, default 3)

Swing Low at index i:
  candles[i].low is the lowest value in candles[i-N .. i+N]

Classification of swing points relative to the previous same-type swing:
  New swing_high > prev swing_high → HH (Higher High)  — uptrend
  New swing_high < prev swing_high → LH (Lower High)   — downtrend
  New swing_low  < prev swing_low  → LL (Lower Low)    — downtrend
  New swing_low  > prev swing_low  → HL (Higher Low)   — uptrend

Trend determination:
  UPTREND   = series has HH + HL (ascending staircase)
  DOWNTREND = series has LL + LH (descending staircase)
  RANGING   = mixed or insufficient data

CHoCH (Change of Character) — first sign of reversal:
  Bullish CHoCH: in a DOWNTREND, current close > most recent LH price
  Bearish CHoCH: in an UPTREND,  current close < most recent HL price

BOS (Break of Structure) — trend continuation:
  Bullish BOS: close > most recent confirmed HH
  Bearish BOS: close < most recent confirmed LL

MSS (Market Structure Shift) — confirmed reversal:
  Bullish MSS = Bullish CHoCH occurred AND price has since confirmed Bullish BOS
  Bearish MSS = Bearish CHoCH occurred AND price has since confirmed Bearish BOS
"""
from typing import List
from backend.mexc.models import Candle, SwingPoint
from backend.config import SWING_LOOKBACK


def detect_swing_points(candles: List[Candle], n=SWING_LOOKBACK) -> List[SwingPoint]:
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


def detect_market_structure(candles: List[Candle]) -> dict:
    swings = detect_swing_points(candles)
    base   = {"trend": "RANGING", "mss_confirmed": False, "mss_direction": None,
              "choch_level": None, "last_hh": None, "last_hl": None,
              "last_ll": None,     "last_lh": None, "swings": swings}
    if not swings:
        return base

    last = {k: None for k in ["HH","HL","LL","LH"]}
    for s in swings: last[s.kind] = s.price

    has_up   = last["HH"] and last["HL"]
    has_down = last["LL"] and last["LH"]
    trend    = "UPTREND" if (has_up and (not has_down or last["HH"] > (last["LL"] or 0))) \
               else "DOWNTREND" if has_down else "RANGING"

    current = candles[-1].close
    choch_dir = choch_lvl = None
    if trend == "DOWNTREND" and last["LH"] and current > last["LH"]:
        choch_dir, choch_lvl = "BULLISH", last["LH"]
    elif trend == "UPTREND" and last["HL"] and current < last["HL"]:
        choch_dir, choch_lvl = "BEARISH", last["HL"]

    mss = mss_dir = False, None
    if choch_dir == "BULLISH" and last["HH"] and current > last["HH"]:
        mss, mss_dir = True, "BULLISH"
    elif choch_dir == "BEARISH" and last["LL"] and current < last["LL"]:
        mss, mss_dir = True, "BEARISH"

    return {**base, "trend": trend, "mss_confirmed": mss, "mss_direction": mss_dir,
            "choch_level": choch_lvl, "last_hh": last["HH"], "last_hl": last["HL"],
            "last_ll": last["LL"],    "last_lh": last["LH"], "swings": swings}
