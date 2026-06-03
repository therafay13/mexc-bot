"""
Volume-Pivot Order Block Detection — replicates LuxAlgo OB from SMC Flow indicator.

lengthOB = 5  (Volume Pivot Length setting)
upper = ta.highest(lengthOB)
lower = ta.lowest(lengthOB)
os := high[lengthOB] > upper ? 0 : low[lengthOB] < lower ? 1 : os[1]
phv = ta.pivothigh(volume, lengthOB, lengthOB)
Bullish OB: phv_detected AND os == 1 -> top=hl2, bottom=low
Bearish OB: phv_detected AND os == 0 -> top=high, bottom=hl2
Mitigation (Wick): Bullish OB mitigated when low < ob_bottom
                    Bearish OB mitigated when high > ob_top
"""
import logging
from dataclasses import dataclass
from typing import List, Optional
from backend.mexc.models import Candle
from backend.config import OB_PIVOT_LENGTH, OB_MAX_DISTANCE_PCT, OB_MITIGATION

logger = logging.getLogger(__name__)


@dataclass
class VolumeOrderBlock:
    ob_top:     float
    ob_bottom:  float
    ob_avg:     float
    direction:  str            # "BULLISH" or "BEARISH"
    bar_index:  int
    timestamp:  int
    mitigated:  bool = False

    @property
    def midpoint(self) -> float:
        return (self.ob_top + self.ob_bottom) / 2


def _is_volume_pivot_high(candles: List[Candle], i: int, n: int) -> bool:
    """True if candles[i].volume > all candles in [i-n, i+n] excluding i."""
    if i < n or i + n >= len(candles):
        return False
    pivot_vol = candles[i].volume
    for j in range(i - n, i + n + 1):
        if j != i and candles[j].volume >= pivot_vol:
            return False
    return True


def detect_volume_obs(candles: List[Candle],
                      n: int = OB_PIVOT_LENGTH) -> List[VolumeOrderBlock]:
    """Detect all unmitigated volume-pivot order blocks, sorted by recency."""
    if len(candles) < n * 2 + 5:
        return []

    obs: List[VolumeOrderBlock] = []
    os_flag = 0  # 0=upswing, 1=downswing

    for i in range(n, len(candles) - n):
        c = candles[i]

        # Update os state
        look_back = i - n
        if look_back >= 0:
            window_start = max(0, look_back - n + 1)
            window_end = look_back
            if window_end >= window_start:
                recent_high = max(c2.high for c2 in candles[window_start:window_end+1])
                recent_low  = min(c2.low  for c2 in candles[window_start:window_end+1])
                if candles[look_back].high > recent_high:
                    os_flag = 0
                elif candles[look_back].low < recent_low:
                    os_flag = 1

        if not _is_volume_pivot_high(candles, i, n):
            continue

        hl2 = (c.high + c.low) / 2

        if os_flag == 1:
            ob = VolumeOrderBlock(
                ob_top=hl2, ob_bottom=c.low, ob_avg=hl2,
                direction="BULLISH", bar_index=i, timestamp=c.timestamp
            )
        elif os_flag == 0:
            ob = VolumeOrderBlock(
                ob_top=c.high, ob_bottom=hl2, ob_avg=hl2,
                direction="BEARISH", bar_index=i, timestamp=c.timestamp
            )
        else:
            continue
        obs.append(ob)

    # Apply mitigation
    for ob in obs:
        for j in range(ob.bar_index + 1, len(candles)):
            c = candles[j]
            if OB_MITIGATION == "Wick":
                if ob.direction == "BULLISH" and c.low < ob.ob_bottom:
                    ob.mitigated = True; break
                if ob.direction == "BEARISH" and c.high > ob.ob_top:
                    ob.mitigated = True; break
            else:
                if ob.direction == "BULLISH" and c.close < ob.ob_bottom:
                    ob.mitigated = True; break
                if ob.direction == "BEARISH" and c.close > ob.ob_top:
                    ob.mitigated = True; break

    valid = [ob for ob in obs if not ob.mitigated]
    valid.sort(key=lambda ob: ob.bar_index, reverse=True)
    return valid


def find_nearest_ob(candles: List[Candle],
                    direction: str,
                    max_distance_pct: float = OB_MAX_DISTANCE_PCT
                    ) -> Optional[VolumeOrderBlock]:
    """Find the nearest unmitigated OB in a direction. LONG->BULLISH, SHORT->BEARISH."""
    current_price = candles[-1].close
    ob_direction  = "BULLISH" if direction == "LONG" else "BEARISH"
    all_obs       = detect_volume_obs(candles)
    matching      = [ob for ob in all_obs if ob.direction == ob_direction]

    best    = None
    best_dist = float("inf")

    for ob in matching:
        dist = abs(ob.midpoint - current_price) / current_price
        if dist <= max_distance_pct and dist < best_dist:
            best_dist = dist
            best      = ob

    if best:
        logger.info(f"Volume OB found: {ob_direction} zone "
                    f"[{best.ob_bottom:.4f}-{best.ob_top:.4f}] "
                    f"dist={best_dist*100:.2f}%")
    else:
        logger.info(f"No {ob_direction} volume OB within {max_distance_pct*100:.0f}% of price")

    return best
