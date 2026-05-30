"""
LAYER 6 — Kronos AI Foundation Model Integration.

This is the final veto gate before a trade fires.

HOW IT WORKS:
  1. Take the last KRONOS_LOOKBACK closed candles for the symbol (15m timeframe)
  2. Feed them into KronosPredictor
  3. Predict the next KRONOS_PRED_LEN candles (default: 24 candles = 6 hours of 15m)
  4. Measure: does the predicted final close move in the SMC trade direction?
  5. If yes AND predicted move > KRONOS_MIN_MOVE_PCT → APPROVE
  6. If no OR predicted move is too small → VETO (trade is cancelled)
  7. Use Kronos predicted high/low to refine TP2 and TP3 levels

KRONOS MODELS (choose in .env as KRONOS_MODEL):
  mini:  Kronos-mini  + Kronos-Tokenizer-2k  | 4.1M params  | context 2048 | fastest
  small: Kronos-small + Kronos-Tokenizer-base | 24.7M params | context 512  | balanced
  base:  Kronos-base  + Kronos-Tokenizer-base | 102.3M params| context 512  | best accuracy

CONFIDENCE METRIC:
  Run KRONOS_SAMPLES forecast paths (default 3).
  Measure standard deviation of final predicted closes across samples.
  Confidence = 1 - (std / mean) clamped to [0, 1]
  High confidence (>0.7) = samples agree = stronger signal

IMPORTANT:
  - Kronos is run synchronously in an executor thread (it uses PyTorch — not async-safe)
  - If Kronos fails (model download error, OOM, etc.) → log warning and ALLOW trade
    (fail-open: SMC signal alone is sufficient, Kronos is enhancement not dependency)
  - Model is loaded ONCE at startup and cached in memory
  - On CPU with Kronos-small: inference takes ~2-4 seconds per coin (acceptable)
  - On GPU: inference takes <1 second
"""
import asyncio
import logging
import sys
import os
import numpy as np
import pandas as pd
from typing import List, Optional
from backend.mexc.models import Candle, KronosForecast
from backend.config import (KRONOS_MODEL, KRONOS_DEVICE, KRONOS_LOOKBACK,
                             KRONOS_PRED_LEN, KRONOS_SAMPLES, KRONOS_TEMPERATURE,
                             KRONOS_TOP_P, KRONOS_MIN_MOVE_PCT)

logger = logging.getLogger(__name__)

# ── Model loading ─────────────────────────────────────────────────
_predictor = None
_load_error = None


def _load_kronos():
    """Load Kronos model on first call. Called once at startup."""
    global _predictor, _load_error
    try:
        # Add kronos/ directory to Python path
        kronos_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                    "kronos")
        if kronos_path not in sys.path:
            sys.path.insert(0, kronos_path)

        from model import Kronos, KronosTokenizer, KronosPredictor

        if KRONOS_MODEL == "mini":
            tokenizer_name = "NeoQuasar/Kronos-Tokenizer-2k"
            model_name     = "NeoQuasar/Kronos-mini"
            max_context    = 2048
        else:
            tokenizer_name = "NeoQuasar/Kronos-Tokenizer-base"
            model_name     = f"NeoQuasar/Kronos-{KRONOS_MODEL}"
            max_context    = 512

        logger.info(f"Loading Kronos model: {model_name} on {KRONOS_DEVICE}...")
        tokenizer  = KronosTokenizer.from_pretrained(tokenizer_name)
        model      = Kronos.from_pretrained(model_name)
        model      = model.to(KRONOS_DEVICE)
        model.eval()

        _predictor = KronosPredictor(model, tokenizer,
                                      max_context=min(max_context, KRONOS_LOOKBACK))
        logger.info(f"Kronos {KRONOS_MODEL} loaded successfully on {KRONOS_DEVICE}")
    except Exception as e:
        _load_error = str(e)
        logger.warning(f"Kronos failed to load: {e}. Bot will run on SMC signals only.")


def candles_to_dataframe(candles: List[Candle]) -> pd.DataFrame:
    """Convert list of Candle objects to DataFrame format Kronos expects."""
    return pd.DataFrame({
        "open":   [c.open   for c in candles],
        "high":   [c.high   for c in candles],
        "low":    [c.low    for c in candles],
        "close":  [c.close  for c in candles],
        "volume": [c.volume for c in candles],
    })


def _run_kronos_sync(candles: List[Candle], direction: str,
                      current_price: float) -> KronosForecast:
    """
    Synchronous Kronos inference. Run in executor thread from async code.
    Returns KronosForecast with agreement flag, confidence, and predicted levels.
    """
    if _predictor is None:
        # Kronos not loaded → fail open (allow trade, no veto)
        return KronosForecast(
            symbol="", direction_agreement=True, predicted_move_pct=0,
            predicted_high=current_price * 1.01, predicted_low=current_price * 0.99,
            predicted_close=current_price, confidence=0, vetoed=False,
            veto_reason="Kronos unavailable (fail-open)"
        )

    # Use last KRONOS_LOOKBACK candles, cap by model max context
    lookback = min(KRONOS_LOOKBACK, len(candles))
    hist     = candles[-lookback:]

    # Generate timestamps
    last_ts  = hist[-1].timestamp
    interval = 900  # 15 minutes in seconds
    x_ts     = pd.Series([c.timestamp for c in hist])
    y_ts     = pd.Series([last_ts + interval * (i+1) for i in range(KRONOS_PRED_LEN)])

    df = candles_to_dataframe(hist)

    # Run multiple forecast samples for confidence estimation
    final_closes = []
    all_preds    = []
    for _ in range(KRONOS_SAMPLES):
        try:
            pred = _predictor.predict(
                df=df,
                x_timestamp=x_ts,
                y_timestamp=y_ts,
                pred_len=KRONOS_PRED_LEN,
                T=KRONOS_TEMPERATURE,
                top_p=KRONOS_TOP_P,
                sample_count=1
            )
            final_closes.append(pred["close"].iloc[-1])
            all_preds.append(pred)
        except Exception as e:
            logger.warning(f"Kronos sample failed: {e}")

    if not final_closes:
        # All samples failed → fail open
        return KronosForecast(
            symbol="", direction_agreement=True, predicted_move_pct=0,
            predicted_high=current_price, predicted_low=current_price,
            predicted_close=current_price, confidence=0, vetoed=False,
            veto_reason="All Kronos samples failed (fail-open)"
        )

    # Aggregate predictions
    mean_close = float(np.mean(final_closes))
    std_close  = float(np.std(final_closes))
    confidence = float(max(0, 1 - std_close / max(abs(mean_close), 1e-8)))
    confidence = min(1.0, confidence)

    # Use first successful pred for high/low range
    pred_df = all_preds[0]
    pred_high  = float(pred_df["high"].max())
    pred_low   = float(pred_df["low"].min())

    # Direction agreement
    predicted_move_pct = (mean_close - current_price) / current_price
    if direction == "LONG":
        agreement = predicted_move_pct >= KRONOS_MIN_MOVE_PCT
    else:
        agreement = predicted_move_pct <= -KRONOS_MIN_MOVE_PCT

    vetoed     = not agreement
    veto_reason = ""
    if vetoed:
        veto_reason = (f"Kronos predicts {predicted_move_pct*100:.2f}% move "
                       f"({'up' if predicted_move_pct > 0 else 'down'}), "
                       f"need {'up' if direction == 'LONG' else 'down'} "
                       f">= {KRONOS_MIN_MOVE_PCT*100:.1f}%")

    return KronosForecast(
        symbol="",
        direction_agreement=agreement,
        predicted_move_pct=predicted_move_pct,
        predicted_high=pred_high,
        predicted_low=pred_low,
        predicted_close=mean_close,
        confidence=confidence,
        vetoed=vetoed,
        veto_reason=veto_reason
    )


async def run_kronos_filter(symbol: str, candles_15m: List[Candle],
                             direction: str, current_price: float,
                             confluence_result: dict) -> tuple[dict, Optional[KronosForecast]]:
    """
    Async wrapper. Runs Kronos in a thread executor to avoid blocking the event loop.
    Returns (updated_confluence_result, KronosForecast).
    If Kronos vetoes → confluence_result["approved"] is set to False.
    If Kronos approves → TP2/TP3 may be adjusted to predicted high/low.
    """
    loop = asyncio.get_event_loop()
    try:
        forecast = await loop.run_in_executor(
            None, _run_kronos_sync, candles_15m, direction, current_price
        )
        forecast.symbol = symbol
    except Exception as e:
        logger.error(f"Kronos filter error for {symbol}: {e}")
        # Fail open: return original result with empty forecast
        return confluence_result, None

    if forecast.vetoed:
        confluence_result = dict(confluence_result)
        confluence_result["approved"]     = False
        confluence_result["reject_reason"] = f"Kronos veto: {forecast.veto_reason}"
        logger.info(f"Kronos VETOED {symbol} {direction}: {forecast.veto_reason}")
    else:
        # Refine TP levels using Kronos predicted range
        if direction == "LONG" and forecast.predicted_high > current_price:
            confluence_result = dict(confluence_result)
            # Use Kronos predicted high as a guide for TP3 if it's better
            kronos_tp_guide = forecast.predicted_high
            confluence_result["kronos_tp_guide"] = kronos_tp_guide
        elif direction == "SHORT" and forecast.predicted_low < current_price:
            kronos_tp_guide = forecast.predicted_low
            confluence_result["kronos_tp_guide"] = kronos_tp_guide
        logger.info(f"Kronos APPROVED {symbol} {direction} | "
                    f"move={forecast.predicted_move_pct*100:.2f}% | "
                    f"confidence={forecast.confidence:.2f}")

    return confluence_result, forecast
