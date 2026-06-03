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
    entry_fee: float = 0.0
    exit_fees_paid: float = 0.0
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    db_id: int = 0
    kronos_confidence: float = 0.0
