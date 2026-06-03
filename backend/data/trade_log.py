import logging
from sqlalchemy import create_engine, Column, Integer, Float, String, Boolean, Text, func, desc
from sqlalchemy.orm import declarative_base, Session
from collections import defaultdict
from datetime import datetime, timedelta
import json, time

logger = logging.getLogger(__name__)

Base   = declarative_base()
engine = create_engine("sqlite:///smc_bot.db",
                       connect_args={"check_same_thread": False})


class Trade(Base):
    __tablename__    = "trades"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    symbol           = Column(String)
    direction        = Column(String)
    entry_price      = Column(Float)
    exit_price       = Column(Float)
    sl_price         = Column(Float)
    tp1              = Column(Float)
    tp2              = Column(Float)
    tp3              = Column(Float)
    leverage         = Column(Integer)
    margin_usdt      = Column(Float)
    risk_usdt        = Column(Float)
    pnl_usdt         = Column(Float, default=0)
    rr_achieved      = Column(Float, default=0)
    exit_reason      = Column(String)
    confluence_score = Column(Integer)
    kronos_confidence= Column(Float, default=0)
    was_winner       = Column(Boolean, default=False)
    entry_time       = Column(Integer)
    exit_time        = Column(Integer)


class Signal(Base):
    __tablename__    = "signals"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    symbol           = Column(String)
    direction        = Column(String)
    score            = Column(Integer)
    score_breakdown  = Column(Text)
    approved         = Column(Boolean)
    reject_reason    = Column(String)
    kronos_vetoed    = Column(Boolean, default=False)
    kronos_move_pct  = Column(Float, default=0)
    detail           = Column(Text, default="{}")
    timestamp        = Column(Integer)


class AccountSnapshot(Base):
    __tablename__    = "account_snapshots"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    timestamp        = Column(Integer)
    balance_usdt     = Column(Float)
    equity_usdt      = Column(Float)
    open_positions   = Column(Integer)
    daily_pnl        = Column(Float)


Base.metadata.create_all(engine)


class TradeLog:
    def log_trade_open(self, signal) -> int:
        with Session(engine) as s:
            t = Trade(
                symbol=signal.symbol, direction=signal.direction,
                entry_price=signal.entry_price, sl_price=signal.sl_price,
                tp1=signal.tp1, tp2=signal.tp2, tp3=signal.tp3,
                leverage=signal.leverage, margin_usdt=signal.margin_usdt,
                risk_usdt=signal.risk_usdt, confluence_score=signal.score,
                kronos_confidence=signal.kronos.confidence if signal.kronos else 0,
                entry_time=int(time.time())
            )
            s.add(t); s.commit(); s.refresh(t)
            return t.id

    def log_trade_close(self, trade_id: int, exit_price: float,
                        pnl_usdt: float, exit_reason: str, risk_usdt: float = 6.0):
        with Session(engine) as s:
            t = s.get(Trade, trade_id)
            if t:
                t.exit_price   = exit_price
                t.pnl_usdt     = pnl_usdt
                t.exit_reason  = exit_reason
                t.was_winner   = pnl_usdt > 0
                t.exit_time    = int(time.time())
                t.rr_achieved  = pnl_usdt / risk_usdt if risk_usdt else 0
                s.commit()

    def log_signal(self, symbol, direction, score, breakdown, approved,
                    reason="", kronos_vetoed=False, kronos_move_pct=0.0,
                    detail=None):
        with Session(engine) as s:
            s.add(Signal(symbol=symbol, direction=direction, score=score,
                         score_breakdown=json.dumps(breakdown), approved=approved,
                         reject_reason=reason, kronos_vetoed=kronos_vetoed,
                         kronos_move_pct=kronos_move_pct,
                         detail=json.dumps(detail or {}),
                         timestamp=int(time.time())))
            s.commit()
            # Keep only the 30 most recent signals
            s.query(Signal).filter(Signal.id.notin_(
                s.query(Signal.id).order_by(desc(Signal.id)).limit(30).subquery()
            )).delete(synchronize_session=False)
            s.commit()

    def get_recent_trades(self, limit=50):
        with Session(engine) as s:
            return s.query(Trade).order_by(desc(Trade.entry_time)).limit(limit).all()

    def get_win_rate(self, last_n=50) -> float:
        with Session(engine) as s:
            trades = s.query(Trade).filter(
                Trade.exit_time.isnot(None),
                Trade.exit_reason.notin_(["STALE_CLOSE", "RESTART"])
            ).order_by(desc(Trade.exit_time)).limit(last_n).all()
            return sum(1 for t in trades if t.was_winner) / len(trades) if trades else 0.0

    def get_total_pnl(self) -> float:
        with Session(engine) as s:
            return s.query(func.sum(Trade.pnl_usdt)).scalar() or 0.0

    def get_daily_pnl_series(self, days=30) -> list:
        with Session(engine) as s:
            trades  = s.query(Trade).filter(Trade.exit_time.isnot(None)).all()
            daily   = defaultdict(float)
            for t in trades:
                day = datetime.fromtimestamp(t.exit_time).strftime("%Y-%m-%d")
                daily[day] += t.pnl_usdt or 0
            result, balance = [], 300.0
            for i in range(days):
                day      = (datetime.now() - timedelta(days=days-i)).strftime("%Y-%m-%d")
                balance += daily.get(day, 0)
                result.append({"day": day, "equity": round(balance, 2)})
            return result

    def get_recent_signals(self, limit=30):
        with Session(engine) as s:
            return s.query(Signal).order_by(desc(Signal.timestamp)).limit(limit).all()

    def cleanup_orphaned_trades(self):
        """Close any trades still marked as open (stale from previous sessions)."""
        with Session(engine) as s:
            orphans = s.query(Trade).filter(Trade.exit_time.is_(None)).all()
            now = int(time.time())
            for t in orphans:
                t.exit_price = 0
                t.pnl_usdt = 0.0
                t.exit_reason = "RESTART"
                t.was_winner = False
                t.exit_time = now
                t.rr_achieved = 0.0
            s.commit()
            if orphans:
                logger.info(f"Cleaned up {len(orphans)} orphaned trades from previous session")

    def get_kronos_accuracy(self) -> float:
        """What % of Kronos-approved trades were winners."""
        with Session(engine) as s:
            trades = s.query(Trade).filter(
                Trade.kronos_confidence > 0,
                Trade.exit_time.isnot(None),
                Trade.exit_reason.notin_(["STALE_CLOSE", "RESTART"])
            ).all()
            return sum(1 for t in trades if t.was_winner) / len(trades) if trades else 0.0


trade_log = TradeLog()
