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
from backend.strategy.kronos_oracle import load_model as load_kronos
from backend.strategy.order_flow import OrderFlowEngine
from backend.risk.manager import calculate_trade
from backend.risk.account import AccountGuard
from backend.executor.order_manager import OrderManager
from backend.executor.position_tracker import PositionTracker
from backend.data.trade_log import trade_log
from backend.data.learning_loop import run_learning_loop
from backend.mexc.models import TradeSignal
from backend.config import (SCAN_INTERVAL_SECONDS, PAPER_TRADING, PRIMARY_TF,
                             BIAS_TF, CANDLES_NEEDED, COIN_COOLDOWN_HOURS,
                             MAX_OPEN_POSITIONS, LEARNING_ENABLED,
                             LEARNING_RUN_INTERVAL_HOURS,
                             KRONOS_TIEBREAKER_PRED, KRONOS_TIEBREAKER_TF,
                             KRONOS_TIEBREAKER_BARS, KRONOS_SYNTHETIC_CONFIDENCE,
                             FLOW_NEUTRAL_LEVERAGE_MULT)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Singletons ────────────────────────────────────────────────────
client   = MexcClient()
ws_mgr   = MexcWebSocket()
account  = AccountGuard(client)
executor = OrderManager(client, account)
tracker  = None  # Created in lifespan after broadcast() is defined
scanner  = MarketScanner(client)
order_flow = OrderFlowEngine()

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
last_prices: dict = {}  # Latest ticker prices, updated by on_ticker


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
        "trend_15m": result.get("trend_15m"),
        "trend_1h": result.get("trend_1h"),
        "mss_direction_15m": result.get("mss_direction_15m"),
        "mss_direction_1h": result.get("mss_direction_1h"),
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


def _atr_pct(candles, period=14):
    """ATR(period) as fraction of current price."""
    if len(candles) < period + 1:
        return 0.01
    trs = [max(candles[i].high - candles[i].low,
               abs(candles[i].high - candles[i-1].close),
               abs(candles[i].low - candles[i-1].close))
           for i in range(1, len(candles))]
    return (sum(trs[-period:]) / period) / candles[-1].close


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
    from backend.strategy.hma_filter   import get_dual_tf_direction
    from backend.strategy.volume_ob    import find_nearest_ob
    from backend.strategy.smc_entry    import find_entry
    from backend.strategy.kronos_oracle import run_oracle
    from backend.risk.manager          import calculate_trade
    from backend.mexc.models           import TradeSignal
    from backend.data.learning_loop    import get_adjusted

    # Fetch candles
    raw_15m = await client.get_klines(symbol, PRIMARY_TF, CANDLES_NEEDED)
    raw_1h  = await client.get_klines(symbol, BIAS_TF, 100)
    c15m    = parse_candles(raw_15m)
    c1h     = parse_candles(raw_1h)
    if len(c15m) < 50:
        return

    # LAYER 2: HMA Dual-Timeframe Direction Lock
    direction = get_dual_tf_direction(c1h, c15m)
    if direction is None:
        reason = "HMA disagreement (1h vs 15m)"
        trade_log.log_signal(norm, "N/A", 0, {}, False, reason)
        await broadcast("signal", {"symbol": norm, "score": 0,
                                   "direction": None, "approved": False,
                                   "reason": reason})
        return

    current_price = c15m[-1].close

    # LAYER 3: Volume-Pivot Order Block
    ob = find_nearest_ob(c15m, direction,
                         max_distance_pct=get_adjusted("OB_MAX_DISTANCE_PCT"))
    if ob is None:
        reason = f"No volume OB within {get_adjusted('OB_MAX_DISTANCE_PCT')*100:.1f}% of price"
        trade_log.log_signal(norm, direction, 40, {}, False, reason)
        await broadcast("signal", {"symbol": norm, "score": 40,
                                   "direction": direction, "approved": False,
                                   "reason": reason})
        return

    # LAYER 4: Order Flow Confirmation (WS deal stream)
    await ws_mgr.subscribe_deals(symbol)
    flow_signal = order_flow.get_signal(norm, direction)
    logger.info(f"{norm}: flow_signal={flow_signal}")

    # LAYER 5: Kronos Tiebreaker (NEUTRAL only — CONFIRMS/OPPOSES skip)
    kronos_forecast = None
    kronos_confidence = None

    if flow_signal == "NEUTRAL":
        try:
            raw_1h_kronos = await client.get_klines(symbol, KRONOS_TIEBREAKER_TF,
                                                    KRONOS_TIEBREAKER_BARS)
            c1h_kronos    = parse_candles(raw_1h_kronos)
            if len(c1h_kronos) >= 50:
                kronos_forecast = await run_oracle(norm, c1h_kronos,
                                                   pred_len=KRONOS_TIEBREAKER_PRED)
                kronos_confidence = kronos_forecast.confidence
                kronos_dir = "LONG" if kronos_forecast.predicted_move_pct > 0 else "SHORT"
                if kronos_dir != direction:
                    reason = (f"Kronos tiebreaker disagrees: "
                              f"HMA={direction}, Kronos={kronos_dir}")
                    logger.warning(f"{norm}: {reason}")
                    trade_log.log_signal(norm, direction, 55,
                                         {"kronos": kronos_dir, "flow": flow_signal},
                                         False, reason)
                    await broadcast("signal", {"symbol": norm, "score": 55,
                                               "direction": direction, "approved": False,
                                               "reason": reason})
                    return
        except Exception as e:
            logger.warning(f"Kronos tiebreaker failed for {norm}: {e}. Skipping trade.")
            reason = "Kronos tiebreaker failed"
            trade_log.log_signal(norm, direction, 55, {"flow": flow_signal}, False, reason)
            return

    # All layers passed — find precise entry
    pred_high  = (kronos_forecast.predicted_high  if kronos_forecast
                  else current_price * 1.02)
    pred_low   = (kronos_forecast.predicted_low   if kronos_forecast
                  else current_price * 0.98)
    pred_close = (kronos_forecast.predicted_close if kronos_forecast
                  else current_price)

    smc_result = find_entry(c15m, direction, pred_high, pred_low, pred_close)

    # Derive confidence for leverage table
    if kronos_confidence is not None:
        effective_confidence = kronos_confidence
    else:
        quality_map   = KRONOS_SYNTHETIC_CONFIDENCE
        effective_confidence = quality_map.get(smc_result["smc_quality"], 0.65)

    # Flow-based confidence adjustment (synergy, not blocker)
    if flow_signal == "CONFIRMS":
        pass  # full confidence, no penalty
    elif flow_signal == "NEUTRAL":
        effective_confidence *= FLOW_NEUTRAL_LEVERAGE_MULT  # 0.7
    elif flow_signal == "OPPOSES":
        effective_confidence *= 0.5  # stronger penalty but still tradeable

    logger.info(f"PIPELINE: {norm} dir={direction} flow={flow_signal} kval={kronos_confidence} eff_conf={effective_confidence:.4f} smc_q={smc_result['smc_quality']} entry={smc_result['entry_zone']}")

    # LAYER 6: Risk Engine
    balance = account.get_balance() or 300.0
    max_lev = 50
    try:
        detail = await client.get_contract_detail(symbol)
        if detail:
            max_lev = min(int(detail.get("maxLeverage", 50)), 50)
    except Exception:
        pass
    params  = calculate_trade(effective_confidence, smc_result, balance,
                              direction, contract_max_lev=max_lev)

    if params is None:
        logger.info(f"Trade skipped {norm}: SL too tight or profit too small")
        return

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

    # Place limit order with ATR-based dynamic timeout (max 60min, min 15min)
    atrp = _atr_pct(c15m)
    dist_pct = abs(params["entry_price"] - current_price) / current_price
    expected_bars = dist_pct / atrp if atrp > 1e-6 else 1
    timeout_min = max(15, min(60, int(expected_bars * 15) + 30))
    executor.place_limit_order(signal, timeout_minutes=timeout_min)
    await ws_mgr.subscribe_ticker(symbol)

    await broadcast("pending_order", {
        "symbol": norm, "direction": direction,
        "entry": params["entry_price"], "sl": params["sl_price"],
        "tp1": params["tp1"], "leverage": params["leverage"],
        "score": score, "flow": flow_signal,
        "entry_reason": smc_result["entry_reason"],
        "paper": PAPER_TRADING,
        "remaining_sec": timeout_min * 60,
    })


# ── Background loops ──────────────────────────────────────────────

async def scanner_loop():
    await asyncio.sleep(8)   # Let WebSocket connect first
    await account.start_of_day()
    while True:
        if bot_running and account.check_daily_limit():
            scanner.blocked = set(executor.open_positions.keys()) | set(executor._pending_orders.keys())
            top = await scanner.scan()
            bot_stats["scan_count"] += 1
            bot_stats["last_scan"]   = int(time.time())
            
            global last_scan_results
            last_scan_results = top
            
            await broadcast("scanner_update", {
                "coins": [{"symbol": s.replace("_",""), "score": sc} for s, sc, _ in top]
            })
            logger.info(f"Scanner top coins: {[s.replace('_','') for s, _, _ in top]}")

            # Subscribe to deal streams early so flow data accumulates
            for ws_symbol, _, _ in top:
                await ws_mgr.subscribe_deals(ws_symbol)
                await asyncio.sleep(0.1)

            # Wait for flow data to accumulate before analysis
            await asyncio.sleep(15)

            for ws_symbol, _, _ in top:
                norm       = ws_symbol.replace("_","")
                cooldown_ok = (norm not in coin_cooldown or
                                (time.time() - coin_cooldown[norm]) / 3600 >= COIN_COOLDOWN_HOURS)
                if not cooldown_ok:
                    continue
                if executor.total_active >= MAX_OPEN_POSITIONS:
                    break
                try:
                    await _analyze_and_trade(ws_symbol, norm)
                except Exception as e:
                    logger.error(f"Analysis error {ws_symbol}: {e}", exc_info=True)
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
    last_prices[symbol.replace("_", "")] = price
    tracker.update_price(symbol, price)
    await broadcast("price", {"symbol": symbol.replace("_",""), "price": price})


async def on_order_update(data: dict):
    await executor.on_order_filled(data)


ws_mgr.on("push.ticker",          on_ticker)
ws_mgr.on("push.personal.order",  on_order_update)
ws_mgr.on("push.deal",            order_flow.on_deal)


# ── FastAPI lifespan ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global tracker
    tracker = PositionTracker(executor, account, broadcast, coin_cooldown)

    # Clean up stale trades from previous sessions
    trade_log.cleanup_orphaned_trades()

    # Load Kronos model in background thread (non-blocking)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, load_kronos)

    asyncio.create_task(ws_connect_loop())
    asyncio.create_task(scanner_loop())
    asyncio.create_task(stale_order_loop())
    asyncio.create_task(tracker.run())
    if LEARNING_ENABLED:
        asyncio.create_task(learning_loop_task())
    yield
    await client.close()
    await ws_mgr.stop()


async def learning_loop_task():
    """Run learning loop every LEARNING_RUN_INTERVAL_HOURS hours."""
    while True:
        await asyncio.sleep(LEARNING_RUN_INTERVAL_HOURS * 3600)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, run_learning_loop)


from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI(lifespan=lifespan, title="SMCbot + Kronos API")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                    allow_methods=["*"], allow_headers=["*"])

# Serve static files and the root index.html
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def read_index():
    return FileResponse("static/index.html")

# ── REST Endpoints ────────────────────────────────────────────────

@app.get("/api/init")
async def init_data():
    from backend.strategy.kronos_oracle import _predictor
    bot_stats["kronos_loaded"] = _predictor is not None
    sigs = trade_log.get_recent_signals(30)
    import backend.config as config
    return {
        "running": bot_running, "kronos_loaded": _predictor is not None,
        "mode": "PAPER" if PAPER_TRADING else "LIVE",
        "open_positions": len(executor.open_positions),
        "positions": [p.dict() for p in executor.open_positions.values()],
        "settings": {
            "MEXC_API_KEY": config.MEXC_API_KEY,
            "MEXC_SECRET_KEY": config.MEXC_SECRET_KEY,
            "PAPER_TRADING": config.PAPER_TRADING,
            "KRONOS_MODEL": config.KRONOS_MODEL,
            "KRONOS_DEVICE": config.KRONOS_DEVICE,
        },
        "signals": [{"symbol": s.symbol, "direction": s.direction, "score": s.score,
                     "approved": s.approved, "reason": s.reject_reason,
                     "kronos_vetoed": s.kronos_vetoed,
                     "kronos_move_pct": s.kronos_move_pct,
                     "detail": json.loads(s.detail) if s.detail else {},
                     "timestamp": s.timestamp} for s in sigs],
        "trades": [{"id": t.id, "symbol": t.symbol, "direction": t.direction,
                    "entry": t.entry_price, "exit": t.exit_price, "pnl": t.pnl_usdt,
                    "rr": t.rr_achieved, "score": t.confluence_score,
                    "winner": t.was_winner, "reason": t.exit_reason,
                    "kronos_confidence": t.kronos_confidence,
                    "entry_time": t.entry_time, "exit_time": t.exit_time}
                   for t in trade_log.get_recent_trades(30)],
        "scanner": [{"symbol": s, "score": sc} for s, sc, _ in last_scan_results],
    }

@app.get("/api/status")
async def get_status():
    from backend.strategy.kronos_oracle import _predictor
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
        "total_fees":    round(account.total_fees_paid, 2),
        "win_rate":      round(trade_log.get_win_rate() * 100, 1),
        "kronos_acc":    round(trade_log.get_kronos_accuracy() * 100, 1),
        "equity_curve":  trade_log.get_daily_pnl_series(30),
        "mode":          "PAPER" if PAPER_TRADING else "LIVE",
    }


@app.get("/api/positions")
async def get_positions():
    return [p.dict() for p in executor.open_positions.values()]

@app.get("/api/pending")
async def get_pending():
    result = []
    for sym, p in executor._pending_orders.items():
        sig = p["signal"]
        remaining = max(0, int(p["deadline"] - time.time()))
        result.append({
            "symbol": sym,
            "direction": sig.direction,
            "entry_price": sig.entry_price,
            "sl_price": sig.sl_price,
            "tp1": sig.tp1,
            "tp2": sig.tp2,
            "tp3": sig.tp3,
            "leverage": sig.leverage,
            "score": sig.score,
            "entry_reason": sig.score_breakdown.get("entry_reason", "") if sig.score_breakdown else "",
            "flow": sig.score_breakdown.get("flow_signal", "") if sig.score_breakdown else "",
            "remaining_sec": remaining,
        })
    return result


@app.get("/api/trades")
async def get_trades(limit: int = 30):
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


@app.post("/api/orders/{symbol}/cancel")
async def cancel_pending_order(symbol: str):
    """Cancel a pending limit order."""
    executor.cancel_pending(symbol)
    logger.info(f"Manual cancel: {symbol}")
    return {"status": "cancelled", "symbol": symbol}


@app.post("/api/orders/{symbol}/chase")
async def chase_order(symbol: str):
    """Cancel pending and immediately enter at current market price."""
    norm = symbol.upper().replace("_", "")
    pending = executor._pending_orders.get(norm)
    if not pending:
        return {"status": "error", "message": f"No pending order for {norm}"}
    price = last_prices.get(norm)
    if not price or price <= 0:
        return {"status": "error", "message": f"No price data for {norm}"}
    executor.cancel_pending(norm)
    signal = pending["signal"]
    if signal.direction == "LONG":
        old_risk = signal.entry_price - signal.sl_price
        new_risk = price - signal.sl_price
    else:
        old_risk = signal.sl_price - signal.entry_price
        new_risk = signal.sl_price - price
    if new_risk > 1e-8 and old_risk > 1e-8:
        for field in ("tp1", "tp2", "tp3"):
            old_val = getattr(signal, field)
            if signal.direction == "LONG":
                rr = (old_val - signal.entry_price) / old_risk
                setattr(signal, field, round(price + new_risk * rr, 8))
            else:
                rr = (signal.entry_price - old_val) / old_risk
                setattr(signal, field, round(price - new_risk * rr, 8))
    await executor._paper_open(signal, fill_price=price)
    logger.info(f"Manual chase: {norm} @ {price} (TP1={signal.tp1:.6f} TP2={signal.tp2:.6f} TP3={signal.tp3:.6f})")
    return {"status": "executed", "symbol": norm, "entry_price": price}


@app.post("/api/positions/{symbol}/close")
async def close_position(symbol: str):
    """Close an open position at current market price."""
    norm = symbol.upper().replace("_", "")
    if norm not in executor.open_positions:
        return {"status": "error", "message": f"No open position for {norm}"}
    await executor.close_position_market(norm, "MANUAL_CLOSE")
    logger.info(f"Manual close: {norm}")
    return {"status": "closed", "symbol": norm}


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
    from backend.strategy.kronos_oracle import _predictor
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
