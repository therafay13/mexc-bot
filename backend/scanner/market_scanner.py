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
        price    = float(t.get("lastPrice", 0))
        high     = float(t.get("high24Price", 0))
        low      = float(t.get("lower24Price", 0))
        change   = abs(float(t.get("riseFallRate", 0)) * 100)
        vol      = float(t.get("amount24", 0))  # amount24 is the 24h volume in USDT
        hold_vol = float(t.get("holdVol", 0))   # holdVol is Open Interest in contracts
        
        # 1. Safety Liquidity Filter (Must have at least $10M 24h volume to avoid manipulation/slippage)
        if vol < MIN_VOLUME_24H_USDT or price < 0.00001:
            return 0
            
        # 2. Volatility Score (Up to 30 pts)
        # Calculates the 24h high-to-low range as a percentage. Ideal trading range is 4% to 15%
        range_pct = ((high - low) / price * 100) if price > 0 else 0
        vol_pts = 0
        if 4.0 <= range_pct <= 15.0:
            vol_pts = 30
        elif range_pct > 15.0:
            vol_pts = 15  # Too volatile/dangerous
        else:
            vol_pts = (range_pct / 4.0) * 15  # Scale down for low volatility
            
        # 3. Momentum / Gainer-Loser Squeeze Score (Up to 30 pts)
        # Reward coins that are actively moving (either pumping or dumping). Ideal: 3% to 15% move.
        chg_pts = 0
        if 3.0 <= change <= 15.0:
            chg_pts = 30
        elif change > 15.0:
            chg_pts = 15  # Extreme extensions are risky for SMC setups
        else:
            chg_pts = (change / 3.0) * 15  # Scale down for low momentum

        # 4. Open Interest (OI) Squeeze Score (Up to 30 pts)
        # Higher Open Interest means more leveraged traders. 
        # When OI is high relative to daily volume, it creates explosive short/long squeeze triggers.
        oi_usd = hold_vol * price
        oi_to_vol = oi_usd / vol if vol > 0 else 0
        
        # Base points for absolute OI size (we want liquid markets)
        oi_size_pts = 0
        if oi_usd >= 10_000_000:
            oi_size_pts = 15
        elif oi_usd >= 5_000_000:
            oi_size_pts = 10
        elif oi_usd >= 2_000_000:
            oi_size_pts = 5
            
        # Squeeze bonus (ratio of open positions to daily volume)
        oi_ratio_pts = 0
        if oi_to_vol >= 0.5:
            oi_ratio_pts = 15
        elif oi_to_vol >= 0.3:
            oi_ratio_pts = 10
        elif oi_to_vol >= 0.15:
            oi_ratio_pts = 5
            
        oi_pts = oi_size_pts + oi_ratio_pts

        # 5. Funding Rate Divergence (Up to 10 pts)
        # Extreme funding rates indicate crowd panic/greed, setting up perfect institutional reversals
        funding   = abs(float(t.get("fundingRate", 0)))
        fund_pts  = 10 if funding > 0.001 else 5 if funding > 0.0005 else 2
        
        return vol_pts + chg_pts + oi_pts + fund_pts
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
