"""
MEXC Futures WebSocket endpoint: wss://contract.mexc.com/edge

EXACT MESSAGE FORMATS — use these verbatim in your send() calls:

Subscribe to live ticker:
  {"method": "sub.ticker", "param": {"symbol": "BTC_USDT"}}

Subscribe to live klines:
  {"method": "sub.kline",  "param": {"symbol": "BTC_USDT", "interval": "Min15"}}

Authenticate (must do before subscribing to personal channels):
  {
    "method": "login",
    "param": {
      "apiKey": "<YOUR_API_KEY>",
      "reqTime": "<unix_ms_timestamp_as_string>",
      "signature": "<HMAC_SHA256_signature>"
    }
  }
  signature = HMAC-SHA256(apiKey + reqTime, secretKey).hexdigest()

After successful login, subscribe to personal channels:
  {"method": "sub.personal.order",    "param": {}}
  {"method": "sub.personal.position", "param": {}}
  {"method": "sub.personal.asset",    "param": {}}

Heartbeat (send every 15 seconds to prevent timeout):
  {"method": "ping"}

INCOMING MESSAGES — what the server pushes to you:

Ticker update:
  {
    "channel": "push.ticker",
    "data": {
      "symbol": "BTC_USDT",
      "lastPrice": "68450.00",
      "bid1": "68449.50",
      "ask1": "68450.50",
      "volume24": "12500000",
      "changeRate": "0.024"    <- decimal, not percent (0.024 = +2.4%)
    }
  }

Kline update:
  {
    "channel": "push.kline",
    "data": {
      "symbol":   "BTC_USDT",
      "interval": "Min15",
      "o": "68200.00",
      "h": "68500.00",
      "l": "68100.00",
      "c": "68450.00",
      "v": "450.25",
      "t": 1716900000,     <- candle open timestamp (unix seconds)
      "end": false         <- TRUE when this candle is finalized/closed
    }
  }
  When end=true: trigger re-analysis pipeline for this symbol.

Personal order update (fires when an order is filled or cancelled):
  {
    "channel": "push.personal.order",
    "data": {
      "orderId":      "123456789",
      "symbol":       "BTC_USDT",
      "side":         1,
      "type":         1,
      "state":        2,            <- 2=filled, 4=cancelled, 1=pending
      "dealAvgPrice": "68450.00",
      "dealVol":      "1",
      "createTime":   1716900000000
    }
  }

Personal position update:
  {
    "channel": "push.personal.position",
    "data": {
      "symbol":        "BTC_USDT",
      "positionType":  1,
      "openType":      1,
      "side":          1,            <- 1=Long, 2=Short
      "vol":           "1",
      "avgPrice":      "68450.00",
      "im":            "136.90",     <- initial margin used
      "unrealisedPnl": "12.50"
    }
  }
"""
import asyncio, json, time, hmac, hashlib, logging, websockets
from typing import Callable, Dict, Set
from backend.config import MEXC_API_KEY, MEXC_SECRET_KEY, MEXC_WS_URL

logger = logging.getLogger(__name__)


class MexcWebSocket:
    def __init__(self):
        self.ws          = None
        self.subscriptions: Set[str] = set()
        self.handlers:    Dict[str, list] = {}
        self._running    = False
        self._connected  = False

    def on(self, channel: str, handler: Callable):
        """Register an async handler function for a given channel."""
        self.handlers.setdefault(channel, []).append(handler)

    async def connect(self):
        self._running = True
        while self._running:
            try:
                async with websockets.connect(MEXC_WS_URL, ping_interval=None) as ws:
                    self.ws = ws
                    self._connected = True
                    logger.info("MEXC WebSocket connected")
                    await self._authenticate()
                    await self._resubscribe()
                    asyncio.create_task(self._heartbeat())
                    await self._listen()
            except Exception as e:
                self._connected = False
                logger.warning(f"WS disconnected ({e}). Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def _authenticate(self):
        ts  = str(int(time.time() * 1000))
        sig = hmac.new(MEXC_SECRET_KEY.encode(),
                       (MEXC_API_KEY + ts).encode(), hashlib.sha256).hexdigest()
        await self.ws.send(json.dumps({
            "method": "login",
            "param": {"apiKey": MEXC_API_KEY, "reqTime": ts, "signature": sig}
        }))
        for method in ["sub.personal.order", "sub.personal.position", "sub.personal.asset"]:
            await self.ws.send(json.dumps({"method": method, "param": {}}))

    async def _resubscribe(self):
        for msg in self.subscriptions:
            await self.ws.send(msg)

    async def _heartbeat(self):
        while self._running and self._connected:
            try:
                await self.ws.send(json.dumps({"method": "ping"}))
                await asyncio.sleep(15)
            except: break

    async def _listen(self):
        async for message in self.ws:
            try:
                data    = json.loads(message)
                channel = data.get("channel", "")
                if channel in self.handlers:
                    for h in self.handlers[channel]:
                        asyncio.create_task(h(data.get("data", {})))
            except Exception as e:
                logger.error(f"WS handler error: {e}")

    async def subscribe_ticker(self, symbol: str):
        """symbol must be underscore format: BTC_USDT"""
        msg = json.dumps({"method": "sub.ticker", "param": {"symbol": symbol}})
        self.subscriptions.add(msg)
        if self._connected: await self.ws.send(msg)

    async def subscribe_kline(self, symbol: str, interval: str = "Min15"):
        msg = json.dumps({"method": "sub.kline",
                          "param": {"symbol": symbol, "interval": interval}})
        self.subscriptions.add(msg)
        if self._connected: await self.ws.send(msg)

    async def stop(self):
        self._running = False
        if self.ws: await self.ws.close()
