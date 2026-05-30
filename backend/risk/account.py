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
