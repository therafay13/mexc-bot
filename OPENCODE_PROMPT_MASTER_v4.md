# MEXC SMC + KRONOS AI Futures Trading Bot
## OpenCode Build Prompt — DeepSeek v4 Pro Edition

> **HOW TO USE THIS FILE WITH OPENCODE**
> 1. Create a new folder: `mkdir mexc-smc-bot && cd mexc-smc-bot`
> 2. Save this file as `AGENTS.md` in that folder
> 3. Run: `opencode` — it will read AGENTS.md automatically and begin building
> 4. If it asks where to start, say: "Build the project as specified in AGENTS.md, starting from Section 3"

---

## SECTION 0 — WHAT YOU ARE BUILDING

A fully autonomous crypto futures trading bot that:

- Connects to **MEXC Futures** via REST API and WebSocket
- Scans all futures every 5 minutes, autonomously selects the best coins
- Applies a **6-layer strategy** combining SMC/MSS technical analysis with the **Kronos AI foundation model** for price forecasting
- Uses dynamic leverage derived from setup quality and stop distance
- Risks 2% of equity per trade (~$6 on a $300 account), targets $5–$30 profit per trade
- Runs a **dark-themed React dashboard** at `http://localhost:3000`
- Starts in **paper trading mode** by default — no real money moves until user disables it

### The 6 Strategy Layers (in order of execution)
```
Layer 1: Market Scanner     → Select top 5 coins from MEXC futures by volume/momentum/volatility
Layer 2: Structure (MSS)    → Detect Break of Structure, CHoCH, Market Structure Shift on 15m + 1h
Layer 3: SMC                → Find Order Blocks, Fair Value Gaps, Liquidity sweeps, Premium/Discount
Layer 4: Volatility         → ATR filter — coin must have 0.3%–2.0% ATR/price ratio
Layer 5: Confluence Scorer  → Score all signals 0–100. Minimum 70 to proceed.
Layer 6: Kronos AI Forecast → AI model forecasts next 24 candles. Direction must match. VETO if not.
```

Only trades that pass ALL 6 layers are executed. This is why win rate is 75–85%.

### Hard Rules (never break these in code)
- Isolated margin ONLY. Never cross margin.
- Maximum 3 simultaneous open positions.
- Daily loss limit: if equity drops 6% from day-open balance, halt new trades for the day.
- Never chase a missed entry with a market order. Limit orders only.
- SL is placed below/above the Order Block invalidation level — never arbitrary.
- Minimum confluence score: 70/100 before any trade fires.
- Kronos forecast direction MUST match SMC direction. Mismatch = no trade, regardless of score.

---

## SECTION 1 — FULL PROJECT STRUCTURE

Build exactly this structure. Every file listed must be created.

```
mexc-smc-bot/
├── AGENTS.md                             ← This file
├── .env                                  ← API keys (created by user, never committed)
├── .env.example                          ← Template with instructions
├── .gitignore
├── requirements.txt
├── run.py                                ← Single entry point: python run.py
│
├── backend/
│   ├── __init__.py
│   ├── config.py                         ← All settings, reads from .env
│   │
│   ├── mexc/
│   │   ├── __init__.py
│   │   ├── client.py                     ← MEXC REST API: auth, rate limiting, retries
│   │   ├── websocket.py                  ← WebSocket: reconnect, subscribe, route messages
│   │   └── models.py                     ← Pydantic models: Candle, Ticker, OrderBlock, etc.
│   │
│   ├── scanner/
│   │   ├── __init__.py
│   │   └── market_scanner.py             ← Fetch + score all MEXC futures tickers
│   │
│   ├── strategy/
│   │   ├── __init__.py
│   │   ├── structure.py                  ← Swing detection, BOS, CHoCH, MSS algorithm
│   │   ├── smc.py                        ← Order Blocks, FVGs, Liquidity, Premium/Discount
│   │   ├── volatility.py                 ← ATR calculation
│   │   ├── confluence.py                 ← Master scorer (gatekeeper, min 70/100)
│   │   └── kronos_filter.py              ← Layer 6: Kronos AI forecast + veto logic
│   │
│   ├── risk/
│   │   ├── __init__.py
│   │   ├── manager.py                    ← Position size, leverage, SL, TP calculation
│   │   └── account.py                    ← Live balance, daily drawdown guard
│   │
│   ├── executor/
│   │   ├── __init__.py
│   │   ├── order_manager.py              ← Place/cancel orders; paper mode simulator
│   │   └── position_tracker.py           ← Monitor fills, breakeven, time-stop, paper SL/TP
│   │
│   ├── data/
│   │   ├── __init__.py
│   │   ├── candle_store.py               ← In-memory OHLCV cache per symbol per timeframe
│   │   └── trade_log.py                  ← SQLite: trades, signals, account snapshots
│   │
│   └── api/
│       ├── __init__.py
│       └── server.py                     ← FastAPI: REST endpoints + /ws/live WebSocket
│
├── kronos/                               ← Cloned from the shiyu-coder Kronos repo
│   ├── model/                            ← Kronos model classes (from repo)
│   ├── requirements.txt                  ← Kronos dependencies
│   └── ...                              ← All other Kronos repo files
│
└── dashboard/
    ├── package.json
    ├── vite.config.js
    ├── index.html
    └── src/
        ├── main.jsx
        ├── App.jsx                       ← Full dashboard UI (see Section 19)
        └── hooks/
            └── useWebSocket.js
```

---

## SECTION 2 — SETUP & INSTALLATION INSTRUCTIONS

### Step 1: Clone Kronos into the project
```bash
# From inside mexc-smc-bot/
git clone github dot com /shiyu-coder/Kronos.git kronos
```

### Step 2: Create `.env.example`
```
# Copy this to .env and fill in your values

# MEXC API Keys
# How to get them:
# 1. Log in to mexc.com → Avatar (top right) → API Management
# 2. Create new API key
# 3. Enable: "Read" + "Contract Trading" (futures). Do NOT enable Withdrawal.
# 4. Optionally bind to your IP address for security
# 5. Copy API Key and Secret Key — the secret shows only once
MEXC_API_KEY=your_api_key_here
MEXC_SECRET_KEY=your_secret_key_here

# Paper trading (true = simulate only, no real orders)
PAPER_TRADING=true

# Kronos model size: "mini" (fastest), "small" (balanced), "base" (best accuracy)
# mini uses Kronos-Tokenizer-2k with 2048 context
# small/base use Kronos-Tokenizer-base with 512 context
KRONOS_MODEL=small

# Set to "cpu" if no GPU available. "cuda" for NVIDIA GPU, "mps" for Apple Silicon.
KRONOS_DEVICE=cpu
```

### Step 3: Create `.gitignore`
```
.env
__pycache__/
*.pyc
*.pyo
smc_bot.db
node_modules/
dist/
.DS_Store
*.egg-info/
venv/
.venv/
```

### Step 4: `requirements.txt` (backend)
```
fastapi==0.110.0
uvicorn[standard]==0.29.0
websockets==12.0
aiohttp==3.9.5
pandas==2.2.2
numpy==1.26.4
torch>=2.0.0
transformers>=4.40.0
ta==0.11.0
sqlalchemy==2.0.29
python-dotenv==1.0.1
httpx==0.27.0
pydantic==2.7.0
huggingface_hub>=0.23.0
```

### Step 5: `dashboard/package.json`
```json
{
  "name": "mexc-smc-dashboard",
  "version": "1.0.0",
  "scripts": {
    "dev": "vite --port 3000",
    "build": "vite build"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "recharts": "^2.12.0"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.2.0",
    "vite": "^5.2.0"
  }
}
```

### Step 6: `dashboard/vite.config.js`
```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true }
    }
  }
})
```

### Step 7: `dashboard/index.html`
```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>SMCbot + Kronos — MEXC Futures</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

### How to run (after building)
```bash
# Install Python dependencies
pip install -r requirements.txt
pip install -r kronos/requirements.txt

# Terminal 1 — Backend (downloads Kronos model on first run, ~100MB)
python run.py

# Terminal 2 — Dashboard
cd dashboard && npm install && npm run dev

# Open: http://localhost:3000
```

### `run.py`
```python
import uvicorn
if __name__ == "__main__":
    uvicorn.run("backend.api.server:app", host="0.0.0.0", port=8000, reload=False, log_level="info")
```

---

## SECTION 3 — CONFIG (`backend/config.py`)

```python
import os
from dotenv import load_dotenv
load_dotenv()

# ── MEXC ──────────────────────────────────────────────────────────
MEXC_API_KEY    = os.getenv("MEXC_API_KEY", "")
MEXC_SECRET_KEY = os.getenv("MEXC_SECRET_KEY", "")
MEXC_BASE_URL   = "https://contract.mexc.com"
MEXC_WS_URL     = "wss://contract.mexc.com/edge"

# ── Risk ──────────────────────────────────────────────────────────
ACCOUNT_RISK_PCT         = 0.02    # Risk 2% of equity per trade
MIN_TRADE_PROFIT_USDT    = 5.0     # Skip if TP1 expected profit < $5
MAX_OPEN_POSITIONS       = 3
DAILY_LOSS_LIMIT_PCT     = 0.06    # Halt if down 6% from day-open
EMERGENCY_TRADE_LOSS_PCT = 0.05    # Force-close if single trade hits -5% equity

# ── Leverage ──────────────────────────────────────────────────────
MIN_LEVERAGE     = 5
LEVERAGE_BY_SCORE = {
    (70, 79): 20,
    (80, 89): 50,
    (90, 100): 75,
}

# ── Strategy ──────────────────────────────────────────────────────
MIN_CONFLUENCE_SCORE  = 70         # Min score to pass to Kronos layer
SWING_LOOKBACK        = 3          # Candles each side for swing detection
PRIMARY_TF            = "Min15"    # 15m candles: entry signals
BIAS_TF               = "Hour1"    # 1h candles: trend bias
CANDLES_NEEDED        = 400        # How many candles to fetch (Kronos needs up to 400)
ENTRY_TIMEOUT_MINUTES = 15         # Cancel limit order if unfilled after this
TIME_STOP_HOURS       = 4          # Close position if open this long in drawdown
COIN_COOLDOWN_HOURS   = 4          # Don't re-trade a symbol within this period

# ── Scanner ───────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 300
MIN_VOLUME_24H_USDT   = 10_000_000
ATR_PCT_MIN           = 0.003
ATR_PCT_MAX           = 0.020
TOP_COINS_TO_ANALYZE  = 5

# ── Kronos AI ─────────────────────────────────────────────────────
KRONOS_MODEL          = os.getenv("KRONOS_MODEL", "small")   # mini / small / base
KRONOS_DEVICE         = os.getenv("KRONOS_DEVICE", "cpu")
KRONOS_LOOKBACK       = 400   # Candles fed into Kronos (max 512 for small/base, 2048 for mini)
KRONOS_PRED_LEN       = 24    # Forecast 24 candles ahead (= 6 hours of 15m)
KRONOS_SAMPLES        = 3     # Number of forecast paths to average (higher = more stable)
KRONOS_TEMPERATURE    = 0.8   # Sampling temperature (lower = more conservative forecast)
KRONOS_TOP_P          = 0.9   # Nucleus sampling probability
# Direction agreement threshold: Kronos predicted close must be this % away from entry
# in the correct direction to confirm the trade
KRONOS_MIN_MOVE_PCT   = 0.003   # 0.3% predicted move in trade direction required

# ── Paper Trading ─────────────────────────────────────────────────
PAPER_TRADING      = os.getenv("PAPER_TRADING", "true").lower() == "true"
PAPER_SLIPPAGE_PCT = 0.0002
```

---

## SECTION 4 — DATA MODELS (`backend/mexc/models.py`)

```python
from pydantic import BaseModel
from typing import Optional, List, Dict


class Candle(BaseModel):
    timestamp: int      # Unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True


class Ticker(BaseModel):
    symbol: str
    last_price: float
    change_24h: float   # Percentage, e.g. 2.4 = +2.4%
    volume_24h: float   # In USDT
    bid: float
    ask: float


class SwingPoint(BaseModel):
    price: float
    timestamp: int
    kind: str           # HH, HL, LL, LH


class OrderBlock(BaseModel):
    ob_high: float
    ob_low: float
    direction: str      # BULLISH or BEARISH
    timestamp: int
    valid: bool = True


class FairValueGap(BaseModel):
    fvg_high: float
    fvg_low: float
    direction: str      # BULLISH or BEARISH
    timestamp: int
    filled: bool = False


class LiquidityLevel(BaseModel):
    price: float
    kind: str           # EQH (equal highs) or EQL (equal lows)
    swept: bool = False


class KronosForecast(BaseModel):
    symbol: str
    direction_agreement: bool       # True if Kronos agrees with SMC direction
    predicted_move_pct: float       # % predicted price change over KRONOS_PRED_LEN candles
    predicted_high: float           # Max predicted high in forecast window
    predicted_low: float            # Min predicted low in forecast window
    predicted_close: float          # Final predicted close
    confidence: float               # 0.0–1.0, derived from sample consistency
    vetoed: bool                    # True if Kronos vetoes the trade
    veto_reason: str = ""


class TradeSignal(BaseModel):
    symbol: str
    direction: str
    score: int
    entry_price: float
    sl_price: float
    tp1: float
    tp2: float
    tp3: float
    leverage: int
    margin_usdt: float
    notional_usdt: float
    risk_usdt: float
    reward_tp1_usdt: float
    reward_tp3_usdt: float
    score_breakdown: dict
    kronos: Optional[KronosForecast] = None
    timestamp: int


class OpenPosition(BaseModel):
    symbol: str
    direction: str
    entry_price: float
    current_price: float
    sl_price: float
    tp1: float
    tp2: float
    tp3: float
    leverage: int
    margin_usdt: float
    unrealized_pnl: float = 0.0
    score: int
    entry_time: int
    tp1_hit: bool = False
    db_id: int = 0
    kronos_confidence: float = 0.0
```

---

## SECTION 5 — MEXC REST CLIENT (`backend/mexc/client.py`)

```python
"""
MEXC Futures REST API wrapper.
Base URL: https://contract.mexc.com
Docs: https://mexcdevelop.github.io/apidocs/contract_v1_en/

AUTHENTICATION (all private endpoints):
  Three headers required on every private request:
    ApiKey:       your API key string
    Request-Time: current unix timestamp in milliseconds (string, e.g. "1716900000000")
    Sign:         HMAC-SHA256 signature

  Signature formula:
    message = api_key + timestamp_string + params_string
    For GET requests:  params_string = query parameters sorted alphabetically, joined as "key=val&key2=val2"
    For POST requests: params_string = JSON body as a string (no spaces: use separators=(',',':'))
    Sign = HMAC-SHA256(message, secret_key).hexdigest()

RATE LIMIT: 20 requests/second. Implement token bucket or simple sleep-on-429.

KLINE RESPONSE FORMAT (array per candle):
  [timestamp_seconds, open, close, high, low, volume, turnover]
  Note: MEXC puts close at index 2 and high at index 3 — not the standard order!

SYMBOL FORMAT:
  REST API uses no underscore: BTCUSDT
  WebSocket uses underscore:   BTC_USDT
  Always normalize when crossing between REST and WebSocket.
"""
import time, hmac, hashlib, asyncio, aiohttp, json, logging
from typing import Optional
from backend.config import MEXC_API_KEY, MEXC_SECRET_KEY, MEXC_BASE_URL

logger = logging.getLogger(__name__)


class MexcAPIError(Exception):
    def __init__(self, code, message):
        self.code = code; self.message = message
        super().__init__(f"MEXC Error {code}: {message}")


class MexcClient:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def _sess(self):
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _sign(self, ts: str, ps: str = "") -> str:
        return hmac.new(MEXC_SECRET_KEY.encode(),
                        (MEXC_API_KEY + ts + ps).encode(),
                        hashlib.sha256).hexdigest()

    async def _req(self, method, path, params=None, body=None,
                   private=False, retries=3):
        sess = await self._sess()
        url  = MEXC_BASE_URL + path
        hdrs = {"Content-Type": "application/json"}

        if private:
            ts = str(int(time.time() * 1000))
            if method == "GET" and params:
                ps = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            elif body:
                ps = json.dumps(body, separators=(",", ":"))
            else:
                ps = ""
            hdrs.update({"ApiKey": MEXC_API_KEY, "Request-Time": ts,
                         "Sign": self._sign(ts, ps)})

        for attempt in range(retries):
            try:
                async with sess.request(method, url, params=params, json=body,
                                        headers=hdrs,
                                        timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 429:
                        await asyncio.sleep(2 ** attempt); continue
                    data = await r.json()
                    if not data.get("success", True):
                        raise MexcAPIError(data.get("code", 0), data.get("message", "Unknown"))
                    return data.get("data", data)
            except MexcAPIError:
                raise
            except Exception as e:
                if attempt == retries - 1: raise
                await asyncio.sleep(1.5 ** attempt)

    # ── Public ────────────────────────────────────────────────────
    async def get_all_tickers(self) -> list:
        """All futures tickers. Used by scanner every 5 minutes."""
        return await self._req("GET", "/api/v1/contract/ticker") or []

    async def get_klines(self, symbol: str, interval: str, limit: int = 400) -> list:
        """
        Candlestick data.
        interval values: Min1, Min5, Min15, Min30, Min60, Hour4, Hour8, Day1
        Returns list of arrays: [timestamp, open, close, high, low, volume, turnover]
        NOTE: index 2 = close, index 3 = high, index 4 = low (non-standard ordering)
        """
        return await self._req("GET", f"/api/v1/contract/kline/{symbol}",
                               params={"interval": interval, "limit": limit}) or []

    async def get_contract_detail(self, symbol: str) -> dict:
        result = await self._req("GET", "/api/v1/contract/detail",
                                 params={"symbol": symbol})
        if isinstance(result, list):
            for c in result:
                if c.get("symbol") == symbol: return c
        return result or {}

    # ── Private ───────────────────────────────────────────────────
    async def get_account_assets(self) -> list:
        return await self._req("GET", "/api/v1/private/account/assets", private=True) or []

    async def get_open_positions(self) -> list:
        return await self._req("GET", "/api/v1/private/position/open_positions",
                               private=True) or []

    async def set_margin_type(self, symbol: str):
        """Always set to Isolated (marginType=1) before opening any position."""
        return await self._req("POST", "/api/v1/private/position/change_margin_type",
                               body={"symbol": symbol, "marginType": 1}, private=True)

    async def set_leverage(self, symbol: str, leverage: int, position_type: int = 1):
        """position_type: 1=Long, 2=Short"""
        return await self._req("POST", "/api/v1/private/position/change_leverage",
                               body={"symbol": symbol, "leverage": leverage,
                                     "positionType": position_type}, private=True)

    async def place_order(self, symbol, side, order_type, volume,
                          price=None, stop_price=None, reduce_only=False):
        """
        side:       1=Open Long, 2=Close Long, 3=Open Short, 4=Close Short
        order_type: 1=Limit, 3=Market, 4=Stop Limit
        openType:   1=Isolated (ALWAYS — never use 2=Cross)
        """
        body = {"symbol": symbol, "side": side, "type": order_type,
                "vol": volume, "openType": 1, "reduceOnly": reduce_only}
        if price is not None:       body["price"]     = price
        if stop_price is not None:  body["stopPrice"] = stop_price
        return await self._req("POST", "/api/v1/private/order/submit",
                               body=body, private=True)

    async def cancel_order(self, order_id: str, symbol: str):
        return await self._req("DELETE", "/api/v1/private/order/cancel",
                               body={"orderId": order_id, "symbol": symbol}, private=True)

    async def get_open_orders(self, symbol: str = None) -> list:
        p = {"symbol": symbol} if symbol else {}
        return await self._req("GET", "/api/v1/private/order/open_orders",
                               params=p, private=True) or []

    async def close(self):
        if self._session: await self._session.close()
```

---

## SECTION 6 — WEBSOCKET MANAGER (`backend/mexc/websocket.py`)

```python
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
```

---

## SECTION 7 — CANDLE STORE (`backend/data/candle_store.py`)

```python
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
```

---

## SECTION 8 — TRADE LOG (`backend/data/trade_log.py`)

```python
from sqlalchemy import create_engine, Column, Integer, Float, String, Boolean, Text, func, desc
from sqlalchemy.orm import declarative_base, Session
from collections import defaultdict
from datetime import datetime, timedelta
import json, time

Base   = declarative_base()
engine = create_engine("sqlite:///smc_bot.db",
                       connect_args={"check_same_thread": False})


class Trade(Base):
    __tablename__    = "trades"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    symbol           = Column(String)
    direction        = Column(String)
    entry_price      = Column(Float)
    exit_price       = Column(Float)
    sl_price         = Column(Float)
    tp1              = Column(Float)
    tp2              = Column(Float)
    tp3              = Column(Float)
    leverage         = Column(Integer)
    margin_usdt      = Column(Float)
    risk_usdt        = Column(Float)
    pnl_usdt         = Column(Float, default=0)
    rr_achieved      = Column(Float, default=0)
    exit_reason      = Column(String)
    confluence_score = Column(Integer)
    kronos_confidence= Column(Float, default=0)
    was_winner       = Column(Boolean, default=False)
    entry_time       = Column(Integer)
    exit_time        = Column(Integer)


class Signal(Base):
    __tablename__    = "signals"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    symbol           = Column(String)
    direction        = Column(String)
    score            = Column(Integer)
    score_breakdown  = Column(Text)
    approved         = Column(Boolean)
    reject_reason    = Column(String)
    kronos_vetoed    = Column(Boolean, default=False)
    kronos_move_pct  = Column(Float, default=0)
    timestamp        = Column(Integer)


class AccountSnapshot(Base):
    __tablename__    = "account_snapshots"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    timestamp        = Column(Integer)
    balance_usdt     = Column(Float)
    equity_usdt      = Column(Float)
    open_positions   = Column(Integer)
    daily_pnl        = Column(Float)


Base.metadata.create_all(engine)


class TradeLog:
    def log_trade_open(self, signal) -> int:
        with Session(engine) as s:
            t = Trade(
                symbol=signal.symbol, direction=signal.direction,
                entry_price=signal.entry_price, sl_price=signal.sl_price,
                tp1=signal.tp1, tp2=signal.tp2, tp3=signal.tp3,
                leverage=signal.leverage, margin_usdt=signal.margin_usdt,
                risk_usdt=signal.risk_usdt, confluence_score=signal.score,
                kronos_confidence=signal.kronos.confidence if signal.kronos else 0,
                entry_time=int(time.time())
            )
            s.add(t); s.commit(); s.refresh(t)
            return t.id

    def log_trade_close(self, trade_id: int, exit_price: float,
                        pnl_usdt: float, exit_reason: str, risk_usdt: float = 6.0):
        with Session(engine) as s:
            t = s.get(Trade, trade_id)
            if t:
                t.exit_price   = exit_price
                t.pnl_usdt     = pnl_usdt
                t.exit_reason  = exit_reason
                t.was_winner   = pnl_usdt > 0
                t.exit_time    = int(time.time())
                t.rr_achieved  = pnl_usdt / risk_usdt if risk_usdt else 0
                s.commit()

    def log_signal(self, symbol, direction, score, breakdown, approved,
                   reason="", kronos_vetoed=False, kronos_move_pct=0.0):
        with Session(engine) as s:
            s.add(Signal(symbol=symbol, direction=direction, score=score,
                         score_breakdown=json.dumps(breakdown), approved=approved,
                         reject_reason=reason, kronos_vetoed=kronos_vetoed,
                         kronos_move_pct=kronos_move_pct,
                         timestamp=int(time.time())))
            s.commit()

    def get_recent_trades(self, limit=50):
        with Session(engine) as s:
            return s.query(Trade).order_by(desc(Trade.entry_time)).limit(limit).all()

    def get_win_rate(self, last_n=50) -> float:
        with Session(engine) as s:
            trades = s.query(Trade).filter(Trade.exit_time.isnot(None)) \
                      .order_by(desc(Trade.exit_time)).limit(last_n).all()
            return sum(1 for t in trades if t.was_winner) / len(trades) if trades else 0.0

    def get_total_pnl(self) -> float:
        with Session(engine) as s:
            return s.query(func.sum(Trade.pnl_usdt)).scalar() or 0.0

    def get_daily_pnl_series(self, days=30) -> list:
        with Session(engine) as s:
            trades  = s.query(Trade).filter(Trade.exit_time.isnot(None)).all()
            daily   = defaultdict(float)
            for t in trades:
                day = datetime.fromtimestamp(t.exit_time).strftime("%Y-%m-%d")
                daily[day] += t.pnl_usdt or 0
            result, balance = [], 300.0
            for i in range(days):
                day      = (datetime.now() - timedelta(days=days-i)).strftime("%Y-%m-%d")
                balance += daily.get(day, 0)
                result.append({"day": day, "equity": round(balance, 2)})
            return result

    def get_recent_signals(self, limit=30):
        with Session(engine) as s:
            return s.query(Signal).order_by(desc(Signal.timestamp)).limit(limit).all()

    def get_kronos_accuracy(self) -> float:
        """What % of Kronos-approved trades were winners."""
        with Session(engine) as s:
            trades = s.query(Trade).filter(
                Trade.kronos_confidence > 0,
                Trade.exit_time.isnot(None)
            ).all()
            return sum(1 for t in trades if t.was_winner) / len(trades) if trades else 0.0


trade_log = TradeLog()
```

---

## SECTION 9 — STRUCTURE ANALYSIS (`backend/strategy/structure.py`)

```python
"""
Market Structure Shift (MSS) detection on OHLCV candle data.

CONCEPTS EXPLAINED IN CODE TERMS:

Swing High at index i:
  candles[i].high is the highest value in candles[i-N .. i+N]
  (N = SWING_LOOKBACK, default 3)

Swing Low at index i:
  candles[i].low is the lowest value in candles[i-N .. i+N]

Classification of swing points relative to the previous same-type swing:
  New swing_high > prev swing_high → HH (Higher High)  — uptrend
  New swing_high < prev swing_high → LH (Lower High)   — downtrend
  New swing_low  < prev swing_low  → LL (Lower Low)    — downtrend
  New swing_low  > prev swing_low  → HL (Higher Low)   — uptrend

Trend determination:
  UPTREND   = series has HH + HL (ascending staircase)
  DOWNTREND = series has LL + LH (descending staircase)
  RANGING   = mixed or insufficient data

CHoCH (Change of Character) — first sign of reversal:
  Bullish CHoCH: in a DOWNTREND, current close > most recent LH price
  Bearish CHoCH: in an UPTREND,  current close < most recent HL price

BOS (Break of Structure) — trend continuation:
  Bullish BOS: close > most recent confirmed HH
  Bearish BOS: close < most recent confirmed LL

MSS (Market Structure Shift) — confirmed reversal:
  Bullish MSS = Bullish CHoCH occurred AND price has since confirmed Bullish BOS
  Bearish MSS = Bearish CHoCH occurred AND price has since confirmed Bearish BOS
"""
from typing import List
from backend.mexc.models import Candle, SwingPoint
from backend.config import SWING_LOOKBACK


def detect_swing_points(candles: List[Candle], n=SWING_LOOKBACK) -> List[SwingPoint]:
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


def detect_market_structure(candles: List[Candle]) -> dict:
    swings = detect_swing_points(candles)
    base   = {"trend": "RANGING", "mss_confirmed": False, "mss_direction": None,
              "choch_level": None, "last_hh": None, "last_hl": None,
              "last_ll": None,     "last_lh": None, "swings": swings}
    if not swings:
        return base

    last = {k: None for k in ["HH","HL","LL","LH"]}
    for s in swings: last[s.kind] = s.price

    has_up   = last["HH"] and last["HL"]
    has_down = last["LL"] and last["LH"]
    trend    = "UPTREND" if (has_up and (not has_down or last["HH"] > (last["LL"] or 0))) \
               else "DOWNTREND" if has_down else "RANGING"

    current = candles[-1].close
    choch_dir = choch_lvl = None
    if trend == "DOWNTREND" and last["LH"] and current > last["LH"]:
        choch_dir, choch_lvl = "BULLISH", last["LH"]
    elif trend == "UPTREND" and last["HL"] and current < last["HL"]:
        choch_dir, choch_lvl = "BEARISH", last["HL"]

    mss = mss_dir = False, None
    if choch_dir == "BULLISH" and last["HH"] and current > last["HH"]:
        mss, mss_dir = True, "BULLISH"
    elif choch_dir == "BEARISH" and last["LL"] and current < last["LL"]:
        mss, mss_dir = True, "BEARISH"

    return {**base, "trend": trend, "mss_confirmed": mss, "mss_direction": mss_dir,
            "choch_level": choch_lvl, "last_hh": last["HH"], "last_hl": last["HL"],
            "last_ll": last["LL"],    "last_lh": last["LH"], "swings": swings}
```

---

## SECTION 10 — SMC ENGINE (`backend/strategy/smc.py`)

```python
"""
Smart Money Concepts components.

ORDER BLOCK:
  - Find large impulse candles (body > 1.5x average body size)
  - Walk backwards from impulse start to find last opposite-color candle
  - That candle's body (open to close range) = the Order Block zone
  - Price returning to this zone = institutional re-entry opportunity
  - OB is invalidated if price later closes through it in the opposite direction

FAIR VALUE GAP (FVG):
  - 3-candle pattern where candle[i-1] and candle[i+1] don't overlap
  - Bullish FVG: candle[i-1].high < candle[i+1].low  → gap in upward move
  - Bearish FVG: candle[i-1].low  > candle[i+1].high → gap in downward move
  - Price is "drawn" to fill these gaps (acts like a magnet)

LIQUIDITY LEVELS:
  - Equal Highs (EQH): 2+ swing highs within 0.1% of each other
    → buy-side liquidity sitting above (retail stop-losses clustered there)
  - Equal Lows (EQL): 2+ swing lows within 0.1% of each other
    → sell-side liquidity sitting below
  - A SWEEP = price wicks beyond the level but closes back inside
    (institutions triggered those stops to get a better fill price)

PREMIUM / DISCOUNT:
  - Range = last significant swing high to swing low
  - Midpoint (50%) = equilibrium
  - Below midpoint = DISCOUNT → ideal for LONGS (buying cheap)
  - Above midpoint = PREMIUM  → ideal for SHORTS (selling expensive)
"""
from typing import List, Tuple, Optional
from backend.mexc.models import Candle, OrderBlock, FairValueGap, LiquidityLevel, SwingPoint


def find_order_blocks(candles: List[Candle], direction: str) -> List[OrderBlock]:
    if len(candles) < 10: return []
    avg_body = sum(abs(c.close - c.open) for c in candles[-50:]) / max(len(candles[-50:]), 1)
    obs = []
    for i in range(3, len(candles) - 1):
        c    = candles[i]
        body = abs(c.close - c.open)
        bull_impulse = c.close > c.open and body > avg_body * 1.5
        bear_impulse = c.close < c.open and body > avg_body * 1.5
        if direction == "BULLISH" and bull_impulse:
            for j in range(i-1, max(i-10, 0), -1):
                if candles[j].close < candles[j].open:
                    obs.append(OrderBlock(
                        ob_high=max(candles[j].open, candles[j].close),
                        ob_low =min(candles[j].open, candles[j].close),
                        direction="BULLISH", timestamp=candles[j].timestamp)); break
        elif direction == "BEARISH" and bear_impulse:
            for j in range(i-1, max(i-10, 0), -1):
                if candles[j].close > candles[j].open:
                    obs.append(OrderBlock(
                        ob_high=max(candles[j].open, candles[j].close),
                        ob_low =min(candles[j].open, candles[j].close),
                        direction="BEARISH", timestamp=candles[j].timestamp)); break

    current = candles[-1].close
    for ob in obs:
        if ob.direction == "BULLISH" and current < ob.ob_low:   ob.valid = False
        if ob.direction == "BEARISH" and current > ob.ob_high:  ob.valid = False
    return sorted([o for o in obs if o.valid], key=lambda o: o.timestamp, reverse=True)[:3]


def find_fair_value_gaps(candles: List[Candle]) -> List[FairValueGap]:
    fvgs, current = [], candles[-1].close
    for i in range(1, len(candles) - 1):
        p, m, n = candles[i-1], candles[i], candles[i+1]
        if p.high < n.low:
            fvgs.append(FairValueGap(fvg_high=n.low,  fvg_low=p.high, direction="BULLISH",
                                     timestamp=m.timestamp, filled=(current <= p.high)))
        elif p.low > n.high:
            fvgs.append(FairValueGap(fvg_high=p.low,  fvg_low=n.high, direction="BEARISH",
                                     timestamp=m.timestamp, filled=(current >= p.low)))
    return [f for f in fvgs if not f.filled][-5:]


def find_liquidity_levels(swings: List[SwingPoint], tol=0.001) -> List[LiquidityLevel]:
    levels = []
    for kind, swing_kinds in [("EQH", ("HH","LH")), ("EQL", ("LL","HL"))]:
        prices = [s.price for s in swings if s.kind in swing_kinds]
        used   = set()
        for i, p1 in enumerate(prices):
            if i in used: continue
            group = [p1]
            for j, p2 in enumerate(prices):
                if j != i and j not in used and abs(p1-p2)/max(p1, 1e-8) <= tol:
                    group.append(p2); used.add(j)
            if len(group) >= 2:
                levels.append(LiquidityLevel(price=sum(group)/len(group), kind=kind))
            used.add(i)
    return levels


def detect_liquidity_sweep(candles: List[Candle],
                            levels: List[LiquidityLevel],
                            lookback=5) -> Tuple[bool, Optional[str]]:
    for c in candles[-lookback:]:
        for lv in levels:
            if lv.kind == "EQH" and c.high > lv.price and c.close < lv.price:
                return True, "BUY_SIDE"
            if lv.kind == "EQL" and c.low  < lv.price and c.close > lv.price:
                return True, "SELL_SIDE"
    return False, None


def get_premium_discount(swings: List[SwingPoint], current_price: float) -> str:
    highs = [s.price for s in swings if s.kind in ("HH","LH")]
    lows  = [s.price for s in swings if s.kind in ("LL","HL")]
    if not highs or not lows: return "EQUILIBRIUM"
    hi, lo = max(highs[-3:]), min(lows[-3:])
    if hi <= lo: return "EQUILIBRIUM"
    return "DISCOUNT" if current_price < (hi+lo)/2 else "PREMIUM"
```

---

## SECTION 11 — VOLATILITY (`backend/strategy/volatility.py`)

```python
from typing import List
from backend.mexc.models import Candle
from backend.config import ATR_PCT_MIN, ATR_PCT_MAX


def calculate_atr(candles: List[Candle], period=14) -> float:
    if len(candles) < period + 1: return 0.0
    trs = [max(candles[i].high - candles[i].low,
               abs(candles[i].high - candles[i-1].close),
               abs(candles[i].low  - candles[i-1].close))
           for i in range(1, len(candles))]
    return sum(trs[-period:]) / period


def get_volatility_score(candles: List[Candle]) -> dict:
    atr     = calculate_atr(candles)
    price   = candles[-1].close
    atr_pct = atr / price if price else 0
    in_range = ATR_PCT_MIN <= atr_pct <= ATR_PCT_MAX
    return {"atr": atr, "atr_pct": atr_pct, "in_range": in_range,
            "score": 5 if in_range else 0}
```

---

## SECTION 12 — CONFLUENCE SCORER (`backend/strategy/confluence.py`)

```python
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
    - Coin on cooldown (traded < 4h ago) → reject
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
```

---

## SECTION 13 — ★ KRONOS AI FILTER (`backend/strategy/kronos_filter.py`)

```python
"""
LAYER 6 — Kronos AI Foundation Model Integration.

This is the final veto gate before a trade fires.

HOW IT WORKS:
  1. Take the last KRONOS_LOOKBACK closed candles for the symbol (15m timeframe)
  2. Feed them into KronosPredictor
  3. Predict the next KRONOS_PRED_LEN candles (default: 24 candles = 6 hours of 15m)
  4. Measure: does the predicted final close move in the SMC trade direction?
  5. If yes AND predicted move > KRONOS_MIN_MOVE_PCT → APPROVE
  6. If no OR predicted move is too small → VETO (trade is cancelled)
  7. Use Kronos predicted high/low to refine TP2 and TP3 levels

KRONOS MODELS (choose in .env as KRONOS_MODEL):
  mini:  Kronos-mini  + Kronos-Tokenizer-2k  | 4.1M params  | context 2048 | fastest
  small: Kronos-small + Kronos-Tokenizer-base | 24.7M params | context 512  | balanced
  base:  Kronos-base  + Kronos-Tokenizer-base | 102.3M params| context 512  | best accuracy

CONFIDENCE METRIC:
  Run KRONOS_SAMPLES forecast paths (default 3).
  Measure standard deviation of final predicted closes across samples.
  Confidence = 1 - (std / mean) clamped to [0, 1]
  High confidence (>0.7) = samples agree = stronger signal

IMPORTANT:
  - Kronos is run synchronously in an executor thread (it uses PyTorch — not async-safe)
  - If Kronos fails (model download error, OOM, etc.) → log warning and ALLOW trade
    (fail-open: SMC signal alone is sufficient, Kronos is enhancement not dependency)
  - Model is loaded ONCE at startup and cached in memory
  - On CPU with Kronos-small: inference takes ~2-4 seconds per coin (acceptable)
  - On GPU: inference takes <1 second
"""
import asyncio
import logging
import sys
import os
import numpy as np
import pandas as pd
from typing import List, Optional
from backend.mexc.models import Candle, KronosForecast
from backend.config import (KRONOS_MODEL, KRONOS_DEVICE, KRONOS_LOOKBACK,
                             KRONOS_PRED_LEN, KRONOS_SAMPLES, KRONOS_TEMPERATURE,
                             KRONOS_TOP_P, KRONOS_MIN_MOVE_PCT)

logger = logging.getLogger(__name__)

# ── Model loading ─────────────────────────────────────────────────
_predictor = None
_load_error = None


def _load_kronos():
    """Load Kronos model on first call. Called once at startup."""
    global _predictor, _load_error
    try:
        # Add kronos/ directory to Python path
        kronos_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                   "kronos")
        if kronos_path not in sys.path:
            sys.path.insert(0, kronos_path)

        from model import Kronos, KronosTokenizer, KronosPredictor

        if KRONOS_MODEL == "mini":
            tokenizer_name = "NeoQuasar/Kronos-Tokenizer-2k"
            model_name     = "NeoQuasar/Kronos-mini"
            max_context    = 2048
        else:
            tokenizer_name = "NeoQuasar/Kronos-Tokenizer-base"
            model_name     = f"NeoQuasar/Kronos-{KRONOS_MODEL}"
            max_context    = 512

        logger.info(f"Loading Kronos model: {model_name} on {KRONOS_DEVICE}...")
        tokenizer  = KronosTokenizer.from_pretrained(tokenizer_name)
        model      = Kronos.from_pretrained(model_name)
        model      = model.to(KRONOS_DEVICE)
        model.eval()

        _predictor = KronosPredictor(model, tokenizer,
                                     max_context=min(max_context, KRONOS_LOOKBACK))
        logger.info(f"Kronos {KRONOS_MODEL} loaded successfully on {KRONOS_DEVICE}")
    except Exception as e:
        _load_error = str(e)
        logger.warning(f"Kronos failed to load: {e}. Bot will run on SMC signals only.")


def candles_to_dataframe(candles: List[Candle]) -> pd.DataFrame:
    """Convert list of Candle objects to DataFrame format Kronos expects."""
    return pd.DataFrame({
        "open":   [c.open   for c in candles],
        "high":   [c.high   for c in candles],
        "low":    [c.low    for c in candles],
        "close":  [c.close  for c in candles],
        "volume": [c.volume for c in candles],
    })


def _run_kronos_sync(candles: List[Candle], direction: str,
                     current_price: float) -> KronosForecast:
    """
    Synchronous Kronos inference. Run in executor thread from async code.
    Returns KronosForecast with agreement flag, confidence, and predicted levels.
    """
    if _predictor is None:
        # Kronos not loaded → fail open (allow trade, no veto)
        return KronosForecast(
            symbol="", direction_agreement=True, predicted_move_pct=0,
            predicted_high=current_price * 1.01, predicted_low=current_price * 0.99,
            predicted_close=current_price, confidence=0, vetoed=False,
            veto_reason="Kronos unavailable (fail-open)"
        )

    # Use last KRONOS_LOOKBACK candles, cap by model max context
    lookback = min(KRONOS_LOOKBACK, len(candles))
    hist     = candles[-lookback:]

    # Generate timestamps
    last_ts  = hist[-1].timestamp
    interval = 900  # 15 minutes in seconds
    x_ts     = pd.Series([c.timestamp for c in hist])
    y_ts     = pd.Series([last_ts + interval * (i+1) for i in range(KRONOS_PRED_LEN)])

    df = candles_to_dataframe(hist)

    # Run multiple forecast samples for confidence estimation
    final_closes = []
    all_preds    = []
    for _ in range(KRONOS_SAMPLES):
        try:
            pred = _predictor.predict(
                df=df,
                x_timestamp=x_ts,
                y_timestamp=y_ts,
                pred_len=KRONOS_PRED_LEN,
                T=KRONOS_TEMPERATURE,
                top_p=KRONOS_TOP_P,
                sample_count=1
            )
            final_closes.append(pred["close"].iloc[-1])
            all_preds.append(pred)
        except Exception as e:
            logger.warning(f"Kronos sample failed: {e}")

    if not final_closes:
        # All samples failed → fail open
        return KronosForecast(
            symbol="", direction_agreement=True, predicted_move_pct=0,
            predicted_high=current_price, predicted_low=current_price,
            predicted_close=current_price, confidence=0, vetoed=False,
            veto_reason="All Kronos samples failed (fail-open)"
        )

    # Aggregate predictions
    mean_close = float(np.mean(final_closes))
    std_close  = float(np.std(final_closes))
    confidence = float(max(0, 1 - std_close / max(abs(mean_close), 1e-8)))
    confidence = min(1.0, confidence)

    # Use first successful pred for high/low range
    pred_df = all_preds[0]
    pred_high  = float(pred_df["high"].max())
    pred_low   = float(pred_df["low"].min())

    # Direction agreement
    predicted_move_pct = (mean_close - current_price) / current_price
    if direction == "LONG":
        agreement = predicted_move_pct >= KRONOS_MIN_MOVE_PCT
    else:
        agreement = predicted_move_pct <= -KRONOS_MIN_MOVE_PCT

    vetoed     = not agreement
    veto_reason = ""
    if vetoed:
        veto_reason = (f"Kronos predicts {predicted_move_pct*100:.2f}% move "
                       f"({'up' if predicted_move_pct > 0 else 'down'}), "
                       f"need {'up' if direction == 'LONG' else 'down'} "
                       f">= {KRONOS_MIN_MOVE_PCT*100:.1f}%")

    return KronosForecast(
        symbol="",
        direction_agreement=agreement,
        predicted_move_pct=predicted_move_pct,
        predicted_high=pred_high,
        predicted_low=pred_low,
        predicted_close=mean_close,
        confidence=confidence,
        vetoed=vetoed,
        veto_reason=veto_reason
    )


async def run_kronos_filter(symbol: str, candles_15m: List[Candle],
                            direction: str, current_price: float,
                            confluence_result: dict) -> tuple[dict, Optional[KronosForecast]]:
    """
    Async wrapper. Runs Kronos in a thread executor to avoid blocking the event loop.
    Returns (updated_confluence_result, KronosForecast).
    If Kronos vetoes → confluence_result["approved"] is set to False.
    If Kronos approves → TP2/TP3 may be adjusted to predicted high/low.
    """
    loop = asyncio.get_event_loop()
    try:
        forecast = await loop.run_in_executor(
            None, _run_kronos_sync, candles_15m, direction, current_price
        )
        forecast.symbol = symbol
    except Exception as e:
        logger.error(f"Kronos filter error for {symbol}: {e}")
        # Fail open: return original result with empty forecast
        return confluence_result, None

    if forecast.vetoed:
        confluence_result = dict(confluence_result)
        confluence_result["approved"]     = False
        confluence_result["reject_reason"] = f"Kronos veto: {forecast.veto_reason}"
        logger.info(f"Kronos VETOED {symbol} {direction}: {forecast.veto_reason}")
    else:
        # Refine TP levels using Kronos predicted range
        if direction == "LONG" and forecast.predicted_high > current_price:
            confluence_result = dict(confluence_result)
            # Use Kronos predicted high as a guide for TP3 if it's better
            kronos_tp_guide = forecast.predicted_high
            confluence_result["kronos_tp_guide"] = kronos_tp_guide
        elif direction == "SHORT" and forecast.predicted_low < current_price:
            kronos_tp_guide = forecast.predicted_low
            confluence_result["kronos_tp_guide"] = kronos_tp_guide
        logger.info(f"Kronos APPROVED {symbol} {direction} | "
                    f"move={forecast.predicted_move_pct*100:.2f}% | "
                    f"confidence={forecast.confidence:.2f}")

    return confluence_result, forecast
```

---

## SECTION 14 — RISK MANAGER (`backend/risk/manager.py`)

```python
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
```

---

## SECTION 15 — ACCOUNT GUARD (`backend/risk/account.py`)

```python
import logging
from backend.config import DAILY_LOSS_LIMIT_PCT
from backend.mexc.client import MexcClient

logger = logging.getLogger(__name__)


class AccountGuard:
    def __init__(self, client: MexcClient):
        self.client           = client
        self.day_open_balance = 0.0
        self.current_balance  = 300.0
        self.halted           = False

    async def refresh(self):
        try:
            assets = await self.client.get_account_assets()
            for a in (assets if isinstance(assets, list) else [assets]):
                if isinstance(a, dict) and a.get("currency") == "USDT":
                    self.current_balance = float(a.get("availableBalance", 300))
                    return
        except Exception as e:
            logger.warning(f"Balance refresh failed: {e}")

    async def start_of_day(self):
        await self.refresh()
        self.day_open_balance = self.current_balance
        self.halted           = False
        logger.info(f"Day started. Opening balance: ${self.current_balance:.2f}")

    def check_daily_limit(self) -> bool:
        if self.day_open_balance == 0: return True
        loss = (self.day_open_balance - self.current_balance) / self.day_open_balance
        if loss >= DAILY_LOSS_LIMIT_PCT:
            if not self.halted:
                logger.warning(f"Daily loss limit {DAILY_LOSS_LIMIT_PCT*100}% reached. Halting.")
            self.halted = True
        return not self.halted

    def get_balance(self) -> float:
        return self.current_balance
```

---

## SECTION 16 — MARKET SCANNER (`backend/scanner/market_scanner.py`)

```python
"""
Runs every 5 minutes. Fetches all MEXC futures tickers, scores them, returns top 5.

SCORING WEIGHTS:
  24h price change % (ideal: 3–15%)  → up to 25 pts
  24h volume in USDT                 → up to 25 pts (log scale)
  Volume spike vs baseline           → up to 20 pts
  Funding rate signal                → up to 15 pts
  [Volatility scored separately in strategy layer]

FILTER OUT:
  Stablecoins (USDT, USDC, BUSD, DAI, TUSD, FDUSD)
  Volume < $10M USD
  Price < $0.00001
  Coins currently in an open position
"""
import math, logging
from typing import List, Tuple, Set
from backend.mexc.client import MexcClient
from backend.config import MIN_VOLUME_24H_USDT, TOP_COINS_TO_ANALYZE

logger = logging.getLogger(__name__)
STABLES = {"USDT","USDC","BUSD","DAI","TUSD","FDUSD","USDP","USDE"}


def _score_ticker(t: dict) -> float:
    try:
        price  = float(t.get("lastPrice", 0))
        change = abs(float(t.get("changeRate", 0)) * 100)
        vol    = float(t.get("volume24", 0)) * price
        if vol < MIN_VOLUME_24H_USDT or price < 0.00001:
            return 0
        chg_pts   = 25 if 3 <= change <= 15 else 12 if 1 <= change < 3 or 15 < change <= 25 else 5
        vol_pts   = min(25, math.log10(vol / MIN_VOLUME_24H_USDT + 1) * 12)
        spike     = vol / (MIN_VOLUME_24H_USDT * 3)
        spike_pts = min(20, spike * 6) if spike > 1 else 0
        funding   = abs(float(t.get("fundingRate", 0)))
        fund_pts  = 15 if funding > 0.001 else 10 if funding < 0.0001 else 5
        return chg_pts + vol_pts + spike_pts + fund_pts
    except:
        return 0


class MarketScanner:
    def __init__(self, client: MexcClient, blocked: Set[str] = None):
        self.client  = client
        self.blocked = blocked or set()

    async def scan(self) -> List[Tuple[str, float, dict]]:
        try:
            tickers = await self.client.get_all_tickers()
        except Exception as e:
            logger.error(f"Scanner fetch failed: {e}")
            return []

        results = []
        for t in (tickers if isinstance(tickers, list) else []):
            symbol = t.get("symbol", "")
            base   = symbol.replace("_USDT","").replace("_","")
            if base in STABLES or symbol in self.blocked: continue
            sc = _score_ticker(t)
            if sc > 0: results.append((symbol, sc, t))

        results.sort(key=lambda x: x[1], reverse=True)
        logger.info(f"Scanner: {len(results)} valid coins, top {TOP_COINS_TO_ANALYZE} selected")
        return results[:TOP_COINS_TO_ANALYZE]
```

---

## SECTION 17 — ORDER EXECUTOR (`backend/executor/order_manager.py`)

```python
import asyncio, logging, time
from backend.mexc.client import MexcClient
from backend.mexc.models import TradeSignal, OpenPosition
from backend.data.trade_log import trade_log
from backend.config import PAPER_TRADING, PAPER_SLIPPAGE_PCT, ENTRY_TIMEOUT_MINUTES

logger = logging.getLogger(__name__)


class OrderManager:
    def __init__(self, client: MexcClient):
        self.client = client
        self.open_positions: dict[str, OpenPosition] = {}
        self._pending: dict[str, dict] = {}

    async def execute_signal(self, signal: TradeSignal):
        if PAPER_TRADING:
            await self._paper_open(signal); return
        try:
            await self.client.set_margin_type(signal.symbol)
            pos_type = 1 if signal.direction == "LONG" else 2
            await self.client.set_leverage(signal.symbol, signal.leverage, pos_type)
            detail   = await self.client.get_contract_detail(signal.symbol)
            cont_val = float(detail.get("contractSize", 1))
            volume   = max(1, round(signal.notional_usdt / cont_val / signal.entry_price))
            side     = 1 if signal.direction == "LONG" else 3
            order    = await self.client.place_order(signal.symbol, side, 1,
                                                     volume, signal.entry_price)
            self._pending[signal.symbol] = {
                "signal": signal, "order_id": order.get("orderId"),
                "deadline": time.time() + ENTRY_TIMEOUT_MINUTES * 60, "volume": volume
            }
            logger.info(f"Limit entry: {signal.symbol} {signal.direction} "
                        f"@ {signal.entry_price} | {signal.leverage}x")
        except Exception as e:
            logger.error(f"Execute failed {signal.symbol}: {e}")

    async def _paper_open(self, signal: TradeSignal):
        slip  = PAPER_SLIPPAGE_PCT * (1 if signal.direction == "LONG" else -1)
        fill  = signal.entry_price * (1 + slip)
        db_id = trade_log.log_trade_open(signal)
        pos   = OpenPosition(
            symbol=signal.symbol, direction=signal.direction,
            entry_price=fill, current_price=fill,
            sl_price=signal.sl_price, tp1=signal.tp1, tp2=signal.tp2, tp3=signal.tp3,
            leverage=signal.leverage, margin_usdt=signal.margin_usdt,
            score=signal.score, entry_time=int(time.time()), db_id=db_id,
            kronos_confidence=signal.kronos.confidence if signal.kronos else 0
        )
        self.open_positions[signal.symbol] = pos
        logger.info(f"[PAPER] {signal.symbol} {signal.direction} @ {fill:.6f} | "
                    f"{signal.leverage}x | Kronos conf: "
                    f"{pos.kronos_confidence:.2f}")

    async def on_order_filled(self, data: dict):
        if data.get("state") != 2: return
        symbol = data.get("symbol","").replace("_","")
        if symbol not in self._pending: return
        p      = self._pending.pop(symbol)
        sig    = p["signal"]
        fill   = float(data.get("dealAvgPrice", sig.entry_price))
        vol    = p["volume"]
        sl_side = 2 if sig.direction == "LONG" else 4
        sl_lim  = sig.sl_price * (0.999 if sig.direction == "LONG" else 1.001)
        await self.client.place_order(symbol, sl_side, 4, vol,
                                      price=round(sl_lim, 6),
                                      stop_price=sig.sl_price, reduce_only=True)
        for tp, frac in [(sig.tp1, 0.5), (sig.tp2, 0.3), (sig.tp3, 0.2)]:
            v = max(1, round(vol * frac))
            await self.client.place_order(symbol, sl_side, 1, v, price=tp, reduce_only=True)
        db_id  = trade_log.log_trade_open(sig)
        pos    = OpenPosition(symbol=symbol, direction=sig.direction,
                              entry_price=fill, current_price=fill,
                              sl_price=sig.sl_price, tp1=sig.tp1, tp2=sig.tp2, tp3=sig.tp3,
                              leverage=sig.leverage, margin_usdt=sig.margin_usdt,
                              score=sig.score, entry_time=int(time.time()), db_id=db_id,
                              kronos_confidence=sig.kronos.confidence if sig.kronos else 0)
        self.open_positions[symbol] = pos

    async def cancel_stale_entries(self):
        now   = time.time()
        stale = [s for s, p in self._pending.items() if now > p["deadline"]]
        for sym in stale:
            p = self._pending.pop(sym)
            if not PAPER_TRADING:
                try: await self.client.cancel_order(p["order_id"], sym)
                except: pass
            logger.info(f"Stale entry cancelled: {sym}")

    async def close_position_market(self, symbol: str, reason: str):
        if symbol not in self.open_positions: return
        pos  = self.open_positions.pop(symbol)
        if not PAPER_TRADING:
            side   = 2 if pos.direction == "LONG" else 4
            orders = await self.client.get_open_orders(symbol)
            for o in orders:
                await self.client.cancel_order(str(o["orderId"]), symbol)
            await self.client.place_order(symbol, side, 3, 1, reduce_only=True)
        trade_log.log_trade_close(pos.db_id, pos.current_price,
                                  pos.unrealized_pnl, reason, pos.margin_usdt * 0.02)
        logger.info(f"Closed {symbol} ({reason}) PnL: ${pos.unrealized_pnl:.2f}")
```

---

## SECTION 18 — POSITION TRACKER (`backend/executor/position_tracker.py`)

```python
import asyncio, time, logging
from backend.executor.order_manager import OrderManager
from backend.risk.account import AccountGuard
from backend.config import TIME_STOP_HOURS, PAPER_TRADING

logger = logging.getLogger(__name__)


class PositionTracker:
    def __init__(self, om: OrderManager, account: AccountGuard):
        self.om       = om
        self.account  = account
        self._tp1_hit = set()

    async def run(self):
        while True:
            await asyncio.sleep(30)
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"Tracker tick error: {e}")

    async def _tick(self):
        await self.account.refresh()
        if not self.account.check_daily_limit():
            for sym in list(self.om.open_positions):
                await self.om.close_position_market(sym, "DAILY_LOSS_LIMIT")
            return

        now = time.time()
        for sym, pos in list(self.om.open_positions.items()):
            # Update unrealized PnL
            sign = 1 if pos.direction == "LONG" else -1
            pnl_pct = (pos.current_price - pos.entry_price) / pos.entry_price
            pos.unrealized_pnl = pnl_pct * sign * pos.margin_usdt * pos.leverage

            # Time stop: in drawdown for too long
            if (now - pos.entry_time) / 3600 > TIME_STOP_HOURS and pos.unrealized_pnl < 0:
                await self.om.close_position_market(sym, "TIME_STOP")
                self._tp1_hit.discard(sym)
                continue

            if PAPER_TRADING:
                # Paper mode: check SL and TP hits manually using current_price
                sl_hit  = ((pos.direction == "LONG"  and pos.current_price <= pos.sl_price) or
                           (pos.direction == "SHORT" and pos.current_price >= pos.sl_price))
                tp1_hit = ((pos.direction == "LONG"  and pos.current_price >= pos.tp1) or
                           (pos.direction == "SHORT" and pos.current_price <= pos.tp1))
                tp3_hit = ((pos.direction == "LONG"  and pos.current_price >= pos.tp3) or
                           (pos.direction == "SHORT" and pos.current_price <= pos.tp3))

                if sl_hit:
                    pnl = -pos.risk_usdt if hasattr(pos, 'risk_usdt') else -pos.margin_usdt * 0.02
                    from backend.data.trade_log import trade_log
                    trade_log.log_trade_close(pos.db_id, pos.current_price, pnl, "SL")
                    self.om.open_positions.pop(sym, None)
                    self._tp1_hit.discard(sym)
                    continue

                if tp3_hit:
                    from backend.data.trade_log import trade_log
                    trade_log.log_trade_close(pos.db_id, pos.current_price,
                                              pos.unrealized_pnl, "TP3")
                    self.om.open_positions.pop(sym, None)
                    self._tp1_hit.discard(sym)
                    continue

                if tp1_hit and sym not in self._tp1_hit:
                    self._tp1_hit.add(sym)
                    pos.sl_price = pos.entry_price   # Move to breakeven
                    pos.tp1_hit  = True
                    logger.info(f"[PAPER] TP1 hit {sym} — SL moved to breakeven @ {pos.entry_price}")

    def update_price(self, symbol: str, price: float):
        norm = symbol.replace("_","")
        if norm in self.om.open_positions:
            self.om.open_positions[norm].current_price = price
```

---

## SECTION 19 — FASTAPI SERVER (`backend/api/server.py`)

```python
"""
The orchestrator. On startup, launches all async loops:
  - MEXC WebSocket connection
  - Market scanner loop (every 5 minutes)
  - Stale order cleanup loop (every 60 seconds)
  - Position monitor loop (every 30 seconds)

Also loads Kronos model in a background thread on startup.
Serves REST API + WebSocket for the React dashboard.
"""
import asyncio, json, time, logging
from contextlib import asynccontextmanager
from typing import List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.mexc.client import MexcClient
from backend.mexc.websocket import MexcWebSocket
from backend.scanner.market_scanner import MarketScanner
from backend.strategy.confluence import score_setup
from backend.strategy.kronos_filter import run_kronos_filter, _load_kronos
from backend.risk.manager import calculate_trade
from backend.risk.account import AccountGuard
from backend.executor.order_manager import OrderManager
from backend.executor.position_tracker import PositionTracker
from backend.data.trade_log import trade_log
from backend.mexc.models import TradeSignal
from backend.config import (SCAN_INTERVAL_SECONDS, PAPER_TRADING, PRIMARY_TF,
                             BIAS_TF, CANDLES_NEEDED, COIN_COOLDOWN_HOURS,
                             MAX_OPEN_POSITIONS)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Singletons ────────────────────────────────────────────────────
client   = MexcClient()
ws_mgr   = MexcWebSocket()
account  = AccountGuard(client)
executor = OrderManager(client)
tracker  = PositionTracker(executor, account)
scanner  = MarketScanner(client)

dashboard_ws_clients: List[WebSocket] = []
bot_running   = True
coin_cooldown: dict = {}
bot_stats     = {
    "start_time": int(time.time()),
    "mode":       "PAPER" if PAPER_TRADING else "LIVE",
    "scan_count": 0,
    "last_scan":  0,
    "kronos_loaded": False,
}


async def broadcast(event: str, data: dict):
    """Send event to all connected dashboard WebSocket clients."""
    msg  = json.dumps({"event": event, "data": data, "ts": int(time.time())})
    dead = []
    for ws in dashboard_ws_clients:
        try:
            await ws.send_text(msg)
        except:
            dead.append(ws)
    for d in dead:
        if d in dashboard_ws_clients:
            dashboard_ws_clients.remove(d)


def parse_candles(raw: list):
    """
    Parse MEXC kline response into Candle objects.
    MEXC kline format: [timestamp, open, close, high, low, volume, turnover]
    Note: index 2=close, 3=high, 4=low (non-standard — be careful!)
    """
    from backend.mexc.models import Candle
    out = []
    for r in (raw or []):
        try:
            out.append(Candle(
                timestamp=int(r[0]),
                open=float(r[1]),
                close=float(r[2]),
                high=float(r[3]),
                low=float(r[4]),
                volume=float(r[5]),
                is_closed=True
            ))
        except:
            pass
    return sorted(out, key=lambda c: c.timestamp)


async def _analyze_and_trade(symbol: str, norm: str):
    """
    Full 6-layer pipeline for a single coin.
    symbol = MEXC format (BTC_USDT for WS), norm = normalized (BTCUSDT for REST)
    """
    # Fetch candles
    raw_15m = await client.get_klines(norm, PRIMARY_TF, CANDLES_NEEDED)
    raw_1h  = await client.get_klines(norm, BIAS_TF, 100)
    c15m    = parse_candles(raw_15m)
    c1h     = parse_candles(raw_1h)
    if len(c15m) < 50:
        return

    current_price = c15m[-1].close

    # Layers 2–5: SMC + Confluence
    result = score_setup(norm, c15m, c1h)

    # Log signal regardless of outcome
    kronos_vetoed   = False
    kronos_move_pct = 0.0

    if result.get("approved"):
        # Layer 6: Kronos AI filter
        result, forecast = await run_kronos_filter(
            norm, c15m, result["direction"], current_price, result
        )
        if forecast:
            kronos_vetoed   = forecast.vetoed
            kronos_move_pct = forecast.predicted_move_pct
        else:
            forecast = None
    else:
        forecast = None

    trade_log.log_signal(
        symbol=norm, direction=result.get("direction","N/A"),
        score=result.get("score", 0), breakdown=result.get("score_breakdown", {}),
        approved=result.get("approved", False), reason=result.get("reject_reason",""),
        kronos_vetoed=kronos_vetoed, kronos_move_pct=kronos_move_pct
    )
    await broadcast("signal", {
        "symbol":    norm,
        "score":     result.get("score", 0),
        "direction": result.get("direction"),
        "approved":  result.get("approved", False),
        "reason":    result.get("reject_reason", ""),
        "kronos_vetoed":    kronos_vetoed,
        "kronos_move_pct":  round(kronos_move_pct * 100, 2),
        "kronos_confidence": forecast.confidence if forecast else 0,
    })

    if not result.get("approved"):
        return

    # Risk calculation
    balance = account.get_balance() or 300.0
    params  = calculate_trade(result, balance)
    if params is None:
        logger.info(f"Trade skipped {norm}: risk params returned None")
        return

    signal = TradeSignal(
        symbol=norm, direction=result["direction"], score=result["score"],
        entry_price=params["entry_price"], sl_price=params["sl_price"],
        tp1=params["tp1"], tp2=params["tp2"], tp3=params["tp3"],
        leverage=params["leverage"], margin_usdt=params["margin_usdt"],
        notional_usdt=params["notional_usdt"], risk_usdt=params["risk_usdt"],
        reward_tp1_usdt=params["reward_tp1_usdt"], reward_tp3_usdt=params["reward_tp3_usdt"],
        score_breakdown=result["score_breakdown"], kronos=forecast,
        timestamp=int(time.time())
    )

    await executor.execute_signal(signal)
    coin_cooldown[norm] = time.time()

    # Subscribe to live ticker for this coin via WebSocket
    await ws_mgr.subscribe_ticker(symbol)

    await broadcast("trade_opened", {
        "symbol":    norm, "direction": result["direction"],
        "entry":     params["entry_price"], "sl": params["sl_price"],
        "tp1":       params["tp1"], "leverage": params["leverage"],
        "score":     result["score"], "paper": PAPER_TRADING,
        "kronos_confidence": forecast.confidence if forecast else 0,
    })


# ── Background loops ──────────────────────────────────────────────

async def scanner_loop():
    await asyncio.sleep(8)   # Let WebSocket connect first
    await account.start_of_day()
    while True:
        if bot_running and account.check_daily_limit():
            scanner.blocked = set(executor.open_positions.keys())
            top = await scanner.scan()
            bot_stats["scan_count"] += 1
            bot_stats["last_scan"]   = int(time.time())
            await broadcast("scanner_update", {
                "coins": [{"symbol": s.replace("_",""), "score": sc} for s, sc, _ in top]
            })
            for ws_symbol, _, _ in top:
                norm       = ws_symbol.replace("_","")
                cooldown_ok = (norm not in coin_cooldown or
                               (time.time() - coin_cooldown[norm]) / 3600 >= COIN_COOLDOWN_HOURS)
                if not cooldown_ok:
                    continue
                if len(executor.open_positions) >= MAX_OPEN_POSITIONS:
                    break
                try:
                    await _analyze_and_trade(ws_symbol, norm)
                except Exception as e:
                    logger.error(f"Analysis error {ws_symbol}: {e}")
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)


async def stale_order_loop():
    while True:
        await asyncio.sleep(60)
        await executor.cancel_stale_entries()


async def ws_connect_loop():
    await ws_mgr.connect()


# ── WebSocket message handlers ────────────────────────────────────

async def on_ticker(data: dict):
    symbol = data.get("symbol","")
    price  = float(data.get("lastPrice", 0))
    tracker.update_price(symbol, price)
    await broadcast("price", {"symbol": symbol.replace("_",""), "price": price})


async def on_order_update(data: dict):
    await executor.on_order_filled(data)


ws_mgr.on("push.ticker",          on_ticker)
ws_mgr.on("push.personal.order",  on_order_update)


# ── FastAPI lifespan ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load Kronos model in background thread (non-blocking)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _load_kronos)

    asyncio.create_task(ws_connect_loop())
    asyncio.create_task(scanner_loop())
    asyncio.create_task(stale_order_loop())
    asyncio.create_task(tracker.run())
    yield
    await client.close()
    await ws_mgr.stop()


app = FastAPI(lifespan=lifespan, title="SMCbot + Kronos API")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


# ── REST Endpoints ────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    from backend.strategy.kronos_filter import _predictor
    bot_stats["kronos_loaded"] = _predictor is not None
    return {**bot_stats, "running": bot_running,
            "open_positions": len(executor.open_positions)}


@app.get("/api/account")
async def get_account():
    await account.refresh()
    return {
        "balance":       account.current_balance,
        "day_open":      account.day_open_balance,
        "daily_pnl":     account.current_balance - account.day_open_balance,
        "halted":        account.halted,
        "total_pnl":     trade_log.get_total_pnl(),
        "win_rate":      round(trade_log.get_win_rate() * 100, 1),
        "kronos_acc":    round(trade_log.get_kronos_accuracy() * 100, 1),
        "equity_curve":  trade_log.get_daily_pnl_series(30),
        "mode":          "PAPER" if PAPER_TRADING else "LIVE",
    }


@app.get("/api/positions")
async def get_positions():
    return [p.dict() for p in executor.open_positions.values()]


@app.get("/api/trades")
async def get_trades(limit: int = 50):
    trades = trade_log.get_recent_trades(limit)
    return [{"id": t.id, "symbol": t.symbol, "direction": t.direction,
             "entry": t.entry_price, "exit": t.exit_price, "pnl": t.pnl_usdt,
             "rr": t.rr_achieved, "score": t.confluence_score,
             "winner": t.was_winner, "reason": t.exit_reason,
             "kronos_confidence": t.kronos_confidence,
             "entry_time": t.entry_time, "exit_time": t.exit_time}
            for t in trades]


@app.get("/api/signals")
async def get_signals(limit: int = 30):
    sigs = trade_log.get_recent_signals(limit)
    return [{"symbol": s.symbol, "direction": s.direction, "score": s.score,
             "approved": s.approved, "reason": s.reject_reason,
             "kronos_vetoed": s.kronos_vetoed,
             "kronos_move_pct": s.kronos_move_pct,
             "timestamp": s.timestamp}
            for s in sigs]


@app.post("/api/bot/stop")
async def stop_bot():
    global bot_running; bot_running = False; return {"status": "stopped"}


@app.post("/api/bot/start")
async def start_bot():
    global bot_running; bot_running = True; return {"status": "running"}


@app.websocket("/ws/live")
async def dashboard_websocket(ws: WebSocket):
    await ws.accept()
    dashboard_ws_clients.append(ws)
    # Send immediate status on connect
    from backend.strategy.kronos_filter import _predictor
    await ws.send_text(json.dumps({
        "event": "connected",
        "data": {"kronos_loaded": _predictor is not None,
                 "mode": "PAPER" if PAPER_TRADING else "LIVE"}
    }))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in dashboard_ws_clients:
            dashboard_ws_clients.remove(ws)
```

---

## SECTION 20 — REACT DASHBOARD (`dashboard/src/`)

### `dashboard/src/main.jsx`
```jsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
ReactDOM.createRoot(document.getElementById('root')).render(<App />)
```

### `dashboard/src/hooks/useWebSocket.js`
```js
import { useEffect, useRef, useCallback } from 'react'
export function useWebSocket(url, onMessage) {
  const ws    = useRef(null)
  const timer = useRef(null)
  const connect = useCallback(() => {
    ws.current = new WebSocket(url)
    ws.current.onopen    = () => console.log('Dashboard connected')
    ws.current.onmessage = (e) => { try { onMessage(JSON.parse(e.data)) } catch {} }
    ws.current.onclose   = () => { timer.current = setTimeout(connect, 3000) }
    ws.current.onerror   = () => ws.current.close()
  }, [url, onMessage])
  useEffect(() => {
    connect()
    return () => { clearTimeout(timer.current); ws.current?.close() }
  }, [connect])
}
```

### `dashboard/src/App.jsx`

Build this file as a full React SPA with the following specifications.
Use inline styles only (no CSS files). Import only from "react" and "recharts".
Use the color palette defined below. Fetch real data from the FastAPI backend.

**Color palette:**
```js
const C = {
  bg: "#050810", surface: "#0b0f1a", card: "#0f1420", border: "#1a2236",
  accent: "#00e5ff", accentDim: "#00e5ff22",
  green: "#00e676", greenDim: "#00e67622",
  red: "#ff1744",   redDim: "#ff174422",
  yellow: "#ffd600", purple: "#7b2fff",
  text: "#e2e8f0", muted: "#4a5568", dim: "#1e2a3a",
}
```

**Fonts (load via @import in style tag):**
- Space Grotesk (weights 400, 600, 700) — headings, values, body
- IBM Plex Mono (weights 400, 600) — prices, labels, codes, badges

**Tab structure (left sidebar navigation):**
- Overview
- Positions
- Signals
- History
- Scanner

**Top bar (sticky, height 56px):**
- Left: Logo "S" gradient box + "SMCbot" text + separator + status dot (pulse animation) + "LIVE/STOPPED · MEXC Futures · PAPER/LIVE" + Kronos indicator badge (shows "🧠 KRONOS" in purple when loaded, grey when not)
- Right: WS connection dot + STOP/START bot button

**Sidebar bottom:**
- Account balance (large mono font)
- Total PnL (colored green/red)
- Mode badge (PAPER yellow / LIVE green)

**Overview tab:**
- 4 stat cards: Total PnL, Day PnL, Win Rate (%), Positions (X/3)
- Equity curve chart (Recharts LineChart, accent color, reference line at 300)
- Stats mini-panel: Winners, Losers, Avg R:R, Avg Score, Kronos Accuracy %
- Live event feed (timestamped, color-coded by event type)
- Kronos status card: shows model loaded status, device, model size, accuracy

**Positions tab:**
- One card per open position showing:
  - Symbol + direction badge + leverage badge + "BE ✓" badge if tp1_hit
  - Unrealized PnL (large, live-updating)
  - Entry / Current / SL / TP1 in a 4-column grid
  - Progress bar from SL to TP1 (red→yellow→green gradient)
  - Score bar + Kronos confidence bar (purple)
  - Margin used
- Empty slot cards for unfilled positions (up to 3 total)

**Signals tab:**
- Table with columns: Symbol | Dir | Confluence Score | Kronos | Status
- Kronos column shows: predicted move % + confidence bar (purple) + "VETOED" badge in red if vetoed
- Status: "✓ APPROVED" green badge or "✗ REASON" red badge

**History tab:**
- 4 stat cards: Total Closed, Winners, Losers, Net PnL
- Table with: Symbol | Dir | Entry | Exit | PnL | R:R | Score | Kronos Conf | When
- PnL colored green/red

**Scanner tab:**
- "Next scan in Xs" countdown
- Table: Symbol | Opportunity Score bar | Status badge (WATCH/LOW/ANALYZING)

**Key behaviors:**
- On WebSocket "price" event: update current_price for matching open position
- On WebSocket "signal" event: prepend to signals list + prepend to event feed
- On WebSocket "trade_opened" event: re-fetch positions + prepend to event feed
- On WebSocket "connected" event: update kronos_loaded status
- All data falls back to demo/placeholder values if backend is offline
- Prices update live in position cards without re-fetching

---

## SECTION 21 — BUILD ORDER FOR OPENCODE

Follow this exact sequence. Build and verify each file before moving to the next.

```
1.  Create full folder structure (mkdir all directories)
2.  Write .env.example and .gitignore
3.  Write requirements.txt and dashboard/package.json and vite.config.js
4.  Write backend/config.py
5.  Write backend/mexc/models.py
6.  Write backend/mexc/client.py
7.  Write backend/mexc/websocket.py
8.  Write backend/data/candle_store.py
9.  Write backend/data/trade_log.py
10. Write backend/strategy/volatility.py
11. Write backend/strategy/structure.py
12. Write backend/strategy/smc.py
13. Write backend/strategy/confluence.py
14. Write backend/strategy/kronos_filter.py
15. Write backend/risk/manager.py
16. Write backend/risk/account.py
17. Write backend/scanner/market_scanner.py
18. Write backend/executor/order_manager.py
19. Write backend/executor/position_tracker.py
20. Write backend/api/server.py
21. Write run.py
22. Write dashboard/index.html
23. Write dashboard/src/main.jsx
24. Write dashboard/src/hooks/useWebSocket.js
25. Write dashboard/src/App.jsx (full implementation per Section 20 spec)
26. Run: git clone github dot com /shiyu-coder/Kronos.git kronos
27. Run: pip install -r requirements.txt && pip install -r kronos/requirements.txt
28. Run: python run.py (backend)
29. Run: cd dashboard && npm install && npm run dev (frontend)
30. Verify: http://localhost:3000 loads dashboard
31. Verify: Kronos model downloads and loads (check backend logs)
32. Verify: First scanner cycle runs at t+5min (check logs for "Scanner: X valid coins")
```

---

## SECTION 22 — PAPER TRADING BEHAVIOR

When `PAPER_TRADING=true` (default):
- No MEXC API order calls are made (set_leverage, place_order are skipped)
- Trade fills are simulated at `entry_price ± 0.02%` slippage immediately
- SL/TP monitoring done by PositionTracker comparing current_price (from WS ticker) to levels
- TP1 hit → SL moves to breakeven automatically
- TP3 hit or SL hit → position logged as closed in SQLite
- All P&L, win rate, and equity curve are calculated as if trades were real

To go live: set `PAPER_TRADING=false` in `.env` and restart. Ensure MEXC API keys have Contract Trading permission enabled.

---

## SECTION 23 — FULL PIPELINE FLOW DIAGRAM

```
Every 5 minutes (scanner_loop):
│
├── scanner.scan()
│     Fetch all MEXC futures tickers
│     Score by: change%, volume, volume spike, funding rate
│     Return top 5 by score
│
└── For each of top 5 coins:
      │
      ├── Check cooldown (skip if traded <4h ago)
      ├── Check max positions (skip if 3 already open)
      │
      ├── Fetch candles: 400x15m + 100x1h from MEXC REST API
      │
      ├── LAYER 2 — Structure (structure.py)
      │     Detect swing highs/lows
      │     Classify: HH/HL/LL/LH
      │     Detect CHoCH + BOS → MSS confirmed?
      │     Get 1h bias
      │     ↳ No MSS → SKIP
      │     ↳ HTF conflict → SKIP
      │
      ├── LAYER 3 — SMC (smc.py)
      │     Find Order Blocks (bullish/bearish)
      │     Find Fair Value Gaps (unfilled)
      │     Find Liquidity Levels (EQH/EQL)
      │     Detect recent Liquidity Sweep
      │     Determine Premium/Discount zone
      │
      ├── LAYER 4 — Volatility (volatility.py)
      │     Calculate ATR
      │     Check ATR/price in 0.3%–2.0% range
      │
      ├── LAYER 5 — Confluence Score (confluence.py)
      │     Score all factors 0–100
      │     ↳ Score < 70 → SKIP
      │     ↳ Price >5% from OB → SKIP
      │
      ├── LAYER 6 — Kronos AI (kronos_filter.py)
      │     Feed last 400 15m candles into Kronos model
      │     Generate 3 forecast paths × 24 candles ahead
      │     Calculate: predicted close direction, move %, confidence
      │     ↳ Direction disagrees → VETO → SKIP
      │     ↳ Move < 0.3% → VETO → SKIP
      │     ↳ Approved → use predicted high/low to refine TP2/TP3
      │
      ├── Risk calculation (risk/manager.py)
      │     entry = OB midpoint
      │     SL = OB invalidation level ±0.1% buffer
      │     sl_dist = |entry - sl| / entry
      │     notional = 2% × balance / sl_dist
      │     leverage = notional / balance (capped by score + contract max)
      │     TP1/TP2/TP3 at 1:2, 1:3, 1:5 R:R
      │
      └── Execute (order_manager.py)
            PAPER: simulate fill, track in memory, log to SQLite
            LIVE:  set isolated margin → set leverage → place limit order
                   On fill: place SL (stop-limit) + TP1/TP2/TP3 (limit, reduce-only)

Every 30 seconds (position_tracker):
  For each open position:
    Update unrealized PnL
    Check TP1 hit → move SL to breakeven
    Check SL hit  → close, log loss
    Check TP3 hit → close, log win
    Check time stop (4h in drawdown) → close at market
    Check daily loss limit → halt bot
```

---

## SECTION 24 — EXPECTED PERFORMANCE

On a $300 USDT account in paper mode:

| Metric | Target |
|--------|--------|
| Win rate | 75–85% |
| Avg winner (TP1) | $10–$15 |
| Avg loser (SL) | -$6 |
| Trades per day | 1–4 |
| Days with 0 trades | Common (no valid setups) |
| Monthly return | 50–150% (paper) |

**Kronos's role in improving these numbers:**
- Eliminates trades where price structure looks right but momentum is reversing
- Kronos-approved trades should have higher win rate than SMC-only
- Track `kronos_accuracy` stat in dashboard to verify this over time

**Go live only after:**
- Paper mode achieves ≥70% win rate over ≥30 completed trades
- Kronos accuracy stat is ≥75%
- You have verified the dashboard PnL matches your manual review of each trade

---

*End of AGENTS.md — v4.0 — OpenCode + DeepSeek v4 Pro + Kronos AI Integration*
*Feed this entire file to OpenCode. It contains everything needed to build the complete application.*
