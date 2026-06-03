import asyncio, logging, time
from typing import Optional
from backend.mexc.client import MexcClient
from backend.mexc.models import TradeSignal, OpenPosition
from backend.data.trade_log import trade_log
from backend.config import PAPER_TRADING, PAPER_SLIPPAGE_PCT, ENTRY_TIMEOUT_MINUTES, MEXC_API_MAKER_FEE

logger = logging.getLogger(__name__)


class OrderManager:
    def __init__(self, client: MexcClient, account=None):
        self.client = client
        self.account = account
        self.open_positions: dict[str, OpenPosition] = {}
        self._pending: dict[str, dict] = {}        # Live pending limit orders
        self._pending_orders: dict[str, dict] = {}  # Paper pending limit orders

    @property
    def total_active(self) -> int:
        """Count of open positions + pending limit orders."""
        return len(self.open_positions) + len(self._pending_orders)

    def place_limit_order(self, signal: TradeSignal, timeout_minutes: int = None) -> dict:
        """Place a pending limit order. Position opens when price reaches entry."""
        if signal.symbol in self._pending_orders:
            logger.warning(f"Order already pending for {signal.symbol}, skipping duplicate")
            return self._pending_orders[signal.symbol]
        if signal.symbol in self.open_positions:
            logger.warning(f"Position already open for {signal.symbol}, skipping duplicate order")
            return None
        timeout = timeout_minutes or ENTRY_TIMEOUT_MINUTES
        pending = {
            "signal": signal,
            "entry_price": signal.entry_price,
            "direction": signal.direction,
            "deadline": time.time() + timeout * 60,
            "placed_at": int(time.time()),
        }
        self._pending_orders[signal.symbol] = pending
        logger.info(f"[PAPER] Limit order placed: {signal.symbol} {signal.direction} "
                    f"@ {signal.entry_price:.4f} | {signal.leverage}x | timeout={timeout}m")
        return pending

    def check_pending_fill(self, symbol: str, current_price: float) -> Optional[dict]:
        """Check if a pending limit order's price has been reached.
        Returns the order dict if filled, None otherwise.
        LONG fills when price drops to or below entry, SHORT when price rises to or above entry."""
        pending = self._pending_orders.get(symbol)
        if not pending:
            return None
        if time.time() > pending["deadline"]:
            self._pending_orders.pop(symbol)
            logger.info(f"Pending order expired: {symbol} @ {pending['entry_price']:.4f}")
            return None
        entry = pending["entry_price"]
        if ((pending["direction"] == "LONG" and current_price <= entry) or
                (pending["direction"] == "SHORT" and current_price >= entry)):
            return self._pending_orders.pop(symbol)
        return None

    def cancel_pending(self, symbol: str):
        self._pending_orders.pop(symbol, None)
        self._pending.pop(symbol, None)

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

    async def _paper_open(self, signal: TradeSignal, fill_price: float = None):
        if fill_price is not None:
            fill = fill_price
        else:
            slip = PAPER_SLIPPAGE_PCT * (1 if signal.direction == "LONG" else -1)
            fill = signal.entry_price * (1 + slip)
        db_id = trade_log.log_trade_open(signal)
        entry_fee = signal.margin_usdt * signal.leverage * MEXC_API_MAKER_FEE
        pos   = OpenPosition(
            symbol=signal.symbol, direction=signal.direction,
            entry_price=fill, current_price=fill,
            sl_price=signal.sl_price, tp1=signal.tp1, tp2=signal.tp2, tp3=signal.tp3,
            leverage=signal.leverage, margin_usdt=signal.margin_usdt,
            score=signal.score, entry_time=int(time.time()), db_id=db_id,
            kronos_confidence=signal.kronos.confidence if signal.kronos else 0,
            entry_fee=entry_fee
        )
        self.open_positions[signal.symbol] = pos
        if self.account:
            self.account.adjust_balance(0.0, entry_fee)
        logger.info(f"[PAPER] {signal.symbol} {signal.direction} @ {fill:.6f} | "
                    f"{signal.leverage}x | Kronos conf: "
                    f"{pos.kronos_confidence:.2f} | Fee: ${entry_fee:.4f}")

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
        # Also clean stale pending limit orders
        stale_pending = [s for s, p in self._pending_orders.items() if now > p["deadline"]]
        for sym in stale_pending:
            self._pending_orders.pop(sym)
            logger.info(f"Stale limit order expired: {sym}")

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
