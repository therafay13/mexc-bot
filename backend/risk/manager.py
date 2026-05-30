"""
Position sizing math:

  risk_usdt   = account_balance * 0.02
  sl_dist     = |entry - sl| / entry          (as a fraction)
  notional    = risk_usdt / sl_dist           (total position value needed)
  raw_lev     = notional / account_balance
  final_lev   = clamp(raw_lev, MIN_LEVERAGE, score_cap, contract_max)
  margin      = notional / final_lev

TP levels:
  TP1 = entry ± (2 × sl_dist × entry)    [50% of position, 1:2 R:R]
  TP2 = entry ± (3 × sl_dist × entry)    [30% of position, 1:3 R:R]
  TP3 = entry ± (5 × sl_dist × entry)    [20% of position, 1:5 R:R]

If Kronos provides a TP guide, use the better of: formula TP3 vs Kronos predicted extreme.
"""
from backend.config import (ACCOUNT_RISK_PCT, MIN_LEVERAGE,
                             MIN_TRADE_PROFIT_USDT, LEVERAGE_BY_SCORE)


def calculate_trade(confluence: dict, balance: float, contract_max_lev=75) -> dict | None:
    direction  = confluence["direction"]
    entry      = sum(confluence["entry_zone"]) / 2
    sl         = confluence["sl_price"]
    score      = confluence["score"]
    kronos_tp  = confluence.get("kronos_tp_guide")

    risk_usdt  = balance * ACCOUNT_RISK_PCT
    sl_dist    = abs(entry - sl) / entry
    if sl_dist < 0.001: return None

    notional   = risk_usdt / sl_dist
    raw_lev    = notional / balance

    score_max  = MIN_LEVERAGE
    for (lo, hi), cap in LEVERAGE_BY_SCORE.items():
        if lo <= score <= hi: score_max = cap; break

    lev    = max(MIN_LEVERAGE, min(int(round(raw_lev)), score_max, contract_max_lev))
    margin = notional / lev
    dist   = sl_dist * entry

    if direction == "LONG":
        tp1 = entry + 2 * dist
        tp2 = entry + 3 * dist
        tp3 = entry + 5 * dist
        # If Kronos predicted high is more conservative than formula TP3, use it for TP2
        if kronos_tp and kronos_tp > entry:
            tp2 = min(kronos_tp, tp3)   # Don't exceed TP3
    else:
        tp1 = entry - 2 * dist
        tp2 = entry - 3 * dist
        tp3 = entry - 5 * dist
        if kronos_tp and kronos_tp < entry:
            tp2 = max(kronos_tp, tp3)

    if risk_usdt * 2 < MIN_TRADE_PROFIT_USDT: return None

    return {
        "entry_price":     round(entry, 6),
        "sl_price":        round(sl, 6),
        "tp1":             round(tp1, 6),
        "tp2":             round(tp2, 6),
        "tp3":             round(tp3, 6),
        "leverage":        lev,
        "notional_usdt":   round(notional, 2),
        "margin_usdt":     round(margin, 2),
        "risk_usdt":       round(risk_usdt, 2),
        "reward_tp1_usdt": round(risk_usdt * 2, 2),
        "reward_tp3_usdt": round(risk_usdt * 5, 2),
        "sl_distance_pct": round(sl_dist * 100, 4),
    }
