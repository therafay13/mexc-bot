# STRATEGY REDESIGN — Feed to OpenCode to replace the existing strategy modules
# This replaces: confluence.py, structure.py usage, kronos_filter.py, and server.py pipeline
# Kronos is now the PRIMARY direction engine. SMC is the entry precision layer.
# MSS/Structure is a light sanity check only — NOT a direction generator.

---

## THE NEW 5-LAYER ARCHITECTURE

```
OLD (broken):
  LTF MSS → commits direction → scores SMC → HTF check → Kronos veto
  Problem: mislabels pullbacks as reversals, rejects valid trades, Kronos too late

NEW (fixed):
  Kronos Oracle → SMC Entry Finder → Structure Sanity → Risk Engine → Execute
  Kronos decides direction. SMC finds the exact entry. Structure just sanity-checks.
```

```
Layer 1: Scanner
  Fetch top coins by volume/momentum/change
  No direction assumed yet

Layer 2: ★ KRONOS ORACLE (PRIMARY — replaces all direction logic)
  Feed last 400 closed 15m candles into Kronos
  Run KRONOS_SAMPLES forecast paths
  Output:
    direction:         LONG or SHORT (from predicted_close vs current_close)
    confidence:        0.0–1.0 (consistency across samples)
    predicted_move_pct: expected % move in forecast window
    pred_high:         max predicted high across all samples
    pred_low:          min predicted low across all samples
    pred_close:        mean final close across all samples
  Gates:
    confidence < 0.50      → SKIP (Kronos unsure)
    |predicted_move_pct| < 0.25%  → SKIP (not enough expected movement)
  The direction from Kronos IS the trade direction. No overriding it.

Layer 3: SMC ENTRY PRECISION (direction already known from Kronos)
  Now that direction is locked, find the best entry in that direction:
    find_order_blocks(candles, kronos_direction)    → entry zone + SL anchor
    find_fair_value_gaps(candles, kronos_direction) → confluence + TP guide
    detect_liquidity_sweep(candles, levels)         → ideal entry timing
    get_premium_discount(swings, current_price)     → zone validation
  This layer gives us: entry_zone, sl_price, tp_guide
  It does NOT gate the trade on its own — it just refines parameters.
  If no OB found: entry = current_price, SL = ATR-based fallback.

Layer 4: STRUCTURE SANITY CHECK (light filter — not direction generator)
  Simple check: is the market in VIOLENT opposition to Kronos direction?
  REJECT only if:
    Kronos says LONG  AND 1h shows clear LL+LH with no CHoCH whatsoever
    Kronos says SHORT AND 1h shows clear HH+HL with no CHoCH whatsoever
  ALLOW:
    HTF trending same direction as Kronos ✓
    HTF ranging/consolidating             ✓
    HTF in early CHoCH same direction     ✓
    HTF pullback within larger trend      ✓ (this was the HYPEUSDT case — now fixed)
  This is a sanity check, not a veto machine.

Layer 5: RISK ENGINE
  Entry: midpoint of OB zone (or current_price if no OB)
  SL: OB invalidation ± 0.1% buffer (or entry ± ATR×1.2 if no OB)
  TP1: 1:2 R:R (50% of position)
  TP2: Kronos pred_high (LONG) or pred_low (SHORT) — 30% of position
  TP3: 1:5 R:R or Kronos pred_close if more aggressive — 20% of position
  Leverage: scales with Kronos confidence (see table below)
  Execute.
```

---

## LEVERAGE TABLE (Kronos Confidence → Leverage)

```python
# Replace LEVERAGE_BY_SCORE with LEVERAGE_BY_CONFIDENCE in config.py
LEVERAGE_BY_CONFIDENCE = {
    (0.50, 0.59): 5,    # Kronos barely confident → minimum leverage
    (0.60, 0.69): 10,   # Some confidence
    (0.70, 0.79): 20,   # Good confidence
    (0.80, 0.89): 50,   # High confidence
    (0.90, 1.00): 75,   # Very high confidence (cap at contract max)
}

# Plus: scale up if SMC entry is clean (has OB + FVG + sweep)
SMC_CLEAN_ENTRY_BONUS = 1.25   # Multiply leverage by this if all 3 present
```

---

## NEW FILE: `backend/strategy/kronos_oracle.py`
## REPLACES: kronos_filter.py (which was a veto layer — wrong role)

```python
"""
Kronos Oracle — PRIMARY direction and confidence engine.

This is now Layer 2, not Layer 6.
It runs FIRST and its output drives everything downstream.

Kronos tells us:
  - Which direction to trade (LONG/SHORT)
  - How confident it is (informs leverage)
  - Where price is likely to go (informs TP levels)
  - Whether this coin is worth trading at all (confidence + move threshold)

SMC then finds us the best ENTRY for that direction.
MSS then does a light sanity check.
"""
import os
import sys
import logging
import time as time_module
import numpy as np
import pandas as pd
from typing import List, Optional
from backend.mexc.models import Candle, KronosForecast
from backend.config import (KRONOS_MODEL, KRONOS_DEVICE, KRONOS_LOOKBACK,
                            KRONOS_PRED_LEN, KRONOS_SAMPLES, KRONOS_TEMPERATURE,
                            KRONOS_TOP_P)

logger = logging.getLogger(__name__)

# Minimum confidence to trade at all
KRONOS_MIN_CONFIDENCE = float(os.getenv("KRONOS_MIN_CONFIDENCE", "0.50"))
# Minimum absolute predicted move to trade
KRONOS_MIN_MOVE_PCT   = float(os.getenv("KRONOS_MIN_MOVE_PCT", "0.0025"))  # 0.25%

_predictor  = None
_load_error = None


def load_model():
    """Load Kronos model. Called once at startup in background thread."""
    global _predictor, _load_error

    if KRONOS_MODEL == "mini":
        tok_name = "NeoQuasar/Kronos-Tokenizer-2k"
        mod_name = "NeoQuasar/Kronos-mini"
        max_ctx  = min(2048, KRONOS_LOOKBACK)
    elif KRONOS_MODEL == "base":
        tok_name = "NeoQuasar/Kronos-Tokenizer-base"
        mod_name = "NeoQuasar/Kronos-base"
        max_ctx  = min(512, KRONOS_LOOKBACK)
    else:
        tok_name = "NeoQuasar/Kronos-Tokenizer-base"
        mod_name = "NeoQuasar/Kronos-small"
        max_ctx  = min(512, KRONOS_LOOKBACK)

    # Try local source first
    kronos_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "kronos"
    )
    if os.path.isdir(kronos_path) and kronos_path not in sys.path:
        sys.path.insert(0, kronos_path)

    try:
        from model import Kronos, KronosTokenizer, KronosPredictor
        logger.info(f"Loading Kronos {KRONOS_MODEL} on {KRONOS_DEVICE}...")
        tokenizer  = KronosTokenizer.from_pretrained(tok_name)
        model      = Kronos.from_pretrained(mod_name)
        model      = model.to(KRONOS_DEVICE)
        model.eval()
        _predictor = KronosPredictor(model, tokenizer, max_context=max_ctx)
        logger.info(f"✓ Kronos {KRONOS_MODEL} ready on {KRONOS_DEVICE}")
        return
    except Exception as e:
        logger.warning(f"Primary Kronos load failed: {e}. Trying HuggingFace Hub...")

    # HuggingFace Hub fallback
    try:
        from huggingface_hub import snapshot_download
        local = snapshot_download(repo_id=mod_name, local_dir=f"./hf_cache/{KRONOS_MODEL}")
        if local not in sys.path:
            sys.path.insert(0, local)
        from model import Kronos, KronosTokenizer, KronosPredictor
        tokenizer  = KronosTokenizer.from_pretrained(tok_name)
        model      = Kronos.from_pretrained(local)
        model      = model.to(KRONOS_DEVICE)
        model.eval()
        _predictor = KronosPredictor(model, tokenizer, max_context=max_ctx)
        logger.info(f"✓ Kronos loaded from HuggingFace Hub")
        return
    except Exception as e:
        logger.warning(f"HF Hub load failed: {e}")

    _load_error = "Kronos unavailable — bot will not trade (requires Kronos for direction)"
    logger.error(_load_error)


def _run_sync(candles: List[Candle], symbol: str) -> KronosForecast:
    """
    Synchronous Kronos inference. Run in asyncio executor (not on event loop).

    Returns KronosForecast with:
      - direction:          LONG or SHORT
      - confidence:         0.0–1.0
      - predicted_move_pct: signed % move (positive=up, negative=down)
      - predicted_high:     max high across all sample paths
      - predicted_low:      min low across all sample paths
      - predicted_close:    mean final close across all sample paths
      - vetoed:             True if confidence or move below threshold
    """
    current_price = candles[-1].close

    if _predictor is None:
        # Kronos not loaded — return neutral veto (don't trade blind)
        return KronosForecast(
            symbol=symbol, direction_agreement=False,
            predicted_move_pct=0, predicted_high=current_price,
            predicted_low=current_price, predicted_close=current_price,
            confidence=0, vetoed=True,
            veto_reason="Kronos not loaded — cannot determine direction"
        )

    lookback = min(KRONOS_LOOKBACK, len(candles))
    hist     = candles[-lookback:]
    interval = 900  # 15m in seconds
    last_ts  = hist[-1].timestamp

    df = pd.DataFrame({
        "open":   [c.open   for c in hist],
        "high":   [c.high   for c in hist],
        "low":    [c.low    for c in hist],
        "close":  [c.close  for c in hist],
        "volume": [c.volume for c in hist],
    })

    x_ts = pd.Series([c.timestamp for c in hist])
    y_ts = pd.Series([last_ts + interval * (i + 1) for i in range(KRONOS_PRED_LEN)])

    all_closes, all_highs, all_lows, all_preds = [], [], [], []

    for s in range(KRONOS_SAMPLES):
        try:
            pred = _predictor.predict(
                df=df, x_timestamp=x_ts, y_timestamp=y_ts,
                pred_len=KRONOS_PRED_LEN,
                T=KRONOS_TEMPERATURE, top_p=KRONOS_TOP_P, sample_count=1
            )
            all_closes.append(float(pred["close"].iloc[-1]))
            all_highs.append(float(pred["high"].max()))
            all_lows.append(float(pred["low"].min()))
            all_preds.append(pred)
        except Exception as e:
            logger.warning(f"Kronos sample {s+1} failed for {symbol}: {e}")

    if not all_closes:
        return KronosForecast(
            symbol=symbol, direction_agreement=False,
            predicted_move_pct=0, predicted_high=current_price,
            predicted_low=current_price, predicted_close=current_price,
            confidence=0, vetoed=True, veto_reason="All Kronos samples failed"
        )

    mean_close     = float(np.mean(all_closes))
    std_close      = float(np.std(all_closes)) if len(all_closes) > 1 else 0
    confidence     = float(max(0.0, min(1.0, 1.0 - std_close / max(abs(mean_close), 1e-8))))

    pred_high      = float(np.max(all_highs))
    pred_low       = float(np.min(all_lows))
    predicted_move = (mean_close - current_price) / current_price

    # Direction is determined purely by Kronos
    direction      = "LONG" if predicted_move > 0 else "SHORT"

    # Veto conditions
    veto_reason = ""
    vetoed      = False
    if confidence < KRONOS_MIN_CONFIDENCE:
        vetoed      = True
        veto_reason = f"Confidence {confidence:.2f} < minimum {KRONOS_MIN_CONFIDENCE}"
    elif abs(predicted_move) < KRONOS_MIN_MOVE_PCT:
        vetoed      = True
        veto_reason = f"Move {predicted_move*100:.3f}% < minimum {KRONOS_MIN_MOVE_PCT*100:.2f}%"

    return KronosForecast(
        symbol=symbol,
        direction_agreement=not vetoed,   # True = Kronos approved, has direction
        predicted_move_pct=round(predicted_move, 6),
        predicted_high=round(pred_high, 6),
        predicted_low=round(pred_low, 6),
        predicted_close=round(mean_close, 6),
        confidence=round(confidence, 4),
        vetoed=vetoed,
        veto_reason=veto_reason
    )


async def run_oracle(symbol: str, candles: List[Candle]) -> KronosForecast:
    """
    Async wrapper. Runs Kronos in thread executor.
    Returns KronosForecast. Check forecast.vetoed before proceeding.
    forecast.direction_agreement == True means: trade in this direction.
    """
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run_sync, candles, symbol)


def is_loaded() -> bool:
    return _predictor is not None


def get_load_error() -> Optional[str]:
    return _load_error
```

---

## UPDATED FILE: `backend/strategy/structure.py`
## ROLE CHANGE: Direction generator → Light sanity check only

```python
"""
Structure Sanity Check — Layer 4.

Role: Reject only if Kronos direction is in CLEAR violent opposition to market.
Does NOT generate trade direction. Direction comes from Kronos.

Definition of "violent opposition":
  Kronos says LONG  AND 1h has clear LL+LH sequence with NO CHoCH toward bullish
  Kronos says SHORT AND 1h has clear HH+HL sequence with NO CHoCH toward bearish

Everything else is ALLOWED:
  - HTF trending with Kronos        ✓ (best case)
  - HTF ranging or consolidating    ✓ (Kronos saw the breakout coming)
  - HTF in early CHoCH              ✓ (Kronos caught the reversal early)
  - HTF pullback within main trend  ✓ (THIS fixes the HYPEUSDT case)
  - HTF slightly against Kronos     ✓ (Kronos has more data + AI — trust it)

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


def structure_sanity_check(candles_1h: List[Candle],
                            kronos_direction: str) -> dict:
    """
    Returns: {passed: bool, reason: str, trend_1h: str}

    ONLY fails if 1h shows a clear, well-established trend
    in the OPPOSITE direction to Kronos with NO signs of reversal.

    Defaults to PASS in all ambiguous cases.
    """
    swings = detect_swing_points(candles_1h)
    if len(swings) < 4:
        return {"passed": True, "reason": "Insufficient 1h swings — defaulting to pass",
                "trend_1h": "UNKNOWN"}

    last = {k: None for k in ["HH", "HL", "LL", "LH"]}
    for s in swings:
        last[s.kind] = s.price

    # Recent swing counts — how established is the trend?
    recent_swings = swings[-8:]  # Last 8 swing points
    recent_hh = sum(1 for s in recent_swings if s.kind == "HH")
    recent_hl = sum(1 for s in recent_swings if s.kind == "HL")
    recent_ll = sum(1 for s in recent_swings if s.kind == "LL")
    recent_lh = sum(1 for s in recent_swings if s.kind == "LH")

    # Is there a recent CHoCH? (sign of reversal — reduces opposition concern)
    current_close = candles_1h[-1].close
    bullish_choch = last["LH"] and current_close > last["LH"]
    bearish_choch = last["HL"] and current_close < last["HL"]

    # Determine 1h trend quality
    clear_uptrend   = recent_hh >= 2 and recent_hl >= 2 and not bearish_choch
    clear_downtrend = recent_ll >= 2 and recent_lh >= 2 and not bullish_choch

    trend_1h = "UPTREND" if clear_uptrend else "DOWNTREND" if clear_downtrend else "RANGING"

    # Only veto on clear violent opposition
    if kronos_direction == "LONG" and clear_downtrend:
        return {
            "passed": False,
            "reason": f"1h in clear downtrend (LL:{recent_ll} LH:{recent_lh}) opposing Kronos LONG",
            "trend_1h": trend_1h
        }
    if kronos_direction == "SHORT" and clear_uptrend:
        return {
            "passed": False,
            "reason": f"1h in clear uptrend (HH:{recent_hh} HL:{recent_hl}) opposing Kronos SHORT",
            "trend_1h": trend_1h
        }

    # Everything else passes
    reason = f"1h trend: {trend_1h} — compatible with Kronos {kronos_direction}"
    if (kronos_direction == "LONG" and bullish_choch) or \
       (kronos_direction == "SHORT" and bearish_choch):
        reason += " (CHoCH in Kronos direction detected — strong alignment)"

    return {"passed": True, "reason": reason, "trend_1h": trend_1h}
```

---

## UPDATED FILE: `backend/strategy/smc_entry.py`
## REPLACES: confluence.py — now purely for entry precision, not direction

```python
"""
SMC Entry Precision Layer — Layer 3.

Direction is already decided by Kronos.
This layer's job: find the best possible entry point for that direction.

Returns:
  entry_zone:  [low, high] of the ideal entry range (usually an OB)
  sl_price:    Stop loss level (OB invalidation or ATR fallback)
  tp_guide:    Kronos-refined TP target (predicted_high for longs, predicted_low for shorts)
  smc_quality: 0–3 score counting: OB present, FVG present, sweep present
               Higher quality → leverage bonus in risk engine
  entry_reason: Human-readable description of entry basis
"""
from typing import List, Optional, Tuple
from backend.mexc.models import Candle, OrderBlock, FairValueGap, LiquidityLevel, SwingPoint
from backend.strategy import smc
from backend.strategy.volatility import calculate_atr
from backend.strategy.structure import detect_swing_points


def find_entry(candles: List[Candle], direction: str,
               kronos_pred_high: float, kronos_pred_low: float,
               kronos_pred_close: float) -> dict:
    """
    Find the best SMC entry in the given direction.
    direction: "LONG" or "SHORT" (already decided by Kronos)

    Process:
    1. Find Order Blocks in Kronos direction
    2. Find FVGs in Kronos direction
    3. Check for recent liquidity sweep
    4. Determine entry zone and SL
    5. Use Kronos predictions to set TP2
    """
    current_price = candles[-1].close
    atr           = calculate_atr(candles)
    swings        = detect_swing_points(candles)
    liq_levels    = smc.find_liquidity_levels(swings)

    # ── Step 1: Order Block search ────────────────────────────────
    obs = smc.find_order_blocks(candles, direction)

    # Find the nearest valid OB to current price in the correct direction
    active_ob = None
    ob_dist_pct = float("inf")

    for ob in obs:
        dist = abs(ob.ob_high - current_price) / current_price
        if dist < ob_dist_pct:
            ob_dist_pct = dist
            active_ob   = ob

    has_ob = active_ob is not None and ob_dist_pct <= 0.05  # Within 5% of price

    # ── Step 2: Fair Value Gap ────────────────────────────────────
    fvgs     = smc.find_fair_value_gaps(candles)
    dir_fvgs = [f for f in fvgs if f.direction == direction]
    has_fvg  = len(dir_fvgs) > 0

    # ── Step 3: Liquidity Sweep ───────────────────────────────────
    swept, sweep_dir = smc.detect_liquidity_sweep(candles, liq_levels)
    sweep_correct = (
        (direction == "LONG"  and sweep_dir == "SELL_SIDE") or
        (direction == "SHORT" and sweep_dir == "BUY_SIDE")
    )
    has_sweep = swept and sweep_correct

    # ── Step 4: SMC Quality Score (0–3) ──────────────────────────
    smc_quality = sum([has_ob, has_fvg, has_sweep])

    # ── Step 5: Entry Zone ────────────────────────────────────────
    if has_ob:
        entry_zone   = [active_ob.ob_low, active_ob.ob_high]
        entry_reason = f"Order Block [{active_ob.ob_low:.4f}–{active_ob.ob_high:.4f}]"
        if has_fvg:
            entry_reason += " + FVG confluence"
        if has_sweep:
            entry_reason += " + liquidity sweep"
    elif has_fvg:
        # Use FVG midpoint as entry zone
        best_fvg   = dir_fvgs[-1]
        mid        = (best_fvg.fvg_high + best_fvg.fvg_low) / 2
        entry_zone = [best_fvg.fvg_low, best_fvg.fvg_high]
        entry_reason = f"FVG entry [{best_fvg.fvg_low:.4f}–{best_fvg.fvg_high:.4f}]"
    else:
        # No OB or FVG — enter at current price with ATR-based zone
        spread     = atr * 0.3
        entry_zone = [current_price - spread, current_price + spread]
        entry_reason = f"ATR-based entry (no OB/FVG found) — Kronos signal only"

    # ── Step 6: Stop Loss ─────────────────────────────────────────
    if has_ob:
        if direction == "LONG":
            sl_price = active_ob.ob_low * (1 - 0.001)   # 0.1% below OB low
        else:
            sl_price = active_ob.ob_high * (1 + 0.001)  # 0.1% above OB high
        sl_reason = "OB invalidation"
    else:
        # ATR-based SL: 1.5× ATR from entry midpoint
        entry_mid = sum(entry_zone) / 2
        sl_buffer = atr * 1.5
        sl_price  = entry_mid - sl_buffer if direction == "LONG" else entry_mid + sl_buffer
        sl_reason = "ATR×1.5 fallback"

    # ── Step 7: TP Guide from Kronos ─────────────────────────────
    # TP2 uses Kronos predicted extreme in the trade direction
    # This is more accurate than a fixed 1:3 formula
    if direction == "LONG":
        tp_guide = kronos_pred_high   # Kronos sees price going to this high
    else:
        tp_guide = kronos_pred_low    # Kronos sees price falling to this low

    # ── Step 8: Premium/Discount zone ────────────────────────────
    zone      = smc.get_premium_discount(swings, current_price)
    zone_good = ((direction == "LONG" and zone == "DISCOUNT") or
                 (direction == "SHORT" and zone == "PREMIUM"))

    return {
        "entry_zone":   entry_zone,
        "sl_price":     round(sl_price, 6),
        "sl_reason":    sl_reason,
        "tp_guide":     round(tp_guide, 6),
        "smc_quality":  smc_quality,   # 0, 1, 2, or 3
        "has_ob":       has_ob,
        "has_fvg":      has_fvg,
        "has_sweep":    has_sweep,
        "zone":         zone,
        "zone_good":    zone_good,
        "entry_reason": entry_reason,
        "atr":          atr,
        "current_price": current_price,
    }
```

---

## UPDATED FILE: `backend/risk/manager.py`
## Now uses Kronos confidence for leverage, not confluence score

```python
"""
Risk Manager — now driven by Kronos confidence + SMC quality.

Leverage formula:
  Base leverage   = KRONOS_MIN_CONFIDENCE → LEVERAGE_BY_CONFIDENCE table
  Quality bonus   = multiply by SMC_CLEAN_ENTRY_BONUS if smc_quality == 3
  Final leverage  = clamp(base × bonus, MIN_LEVERAGE, contract_max)

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
    (0.90, 1.00): 75,
}
SMC_CLEAN_ENTRY_BONUS = 1.25   # Applied when OB + FVG + sweep all present


def calculate_trade(kronos_forecast, smc_entry: dict,
                    balance: float, contract_max_lev: int = 75) -> dict | None:
    """
    kronos_forecast: KronosForecast object (has .confidence, .direction, etc.)
    smc_entry: dict from smc_entry.find_entry()
    """
    direction   = "LONG" if kronos_forecast.predicted_move_pct > 0 else "SHORT"
    confidence  = kronos_forecast.confidence
    entry       = sum(smc_entry["entry_zone"]) / 2
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
```

---

## UPDATED PIPELINE: Replace `_analyze_and_trade()` in `backend/api/server.py`

```python
async def _analyze_and_trade(symbol: str, norm: str):
    """
    NEW 5-layer pipeline. Kronos runs FIRST and decides direction.
    SMC then finds entry. Structure does a light sanity check.
    """
    from backend.strategy.kronos_oracle import run_oracle
    from backend.strategy.structure import structure_sanity_check, detect_swing_points
    from backend.strategy.smc_entry import find_entry
    from backend.risk.manager import calculate_trade
    from backend.mexc.models import TradeSignal

    # ── Fetch candles ─────────────────────────────────────────────
    raw_15m = await client.get_klines(norm, PRIMARY_TF, CANDLES_NEEDED)
    raw_1h  = await client.get_klines(norm, BIAS_TF, 100)
    c15m    = parse_candles(raw_15m)
    c1h     = parse_candles(raw_1h)
    if len(c15m) < 100:
        return

    current_price = c15m[-1].close

    # ── LAYER 2: Kronos Oracle (PRIMARY direction engine) ─────────
    forecast = await run_oracle(norm, c15m)

    if forecast.vetoed:
        trade_log.log_signal(
            symbol=norm, direction="N/A", score=0, breakdown={},
            approved=False, reason=f"Kronos: {forecast.veto_reason}",
            kronos_vetoed=True, kronos_move_pct=forecast.predicted_move_pct
        )
        await broadcast("signal", {
            "symbol": norm, "score": 0,
            "direction": None, "approved": False,
            "reason": f"Kronos: {forecast.veto_reason}",
            "kronos_vetoed": True,
            "kronos_confidence": forecast.confidence,
            "kronos_move_pct": round(forecast.predicted_move_pct * 100, 2),
        })
        return

    # Direction is now LOCKED from Kronos
    direction = "LONG" if forecast.predicted_move_pct > 0 else "SHORT"

    # ── LAYER 3: SMC Entry Precision ─────────────────────────────
    smc_result = find_entry(
        candles=c15m,
        direction=direction,
        kronos_pred_high=forecast.predicted_high,
        kronos_pred_low=forecast.predicted_low,
        kronos_pred_close=forecast.predicted_close
    )

    # ── LAYER 4: Structure Sanity Check (light) ───────────────────
    sanity = structure_sanity_check(c1h, direction)

    if not sanity["passed"]:
        trade_log.log_signal(
            symbol=norm, direction=direction,
            score=int(forecast.confidence * 100), breakdown={},
            approved=False, reason=f"Structure sanity: {sanity['reason']}",
            kronos_vetoed=False, kronos_move_pct=forecast.predicted_move_pct
        )
        await broadcast("signal", {
            "symbol": norm,
            "score": int(forecast.confidence * 100),
            "direction": direction, "approved": False,
            "reason": f"Sanity check: {sanity['reason']}",
            "kronos_vetoed": False,
            "kronos_confidence": forecast.confidence,
            "kronos_move_pct": round(forecast.predicted_move_pct * 100, 2),
        })
        return

    # ── LAYER 5: Risk Engine ──────────────────────────────────────
    balance = account.get_balance() or 300.0
    params  = calculate_trade(forecast, smc_result, balance)

    if params is None:
        logger.info(f"Trade skipped {norm}: risk params returned None (SL too tight)")
        return

    # Build and broadcast the approved signal
    score = int(forecast.confidence * 100)   # Confidence IS the score now

    trade_log.log_signal(
        symbol=norm, direction=direction, score=score,
        breakdown={
            "kronos_confidence":  forecast.confidence,
            "kronos_move_pct":    forecast.predicted_move_pct,
            "smc_quality":        smc_result["smc_quality"],
            "has_ob":             smc_result["has_ob"],
            "has_fvg":            smc_result["has_fvg"],
            "has_sweep":          smc_result["has_sweep"],
            "zone":               smc_result["zone"],
            "trend_1h":           sanity["trend_1h"],
            "entry_reason":       smc_result["entry_reason"],
        },
        approved=True,
        kronos_vetoed=False,
        kronos_move_pct=forecast.predicted_move_pct
    )

    await broadcast("signal", {
        "symbol": norm, "score": score,
        "direction": direction, "approved": True, "reason": "",
        "kronos_confidence": forecast.confidence,
        "kronos_move_pct":   round(forecast.predicted_move_pct * 100, 2),
        "smc_quality":       smc_result["smc_quality"],
        "entry_reason":      smc_result["entry_reason"],
        "trend_1h":          sanity["trend_1h"],
    })

    # Build TradeSignal and execute
    signal = TradeSignal(
        symbol=norm,
        direction=direction,
        score=score,
        entry_price=params["entry_price"],
        sl_price=params["sl_price"],
        tp1=params["tp1"],
        tp2=params["tp2"],
        tp3=params["tp3"],
        leverage=params["leverage"],
        margin_usdt=params["margin_usdt"],
        notional_usdt=params["notional_usdt"],
        risk_usdt=params["risk_usdt"],
        reward_tp1_usdt=params["reward_tp1_usdt"],
        reward_tp3_usdt=params["reward_tp3_usdt"],
        score_breakdown=params,
        kronos=forecast,
        timestamp=int(time.time())
    )

    await executor.execute_signal(signal)
    coin_cooldown[norm] = time.time()
    await ws_mgr.subscribe_ticker(symbol)

    await broadcast("trade_opened", {
        "symbol":            norm,
        "direction":         direction,
        "entry":             params["entry_price"],
        "sl":                params["sl_price"],
        "tp1":               params["tp1"],
        "tp2":               params["tp2"],
        "leverage":          params["leverage"],
        "kronos_confidence": forecast.confidence,
        "kronos_move_pct":   round(forecast.predicted_move_pct * 100, 2),
        "smc_quality":       smc_result["smc_quality"],
        "entry_reason":      smc_result["entry_reason"],
        "paper":             PAPER_TRADING,
    })
```

---

## UPDATED CONFIG additions (`backend/config.py`)

Add these lines to your existing config.py:

```python
# ── New Kronos Oracle thresholds ─────────────────────────────────
KRONOS_MIN_CONFIDENCE = float(os.getenv("KRONOS_MIN_CONFIDENCE", "0.50"))
KRONOS_MIN_MOVE_PCT   = float(os.getenv("KRONOS_MIN_MOVE_PCT", "0.0025"))  # 0.25%

# ── Leverage now driven by confidence, not SMC score ─────────────
LEVERAGE_BY_CONFIDENCE = {
    (0.50, 0.59): 5,
    (0.60, 0.69): 10,
    (0.70, 0.79): 20,
    (0.80, 0.89): 50,
    (0.90, 1.00): 75,
}
SMC_CLEAN_ENTRY_BONUS = 1.25

# Remove or keep but ignore: LEVERAGE_BY_SCORE, MIN_CONFLUENCE_SCORE
```

---

## WHAT TO DELETE from the old codebase

```
backend/strategy/confluence.py    → REPLACED by smc_entry.py
backend/strategy/kronos_filter.py → REPLACED by kronos_oracle.py

The confluence.py "gatekeeper" approach is gone.
The kronos_filter.py "veto at the end" approach is gone.
```

## WHAT TO RENAME

```
backend/strategy/structure.py
  - Keep detect_swing_points() — still needed by SMC
  - Remove detect_market_structure() direction logic — Kronos does this now
  - Add structure_sanity_check() as defined above
```

---

## HOW TO APPLY THIS TO OPENCODE

Feed OpenCode this prompt:

"Apply the strategy redesign from STRATEGY_REDESIGN.md to the existing bot.
Specifically:
1. Create backend/strategy/kronos_oracle.py — new primary direction engine
2. Create backend/strategy/smc_entry.py — replaces confluence.py for entry precision
3. Update backend/strategy/structure.py — remove direction logic, add structure_sanity_check()
4. Update backend/risk/manager.py — use LEVERAGE_BY_CONFIDENCE not LEVERAGE_BY_SCORE
5. Update backend/api/server.py — replace _analyze_and_trade() with the new 5-layer pipeline
6. Update backend/config.py — add new Kronos Oracle config variables
7. Delete backend/strategy/confluence.py and backend/strategy/kronos_filter.py
8. Run the server and verify the new pipeline fires in logs"
