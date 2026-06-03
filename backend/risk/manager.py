"""
Risk Manager — driven by effective confidence (Kronos or synthetic) + SMC quality.

Leverage formula:
  Base leverage   = effective_confidence -> LEVERAGE_BY_CONFIDENCE table
  Quality bonus   = multiply by SMC_CLEAN_ENTRY_BONUS if smc_quality == 3
  Final leverage  = clamp(base x bonus, MIN_LEVERAGE, contract_max)

TP levels:
  TP1 = 1:2 R:R (50% position) — formula based
  TP2 = Kronos tp_guide       (30% position) — AI predicted extreme
  TP3 = 1:5 R:R               (20% position) — let it run
"""
from backend.config import (ACCOUNT_RISK_PCT, MIN_LEVERAGE, MIN_TRADE_PROFIT_USDT)

LEVERAGE_BY_CONFIDENCE = {
    (0.50, 0.59): 5,
    (0.60, 0.69): 10,
    (0.70, 0.79): 20,
    (0.80, 0.89): 50,
    (0.90, 1.00): 50,
}
SMC_CLEAN_ENTRY_BONUS = 1.25   # Applied when OB + FVG + sweep all present


def calculate_trade(effective_confidence: float,
                    smc_entry: dict,
                    balance: float,
                    direction: str,
                    contract_max_lev: int = 50) -> dict | None:
    """
    effective_confidence: already computed by the pipeline (Kronos or synthetic)
    direction: LONG or SHORT — from HMA filter
    smc_entry: dict from smc_entry.find_entry()
    """
    confidence  = effective_confidence
    zone_bottom, zone_top = smc_entry["entry_zone"]
    if direction == "LONG":
        entry = zone_top - (zone_top - zone_bottom) * 0.15
    else:
        entry = zone_bottom + (zone_top - zone_bottom) * 0.15
    sl          = smc_entry["sl_price"]
    tp_guide    = smc_entry["tp_guide"]
    smc_quality = smc_entry["smc_quality"]

    risk_usdt  = balance * ACCOUNT_RISK_PCT
    sl_dist    = abs(entry - sl) / entry
    if sl_dist < 0.001:
        return None   # SL too tight — skip

    notional   = risk_usdt / sl_dist
    dist       = sl_dist * entry

    # Base leverage from Kronos confidence
    base_lev   = MIN_LEVERAGE
    for (lo, hi), lev in LEVERAGE_BY_CONFIDENCE.items():
        if lo <= confidence <= hi:
            base_lev = lev; break

    # Bonus if SMC entry is very clean
    bonus_lev  = base_lev * SMC_CLEAN_ENTRY_BONUS if smc_quality == 3 else base_lev
    final_lev  = max(MIN_LEVERAGE, min(int(round(bonus_lev)), contract_max_lev))
    margin     = notional / final_lev

    # TP calculation
    if direction == "LONG":
        tp1 = entry + 2 * dist
        # TP2: use Kronos predicted high if it's better than 1:3, else 1:3
        tp2 = max(tp_guide, entry + 3 * dist)
        tp3 = entry + 5 * dist
    else:
        tp1 = entry - 2 * dist
        tp2 = min(tp_guide, entry - 3 * dist)
        tp3 = entry - 5 * dist

    if risk_usdt * 2 < MIN_TRADE_PROFIT_USDT:
        return None

    return {
        "direction":       direction,
        "entry_price":     round(entry, 6),
        "sl_price":        round(sl, 6),
        "tp1":             round(tp1, 6),
        "tp2":             round(tp2, 6),
        "tp3":             round(tp3, 6),
        "leverage":        final_lev,
        "notional_usdt":   round(notional, 2),
        "margin_usdt":     round(margin, 2),
        "risk_usdt":       round(risk_usdt, 2),
        "reward_tp1_usdt": round(risk_usdt * 2, 2),
        "reward_tp3_usdt": round(risk_usdt * 5, 2),
        "sl_distance_pct": round(sl_dist * 100, 4),
        "smc_quality":     smc_quality,
        "kronos_confidence": confidence,
    }
