import os
from dotenv import load_dotenv
load_dotenv()

# ── MEXC ──────────────────────────────────────────────────────────
MEXC_API_KEY    = os.getenv("MEXC_API_KEY", "")
MEXC_SECRET_KEY = os.getenv("MEXC_SECRET_KEY", "")
MEXC_BASE_URL   = "https://contract.mexc.com"
MEXC_WS_URL     = "wss://contract.mexc.com/edge"

# ── Risk ──────────────────────────────────────────────────────────
ACCOUNT_RISK_PCT         = 0.02    # Risk 2% of equity per trade
MIN_TRADE_PROFIT_USDT    = 5.0     # Skip if TP1 expected profit < $5
MAX_OPEN_POSITIONS       = 3
DAILY_LOSS_LIMIT_PCT     = 0.06    # Halt if down 6% from day-open
EMERGENCY_TRADE_LOSS_PCT = 0.05    # Force-close if single trade hits -5% equity

# ── Leverage ──────────────────────────────────────────────────────
MIN_LEVERAGE     = 5
LEVERAGE_BY_SCORE = {
    (70, 79): 20,
    (80, 89): 50,
    (90, 100): 75,
}

# ── Strategy ──────────────────────────────────────────────────────
MIN_CONFLUENCE_SCORE  = 70         # Min score to pass to Kronos layer
SWING_LOOKBACK        = 3          # Candles each side for swing detection
PRIMARY_TF            = "Min15"    # 15m candles: entry signals
BIAS_TF               = "Min60"    # 1h candles: trend bias
CANDLES_NEEDED        = 400        # How many candles to fetch (Kronos needs up to 400)
ENTRY_TIMEOUT_MINUTES = 15         # Cancel limit order if unfilled after this
TIME_STOP_HOURS       = 4          # Close position if open this long in drawdown
COIN_COOLDOWN_HOURS   = 4          # Don't re-trade a symbol within this period

# ── Scanner ───────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 300
MIN_VOLUME_24H_USDT   = 10_000_000
ATR_PCT_MIN           = 0.003
ATR_PCT_MAX           = 0.020
TOP_COINS_TO_ANALYZE  = 10

# ── Kronos AI ─────────────────────────────────────────────────────
KRONOS_MODEL          = os.getenv("KRONOS_MODEL", "small")   # mini / small / base
KRONOS_DEVICE         = os.getenv("KRONOS_DEVICE", "cpu")
KRONOS_LOOKBACK       = 400   # Candles fed into Kronos (max 512 for small/base, 2048 for mini)
KRONOS_PRED_LEN       = 24    # Forecast 24 candles ahead (= 6 hours of 15m)
KRONOS_SAMPLES        = 3     # Number of forecast paths to average (higher = more stable)
KRONOS_TEMPERATURE    = 0.8   # Sampling temperature (lower = more conservative forecast)
KRONOS_TOP_P          = 0.9   # Nucleus sampling probability
# Direction agreement threshold: Kronos predicted close must be this % away from entry
# in the correct direction to confirm the trade
KRONOS_MIN_MOVE_PCT   = 0.003   # 0.3% predicted move in trade direction required

# ── Paper Trading ─────────────────────────────────────────────────
PAPER_TRADING      = os.getenv("PAPER_TRADING", "true").lower() == "true"
PAPER_SLIPPAGE_PCT = 0.0002
