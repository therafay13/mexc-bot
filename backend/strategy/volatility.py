from typing import List
from backend.mexc.models import Candle
from backend.config import ATR_PCT_MIN, ATR_PCT_MAX


def calculate_atr(candles: List[Candle], period=14) -> float:
    if len(candles) < period + 1: return 0.0
    trs = [max(candles[i].high - candles[i].low,
               abs(candles[i].high - candles[i-1].close),
               abs(candles[i].low  - candles[i-1].close))
           for i in range(1, len(candles))]
    return sum(trs[-period:]) / period


def get_volatility_score(candles: List[Candle]) -> dict:
    atr     = calculate_atr(candles)
    price   = candles[-1].close
    atr_pct = atr / price if price else 0
    in_range = ATR_PCT_MIN <= atr_pct <= ATR_PCT_MAX
    return {"atr": atr, "atr_pct": atr_pct, "in_range": in_range,
            "score": 5 if in_range else 0}
