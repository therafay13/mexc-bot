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

KLINE RESPONSE FORMAT (dict-of-lists):
  {"time": [...], "open": [...], "close": [...], "high": [...], "low": [...], "vol": [...]}
  Note: close is at index 2 inside each array if the old list-of-lists format is returned.
  Current contract API returns dict-of-lists as shown above.

SYMBOL FORMAT:
  REST API (kline endpoint) uses underscore: BTC_USDT
  WebSocket uses underscore:                 BTC_USDT
  The API is inconsistent — some endpoints accept both, stick with underscore.
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

    async def get_klines(self, symbol: str, interval: str, limit: int = 400) -> dict:
        """
        Candlestick data.
        interval values: Min1, Min5, Min15, Min30, Min60, Hour4, Hour8, Day1
        Returns dict-of-lists: {"time": [...], "open": [...], "close": [...],
                                "high": [...], "low": [...], "vol": [...]}
        Falls back to progressively smaller limits if the symbol lacks enough history.
        """
        for try_limit in [limit, 200, 100, 50]:
            result = await self._req("GET", f"/api/v1/contract/kline/{symbol}",
                                      params={"interval": interval, "limit": try_limit}) or {}
            if isinstance(result, dict) and "time" in result:
                count = len(result.get("time", []))
                if count >= 50:
                    return result
                if count > 0 and try_limit == 50:
                    return result
        return {}

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
