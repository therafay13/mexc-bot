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
