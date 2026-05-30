"""
Smart Money Concepts components.

ORDER BLOCK:
  - Find large impulse candles (body > 1.5x average body size)
  - Walk backwards from impulse start to find last opposite-color candle
  - That candle's body (open to close range) = the Order Block zone
  - Price returning to this zone = institutional re-entry opportunity
  - OB is invalidated if price later closes through it in the opposite direction

FAIR VALUE GAP (FVG):
  - 3-candle pattern where candle[i-1] and candle[i+1] don't overlap
  - Bullish FVG: candle[i-1].high < candle[i+1].low  → gap in upward move
  - Bearish FVG: candle[i-1].low  > candle[i+1].high → gap in downward move
  - Price is "drawn" to fill these gaps (acts like a magnet)

LIQUIDITY LEVELS:
  - Equal Highs (EQH): 2+ swing highs within 0.1% of each other
    → buy-side liquidity sitting above (retail stop-losses clustered there)
  - Equal Lows (EQL): 2+ swing lows within 0.1% of each other
    → sell-side liquidity sitting below
  - A SWEEP = price wicks beyond the level but closes back inside
    (institutions triggered those stops to get a better fill price)

PREMIUM / DISCOUNT:
  - Range = last significant swing high to swing low
  - Midpoint (50%) = equilibrium
  - Below midpoint = DISCOUNT → ideal for LONGS (buying cheap)
  - Above midpoint = PREMIUM  → ideal for SHORTS (selling expensive)
"""
from typing import List, Tuple, Optional
from backend.mexc.models import Candle, OrderBlock, FairValueGap, LiquidityLevel, SwingPoint


def find_order_blocks(candles: List[Candle], direction: str) -> List[OrderBlock]:
    if len(candles) < 10: return []
    avg_body = sum(abs(c.close - c.open) for c in candles[-50:]) / max(len(candles[-50:]), 1)
    obs = []
    for i in range(3, len(candles) - 1):
        c    = candles[i]
        body = abs(c.close - c.open)
        bull_impulse = c.close > c.open and body > avg_body * 1.5
        bear_impulse = c.close < c.open and body > avg_body * 1.5
        if direction == "BULLISH" and bull_impulse:
            for j in range(i-1, max(i-10, 0), -1):
                if candles[j].close < candles[j].open:
                    obs.append(OrderBlock(
                        ob_high=max(candles[j].open, candles[j].close),
                        ob_low =min(candles[j].open, candles[j].close),
                        direction="BULLISH", timestamp=candles[j].timestamp)); break
        elif direction == "BEARISH" and bear_impulse:
            for j in range(i-1, max(i-10, 0), -1):
                if candles[j].close > candles[j].open:
                    obs.append(OrderBlock(
                        ob_high=max(candles[j].open, candles[j].close),
                        ob_low =min(candles[j].open, candles[j].close),
                        direction="BEARISH", timestamp=candles[j].timestamp)); break

    current = candles[-1].close
    for ob in obs:
        if ob.direction == "BULLISH" and current < ob.ob_low:   ob.valid = False
        if ob.direction == "BEARISH" and current > ob.ob_high:  ob.valid = False
    return sorted([o for o in obs if o.valid], key=lambda o: o.timestamp, reverse=True)[:3]


def find_fair_value_gaps(candles: List[Candle]) -> List[FairValueGap]:
    fvgs, current = [], candles[-1].close
    for i in range(1, len(candles) - 1):
        p, m, n = candles[i-1], candles[i], candles[i+1]
        if p.high < n.low:
            fvgs.append(FairValueGap(fvg_high=n.low,  fvg_low=p.high, direction="BULLISH",
                                      timestamp=m.timestamp, filled=(current <= p.high)))
        elif p.low > n.high:
            fvgs.append(FairValueGap(fvg_high=p.low,  fvg_low=n.high, direction="BEARISH",
                                      timestamp=m.timestamp, filled=(current >= p.low)))
    return [f for f in fvgs if not f.filled][-5:]


def find_liquidity_levels(swings: List[SwingPoint], tol=0.001) -> List[LiquidityLevel]:
    levels = []
    for kind, swing_kinds in [("EQH", ("HH","LH")), ("EQL", ("LL","HL"))]:
        prices = [s.price for s in swings if s.kind in swing_kinds]
        used   = set()
        for i, p1 in enumerate(prices):
            if i in used: continue
            group = [p1]
            for j, p2 in enumerate(prices):
                if j != i and j not in used and abs(p1-p2)/max(p1, 1e-8) <= tol:
                    group.append(p2); used.add(j)
            if len(group) >= 2:
                levels.append(LiquidityLevel(price=sum(group)/len(group), kind=kind))
            used.add(i)
    return levels


def detect_liquidity_sweep(candles: List[Candle],
                             levels: List[LiquidityLevel],
                             lookback=5) -> Tuple[bool, Optional[str]]:
    for c in candles[-lookback:]:
        for lv in levels:
            if lv.kind == "EQH" and c.high > lv.price and c.close < lv.price:
                return True, "BUY_SIDE"
            if lv.kind == "EQL" and c.low  < lv.price and c.close > lv.price:
                return True, "SELL_SIDE"
    return False, None


def get_premium_discount(swings: List[SwingPoint], current_price: float) -> str:
    highs = [s.price for s in swings if s.kind in ("HH","LH")]
    lows  = [s.price for s in swings if s.kind in ("LL","HL")]
    if not highs or not lows: return "EQUILIBRIUM"
    hi, lo = max(highs[-3:]), min(lows[-3:])
    if hi <= lo: return "EQUILIBRIUM"
    return "DISCOUNT" if current_price < (hi+lo)/2 else "PREMIUM"
