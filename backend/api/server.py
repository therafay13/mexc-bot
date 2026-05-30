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
last_scan_results = [] # Cache for the last scanner run


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


def parse_candles(raw):
    """
    Parse MEXC Futures kline response into Candle objects.
    MEXC contract API returns a dict-of-lists:
      {"time": [...], "open": [...], "close": [...], "high": [...], "low": [...], "vol": [...]}
    """
    from backend.mexc.models import Candle
    out = []
    if not isinstance(raw, dict) or "time" not in raw:
        return []

    times  = raw.get("time", [])
    opens  = raw.get("open", [])
    closes = raw.get("close", [])
    highs  = raw.get("high", [])
    lows   = raw.get("low", [])
    vols   = raw.get("vol", [])

    length = min(len(times), len(opens), len(closes), len(highs), len(lows), len(vols))

    for i in range(length):
        try:
            out.append(Candle(
                timestamp=int(times[i]),
                open=float(opens[i]),
                close=float(closes[i]),
                high=float(highs[i]),
                low=float(lows[i]),
                volume=float(vols[i]),
                is_closed=True
            ))
        except:
            pass
    return sorted(out, key=lambda c: c.timestamp)


def _summarize_analysis(result: dict) -> dict:
    """Extract key analysis fields from score_setup result for storage."""
    summary = {
        "direction": result.get("direction"),
        "score": result.get("score", 0),
        "score_breakdown": result.get("score_breakdown", {}),
        "reject_reason": result.get("reject_reason", ""),
        "entry_zone": result.get("entry_zone"),
        "sl_price": result.get("sl_price"),
        "current_price": result.get("current_price"),
        "atr": result.get("atr"),
        "atr_pct": result.get("atr_pct"),
    }
    ob = result.get("active_ob")
    if ob:
        summary["order_block"] = {
            "high": ob.ob_high, "low": ob.ob_low,
            "direction": ob.direction
        }
    fvgs = result.get("fvgs", [])
    if fvgs:
        summary["fair_value_gaps"] = [
            {"high": f.fvg_high, "low": f.fvg_low, "direction": f.direction}
            for f in fvgs[:5]
        ]
    return summary


async def _analyze_and_trade(symbol: str, norm: str):
    """
    Full 6-layer pipeline for a single coin.
    symbol = MEXC format (BTC_USDT for WS), norm = normalized (BTCUSDT for REST)
    """
    logger.info(f"Analyzing {norm}...")
    
    # Fetch candles
    try:
        raw_15m = await client.get_klines(symbol, PRIMARY_TF, CANDLES_NEEDED)
        raw_1h  = await client.get_klines(symbol, BIAS_TF, 100)
        c15m    = parse_candles(raw_15m)
        c1h     = parse_candles(raw_1h)
    except Exception as e:
        logger.error(f"Fetch error for {norm}: {e}")
        return

    if len(c15m) < 50:
        logger.warning(f"Rejected {norm}: Insufficient data ({len(c15m)}/50 candles)")
        trade_log.log_signal(
            symbol=norm, direction="N/A", score=0, breakdown={},
            approved=False, reason=f"Insufficient data ({len(c15m)} candles)",
            kronos_vetoed=False, kronos_move_pct=0.0, detail={}
        )
        return

    current_price = c15m[-1].close

    # Layers 2–5: SMC + Confluence
    result = score_setup(norm, c15m, c1h)
    result_summary = _summarize_analysis(result)

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
        kronos_vetoed=kronos_vetoed, kronos_move_pct=kronos_move_pct,
        detail=result_summary
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
            
            global last_scan_results
            last_scan_results = top
            
            await broadcast("scanner_update", {
                "coins": [{"symbol": s.replace("_",""), "score": sc} for s, sc, _ in top]
            })
            logger.info(f"Scanner top coins: {[s.replace('_','') for s, _, _ in top]}")
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


from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# ... (previous imports)
from backend.mexc.models import TradeSignal
from backend.config import (SCAN_INTERVAL_SECONDS, PAPER_TRADING, PRIMARY_TF,
                             BIAS_TF, CANDLES_NEEDED, COIN_COOLDOWN_HOURS,
                             MAX_OPEN_POSITIONS)

app = FastAPI(lifespan=lifespan, title="SMCbot + Kronos API")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                    allow_methods=["*"], allow_headers=["*"])

# Serve static files and the root index.html
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def read_index():
    return FileResponse("static/index.html")

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

@app.get("/api/scanner")
async def get_scanner():
    return [{"symbol": s.replace("_",""), "score": sc} for s, sc, _ in last_scan_results]

@app.get("/api/signals")
async def get_signals(limit: int = 30):

    sigs = trade_log.get_recent_signals(limit)
    return [{"symbol": s.symbol, "direction": s.direction, "score": s.score,
             "approved": s.approved, "reason": s.reject_reason,
             "kronos_vetoed": s.kronos_vetoed,
             "kronos_move_pct": s.kronos_move_pct,
             "detail": json.loads(s.detail) if s.detail else {},
             "timestamp": s.timestamp}
            for s in sigs]


@app.post("/api/bot/stop")
async def stop_bot():
    global bot_running; bot_running = False; return {"status": "stopped"}


@app.post("/api/bot/start")
async def start_bot():
    global bot_running; bot_running = True; return {"status": "running"}


@app.post("/api/bot/settings")
async def update_settings(settings: dict):
    import os
    from dotenv import set_key
    env_path = ".env"
    if not os.path.exists(env_path):
        with open(env_path, "w") as f: f.write("")
    
    for k, v in settings.items():
        set_key(env_path, k, str(v))
    
    # Reload config in memory
    import importlib
    import backend.config
    importlib.reload(backend.config)
    
    return {"status": "settings updated. Please restart bot for all changes to take effect."}

@app.get("/api/settings")
async def get_settings():
    import backend.config as config
    return {
        "MEXC_API_KEY": config.MEXC_API_KEY,
        "MEXC_SECRET_KEY": config.MEXC_SECRET_KEY,
        "PAPER_TRADING": config.PAPER_TRADING,
        "KRONOS_MODEL": config.KRONOS_MODEL,
        "KRONOS_DEVICE": config.KRONOS_DEVICE,
    }

@app.get("/api/debug")
async def debug_diagnostic():
    """One-click diagnostic: shows what MEXC returns vs what the bot parses."""
    import json as _json
    out = {"mexc_ticker_sample": None, "scanner_results": [], "kline_test": None}
    
    # 1. Fetch raw tickers and show first 2 samples
    try:
        tickers = await client.get_all_tickers()
        if isinstance(tickers, list) and len(tickers) > 0:
            out["mexc_ticker_sample"] = [
                {k: v for k, v in t.items() if k in ("symbol","lastPrice","high24Price","lower24Price","riseFallRate","amount24","holdVol","fundingRate")}
                for t in tickers[:2]
            ]
            out["ticker_count"] = len(tickers)
        else:
            out["mexc_ticker_sample"] = f"Unexpected type: {type(tickers).__name__} = {str(tickers)[:200]}"
    except Exception as e:
        out["mexc_ticker_error"] = str(e)
    
    # 2. Run scanner inline
    try:
        from backend.config import MIN_VOLUME_24H_USDT
        scanner.blocked = set()
        top = await scanner.scan()
        out["scanner_results"] = [
            {"symbol": s.replace("_",""), "score": sc}
            for s, sc, _ in top
        ]
        out["scanner_count"] = len(top)
        if len(top) > 0:
            # 3. Test klines for top coin
            sym = top[0][0]  # keep underscore (MEXC REST format)
            out["kline_test_symbol"] = sym
            raw = await client.get_klines(sym, PRIMARY_TF, CANDLES_NEEDED)
            out["kline_raw_type"] = type(raw).__name__
            if isinstance(raw, dict) and "time" in raw:
                out["kline_raw_count"] = len(raw.get("time", []))
                c15m = parse_candles(raw)
                out["kline_parsed_count"] = len(c15m)
                if len(c15m) > 0:
                    out["kline_parsed_sample"] = {
                        "timestamp": c15m[0].timestamp, "open": c15m[0].open,
                        "high": c15m[0].high, "low": c15m[0].low,
                        "close": c15m[0].close, "volume": c15m[0].volume,
                    }
            else:
                out["kline_raw_preview"] = str(raw)[:300]
    except Exception as e:
        out["scanner_error"] = str(e)
    
    out["bot_running"] = bot_running
    out["last_scan"] = bot_stats["last_scan"]
    out["scan_count"] = bot_stats["scan_count"]
    return out


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
