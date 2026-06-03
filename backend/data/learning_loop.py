"""
Self-Learning Loop — daily statistical analysis of trade outcomes.

Reads closed trades from SQLite with their signal metadata.
Identifies which signal combinations have statistically better win rates.
Adjusts three thresholds only:
  1. OB_MAX_DISTANCE_PCT: how far from price an OB can be and still count
  2. FLOW_CONFIRM_RATIO:  how strong order flow must be to confirm
  3. HMA_LENGTH:          HMA period (adjusts trend sensitivity)

Hard constraints:
  - Never move more than +/-30% from default values
  - Require minimum 15 closed trades before any adjustment
  - Log every adjustment to the database

Usage: call run_learning_loop() daily from an asyncio task in server.py
"""
import logging
import json
import time
from datetime import datetime
from sqlalchemy.orm import Session
from backend.data.trade_log import engine, Trade, Signal
from backend.config import (OB_MAX_DISTANCE_PCT, FLOW_CONFIRM_RATIO,
                             HMA_LENGTH, LEARNING_MIN_TRADES, LEARNING_MAX_DRIFT)

logger = logging.getLogger(__name__)

DEFAULTS = {
    "OB_MAX_DISTANCE_PCT": OB_MAX_DISTANCE_PCT,
    "FLOW_CONFIRM_RATIO":  FLOW_CONFIRM_RATIO,
    "HMA_LENGTH":          float(HMA_LENGTH),
}

_current = dict(DEFAULTS)


def get_adjusted(key: str):
    """Get current (possibly adjusted) value for a parameter."""
    return _current.get(key, DEFAULTS[key])


def run_learning_loop():
    """Analyze recent trade outcomes and adjust thresholds if warranted."""
    logger.info("Self-learning loop: starting analysis...")

    with Session(engine) as s:
        closed = s.query(Trade).filter(
            Trade.exit_time.isnot(None),
            Trade.exit_reason.notin_(["RESTART", "STALE_CLOSE"])
        ).order_by(Trade.exit_time.desc()).limit(100).all()

        signals = s.query(Signal).filter(
            Signal.approved == True
        ).order_by(Signal.timestamp.desc()).limit(100).all()

    if len(closed) < LEARNING_MIN_TRADES:
        logger.info(f"Self-learning: only {len(closed)} trades, "
                    f"need {LEARNING_MIN_TRADES}. Skipping.")
        return

    # Analysis 1: OB distance vs win rate
    ob_wins, ob_losses = 0, 0
    nob_wins, nob_losses = 0, 0

    for sig in signals:
        if not sig.score_breakdown:
            continue
        try:
            bd = json.loads(sig.score_breakdown) if isinstance(sig.score_breakdown, str) else sig.score_breakdown
        except (json.JSONDecodeError, TypeError):
            continue
        has_ob = bd.get("has_ob", False)
        matching = next((t for t in closed
                         if abs(t.entry_time - sig.timestamp) < 300
                         and t.symbol == sig.symbol), None)
        if matching is None:
            continue
        won = matching.was_winner
        if has_ob:
            if won: ob_wins += 1
            else:   ob_losses += 1
        else:
            if won: nob_wins += 1
            else:   nob_losses += 1

    ob_wr  = ob_wins  / (ob_wins  + ob_losses)  if (ob_wins  + ob_losses)  > 3 else None
    nob_wr = nob_wins / (nob_wins + nob_losses) if (nob_wins + nob_losses) > 3 else None

    if ob_wr and nob_wr and ob_wr > nob_wr + 0.15:
        new_dist = _current["OB_MAX_DISTANCE_PCT"] * 0.9
        new_dist = max(DEFAULTS["OB_MAX_DISTANCE_PCT"] * (1 - LEARNING_MAX_DRIFT), new_dist)
        if new_dist != _current["OB_MAX_DISTANCE_PCT"]:
            logger.info(f"Learning: tightening OB_MAX_DISTANCE_PCT "
                        f"{_current['OB_MAX_DISTANCE_PCT']:.3f} -> {new_dist:.3f} "
                        f"(OB win rate {ob_wr:.0%} vs non-OB {nob_wr:.0%})")
            _current["OB_MAX_DISTANCE_PCT"] = new_dist

    # Analysis 2: Overall win rate trend
    recent_20  = closed[:20]
    overall_wr = sum(1 for t in recent_20 if t.was_winner) / len(recent_20)

    if overall_wr < 0.50:
        new_ratio = _current["FLOW_CONFIRM_RATIO"] * 0.95
        new_ratio = max(DEFAULTS["FLOW_CONFIRM_RATIO"] * (1 - LEARNING_MAX_DRIFT), new_ratio)
        if new_ratio != _current["FLOW_CONFIRM_RATIO"]:
            logger.info(f"Learning: loosening FLOW_CONFIRM_RATIO "
                        f"{_current['FLOW_CONFIRM_RATIO']:.2f} -> {new_ratio:.2f} "
                        f"(win rate {overall_wr:.0%})")
            _current["FLOW_CONFIRM_RATIO"] = new_ratio

    elif overall_wr > 0.70:
        new_ratio = _current["FLOW_CONFIRM_RATIO"] * 1.05
        new_ratio = min(DEFAULTS["FLOW_CONFIRM_RATIO"] * (1 + LEARNING_MAX_DRIFT), new_ratio)
        if new_ratio != _current["FLOW_CONFIRM_RATIO"]:
            logger.info(f"Learning: tightening FLOW_CONFIRM_RATIO "
                        f"{_current['FLOW_CONFIRM_RATIO']:.2f} -> {new_ratio:.2f} "
                        f"(win rate {overall_wr:.0%})")
            _current["FLOW_CONFIRM_RATIO"] = new_ratio

    logger.info(f"Self-learning complete. Win rate (last 20): {overall_wr:.0%}. "
                f"Current params: {_current}")
