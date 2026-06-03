"""
SMC Entry Precision Layer — Layer 3.
Direction is already decided by HMA filter.
This layer finds the best volume-validated entry in that direction.

Uses volume_ob.py for order block detection (LuxAlgo method).
FVG detection remains geometric (unchanged — FVGs don't need volume validation).
"""
import logging
from typing import List
from backend.mexc.models import Candle
from backend.strategy.volume_ob import find_nearest_ob
from backend.strategy import smc
from backend.strategy.volatility import calculate_atr
from backend.strategy.structure import detect_swing_points

logger = logging.getLogger(__name__)


def find_entry(candles: List[Candle],
               direction: str,
               kronos_pred_high: float,
               kronos_pred_low: float,
               kronos_pred_close: float) -> dict:
    """
    Find the best SMC entry in the given direction.
    direction: LONG or SHORT — already decided by HMA filter.

    Returns dict with:
      entry_zone, sl_price, sl_reason, tp_guide, smc_quality (0-3),
      has_ob, has_fvg, has_sweep, zone, zone_good, entry_reason, atr, current_price
    """
    current_price = candles[-1].close
    atr           = calculate_atr(candles)
    swings        = detect_swing_points(candles)
    liq_levels    = smc.find_liquidity_levels(swings)

    # Volume-Pivot Order Block (LuxAlgo method)
    active_ob = find_nearest_ob(candles, direction)
    has_ob    = active_ob is not None

    # Fair Value Gaps (geometric — unchanged)
    ob_direction = "BULLISH" if direction == "LONG" else "BEARISH"
    fvgs         = smc.find_fair_value_gaps(candles)
    dir_fvgs     = [f for f in fvgs if f.direction == ob_direction]
    has_fvg      = len(dir_fvgs) > 0

    # Liquidity Sweep
    swept, sweep_dir = smc.detect_liquidity_sweep(candles, liq_levels)
    sweep_correct    = ((direction == "LONG"  and sweep_dir == "SELL_SIDE") or
                        (direction == "SHORT" and sweep_dir == "BUY_SIDE"))
    has_sweep        = swept and sweep_correct

    # SMC Quality Score (0-3)
    smc_quality = sum([has_ob, has_fvg, has_sweep])

    # Entry Zone
    if has_ob:
        entry_zone   = [active_ob.ob_bottom, active_ob.ob_top]
        entry_reason = (f"Volume OB [{active_ob.ob_bottom:.4f}-{active_ob.ob_top:.4f}]"
                        + (" + FVG" if has_fvg else "")
                        + (" + sweep" if has_sweep else ""))
    elif has_fvg:
        best_fvg   = dir_fvgs[-1]
        entry_zone = [best_fvg.fvg_low, best_fvg.fvg_high]
        entry_reason = f"FVG entry [{best_fvg.fvg_low:.4f}-{best_fvg.fvg_high:.4f}]"
    else:
        spread       = atr * 0.3
        entry_zone   = [current_price - spread, current_price + spread]
        entry_reason = "ATR-based entry (no OB/FVG) — HMA signal only"

    # Stop Loss
    if has_ob:
        if direction == "LONG":
            sl_price  = active_ob.ob_bottom * (1 - 0.001)
        else:
            sl_price  = active_ob.ob_top * (1 + 0.001)
        sl_reason = "Volume OB invalidation"
    else:
        entry_mid = sum(entry_zone) / 2
        sl_buffer = atr * 1.5
        sl_price  = (entry_mid - sl_buffer if direction == "LONG"
                     else entry_mid + sl_buffer)
        sl_reason = "ATRx1.5 fallback"

    # TP Guide from Kronos prediction
    tp_guide = kronos_pred_high if direction == "LONG" else kronos_pred_low

    # Premium/Discount zone
    zone      = smc.get_premium_discount(swings, current_price)
    zone_good = ((direction == "LONG"  and zone == "DISCOUNT") or
                 (direction == "SHORT" and zone == "PREMIUM"))

    return {
        "entry_zone":    entry_zone,
        "sl_price":      round(sl_price, 6),
        "sl_reason":     sl_reason,
        "tp_guide":      round(tp_guide, 6),
        "smc_quality":   smc_quality,
        "has_ob":        has_ob,
        "has_fvg":       has_fvg,
        "has_sweep":     has_sweep,
        "zone":          zone,
        "zone_good":     zone_good,
        "entry_reason":  entry_reason,
        "atr":           atr,
        "current_price": current_price,
    }
