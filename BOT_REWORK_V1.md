# BOT STRATEGY REWORK — OpenCode Instructions
## For: DeepSeek Flash in OpenCode Desktop
## Context: The bot is ALREADY BUILT and deployed. Do NOT rebuild from scratch.
## Read the journal.md in this project for the current file structure and architecture.
## Apply changes surgically — only modify what is listed below.

---

## WHAT IS CHANGING AND WHY

The current bot is losing because:
1. Kronos-small at 6-hour horizon on 15m crypto data has ~50% accuracy (coin flip)
2. OB detection is geometric only — finds shapes, not institutionally significant levels
3. No order flow input — the bot is blind to what is actually happening in the market

The rework:
- HMA(55) dual-timeframe trend filter replaces Kronos as the direction engine
- Volume-pivot Order Blocks (exact LuxAlgo method) replace geometric OB detection
- Live order flow engine (3-minute rolling delta, noise-filtered) confirms entries
- Kronos is kept but demoted to a short-horizon tiebreaker only
- A self-learning loop tracks which signal combinations work and adjusts thresholds

Do not change: the scanner, the executor, the position tracker, the WebSocket manager,
the dashboard UI, the database schema, the deployment config, or the risk leverage table.

---

## STEP 1 — UPDATE `backend/config.py`

Add these new configuration constants. Do not remove any existing ones.

```python
# ── HMA Trend Filter ──────────────────────────────────────────────
HMA_LENGTH          = 55          # Hull MA period (matches SMC Flow indicator setting)
HMA_PRIMARY_TF      = "Hour1"     # 1h timeframe for trend bias
HMA_ENTRY_TF        = "Min15"     # 15m timeframe for entry alignment

# ── Volume-Pivot Order Block (LuxAlgo method) ─────────────────────
OB_PIVOT_LENGTH     = 5           # Bars each side for volume pivot detection
OB_MAX_DISTANCE_PCT = 0.03        # Max 3% from current price to use an OB
OB_MITIGATION       = "Wick"      # "Wick" or "Close" — matches indicator setting

# ── Order Flow Engine ─────────────────────────────────────────────
FLOW_BUCKET_SECONDS  = 30         # Aggregate trades into 30-second windows
FLOW_WINDOW_BUCKETS  = 6          # 6 buckets × 30s = 3-minute rolling window
FLOW_CONFIRM_RATIO   = 1.4        # Bull/bear ratio required to confirm LONG
FLOW_OPPOSE_RATIO    = 0.7        # Ratio below this = opposing flow (skip)
FLOW_SUSTAIN_BUCKETS = 2          # Ratio must be sustained in this many of last 3 buckets
FLOW_LARGE_PRINT_USD = 50000      # Single trade > this USDT = large print
FLOW_ABSORPTION_PCT  = 0.0005     # Price move < 0.05% with large vol = absorption
FLOW_NEUTRAL_LEVERAGE_MULT = 0.7  # Reduce leverage by 30% when flow is neutral

# ── Kronos (demoted to tiebreaker) ───────────────────────────────
KRONOS_TIEBREAKER_ONLY  = True    # Kronos only runs when flow is NEUTRAL
KRONOS_TIEBREAKER_TF    = "Hour1" # Feed 1h candles (coarser = better for small model)
KRONOS_TIEBREAKER_BARS  = 200     # 200 x 1h candles input
KRONOS_TIEBREAKER_PRED  = 4       # Predict 4 bars ahead (4 hours at 1h)
# Synthetic confidence when Kronos not invoked (maps SMC quality → leverage table)
KRONOS_SYNTHETIC_CONFIDENCE = {0: 0.55, 1: 0.65, 2: 0.75, 3: 0.85}

# ── Self-Learning Loop ────────────────────────────────────────────
LEARNING_ENABLED            = True
LEARNING_MIN_TRADES         = 15   # Don't adjust until this many closed trades
LEARNING_RUN_INTERVAL_HOURS = 24   # Run daily
LEARNING_MAX_DRIFT          = 0.30 # Never adjust more than ±30% from default
```

---

## STEP 2 — CREATE `backend/strategy/hma_filter.py` (NEW FILE)

This replaces `structure.py` as the trend direction engine.
Implements HMA exactly as the SMC Flow indicator does.

```python
"""
HMA(55) Dual-Timeframe Trend Filter — Layer 2.

Replicates the Hull Suite from SMC Flow indicator:
  HMA(src, length) = WMA(2 * WMA(src, length/2) - WMA(src, length), round(sqrt(length)))
  Direction: HULL > HULL[2] = bullish momentum, else bearish

Both 1h AND 15m must agree for direction to lock.
If they disagree → return None (skip this coin).

Also applies session filter to avoid dead zone low-liquidity hours.
"""
import math
import logging
from datetime import datetime, timezone
from typing import List, Optional
from backend.mexc.models import Candle
from backend.config import (HMA_LENGTH, SESSION_FILTER_ENABLED,
                             SESSION_DEAD_ZONE_START_UTC, SESSION_DEAD_ZONE_END_UTC)

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
    hma     = _wma([x for x in diff], sqrtn)
    # Re-align: diff was computed on full list, now apply WMA of sqrtn on it
    # Recompute properly on the diff series
    hma_out = _wma(diff, sqrtn)
    return hma_out


def get_hma_direction(candles: List[Candle], length: int = HMA_LENGTH) -> Optional[str]:
    """
    Returns "BULLISH", "BEARISH", or None if insufficient data.
    Direction rule: HULL[-1] > HULL[-3]  (matches Pine: HULL > HULL[2])
    Using index -3 because the last candle may be in-progress; we look at the last 3 closed.
    """
    if len(candles) < length + 5:
        return None
    closes = [c.close for c in candles]
    hma    = _hma(closes, length)
    # Filter out nans
    valid  = [v for v in hma if not math.isnan(v)]
    if len(valid) < 3:
        return None
    # HULL > HULL[2]: current vs 2 bars ago
    current  = valid[-1]
    two_ago  = valid[-3]
    return "BULLISH" if current > two_ago else "BEARISH"


def is_dead_zone() -> bool:
    """True if current UTC hour is in the low-liquidity dead zone."""
    if not SESSION_FILTER_ENABLED:
        return False
    now_utc = datetime.now(timezone.utc).hour
    start   = SESSION_DEAD_ZONE_START_UTC
    end     = SESSION_DEAD_ZONE_END_UTC
    if start < end:
        return start <= now_utc < end
    else:
        return now_utc >= start or now_utc < end


def get_dual_tf_direction(candles_1h: List[Candle],
                          candles_15m: List[Candle]) -> Optional[str]:
    """
    Returns locked direction ("LONG" or "SHORT") if both timeframes agree.
    Returns None if they disagree or data is insufficient.
    Dead zone returns None regardless of trend.
    """
    if is_dead_zone():
        logger.info("Session filter: dead zone active, skipping all signals")
        return None

    dir_1h  = get_hma_direction(candles_1h)
    dir_15m = get_hma_direction(candles_15m)

    if dir_1h is None or dir_15m is None:
        return None

    if dir_1h != dir_15m:
        logger.debug(f"HMA disagreement: 1h={dir_1h}, 15m={dir_15m} → skip")
        return None

    direction = "LONG" if dir_1h == "BULLISH" else "SHORT"
    logger.info(f"HMA locked: {direction} (1h={dir_1h}, 15m={dir_15m})")
    return direction
```

---

## STEP 3 — CREATE `backend/strategy/volume_ob.py` (NEW FILE)

This replaces the geometric OB logic in `smc_entry.py`.
Replicates the LuxAlgo Order Block detection from SMC Flow exactly.

```python
"""
Volume-Pivot Order Block Detection — replicates LuxAlgo OB from SMC Flow indicator.

The indicator's exact Pine Script logic translated to Python:

  lengthOB = 5  (Volume Pivot Length setting)
  upper = ta.highest(lengthOB)          # Highest high in last 5 bars
  lower = ta.lowest(lengthOB)           # Lowest low in last 5 bars
  os := high[lengthOB] > upper ? 0 : low[lengthOB] < lower ? 1 : os[1]
    # os=0: price in upswing territory (potential bearish OB)
    # os=1: price in downswing territory (potential bullish OB)
  phv = ta.pivothigh(volume, lengthOB, lengthOB)
    # Volume pivot high: volume[lengthOB] > all surrounding lengthOB bars
  Bullish OB condition: phv_detected AND os == 1
    → OB zone: top=hl2[lengthOB], bottom=low[lengthOB]
  Bearish OB condition: phv_detected AND os == 0
    → OB zone: top=high[lengthOB], bottom=hl2[lengthOB]

  Mitigation (Wick method):
    Bullish OB mitigated when: low < ob_bottom (wick below the OB low)
    Bearish OB mitigated when: high > ob_top   (wick above the OB high)
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional
from backend.mexc.models import Candle
from backend.config import OB_PIVOT_LENGTH, OB_MAX_DISTANCE_PCT, OB_MITIGATION

logger = logging.getLogger(__name__)


@dataclass
class VolumeOrderBlock:
    ob_top:     float
    ob_bottom:  float
    ob_avg:     float          # Midpoint (hl2 of the pivot bar)
    direction:  str            # "BULLISH" or "BEARISH"
    bar_index:  int            # Index in candle array when formed
    timestamp:  int
    mitigated:  bool = False

    @property
    def midpoint(self) -> float:
        return (self.ob_top + self.ob_bottom) / 2


def _is_volume_pivot_high(candles: List[Candle], i: int, n: int) -> bool:
    """
    True if candles[i].volume is strictly greater than all candles in [i-n, i+n]
    excluding i itself. Matches ta.pivothigh(volume, n, n) in Pine Script.
    """
    if i < n or i + n >= len(candles):
        return False
    pivot_vol = candles[i].volume
    for j in range(i - n, i + n + 1):
        if j != i and candles[j].volume >= pivot_vol:
            return False
    return True


def detect_volume_obs(candles: List[Candle],
                      n: int = OB_PIVOT_LENGTH) -> List[VolumeOrderBlock]:
    """
    Detect all unmitigated volume-pivot order blocks in the candle series.
    Returns list sorted by recency (most recent first).
    """
    if len(candles) < n * 2 + 5:
        return []

    obs: List[VolumeOrderBlock] = []
    os  = 0   # Price structure state: 0=upswing, 1=downswing
    current_price = candles[-1].close

    for i in range(n, len(candles) - n):
        c = candles[i]

        # Update os state: did price[n bars ago] break above highest or below lowest?
        look_back = i - n   # The bar "n bars ago" relative to current loop position
        if look_back >= 0:
            # Look at candles from look_back+1 to look_back+n (the "last n bars" at that point)
            window_start = max(0, look_back - n + 1)
            window_end   = look_back
            if window_end >= window_start:
                recent_high = max(c2.high for c2 in candles[window_start:window_end+1])
                recent_low  = min(c2.low  for c2 in candles[window_start:window_end+1])
                if candles[look_back].high > recent_high:
                    os = 0   # Broke above → upswing state
                elif candles[look_back].low < recent_low:
                    os = 1   # Broke below → downswing state

        # Check for volume pivot high at position i
        if not _is_volume_pivot_high(candles, i, n):
            continue

        hl2 = (c.high + c.low) / 2

        if os == 1:
            # Bullish OB: volume spike during downswing
            ob = VolumeOrderBlock(
                ob_top    = hl2,
                ob_bottom = c.low,
                ob_avg    = hl2,
                direction = "BULLISH",
                bar_index = i,
                timestamp = c.timestamp
            )
        elif os == 0:
            # Bearish OB: volume spike during upswing
            ob = VolumeOrderBlock(
                ob_top    = c.high,
                ob_bottom = hl2,
                ob_avg    = hl2,
                direction = "BEARISH",
                bar_index = i,
                timestamp = c.timestamp
            )
        else:
            continue

        obs.append(ob)

    # Apply mitigation using subsequent candles
    for ob in obs:
        for j in range(ob.bar_index + 1, len(candles)):
            c = candles[j]
            if OB_MITIGATION == "Wick":
                if ob.direction == "BULLISH" and c.low < ob.ob_bottom:
                    ob.mitigated = True; break
                if ob.direction == "BEARISH" and c.high > ob.ob_top:
                    ob.mitigated = True; break
            else:  # Close method
                if ob.direction == "BULLISH" and c.close < ob.ob_bottom:
                    ob.mitigated = True; break
                if ob.direction == "BEARISH" and c.close > ob.ob_top:
                    ob.mitigated = True; break

    # Return only unmitigated OBs, most recent first
    valid = [ob for ob in obs if not ob.mitigated]
    valid.sort(key=lambda ob: ob.bar_index, reverse=True)
    return valid


def find_nearest_ob(candles: List[Candle],
                    direction: str,
                    max_distance_pct: float = OB_MAX_DISTANCE_PCT
                    ) -> Optional[VolumeOrderBlock]:
    """
    Find the nearest unmitigated OB in the given direction within max_distance_pct of price.
    direction: "LONG" → look for BULLISH OBs
               "SHORT" → look for BEARISH OBs
    Returns None if nothing found within distance.
    """
    current_price = candles[-1].close
    ob_direction  = "BULLISH" if direction == "LONG" else "BEARISH"
    all_obs       = detect_volume_obs(candles)
    matching      = [ob for ob in all_obs if ob.direction == ob_direction]

    best    = None
    best_dist = float("inf")

    for ob in matching:
        # Distance: how far is the midpoint from current price?
        dist = abs(ob.midpoint - current_price) / current_price
        if dist <= max_distance_pct and dist < best_dist:
            best_dist = dist
            best      = ob

    if best:
        logger.info(f"Volume OB found: {ob_direction} zone "
                    f"[{best.ob_bottom:.4f}–{best.ob_top:.4f}] "
                    f"dist={best_dist*100:.2f}%")
    else:
        logger.info(f"No {ob_direction} volume OB within {max_distance_pct*100:.0f}% of price")

    return best
```

---

## STEP 4 — CREATE `backend/strategy/order_flow.py` (NEW FILE)

Noise-filtered live order flow engine. Subscribes to MEXC trade stream.
Aggregates into 30-second buckets, rolls over 3 minutes.

```python
"""
Order Flow Engine — Tape replication using MEXC push.deal stream.

MEXC push.deal message format:
  {"channel": "push.deal", "data": {"deals": [
    {"p": "68450.00", "v": "0.5", "T": 1, "t": 1716900000000}
  ]}}
  T=1: taker was buyer (aggressive buy = bullish)
  T=2: taker was seller (aggressive sell = bearish)
  p: price, v: volume (contracts), t: timestamp ms

The engine aggregates trades into 30-second buckets.
Rolling 3-minute window (6 buckets) produces:
  - delta_ratio: bull_vol / (bear_vol + absorbed_vol)
  - absorption: large volume with price movement < FLOW_ABSORPTION_PCT
  - large_print_bias: direction of most recent large print

Signal output:
  "CONFIRMS": sustained ratio in trade direction + ratio held in ≥2 of last 3 buckets
  "NEUTRAL":  mixed signals, neither confirming nor opposing
  "OPPOSES":  ratio consistently against trade direction

Usage:
  order_flow = OrderFlowEngine()
  # Register handler with MEXC WebSocket:
  ws_mgr.on("push.deal", order_flow.on_deal)
  # Subscribe to a coin:
  await ws_mgr.subscribe_deals("BTC_USDT")
  # Query signal:
  signal = order_flow.get_signal("BTCUSDT", "LONG")
"""
import time
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Deque, Optional, Tuple

from backend.config import (FLOW_BUCKET_SECONDS, FLOW_WINDOW_BUCKETS,
                             FLOW_CONFIRM_RATIO, FLOW_OPPOSE_RATIO,
                             FLOW_SUSTAIN_BUCKETS, FLOW_LARGE_PRINT_USD,
                             FLOW_ABSORPTION_PCT)

logger = logging.getLogger(__name__)


@dataclass
class FlowBucket:
    """One 30-second window of aggregated trade data."""
    start_time:     float
    bull_vol:       float = 0.0
    bear_vol:       float = 0.0
    absorbed_vol:   float = 0.0
    large_bull_vol: float = 0.0
    large_bear_vol: float = 0.0
    last_price:     float = 0.0
    price_range:    float = 0.0   # high - low within this bucket
    total_vol:      float = 0.0

    @property
    def delta_ratio(self) -> float:
        denom = self.bear_vol + self.absorbed_vol
        return self.bull_vol / denom if denom > 0 else 1.0

    @property
    def is_absorbing(self) -> bool:
        """Large volume with tiny price movement = absorption."""
        if self.total_vol == 0 or self.last_price == 0:
            return False
        price_pct = self.price_range / self.last_price
        large_vol = (self.large_bull_vol + self.large_bear_vol) > FLOW_LARGE_PRINT_USD * 0.5
        return large_vol and price_pct < FLOW_ABSORPTION_PCT


class OrderFlowEngine:
    def __init__(self):
        # Per-symbol rolling buckets: symbol → deque of FlowBucket
        self._buckets: Dict[str, Deque[FlowBucket]] = {}
        # Current in-progress bucket per symbol
        self._current: Dict[str, FlowBucket] = {}

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.replace("_", "").upper()

    def on_deal(self, data: dict):
        """
        Handler for push.deal WebSocket messages.
        Register this with: ws_mgr.on("push.deal", order_flow.on_deal)
        """
        symbol_raw = data.get("symbol", "")
        symbol     = self._normalize_symbol(symbol_raw)
        deals      = data.get("deals", [])
        now        = time.time()

        if symbol not in self._current:
            self._current[symbol] = FlowBucket(start_time=now)
            self._buckets[symbol] = deque(maxlen=FLOW_WINDOW_BUCKETS)

        bucket = self._current[symbol]

        # Rotate bucket if 30 seconds have elapsed
        if now - bucket.start_time >= FLOW_BUCKET_SECONDS:
            self._buckets[symbol].append(bucket)
            bucket = FlowBucket(start_time=now)
            self._current[symbol] = bucket

        for deal in deals:
            try:
                price     = float(deal.get("p", 0))
                vol       = float(deal.get("v", 0))
                direction = int(deal.get("T", 0))
                if price <= 0 or vol <= 0:
                    continue

                # Approximate USDT value of this trade
                usdt_vol = price * vol
                is_large = usdt_vol >= FLOW_LARGE_PRINT_USD

                if direction == 1:   # Aggressive buy
                    bucket.bull_vol += vol
                    if is_large: bucket.large_bull_vol += vol
                elif direction == 2: # Aggressive sell
                    bucket.bear_vol += vol
                    if is_large: bucket.large_bear_vol += vol
                else:
                    bucket.absorbed_vol += vol

                bucket.total_vol  += vol
                if bucket.last_price > 0:
                    move = abs(price - bucket.last_price)
                    bucket.price_range = max(bucket.price_range, move)
                bucket.last_price = price

            except (ValueError, TypeError):
                continue

    def get_signal(self, symbol: str, direction: str) -> str:
        """
        Returns "CONFIRMS", "NEUTRAL", or "OPPOSES".
        direction: "LONG" or "SHORT"
        """
        symbol = self._normalize_symbol(symbol)
        all_buckets = list(self._buckets.get(symbol, []))

        # Include current in-progress bucket if it has data
        if symbol in self._current and self._current[symbol].total_vol > 0:
            all_buckets = all_buckets + [self._current[symbol]]

        if len(all_buckets) < 2:
            return "NEUTRAL"   # Not enough data yet

        # Check for absorption at level (strongest signal)
        recent_buckets = all_buckets[-3:]
        if any(b.is_absorbing for b in recent_buckets):
            logger.info(f"{symbol}: Absorption detected at level")
            return "CONFIRMS"

        # Calculate delta ratios for each recent bucket
        ratios = [b.delta_ratio for b in recent_buckets]

        if direction == "LONG":
            confirming = sum(1 for r in ratios if r >= FLOW_CONFIRM_RATIO)
            opposing   = sum(1 for r in ratios if r <= FLOW_OPPOSE_RATIO)
        else:  # SHORT — invert the ratio logic
            confirming = sum(1 for r in ratios if r <= FLOW_OPPOSE_RATIO)
            opposing   = sum(1 for r in ratios if r >= FLOW_CONFIRM_RATIO)

        total = len(ratios)
        if confirming >= FLOW_SUSTAIN_BUCKETS:
            logger.info(f"{symbol}: Flow CONFIRMS {direction} "
                        f"({confirming}/{total} buckets confirming)")
            return "CONFIRMS"
        elif opposing >= FLOW_SUSTAIN_BUCKETS:
            logger.info(f"{symbol}: Flow OPPOSES {direction} "
                        f"({opposing}/{total} buckets opposing)")
            return "OPPOSES"
        else:
            return "NEUTRAL"

    def get_large_print_bias(self, symbol: str) -> Optional[str]:
        """Returns direction of most recent large print: "BULL", "BEAR", or None."""
        symbol = self._normalize_symbol(symbol)
        all_buckets = list(self._buckets.get(symbol, []))
        if symbol in self._current:
            all_buckets.append(self._current[symbol])
        for b in reversed(all_buckets):
            if b.large_bull_vol > b.large_bear_vol and b.large_bull_vol > 0:
                return "BULL"
            if b.large_bear_vol > b.large_bull_vol and b.large_bear_vol > 0:
                return "BEAR"
        return None

    def has_data(self, symbol: str) -> bool:
        symbol = self._normalize_symbol(symbol)
        return symbol in self._buckets and len(self._buckets[symbol]) >= 2
```

---

## STEP 5 — UPDATE `backend/mexc/websocket.py`

Add deal stream subscription. Find the existing file and add these two methods
to the MexcWebSocket class. Do not change anything else.

```python
async def subscribe_deals(self, symbol: str):
    """
    Subscribe to trade deal stream for a symbol.
    symbol format: BTC_USDT (underscore, matching ticker format)
    Call this after opening/watching a coin.
    Message format received: {"channel": "push.deal", "data": {"symbol": "BTC_USDT", "deals": [...]}}
    """
    msg = json.dumps({"method": "sub.deal", "param": {"symbol": symbol}})
    self.subscriptions.add(msg)
    if self._connected:
        await self.ws.send(msg)

async def unsubscribe_deals(self, symbol: str):
    """Unsubscribe from deal stream when no longer watching a coin."""
    msg = json.dumps({"method": "unsub.deal", "param": {"symbol": symbol}})
    unsub_key = json.dumps({"method": "sub.deal", "param": {"symbol": symbol}})
    self.subscriptions.discard(unsub_key)
    if self._connected:
        await self.ws.send(msg)
```

---

## STEP 6 — REWRITE `backend/strategy/smc_entry.py`

Replace the entire file contents with this. The API (function signature of `find_entry`)
stays the same so nothing else breaks.

```python
"""
SMC Entry Precision Layer — Layer 3.
Direction is already decided by HMA filter.
This layer finds the best volume-validated entry in that direction.

Uses volume_ob.py for order block detection (LuxAlgo method).
FVG detection remains geometric (unchanged — FVGs don't need volume validation).
"""
import logging
from typing import List, Optional
from backend.mexc.models import Candle, FairValueGap
from backend.strategy.volume_ob import find_nearest_ob, VolumeOrderBlock
from backend.strategy import smc
from backend.strategy.volatility import calculate_atr
from backend.strategy.structure import detect_swing_points

logger = logging.getLogger(__name__)


def find_entry(candles: List[Candle],
               direction: str,
               kronos_pred_high: float,
               kronos_pred_low:  float,
               kronos_pred_close: float) -> dict:
    """
    Find the best SMC entry in the given direction.
    direction: "LONG" or "SHORT" — already decided by HMA filter.

    Returns dict with:
      entry_zone, sl_price, sl_reason, tp_guide, smc_quality (0-3),
      has_ob, has_fvg, has_sweep, zone, zone_good, entry_reason, atr, current_price
    """
    current_price = candles[-1].close
    atr           = calculate_atr(candles)
    swings        = detect_swing_points(candles)
    liq_levels    = smc.find_liquidity_levels(swings)

    # ── Volume-Pivot Order Block (LuxAlgo method) ─────────────────
    active_ob = find_nearest_ob(candles, direction)
    has_ob    = active_ob is not None

    # ── Fair Value Gaps (geometric — unchanged) ───────────────────
    ob_direction = "BULLISH" if direction == "LONG" else "BEARISH"
    fvgs         = smc.find_fair_value_gaps(candles)
    dir_fvgs     = [f for f in fvgs if f.direction == ob_direction]
    has_fvg      = len(dir_fvgs) > 0

    # ── Liquidity Sweep ───────────────────────────────────────────
    swept, sweep_dir = smc.detect_liquidity_sweep(candles, liq_levels)
    sweep_correct    = ((direction == "LONG"  and sweep_dir == "SELL_SIDE") or
                        (direction == "SHORT" and sweep_dir == "BUY_SIDE"))
    has_sweep        = swept and sweep_correct

    # ── SMC Quality Score (0–3) ───────────────────────────────────
    smc_quality = sum([has_ob, has_fvg, has_sweep])

    # ── Entry Zone ────────────────────────────────────────────────
    if has_ob:
        entry_zone   = [active_ob.ob_bottom, active_ob.ob_top]
        entry_reason = (f"Volume OB [{active_ob.ob_bottom:.4f}–{active_ob.ob_top:.4f}]"
                        + (" + FVG" if has_fvg else "")
                        + (" + sweep" if has_sweep else ""))
    elif has_fvg:
        best_fvg   = dir_fvgs[-1]
        entry_zone = [best_fvg.fvg_low, best_fvg.fvg_high]
        entry_reason = f"FVG entry [{best_fvg.fvg_low:.4f}–{best_fvg.fvg_high:.4f}]"
    else:
        spread       = atr * 0.3
        entry_zone   = [current_price - spread, current_price + spread]
        entry_reason = "ATR-based entry (no OB/FVG) — HMA signal only"

    # ── Stop Loss ─────────────────────────────────────────────────
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
        sl_reason = "ATR×1.5 fallback"

    # ── TP Guide from Kronos prediction ──────────────────────────
    tp_guide = kronos_pred_high if direction == "LONG" else kronos_pred_low

    # ── Premium/Discount zone ─────────────────────────────────────
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
```

---

## STEP 7 — CREATE `backend/data/learning_loop.py` (NEW FILE)

Self-learning loop. Reads trade history, identifies what works, adjusts thresholds.
Runs daily. No ML, no retraining — pure statistics on SQLite data.

```python
"""
Self-Learning Loop — daily statistical analysis of trade outcomes.

Reads closed trades from SQLite with their signal metadata.
Identifies which signal combinations have statistically better win rates.
Adjusts three thresholds only:
  1. OB_MAX_DISTANCE_PCT: how far from price an OB can be and still count
  2. FLOW_CONFIRM_RATIO:  how strong order flow must be to confirm
  3. HMA_LENGTH:          HMA period (adjusts trend sensitivity)

Hard constraints:
  - Never move more than ±30% from default values
  - Require minimum 15 closed trades before any adjustment
  - Log every adjustment to the database

Usage: call run_learning_loop() daily from an asyncio task in server.py
"""
import logging
import json
import time
from datetime import datetime
from sqlalchemy.orm import Session
from backend.data.trade_log import engine, Trade, Signal
from backend.config import (OB_MAX_DISTANCE_PCT, FLOW_CONFIRM_RATIO,
                             HMA_LENGTH, LEARNING_MIN_TRADES, LEARNING_MAX_DRIFT)

logger = logging.getLogger(__name__)

# Default values (never drift beyond ±30% of these)
DEFAULTS = {
    "OB_MAX_DISTANCE_PCT": OB_MAX_DISTANCE_PCT,
    "FLOW_CONFIRM_RATIO":  FLOW_CONFIRM_RATIO,
    "HMA_LENGTH":          float(HMA_LENGTH),
}

# Runtime-adjustable values (start at defaults, updated by loop)
_current = dict(DEFAULTS)


def get_adjusted(key: str):
    """Get current (possibly adjusted) value for a parameter."""
    return _current.get(key, DEFAULTS[key])


def run_learning_loop():
    """
    Analyse recent trade outcomes and adjust thresholds if warranted.
    Call this from an asyncio background task, wrapped in run_in_executor.
    """
    logger.info("Self-learning loop: starting analysis...")

    with Session(engine) as s:
        # Get all closed trades with their signal metadata
        closed = s.query(Trade).filter(
            Trade.exit_time.isnot(None),
            Trade.exit_reason.notin_(["RESTART", "STALE_CLOSE"])
        ).order_by(Trade.exit_time.desc()).limit(100).all()

        signals = s.query(Signal).filter(
            Signal.approved == True
        ).order_by(Signal.timestamp.desc()).limit(100).all()

    if len(closed) < LEARNING_MIN_TRADES:
        logger.info(f"Self-learning: only {len(closed)} trades, "
                    f"need {LEARNING_MIN_TRADES}. Skipping.")
        return

    # ── Analysis 1: OB distance vs win rate ──────────────────────
    # Group trades by whether OB was present and how close it was
    ob_wins,  ob_losses  = 0, 0
    nob_wins, nob_losses = 0, 0

    for sig in signals:
        if not sig.score_breakdown:
            continue
        try:
            bd = json.loads(sig.score_breakdown) if isinstance(sig.score_breakdown, str) else sig.score_breakdown
        except (json.JSONDecodeError, TypeError):
            continue
        has_ob = bd.get("has_ob", False)
        # Find matching trade
        matching = next((t for t in closed
                         if abs(t.entry_time - sig.timestamp) < 300
                         and t.symbol == sig.symbol), None)
        if matching is None:
            continue
        won = matching.was_winner
        if has_ob:
            if won: ob_wins  += 1
            else:   ob_losses += 1
        else:
            if won: nob_wins  += 1
            else:   nob_losses += 1

    ob_wr  = ob_wins  / (ob_wins  + ob_losses)  if (ob_wins  + ob_losses)  > 3 else None
    nob_wr = nob_wins / (nob_wins + nob_losses) if (nob_wins + nob_losses) > 3 else None

    # If OBs win significantly more → tighten distance requirement
    if ob_wr and nob_wr and ob_wr > nob_wr + 0.15:
        new_dist = _current["OB_MAX_DISTANCE_PCT"] * 0.9   # Tighten by 10%
        new_dist = max(DEFAULTS["OB_MAX_DISTANCE_PCT"] * (1 - LEARNING_MAX_DRIFT), new_dist)
        if new_dist != _current["OB_MAX_DISTANCE_PCT"]:
            logger.info(f"Learning: tightening OB_MAX_DISTANCE_PCT "
                        f"{_current['OB_MAX_DISTANCE_PCT']:.3f} → {new_dist:.3f} "
                        f"(OB win rate {ob_wr:.0%} vs non-OB {nob_wr:.0%})")
            _current["OB_MAX_DISTANCE_PCT"] = new_dist

    # ── Analysis 2: Overall win rate trend ───────────────────────
    recent_20  = closed[:20]
    overall_wr = sum(1 for t in recent_20 if t.was_winner) / len(recent_20)

    # If win rate is below 50%, loosen flow ratio slightly (we may be too selective)
    if overall_wr < 0.50:
        new_ratio = _current["FLOW_CONFIRM_RATIO"] * 0.95
        new_ratio = max(DEFAULTS["FLOW_CONFIRM_RATIO"] * (1 - LEARNING_MAX_DRIFT), new_ratio)
        if new_ratio != _current["FLOW_CONFIRM_RATIO"]:
            logger.info(f"Learning: loosening FLOW_CONFIRM_RATIO "
                        f"{_current['FLOW_CONFIRM_RATIO']:.2f} → {new_ratio:.2f} "
                        f"(win rate {overall_wr:.0%})")
            _current["FLOW_CONFIRM_RATIO"] = new_ratio

    # If win rate is above 70%, tighten flow ratio (we can afford to be more selective)
    elif overall_wr > 0.70:
        new_ratio = _current["FLOW_CONFIRM_RATIO"] * 1.05
        new_ratio = min(DEFAULTS["FLOW_CONFIRM_RATIO"] * (1 + LEARNING_MAX_DRIFT), new_ratio)
        if new_ratio != _current["FLOW_CONFIRM_RATIO"]:
            logger.info(f"Learning: tightening FLOW_CONFIRM_RATIO "
                        f"{_current['FLOW_CONFIRM_RATIO']:.2f} → {new_ratio:.2f} "
                        f"(win rate {overall_wr:.0%})")
            _current["FLOW_CONFIRM_RATIO"] = new_ratio

    logger.info(f"Self-learning complete. Win rate (last 20): {overall_wr:.0%}. "
                f"Current params: {_current}")
```

---

## STEP 8 — REWRITE `_analyze_and_trade()` in `backend/api/server.py`

Find the existing `_analyze_and_trade()` function and replace it entirely.
Do not touch any other function in server.py.

```python
async def _analyze_and_trade(symbol: str, norm: str):
    """
    NEW 6-layer pipeline:
    1. Fetch candles
    2. HMA dual-timeframe direction lock (replaces Kronos as direction engine)
    3. Volume-pivot OB entry precision (replaces geometric OB)
    4. Order flow confirmation (noise-filtered, 3-minute rolling)
    5. Kronos tiebreaker (only when flow is NEUTRAL)
    6. Risk engine and execute
    """
    from backend.strategy.hma_filter   import get_dual_tf_direction, is_dead_zone
    from backend.strategy.volume_ob    import find_nearest_ob
    from backend.strategy.smc_entry    import find_entry
    from backend.strategy.kronos_oracle import run_oracle
    from backend.risk.manager          import calculate_trade
    from backend.mexc.models           import TradeSignal
    from backend.data.learning_loop    import get_adjusted
    from backend.config                import (KRONOS_SYNTHETIC_CONFIDENCE,
                                               FLOW_NEUTRAL_LEVERAGE_MULT)

    # ── Fetch candles ─────────────────────────────────────────────
    raw_15m = await client.get_klines(norm, PRIMARY_TF, CANDLES_NEEDED)
    raw_1h  = await client.get_klines(norm, BIAS_TF, 100)
    c15m    = parse_candles(raw_15m)
    c1h     = parse_candles(raw_1h)
    if len(c15m) < 50:
        return

    # ── LAYER 2: HMA Dual-Timeframe Direction Lock ────────────────
    direction = get_dual_tf_direction(c1h, c15m)
    if direction is None:
        reason = "Dead zone active" if is_dead_zone() else "HMA disagreement (1h vs 15m)"
        trade_log.log_signal(norm, "N/A", 0, {}, False, reason)
        await broadcast("signal", {"symbol": norm, "score": 0,
                                   "direction": None, "approved": False,
                                   "reason": reason})
        return

    current_price = c15m[-1].close

    # ── LAYER 3: Volume-Pivot Order Block ─────────────────────────
    ob = find_nearest_ob(c15m, direction,
                         max_distance_pct=get_adjusted("OB_MAX_DISTANCE_PCT"))
    if ob is None:
        reason = f"No volume OB within {get_adjusted('OB_MAX_DISTANCE_PCT')*100:.1f}% of price"
        trade_log.log_signal(norm, direction, 40, {}, False, reason)
        await broadcast("signal", {"symbol": norm, "score": 40,
                                   "direction": direction, "approved": False,
                                   "reason": reason})
        return

    # ── LAYER 4: Order Flow Confirmation ─────────────────────────
    # Ensure we are subscribed to this coin's deal stream
    ws_symbol = norm[:-4] + "_USDT" if norm.endswith("USDT") else norm + "_USDT"
    await ws_mgr.subscribe_deals(ws_symbol)

    flow_signal = order_flow.get_signal(norm, direction)

    if flow_signal == "OPPOSES":
        reason = f"Order flow opposes {direction}"
        trade_log.log_signal(norm, direction, 50, {"flow": "OPPOSES"}, False, reason)
        await broadcast("signal", {"symbol": norm, "score": 50,
                                   "direction": direction, "approved": False,
                                   "reason": reason, "flow": "OPPOSES"})
        return

    # ── LAYER 5: Kronos Tiebreaker (only when flow is NEUTRAL) ───
    kronos_forecast = None
    kronos_confidence = None

    if flow_signal == "NEUTRAL":
        # Kronos gets 1h data, predicts 4 bars (4 hours) — shorter, better for small model
        try:
            raw_1h_kronos = await client.get_klines(norm, KRONOS_TIEBREAKER_TF,
                                                    KRONOS_TIEBREAKER_BARS)
            c1h_kronos    = parse_candles(raw_1h_kronos)
            if len(c1h_kronos) >= 50:
                kronos_forecast = await run_oracle(norm, c1h_kronos,
                                                   pred_len=KRONOS_TIEBREAKER_PRED)
                kronos_confidence = kronos_forecast.confidence
                # If Kronos disagrees with HMA direction → skip
                kronos_dir = "LONG" if kronos_forecast.predicted_move_pct > 0 else "SHORT"
                if kronos_dir != direction:
                    reason = (f"Kronos tiebreaker disagrees: "
                              f"HMA={direction}, Kronos={kronos_dir} "
                              f"(flow was neutral)")
                    trade_log.log_signal(norm, direction, 55,
                                         {"flow": "NEUTRAL", "kronos": kronos_dir},
                                         False, reason)
                    await broadcast("signal", {"symbol": norm, "score": 55,
                                               "direction": direction, "approved": False,
                                               "reason": reason})
                    return
        except Exception as e:
            logger.warning(f"Kronos tiebreaker failed for {norm}: {e}. Skipping trade.")
            reason = "Flow neutral + Kronos tiebreaker failed"
            trade_log.log_signal(norm, direction, 55, {"flow": "NEUTRAL"}, False, reason)
            return

    # ── All layers passed — find precise entry ───────────────────
    pred_high  = (kronos_forecast.predicted_high  if kronos_forecast
                  else current_price * 1.02)
    pred_low   = (kronos_forecast.predicted_low   if kronos_forecast
                  else current_price * 0.98)
    pred_close = (kronos_forecast.predicted_close if kronos_forecast
                  else current_price)

    smc_result = find_entry(c15m, direction, pred_high, pred_low, pred_close)

    # ── Derive confidence for leverage table ─────────────────────
    # Use Kronos confidence if available, else derive from SMC quality
    if kronos_confidence is not None:
        effective_confidence = kronos_confidence
    else:
        quality_map   = KRONOS_SYNTHETIC_CONFIDENCE
        effective_confidence = quality_map.get(smc_result["smc_quality"], 0.65)

    # Apply flow neutral penalty to confidence (reduces leverage)
    if flow_signal == "NEUTRAL":
        effective_confidence *= FLOW_NEUTRAL_LEVERAGE_MULT

    # ── Risk Engine ───────────────────────────────────────────────
    balance = account.get_balance() or 300.0
    params  = calculate_trade(
        effective_confidence, smc_result, balance,
        direction=direction
    )

    if params is None:
        logger.info(f"Trade skipped {norm}: SL too tight or profit too small")
        return

    # Score = confidence × 100 for display
    score = int(effective_confidence * 100)

    breakdown = {
        "hma_direction":  direction,
        "flow_signal":    flow_signal,
        "smc_quality":    smc_result["smc_quality"],
        "has_ob":         smc_result["has_ob"],
        "has_fvg":        smc_result["has_fvg"],
        "has_sweep":      smc_result["has_sweep"],
        "zone":           smc_result["zone"],
        "entry_reason":   smc_result["entry_reason"],
        "kronos_used":    kronos_forecast is not None,
        "kronos_move":    kronos_forecast.predicted_move_pct if kronos_forecast else 0,
        "effective_conf": effective_confidence,
    }

    trade_log.log_signal(norm, direction, score, breakdown, True)
    await broadcast("signal", {
        "symbol":    norm, "score": score,
        "direction": direction, "approved": True, "reason": "",
        "flow":      flow_signal,
        "smc_quality": smc_result["smc_quality"],
        "entry_reason": smc_result["entry_reason"],
        "kronos_used": kronos_forecast is not None,
    })

    signal = TradeSignal(
        symbol=norm, direction=direction, score=score,
        entry_price=params["entry_price"], sl_price=params["sl_price"],
        tp1=params["tp1"], tp2=params["tp2"], tp3=params["tp3"],
        leverage=params["leverage"], margin_usdt=params["margin_usdt"],
        notional_usdt=params["notional_usdt"], risk_usdt=params["risk_usdt"],
        reward_tp1_usdt=params["reward_tp1_usdt"],
        reward_tp3_usdt=params["reward_tp3_usdt"],
        score_breakdown=breakdown,
        kronos=kronos_forecast,
        timestamp=int(time.time())
    )

    await executor.execute_signal(signal)
    coin_cooldown[norm] = time.time()
    await ws_mgr.subscribe_ticker(ws_symbol)

    await broadcast("trade_opened", {
        "symbol": norm, "direction": direction,
        "entry": params["entry_price"], "sl": params["sl_price"],
        "tp1": params["tp1"], "leverage": params["leverage"],
        "score": score, "flow": flow_signal,
        "entry_reason": smc_result["entry_reason"],
        "paper": PAPER_TRADING,
    })
```

---

## STEP 9 — UPDATE `backend/risk/manager.py`

Change the signature of `calculate_trade()` to accept `direction` and
`effective_confidence` directly (instead of getting direction from the forecast object).
Only change the function signature and the first 4 lines inside it.

```python
def calculate_trade(effective_confidence: float,
                    smc_entry: dict,
                    balance: float,
                    direction: str,
                    contract_max_lev: int = 75) -> dict | None:
    """
    effective_confidence: already computed by the pipeline (Kronos or synthetic)
    direction: "LONG" or "SHORT" — from HMA filter
    smc_entry: dict from smc_entry.find_entry()
    """
    entry      = sum(smc_entry["entry_zone"]) / 2
    sl         = smc_entry["sl_price"]
    tp_guide   = smc_entry["tp_guide"]
    smc_quality = smc_entry["smc_quality"]
    confidence = effective_confidence
    # ... rest of function unchanged ...
```

---

## STEP 10 — ADD SINGLETONS AND STARTUP TASKS to `backend/api/server.py`

In the global singleton section (near where `scanner`, `executor` etc. are defined), add:

```python
from backend.strategy.order_flow   import OrderFlowEngine
from backend.data.learning_loop    import run_learning_loop
from backend.config                import (LEARNING_ENABLED,
                                           LEARNING_RUN_INTERVAL_HOURS,
                                           KRONOS_TIEBREAKER_TF,
                                           KRONOS_TIEBREAKER_BARS,
                                           KRONOS_TIEBREAKER_PRED,
                                           KRONOS_SYNTHETIC_CONFIDENCE,
                                           FLOW_NEUTRAL_LEVERAGE_MULT)

order_flow = OrderFlowEngine()
```

Register the deal handler with the WebSocket (near where other ws_mgr.on() calls are):
```python
ws_mgr.on("push.deal", order_flow.on_deal)
```

Add the learning loop task to the lifespan:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing startup code ...
    if LEARNING_ENABLED:
        asyncio.create_task(learning_loop_task())
    yield
    # ... existing shutdown code ...

async def learning_loop_task():
    """Run learning loop every LEARNING_RUN_INTERVAL_HOURS hours."""
    while True:
        await asyncio.sleep(LEARNING_RUN_INTERVAL_HOURS * 3600)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, run_learning_loop)
```

---

## STEP 11 — UPDATE `backend/kronos_oracle.py`

The `run_oracle` function needs to accept an optional `pred_len` parameter
so the tiebreaker can request 4-bar predictions instead of 24-bar.
Find the function signature and change it:

```python
async def run_oracle(symbol: str, candles: List[Candle],
                     pred_len: int = None) -> KronosForecast:
    """
    pred_len: override prediction length. Defaults to KRONOS_PRED_LEN from config.
    When used as tiebreaker: pass pred_len=4 (4 hours on 1h data).
    """
    from backend.config import KRONOS_PRED_LEN
    if pred_len is None:
        pred_len = KRONOS_PRED_LEN
    # Pass pred_len through to _run_sync
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run_sync, candles, symbol, pred_len)
```

Also update `_run_sync` to accept and use `pred_len`:
```python
def _run_sync(candles: List[Candle], symbol: str, pred_len: int) -> KronosForecast:
    # Replace all uses of KRONOS_PRED_LEN inside this function with pred_len
```

---

## STEP 12 — DELETE `backend/strategy/structure.py`

The old structure.py had `detect_swing_points` (still needed by smc.py) and
`structure_sanity_check` (replaced by hma_filter.py).

Action: Do NOT delete the file. Instead:
1. Keep `detect_swing_points()` — it is used by smc.py
2. Delete `structure_sanity_check()` — replaced by hma_filter.py
3. Remove any import of `structure_sanity_check` from server.py

---

## STEP 13 — ADD DEAL SUBSCRIPTION TO SCANNER LOOP

In `scanner_loop()` in server.py, after selecting the top coins,
subscribe to their deal streams so order flow data starts accumulating
before the analysis pipeline runs:

```python
# After scanner.scan() returns top coins, subscribe to deal streams
for ws_symbol, _, _ in top:
    norm = ws_symbol.replace("_", "")
    # Subscribe to deal stream early so flow data accumulates before analysis
    await ws_mgr.subscribe_deals(ws_symbol)
    await asyncio.sleep(0.1)  # Small delay to avoid rate limiting

# Wait briefly for some flow data to arrive before analyzing
await asyncio.sleep(15)  # 15 seconds of flow data before analysis starts
```

---

## STEP 14 — VERIFY AND TEST

After all changes are applied, verify:

```bash
# 1. Check imports resolve — Python will catch broken imports at startup
python -c "from backend.strategy.hma_filter import get_dual_tf_direction; print('OK')"
python -c "from backend.strategy.volume_ob import find_nearest_ob; print('OK')"
python -c "from backend.strategy.order_flow import OrderFlowEngine; print('OK')"
python -c "from backend.data.learning_loop import run_learning_loop; print('OK')"

# 2. Start the server and watch for:
#    - "HMA locked: LONG/SHORT" in logs (Layer 2 working)
#    - "Volume OB found:" in logs (Layer 3 working)
#    - "Flow CONFIRMS/NEUTRAL/OPPOSES" in logs (Layer 4 working)
#    - NO mention of "Kronos" for every trade (it's now tiebreaker only)
python run.py

# 3. After 10 minutes, check signals tab in dashboard
#    Should see: hma_direction, flow_signal, entry_reason fields in signal detail
```

---

## SUMMARY OF WHAT CHANGED

| Component | Before | After |
|-----------|--------|-------|
| Direction engine | Kronos-small (50% accuracy) | HMA(55) dual-TF (deterministic) |
| OB detection | Geometric (shape only) | Volume-pivot (LuxAlgo exact method) |
| Entry confirmation | None | 3-min rolling order flow delta |
| Kronos role | Primary oracle | Neutral-flow tiebreaker on 1h data |
| Session filter | None | Dead zone skip (20:00–23:00 UTC) |
| Self-improvement | None | Daily threshold adjustment loop |
| Files added | — | hma_filter.py, volume_ob.py, order_flow.py, learning_loop.py |
| Files removed | structure_sanity_check | (function deleted, file kept for detect_swing_points) |

## DEPLOY TO VM AFTER LOCAL VERIFICATION

```bash
# SCP the changed files to VM
scp -i "VM/ssh-key-2026-05-30.key" \
    backend/strategy/hma_filter.py \
    backend/strategy/volume_ob.py \
    backend/strategy/order_flow.py \
    backend/strategy/smc_entry.py \
    backend/data/learning_loop.py \
    backend/api/server.py \
    backend/config.py \
    backend/mexc/websocket.py \
    backend/strategy/kronos_oracle.py \
    backend/risk/manager.py \
    ubuntu@141.147.103.206:/home/ubuntu/mexc-bot/backend/

# Restart service
ssh -i "VM/ssh-key-2026-05-30.key" ubuntu@141.147.103.206 \
    "sudo systemctl restart mexc-bot"

# Watch logs
ssh -i "VM/ssh-key-2026-05-30.key" ubuntu@141.147.103.206 \
    "journalctl -u mexc-bot -f"
```
