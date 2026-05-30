import asyncio
from collections import defaultdict, deque
from typing import List
from backend.mexc.models import Candle

MAX_CANDLES = 500  # Extra headroom for Kronos's 400-candle lookback


class CandleStore:
    def __init__(self):
        self._store = defaultdict(lambda: defaultdict(lambda: deque(maxlen=MAX_CANDLES)))
        self._lock  = asyncio.Lock()

    async def update(self, symbol: str, tf: str, candle: Candle):
        async with self._lock:
            s = self._store[symbol][tf]
            if s and s[-1].timestamp == candle.timestamp:
                s[-1] = candle          # Replace in-progress candle
            else:
                s.append(candle)

    async def bulk_load(self, symbol: str, tf: str, candles: List[Candle]):
        async with self._lock:
            self._store[symbol][tf].clear()
            for c in candles[-MAX_CANDLES:]:
                self._store[symbol][tf].append(c)

    async def get(self, symbol: str, tf: str, limit: int = None) -> List[Candle]:
        async with self._lock:
            s = list(self._store[symbol][tf])
            return s[-limit:] if limit else s

    async def get_closed(self, symbol: str, tf: str, limit: int = 400) -> List[Candle]:
        all_c = await self.get(symbol, tf, limit + 1)
        return [c for c in all_c if c.is_closed][-limit:]

    def has_data(self, symbol: str, tf: str, minimum: int = 50) -> bool:
        return len(self._store[symbol][tf]) >= minimum


candle_store = CandleStore()
