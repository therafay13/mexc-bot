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
    (90, 100): 50,
}

# ── Strategy ──────────────────────────────────────────────────────
MIN_CONFLUENCE_SCORE  = 70         # Min score to pass to Kronos layer
SWING_LOOKBACK        = 3          # Candles each side for swing detection
PRIMARY_TF            = "Min15"    # 15m candles: entry signals
BIAS_TF               = "Min60"    # 1h candles: trend bias
CANDLES_NEEDED        = 400        # How many candles to fetch (Kronos needs up to 400)
ENTRY_TIMEOUT_MINUTES = 15         # Cancel limit order if unfilled after this
TIME_STOP_HOURS       = 4          # Close position if open this long in drawdown
COIN_COOLDOWN_HOURS   = 2          # Don't re-trade a symbol within this period

# ── Scanner ───────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 300
MIN_VOLUME_24H_USDT   = 2_000_000
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
# ── Kronos Oracle thresholds ───────────────────────────────────────
KRONOS_MIN_CONFIDENCE = float(os.getenv("KRONOS_MIN_CONFIDENCE", "0.50"))
KRONOS_MIN_MOVE_PCT   = float(os.getenv("KRONOS_MIN_MOVE_PCT", "0.005"))   # 0.50%

# ── HMA Trend Filter ──────────────────────────────────────────────
HMA_LENGTH          = 55          # Hull MA period (matches SMC Flow indicator setting)
HMA_PRIMARY_TF      = "Hour1"     # 1h timeframe for trend bias
HMA_ENTRY_TF        = "Min15"     # 15m timeframe for entry alignment

# ── Volume-Pivot Order Block (LuxAlgo method) ─────────────────────
OB_PIVOT_LENGTH     = 5           # Bars each side for volume pivot detection
OB_MAX_DISTANCE_PCT = 0.03        # Max 3% from current price to use an OB
OB_MITIGATION       = "Wick"      # "Wick" or "Close" — matches indicator setting

# ── Order Flow Engine ─────────────────────────────────────────────
FLOW_BUCKET_SECONDS  = 30         # Aggregate trades into 30-second windows
FLOW_WINDOW_BUCKETS  = 6          # 6 buckets x 30s = 3-minute rolling window
FLOW_CONFIRM_RATIO   = 1.4        # Bull/bear ratio required to confirm LONG
FLOW_OPPOSE_RATIO    = 0.7        # Ratio below this = opposing flow (skip)
FLOW_SUSTAIN_BUCKETS = 2          # Ratio must be sustained in this many of last 3 buckets
FLOW_LARGE_PRINT_USD = 50000      # Single trade > this USDT = large print
FLOW_ABSORPTION_PCT  = 0.0005     # Price move < 0.05% with large vol = absorption
FLOW_NEUTRAL_LEVERAGE_MULT = 0.7  # Reduce leverage by 30% when flow is neutral

# ── Kronos (demoted to tiebreaker) ────────────────────────────────
KRONOS_TIEBREAKER_ONLY  = True    # Kronos only runs when flow is NEUTRAL
KRONOS_TIEBREAKER_TF    = "Min60" # Feed 1h candles (coarser = better for small model)
KRONOS_TIEBREAKER_BARS  = 200     # 200 x 1h candles input
KRONOS_TIEBREAKER_PRED  = 4       # Predict 4 bars ahead (4 hours at 1h)
# Synthetic confidence when Kronos not invoked (maps SMC quality -> leverage table)
KRONOS_SYNTHETIC_CONFIDENCE = {0: 0.55, 1: 0.65, 2: 0.75, 3: 0.85}

# ── Self-Learning Loop ────────────────────────────────────────────
LEARNING_ENABLED            = True
LEARNING_MIN_TRADES         = 15   # Don't adjust until this many closed trades
LEARNING_RUN_INTERVAL_HOURS = 24   # Run daily
LEARNING_MAX_DRIFT          = 0.30 # Never adjust more than +-30% from default

# ── Fees ─────────────────────────────────────────────────────────
MEXC_API_MAKER_FEE = 0.0004   # 0.04% — API fee rate (Jun 2026)
MEXC_API_TAKER_FEE = 0.0006   # 0.06% — API fee rate (Jun 2026)

# ── Paper Trading ─────────────────────────────────────────────────
PAPER_TRADING      = os.getenv("PAPER_TRADING", "true").lower() == "true"
PAPER_SLIPPAGE_PCT = 0.0002
