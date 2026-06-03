"""
HMA(55) Dual-Timeframe Trend Filter — Layer 2.

Replicates the Hull Suite from SMC Flow indicator:
  HMA(src, length) = WMA(2 * WMA(src, length/2) - WMA(src, length), round(sqrt(length)))
  Direction: HULL > HULL[2] = bullish momentum, else bearish

Both 1h AND 15m must agree for direction to lock.
If they disagree -> return None (skip this coin).
"""
import math
import logging
from typing import List, Optional
from backend.mexc.models import Candle
from backend.config import HMA_LENGTH

logger = logging.getLogger(__name__)


def _wma(values: List[float], length: int) -> List[float]:
    """Weighted Moving Average. Returns same-length list, nan-padded at start."""
    result = []
    for i in range(len(values)):
        if i < length - 1:
            result.append(float("nan"))
        else:
            weights = list(range(1, length + 1))
            window  = values[i - length + 1 : i + 1]
            wsum    = sum(w * v for w, v in zip(weights, window))
            result.append(wsum / sum(weights))
    return result


def _hma(closes: List[float], length: int) -> List[float]:
    """
    Hull Moving Average.
    HMA(n) = WMA(2*WMA(n/2) - WMA(n), sqrt(n))
    Matches Pine Script: ta.wma(2 * ta.wma(src, length/2) - ta.wma(src, length), round(sqrt(length)))
    """
    half    = max(1, length // 2)
    sqrtn   = max(1, round(math.sqrt(length)))
    wma_n   = _wma(closes, length)
    wma_h   = _wma(closes, half)
    diff    = [2 * h - n if not (math.isnan(h) or math.isnan(n)) else float("nan")
               for h, n in zip(wma_h, wma_n)]
    hma_out = _wma(diff, sqrtn)
    return hma_out


def get_hma_direction(candles: List[Candle], length: int = HMA_LENGTH) -> Optional[str]:
    """
    Returns "BULLISH", "BEARISH", or None if insufficient data.
    Direction rule: HULL[-1] > HULL[-3] (matches Pine: HULL > HULL[2])
    """
    if len(candles) < length + 5:
        return None
    closes = [c.close for c in candles]
    hma    = _hma(closes, length)
    valid  = [v for v in hma if not math.isnan(v)]
    if len(valid) < 3:
        return None
    current = valid[-1]
    two_ago = valid[-3]
    return "BULLISH" if current > two_ago else "BEARISH"


def get_dual_tf_direction(candles_1h: List[Candle],
                          candles_15m: List[Candle]) -> Optional[str]:
    """
    Returns locked direction ("LONG" or "SHORT") if both timeframes agree.
    Returns None if they disagree or data is insufficient.
    """
    dir_1h  = get_hma_direction(candles_1h)
    dir_15m = get_hma_direction(candles_15m)

    if dir_1h is None or dir_15m is None:
        return None

    if dir_1h != dir_15m:
        logger.debug(f"HMA disagreement: 1h={dir_1h}, 15m={dir_15m} -> skip")
        return None

    direction = "LONG" if dir_1h == "BULLISH" else "SHORT"
    logger.info(f"HMA locked: {direction} (1h={dir_1h}, 15m={dir_15m})")
    return direction
