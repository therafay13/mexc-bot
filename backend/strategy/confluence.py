"""
SCORING SYSTEM — 100 points total:

  MSS confirmed on 15m             → 20 pts  [REQUIRED — instant reject if False]
  HTF (1h) bias aligns direction   → 15 pts  [REQUIRED — instant reject if False]
  Price inside valid Order Block   → 20 pts
  FVG overlapping OB zone          → 10 pts
  Liquidity sweep (correct side)   → 15 pts
  Price in correct zone            → 10 pts  [DISCOUNT for longs, PREMIUM for shorts]
  Volatility in ideal range        → 5 pts
  Volume above 20-period average   → 5 pts
  ──────────────────────────────────────────
  Total                            → 100 pts

  Minimum to proceed to Kronos:    70 pts
  Hard veto rules:
    - Price >5% away from nearest OB → reject regardless of score
    - Coin on cooldown (traded <T4h ago) → reject
"""
from typing import List
from backend.mexc.models import Candle
from backend.strategy.structure import detect_market_structure
from backend.strategy import smc, volatility
from backend.config import MIN_CONFLUENCE_SCORE


def score_setup(symbol: str, candles_15m: List[Candle], candles_1h: List[Candle]) -> dict:
    if len(candles_15m) < 50 or len(candles_1h) < 30:
        return _reject("Insufficient candle data")

    struct_15m = detect_market_structure(candles_15m)
    struct_1h  = detect_market_structure(candles_1h)

    if not struct_15m["mss_confirmed"]:
        return _reject("No MSS on 15m")

    direction = struct_15m["mss_direction"]
    trade_dir = "LONG" if direction == "BULLISH" else "SHORT"

    htf_bull  = struct_1h["trend"] == "UPTREND"   or struct_1h.get("mss_direction") == "BULLISH"
    htf_bear  = struct_1h["trend"] == "DOWNTREND" or struct_1h.get("mss_direction") == "BEARISH"
    if trade_dir == "LONG"  and not htf_bull: return _reject("HTF bias is bearish")
    if trade_dir == "SHORT" and not htf_bear: return _reject("HTF bias is bullish")

    score     = 35  # MSS(20) + HTF(15) already confirmed
    breakdown = {"mss": 20, "htf_align": 15}
    current   = candles_15m[-1].close

    obs   = smc.find_order_blocks(candles_15m, direction)
    fvgs  = smc.find_fair_value_gaps(candles_15m)
    liq   = smc.find_liquidity_levels(struct_15m["swings"])
    swept, sweep_dir = smc.detect_liquidity_sweep(candles_15m, liq)
    zone  = smc.get_premium_discount(struct_15m["swings"], current)

    # OB (20 pts)
    active_ob = next((o for o in obs if o.ob_low <= current <= o.ob_high), None)
    if active_ob:
        score += 20; breakdown["ob"] = 20
    else:
        breakdown["ob"] = 0
        if obs:
            nearest  = min(obs, key=lambda o: abs(o.ob_high - current))
            dist_pct = abs(nearest.ob_high - current) / current
            if dist_pct > 0.05:
                return _reject("Price >5% from nearest OB")

    # FVG overlaps OB (10 pts)
    fvg_pts = 0
    if active_ob:
        for f in fvgs:
            if f.direction == direction and f.fvg_low <= active_ob.ob_high and f.fvg_high >= active_ob.ob_low:
                fvg_pts = 10; break
    score += fvg_pts; breakdown["fvg"] = fvg_pts

    # Liquidity sweep correct side (15 pts)
    sweep_pts = 0
    if swept:
        ok = (trade_dir == "LONG" and sweep_dir == "SELL_SIDE") or \
             (trade_dir == "SHORT" and sweep_dir == "BUY_SIDE")
        if ok: sweep_pts = 15
    score += sweep_pts; breakdown["liquidity_sweep"] = sweep_pts

    # Zone (10 pts)
    zone_pts = 10 if ((trade_dir == "LONG" and zone == "DISCOUNT") or
                      (trade_dir == "SHORT" and zone == "PREMIUM")) else 0
    score += zone_pts; breakdown["zone"] = zone_pts

    # Volatility (5 pts)
    vol = volatility.get_volatility_score(candles_15m)
    score += vol["score"]; breakdown["volatility"] = vol["score"]

    # Volume (5 pts)
    vols    = [c.volume for c in candles_15m[-21:]]
    avg_vol = sum(vols[:-1]) / 20 if len(vols) > 1 else 0
    vol_pts = 5 if candles_15m[-1].volume > avg_vol else 0
    score += vol_pts; breakdown["volume"] = vol_pts

    # SL placement
    if active_ob:
        sl = active_ob.ob_low * 0.999 if trade_dir == "LONG" else active_ob.ob_high * 1.001
        entry_zone = [active_ob.ob_low, active_ob.ob_high]
    else:
        fallback = struct_15m.get("last_hl") or struct_15m.get("last_ll") or current * 0.98
        sl        = fallback * (0.999 if trade_dir == "LONG" else 1.001)
        entry_zone = [current * 0.999, current * 1.001]

    approved = score >= MIN_CONFLUENCE_SCORE
    return {
        "symbol": symbol, "direction": trade_dir,
        "score": score, "approved": approved,
        "reject_reason": "" if approved else f"Score {score} < {MIN_CONFLUENCE_SCORE}",
        "score_breakdown": breakdown,
        "entry_zone": entry_zone, "sl_price": sl, "current_price": current,
        "atr": vol["atr"], "atr_pct": vol["atr_pct"],
        "active_ob": active_ob, "fvgs": fvgs,
    }


def _reject(reason: str) -> dict:
    return {"approved": False, "reject_reason": reason, "score": 0,
            "score_breakdown": {}, "direction": None, "entry_zone": None, "sl_price": None}
