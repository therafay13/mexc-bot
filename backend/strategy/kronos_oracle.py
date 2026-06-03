"""
Kronos Oracle — PRIMARY direction and confidence engine.

This is now Layer 2, not Layer 6.
It runs FIRST and its output drives everything downstream.

Kronos tells us:
  - Which direction to trade (LONG/SHORT)
  - How confident it is (informs leverage)
  - Where price is likely to go (informs TP levels)
  - Whether this coin is worth trading at all (confidence + move threshold)

SMC then finds us the best ENTRY for that direction.
MSS then does a light sanity check.
"""
import os
import sys
import logging
import time as time_module
import numpy as np
import pandas as pd
from typing import List, Optional
from backend.mexc.models import Candle, KronosForecast
from backend.config import (KRONOS_MODEL, KRONOS_DEVICE, KRONOS_LOOKBACK,
                            KRONOS_PRED_LEN, KRONOS_SAMPLES, KRONOS_TEMPERATURE,
                            KRONOS_TOP_P)

logger = logging.getLogger(__name__)

# Minimum confidence to trade at all
KRONOS_MIN_CONFIDENCE = float(os.getenv("KRONOS_MIN_CONFIDENCE", "0.50"))
# Minimum absolute predicted move to trade
KRONOS_MIN_MOVE_PCT   = float(os.getenv("KRONOS_MIN_MOVE_PCT", "0.0025"))  # 0.25%

_predictor  = None
_load_error = None


def load_model():
    """Load Kronos model. Called once at startup in background thread."""
    global _predictor, _load_error

    if KRONOS_MODEL == "mini":
        tok_name = "NeoQuasar/Kronos-Tokenizer-2k"
        mod_name = "NeoQuasar/Kronos-mini"
        max_ctx  = min(2048, KRONOS_LOOKBACK)
    elif KRONOS_MODEL == "base":
        tok_name = "NeoQuasar/Kronos-Tokenizer-base"
        mod_name = "NeoQuasar/Kronos-base"
        max_ctx  = min(512, KRONOS_LOOKBACK)
    else:
        tok_name = "NeoQuasar/Kronos-Tokenizer-base"
        mod_name = "NeoQuasar/Kronos-small"
        max_ctx  = min(512, KRONOS_LOOKBACK)

    # Try local source first
    kronos_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "kronos"
    )
    if os.path.isdir(kronos_path) and kronos_path not in sys.path:
        sys.path.insert(0, kronos_path)

    try:
        from model import Kronos, KronosTokenizer, KronosPredictor
        logger.info(f"Loading Kronos {KRONOS_MODEL} on {KRONOS_DEVICE}...")
        tokenizer  = KronosTokenizer.from_pretrained(tok_name)
        model      = Kronos.from_pretrained(mod_name)
        model      = model.to(KRONOS_DEVICE)
        model.eval()
        _predictor = KronosPredictor(model, tokenizer, max_context=max_ctx)
        logger.info(f"Kronos {KRONOS_MODEL} ready on {KRONOS_DEVICE}")
        return
    except Exception as e:
        logger.warning(f"Primary Kronos load failed: {e}. Trying HuggingFace Hub...")

    # HuggingFace Hub fallback
    try:
        from huggingface_hub import snapshot_download
        local = snapshot_download(repo_id=mod_name, local_dir=f"./hf_cache/{KRONOS_MODEL}")
        if local not in sys.path:
            sys.path.insert(0, local)
        from model import Kronos, KronosTokenizer, KronosPredictor
        tokenizer  = KronosTokenizer.from_pretrained(tok_name)
        model      = Kronos.from_pretrained(local)
        model      = model.to(KRONOS_DEVICE)
        model.eval()
        _predictor = KronosPredictor(model, tokenizer, max_context=max_ctx)
        logger.info(f"Kronos loaded from HuggingFace Hub")
        return
    except Exception as e:
        logger.warning(f"HF Hub load failed: {e}")

    _load_error = "Kronos unavailable — bot will not trade (requires Kronos for direction)"
    logger.error(_load_error)


def _run_sync(candles: List[Candle], symbol: str, pred_len: int = None) -> KronosForecast:
    """
    Synchronous Kronos inference. Run in asyncio executor (not on event loop).

    pred_len: override prediction length. Defaults to KRONOS_PRED_LEN from config.
              When used as tiebreaker: pass 4 (4 hours on 1h data).

    Returns KronosForecast with:
      - direction:          LONG or SHORT
      - confidence:         0.0–1.0
      - predicted_move_pct: signed % move (positive=up, negative=down)
      - predicted_high:     max high across all sample paths
      - predicted_low:      min low across all sample paths
      - predicted_close:    mean final close across all sample paths
      - vetoed:             True if confidence or move below threshold
    """
    if pred_len is None:
        pred_len = KRONOS_PRED_LEN

    current_price = candles[-1].close

    if _predictor is None:
        return KronosForecast(
            symbol=symbol, direction_agreement=False,
            predicted_move_pct=0, predicted_high=current_price,
            predicted_low=current_price, predicted_close=current_price,
            confidence=0, vetoed=True,
            veto_reason="Kronos not loaded — cannot determine direction"
        )

    lookback = min(KRONOS_LOOKBACK, len(candles))
    hist     = candles[-lookback:]
    interval = hist[-1].timestamp - hist[-2].timestamp if len(hist) >= 2 else 900
    last_ts  = hist[-1].timestamp

    df = pd.DataFrame({
        "open":   [c.open   for c in hist],
        "high":   [c.high   for c in hist],
        "low":    [c.low    for c in hist],
        "close":  [c.close  for c in hist],
        "volume": [c.volume for c in hist],
    })

    x_ts = pd.to_datetime(pd.Series([c.timestamp for c in hist]), unit="s")
    y_ts = pd.to_datetime(pd.Series([last_ts + interval * (i + 1) for i in range(pred_len)]), unit="s")

    all_closes, all_highs, all_lows, all_preds = [], [], [], []

    for s in range(KRONOS_SAMPLES):
        try:
            pred = _predictor.predict(
                df=df, x_timestamp=x_ts, y_timestamp=y_ts,
                pred_len=pred_len,
                T=KRONOS_TEMPERATURE, top_p=KRONOS_TOP_P, sample_count=1
            )
            all_closes.append(float(pred["close"].iloc[-1]))
            all_highs.append(float(pred["high"].max()))
            all_lows.append(float(pred["low"].min()))
            all_preds.append(pred)
        except Exception as e:
            logger.warning(f"Kronos sample {s+1} failed for {symbol}: {e}")

    if not all_closes:
        return KronosForecast(
            symbol=symbol, direction_agreement=False,
            predicted_move_pct=0, predicted_high=current_price,
            predicted_low=current_price, predicted_close=current_price,
            confidence=0, vetoed=True, veto_reason="All Kronos samples failed"
        )

    mean_close     = float(np.mean(all_closes))
    std_close      = float(np.std(all_closes)) if len(all_closes) > 1 else 0
    confidence     = float(max(0.0, min(1.0, 1.0 - std_close / max(abs(mean_close), 1e-8))))

    pred_high      = float(np.max(all_highs))
    pred_low       = float(np.min(all_lows))
    predicted_move = (mean_close - current_price) / current_price

    # Direction is determined purely by Kronos
    direction      = "LONG" if predicted_move > 0 else "SHORT"

    # Veto conditions
    veto_reason = ""
    vetoed      = False
    if confidence < KRONOS_MIN_CONFIDENCE:
        vetoed      = True
        veto_reason = f"Confidence {confidence:.2f} < minimum {KRONOS_MIN_CONFIDENCE}"
    elif abs(predicted_move) < KRONOS_MIN_MOVE_PCT:
        vetoed      = True
        veto_reason = f"Move {predicted_move*100:.3f}% < minimum {KRONOS_MIN_MOVE_PCT*100:.2f}%"

    return KronosForecast(
        symbol=symbol,
        direction_agreement=not vetoed,   # True = Kronos approved, has direction
        predicted_move_pct=round(predicted_move, 6),
        predicted_high=round(pred_high, 6),
        predicted_low=round(pred_low, 6),
        predicted_close=round(mean_close, 6),
        confidence=round(confidence, 4),
        vetoed=vetoed,
        veto_reason=veto_reason
    )


async def run_oracle(symbol: str, candles: List[Candle],
                     pred_len: int = None) -> KronosForecast:
    """
    Async wrapper. Runs Kronos in thread executor.
    pred_len: override prediction length. Defaults to KRONOS_PRED_LEN.
    Returns KronosForecast. Check forecast.vetoed before proceeding.
    forecast.direction_agreement == True means: trade in this direction.
    """
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run_sync, candles, symbol, pred_len)


def is_loaded() -> bool:
    return _predictor is not None


def get_load_error() -> Optional[str]:
    return _load_error
