import asyncio, time, logging
from backend.executor.order_manager import OrderManager
from backend.risk.account import AccountGuard
from backend.config import TIME_STOP_HOURS, PAPER_TRADING

logger = logging.getLogger(__name__)


class PositionTracker:
    def __init__(self, om: OrderManager, account: AccountGuard, broadcast=None, coin_cooldown: dict = None):
        self.om       = om
        self.account  = account
        self._tp1_hit = set()
        self._tp2_hit = set()
        self._broadcast = broadcast
        self._coin_cooldown = coin_cooldown or {}

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
                pos = self.om.open_positions.get(sym)
                if pos:
                    await self._close_position(sym, "DAILY_LOSS_LIMIT", pos.unrealized_pnl)
            return

        now = time.time()
        for sym, pos in list(self.om.open_positions.items()):
            sign = 1 if pos.direction == "LONG" else -1
            pnl_pct = (pos.current_price - pos.entry_price) / pos.entry_price
            pos.unrealized_pnl = pnl_pct * sign * pos.margin_usdt * pos.leverage

            if (now - pos.entry_time) / 3600 > TIME_STOP_HOURS and pos.unrealized_pnl < 0:
                await self._close_position(sym, "TIME_STOP", pos.unrealized_pnl)
                continue

            if PAPER_TRADING:
                await self._check_sl_tp(sym, pos)

    async def _close_position(self, sym: str, reason: str, pnl: float):
        pos = self.om.open_positions.pop(sym, None)
        if pos is None:
            return
        self._tp1_hit.discard(sym)
        self._tp2_hit.discard(sym)
        exit_fee = pos.margin_usdt * pos.leverage * 0.0006
        pos.exit_fees_paid = round(pos.exit_fees_paid + exit_fee, 4)
        from backend.data.trade_log import trade_log
        trade_log.log_trade_close(pos.db_id, pos.current_price, pnl, reason)
        self.account.adjust_balance(pnl, exit_fee)
        if self._broadcast:
            await self._broadcast("position_closed", {
                "symbol": sym, "reason": reason, "pnl": round(pnl, 2),
                "fees": round(pos.entry_fee + pos.exit_fees_paid, 4),
                "exit_price": pos.current_price,
            })
        self._coin_cooldown[sym] = time.time()
        logger.info(f"[PAPER] Closed {sym} ({reason}) PnL: ${pnl:.2f} | Fees: ${pos.entry_fee + pos.exit_fees_paid:.4f}")

    async def _partial_close_tp1(self, sym: str, pos):
        """Close 50% of position at TP1, halve remaining margin, SL to breakeven."""
        closed_margin = pos.margin_usdt * 0.5
        exit_fee = closed_margin * pos.leverage * 0.0006
        pos.exit_fees_paid = round(pos.exit_fees_paid + exit_fee, 4)
        half_pnl = pos.unrealized_pnl * 0.5
        from backend.data.trade_log import trade_log
        trade_log.log_trade_close(pos.db_id, pos.current_price, half_pnl, "TP1_PARTIAL")
        self.account.adjust_balance(half_pnl, exit_fee)

        pos.margin_usdt = round(closed_margin, 2)
        pos.sl_price = pos.entry_price
        pos.tp1_hit = True
        self._tp1_hit.add(sym)

        if self._broadcast:
            await self._broadcast("sl_moved", {
                "symbol": sym, "sl_price": pos.entry_price,
                "margin_usdt": pos.margin_usdt,
                "tp1_pnl": round(half_pnl, 2),
                "fee": round(exit_fee, 4),
            })
        logger.info(f"[PAPER] TP1 partial close {sym}: +${half_pnl:.2f} (fee ${exit_fee:.4f}), SL→BE, margin → ${pos.margin_usdt:.2f}")

    async def _partial_close_tp2(self, sym: str, pos):
        """Close 60% of remaining position at TP2 (= 30% of original)."""
        frac = 0.6
        closed_margin = pos.margin_usdt * frac
        exit_fee = closed_margin * pos.leverage * 0.0006
        pos.exit_fees_paid = round(pos.exit_fees_paid + exit_fee, 4)
        part_pnl = pos.unrealized_pnl * frac
        from backend.data.trade_log import trade_log
        trade_log.log_trade_close(pos.db_id, pos.current_price, part_pnl, "TP2_PARTIAL")
        self.account.adjust_balance(part_pnl, exit_fee)

        pos.margin_usdt = round(pos.margin_usdt * (1 - frac), 2)
        pos.tp2_hit = True
        self._tp2_hit.add(sym)

        if self._broadcast:
            await self._broadcast("tp_hit", {
                "symbol": sym, "level": "TP2", "pnl": round(part_pnl, 2),
                "fee": round(exit_fee, 4),
                "margin_usdt": pos.margin_usdt,
            })
        logger.info(f"[PAPER] TP2 partial close {sym}: +${part_pnl:.2f} (fee ${exit_fee:.4f}) margin → ${pos.margin_usdt:.2f}")

    async def _check_sl_tp(self, sym: str, pos):
        sl_hit  = ((pos.direction == "LONG"  and pos.current_price <= pos.sl_price) or
                   (pos.direction == "SHORT" and pos.current_price >= pos.sl_price))
        tp1_hit = ((pos.direction == "LONG"  and pos.current_price >= pos.tp1) or
                   (pos.direction == "SHORT" and pos.current_price <= pos.tp1))
        tp2_hit = ((pos.direction == "LONG"  and pos.current_price >= pos.tp2) or
                   (pos.direction == "SHORT" and pos.current_price <= pos.tp2))
        tp3_hit = ((pos.direction == "LONG"  and pos.current_price >= pos.tp3) or
                   (pos.direction == "SHORT" and pos.current_price <= pos.tp3))

        if sl_hit:
            await self._close_position(sym, "SL", pos.unrealized_pnl)
            return

        if tp3_hit:
            await self._close_position(sym, "TP3", pos.unrealized_pnl)
            return

        if tp2_hit and sym not in self._tp2_hit and sym in self._tp1_hit:
            await self._partial_close_tp2(sym, pos)
            return

        if tp1_hit and sym not in self._tp1_hit:
            await self._partial_close_tp1(sym, pos)
            return

    async def _fill_pending(self, pending: dict):
        """Open a position when a pending limit order is filled by price arrival."""
        signal = pending["signal"]
        fill   = pending["entry_price"]
        symbol = signal.symbol
        self._coin_cooldown.pop(symbol, None)  # ensure no stale cooldown
        await self.om._paper_open(signal, fill_price=fill)
        if self._broadcast:
            await self._broadcast("trade_opened", {
                "symbol": symbol, "direction": signal.direction,
                "entry": fill, "sl": signal.sl_price,
                "tp1": signal.tp1, "leverage": signal.leverage,
                "score": signal.score, "entry_reason": "limit_filled",
                "paper": True,
            })

    def update_price(self, symbol: str, price: float):
        norm = symbol.replace("_", "")
        pos = self.om.open_positions.get(norm)
        if pos:
            pos.current_price = price
            sign = 1 if pos.direction == "LONG" else -1
            pnl_pct = (price - pos.entry_price) / pos.entry_price
            pos.unrealized_pnl = pnl_pct * sign * pos.margin_usdt * pos.leverage
            if PAPER_TRADING:
                import asyncio
                asyncio.get_event_loop().create_task(self._check_sl_tp(norm, pos))

        # Check pending limit orders for fill
        filled = self.om.check_pending_fill(norm, price)
        if filled:
            import asyncio
            asyncio.get_event_loop().create_task(self._fill_pending(filled))
