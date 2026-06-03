"""
Order Flow Engine — Tape replication using MEXC push.deal stream.

MEXC push.deal message format:
  {"channel": "push.deal", "data": {"deals": [
    {"p": "68450.00", "v": "0.5", "T": 1, "t": 1716900000000}
  ]}}
  T=1: taker was buyer (aggressive buy)
  T=2: taker was seller (aggressive sell)
  p: price, v: volume (contracts), t: timestamp ms

Aggregates into 30-second buckets. Rolling 3-minute window (6 buckets).

Signal output:
  "CONFIRMS": sustained ratio in trade direction
  "NEUTRAL":  mixed signals
  "OPPOSES":  ratio consistently against trade direction
"""
import time
import logging
from collections import deque
from dataclasses import dataclass
from typing import Dict, Deque, Optional

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
    price_range:    float = 0.0
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
        self._buckets: Dict[str, Deque[FlowBucket]] = {}
        self._current: Dict[str, FlowBucket] = {}

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.replace("_", "").upper()

    def ingest_deals(self, symbol: str, deals: list):
        """Inject deal data for a symbol (from REST API or WS)."""
        symbol = self._normalize_symbol(symbol)
        now    = time.time()
        if symbol not in self._current:
            self._current[symbol] = FlowBucket(start_time=now)
            self._buckets[symbol] = deque(maxlen=FLOW_WINDOW_BUCKETS)
        bucket = self._current[symbol]
        if now - bucket.start_time >= FLOW_BUCKET_SECONDS:
            self._buckets[symbol].append(bucket)
            bucket = FlowBucket(start_time=now)
            self._current[symbol] = bucket
        for d in deals:
            try:
                if isinstance(d, dict):
                    price     = float(d.get("p", 0))
                    vol       = float(d.get("v", 0))
                    direction = int(d.get("T", 0))
                elif isinstance(d, (list, tuple)) and len(d) >= 4:
                    price     = float(d[1])
                    vol       = float(d[2])
                    direction = int(d[3])
                else:
                    continue
                if price <= 0 or vol <= 0:
                    continue
                usdt_vol = price * vol
                is_large = usdt_vol >= FLOW_LARGE_PRINT_USD
                if direction == 1:
                    bucket.bull_vol += vol
                    if is_large: bucket.large_bull_vol += vol
                elif direction == 2:
                    bucket.bear_vol += vol
                    if is_large: bucket.large_bear_vol += vol
                else:
                    bucket.absorbed_vol += vol
                bucket.total_vol += vol
                if bucket.last_price > 0:
                    move = abs(price - bucket.last_price)
                    bucket.price_range = max(bucket.price_range, move)
                bucket.last_price = price
            except (ValueError, TypeError):
                continue

    async def on_deal(self, data):
        """WS handler for push.deal. data: {"deals": [...], "symbol": "BCH_USDT"}."""
        if isinstance(data, dict):
            symbol = data.get("symbol", "")
            deals = data.get("deals", data.get("data", []))
            if isinstance(deals, dict):
                deals = deals.get("deals", list(deals.values())[0] if deals else [])
            if not isinstance(deals, list):
                deals = []
        else:
            return
        symbol = self._normalize_symbol(symbol)
        if not symbol or not deals:
            return
        self.ingest_deals(symbol, deals)

    def get_signal(self, symbol: str, direction: str) -> str:
        """Returns 'CONFIRMS', 'NEUTRAL', or 'OPPOSES'. direction: LONG or SHORT."""
        symbol = self._normalize_symbol(symbol)
        all_buckets = list(self._buckets.get(symbol, []))

        if symbol in self._current and self._current[symbol].total_vol > 0:
            all_buckets = all_buckets + [self._current[symbol]]

        if len(all_buckets) < 2:
            return "NEUTRAL"

        recent_buckets = all_buckets[-3:]
        if any(b.is_absorbing for b in recent_buckets):
            logger.info(f"{symbol}: Absorption detected at level")
            return "CONFIRMS"

        ratios = [b.delta_ratio for b in recent_buckets]

        if direction == "LONG":
            confirming = sum(1 for r in ratios if r >= FLOW_CONFIRM_RATIO)
            opposing   = sum(1 for r in ratios if r <= FLOW_OPPOSE_RATIO)
        else:  # SHORT
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
        """Returns direction of most recent large print: 'BULL', 'BEAR', or None."""
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
