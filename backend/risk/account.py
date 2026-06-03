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
        self.total_fees_paid  = 0.0

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
        if self.current_balance == 300.0:
            from backend.data.trade_log import trade_log
            total = trade_log.get_total_pnl()
            self.current_balance = max(10.0, 300.0 + total)
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

    def adjust_balance(self, pnl: float, fee: float = 0.0):
        """Adjust paper balance by realized PnL minus fees."""
        net = pnl - fee
        self.current_balance = max(0.0, self.current_balance + net)
        self.total_fees_paid += max(0, fee)
        if fee:
            logger.debug(f"Fee ${fee:.4f} | Net PnL ${net:.2f} → ${self.current_balance:.2f}")

    def get_balance(self) -> float:
        return self.current_balance
