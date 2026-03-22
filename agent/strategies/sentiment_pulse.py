"""
AEGIS - Sentiment Pulse Strategy
Trades on large, rapid sentiment shifts.
Works in any regime but is higher risk / higher reward.

TUNED:
- Lower shift threshold (0.20)
- Synthetic sentiment proxy for backtesting (momentum + volume spikes)
"""

import logging
from collections import deque
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from strategies.base_strategy import BaseStrategy, TradeSignal, SignalDirection
from regime.regime_types import Regime, RegimeResult

logger = logging.getLogger(__name__)


def _compute_synthetic_sentiment(df: pd.DataFrame) -> float:
    """
    Generate a synthetic sentiment proxy from price momentum + volume spikes.
    Used for backtesting when live FinBERT sentiment is not available.

    More selective than raw momentum — requires BOTH price movement AND volume.
    Without volume confirmation, returns near-zero (neutral).

    Returns float in [-1, 1]
    """
    if len(df) < 25:
        return 0.0

    close = df["close"] if "close" in df.columns else df["Close"]
    volume = df["volume"] if "volume" in df.columns else df["Volume"]

    # 1. Short-term momentum (5-bar return)
    ret_5 = float((close.iloc[-1] / close.iloc[-6] - 1) if len(close) > 5 else 0)

    # 2. Volume spike (relative to 20-bar average)
    vol_mean = float(volume.iloc[-21:-1].mean()) if len(volume) > 20 else float(volume.mean())
    curr_vol = float(volume.iloc[-1])
    vol_ratio = curr_vol / vol_mean if vol_mean > 0 else 1.0

    # Only generate signal if volume is elevated (>1.3x average)
    # Without volume, price moves are noise
    if vol_ratio < 1.3:
        return 0.0

    # Normalize momentum: ±5% move = ±1.0
    momentum_score = np.clip(ret_5 / 0.05, -1, 1)

    # Only care if momentum is significant (>1% in 5 bars)
    if abs(ret_5) < 0.01:
        return 0.0

    # Volume amplifier (capped)
    vol_boost = min(vol_ratio / 3.0, 1.0)

    # 3. Price acceleration (change in momentum direction)
    if len(close) > 12:
        prev_ret = float(close.iloc[-6] / close.iloc[-11] - 1)
        acceleration = ret_5 - prev_ret
        # Acceleration in same direction as momentum adds to signal
        accel_score = np.clip(acceleration / 0.03, -0.3, 0.3)
    else:
        accel_score = 0.0

    raw_sentiment = momentum_score * 0.7 + accel_score * 0.3
    raw_sentiment *= (0.5 + vol_boost * 0.5)  # volume-weighted

    return float(np.clip(raw_sentiment, -1, 1))


class SentimentPulseStrategy(BaseStrategy):
    """
    Trades on rapid, large sentiment score shifts.
    Uses synthetic sentiment proxy when live FinBERT is unavailable.
    """

    def __init__(
        self,
        shift_threshold: float = 0.20,      # Lowered from 0.30
        window_size: int = 6,
        min_abs_score: float = 0.15,        # Lowered from 0.20
        stop_pct: float = 0.015,
        take_profit_pct: float = 0.035,
        min_confidence: float = 0.40,       # Lowered from 0.45
        use_synthetic: bool = True,          # Use synthetic sentiment in backtest
    ):
        super().__init__("SentimentPulseStrategy")
        self.shift_threshold = shift_threshold
        self.window_size = window_size
        self.min_abs_score = min_abs_score
        self.stop_pct = stop_pct
        self.take_profit_pct = take_profit_pct
        self.min_confidence = min_confidence
        self.use_synthetic = use_synthetic

        self._sentiment_history: deque = deque(maxlen=window_size)

    @property
    def genes(self) -> dict:
        return {
            "shift_threshold": self.shift_threshold,
            "min_abs_score": self.min_abs_score,
            "stop_pct": self.stop_pct,
            "take_profit_pct": self.take_profit_pct,
        }

    @genes.setter
    def genes(self, values: dict):
        self.shift_threshold = float(np.clip(values.get("shift_threshold", self.shift_threshold), 0.1, 0.8))
        self.min_abs_score = float(np.clip(values.get("min_abs_score", self.min_abs_score), 0.05, 0.5))
        self.stop_pct = float(np.clip(values.get("stop_pct", self.stop_pct), 0.005, 0.05))
        self.take_profit_pct = float(np.clip(values.get("take_profit_pct", self.take_profit_pct), 0.01, 0.1))

    def push_sentiment(self, score: float, timestamp: Optional[datetime] = None):
        ts = timestamp or datetime.utcnow()
        self._sentiment_history.append((ts, float(score)))

    def _compute_shift(self) -> tuple[float, float]:
        if len(self._sentiment_history) < 2:
            return 0.0, 0.0
        oldest_score = self._sentiment_history[0][1]
        newest_score = self._sentiment_history[-1][1]
        return newest_score, newest_score - oldest_score

    def generate_signal(
        self,
        df: pd.DataFrame,
        regime: RegimeResult,
        sentiment_score: float = 0.0,
    ) -> TradeSignal:
        df_local = df.copy()
        df_local.columns = [c.lower() for c in df_local.columns]
        current_price = float(df_local["close"].iloc[-1])

        # For backtesting: compute synthetic sentiment for the last `window_size` bars
        # This is stateless — avoids deque state leakage across the backtest
        if self.use_synthetic:
            # Rebuild sentiment history from the last window_size bars
            self._sentiment_history.clear()
            n = len(df_local)
            for j in range(max(0, n - self.window_size), n):
                sub = df_local.iloc[0:j + 1]
                score = _compute_synthetic_sentiment(sub)
                ts = df_local.index[j] if hasattr(df_local.index[j], 'timestamp') else datetime.utcnow()
                self._sentiment_history.append((ts, score))
            # Current sentiment is already the last one pushed
        else:
            # Live mode: push live sentiment
            ts = df_local.index[-1] if hasattr(df_local.index[-1], 'timestamp') else datetime.utcnow()
            self.push_sentiment(sentiment_score, ts)

        current_score, shift = self._compute_shift()

        if len(self._sentiment_history) < max(2, self.window_size // 2):
            return self._neutral_signal(current_price, regime.regime)

        direction = SignalDirection.NEUTRAL
        confidence = 0.0

        # Strong positive shift -> LONG
        if shift >= self.shift_threshold and current_score >= self.min_abs_score:
            direction = SignalDirection.LONG
            confidence = min(0.40 + abs(shift) * 0.8 + 0.1 * regime.confidence, 0.85)

        # Strong negative shift -> SHORT
        elif shift <= -self.shift_threshold and current_score <= -self.min_abs_score:
            direction = SignalDirection.SHORT
            confidence = min(0.40 + abs(shift) * 0.8 + 0.1 * regime.confidence, 0.85)

        # Regime adjustment
        if direction == SignalDirection.LONG and regime.regime == Regime.BEAR_TRENDING:
            confidence *= 0.7
        elif direction == SignalDirection.SHORT and regime.regime == Regime.BULL_TRENDING:
            confidence *= 0.7

        if direction == SignalDirection.NEUTRAL or confidence < self.min_confidence:
            return self._neutral_signal(current_price, regime.regime)

        if direction == SignalDirection.LONG:
            stop_loss = current_price * (1 - self.stop_pct)
            take_profit = current_price * (1 + self.take_profit_pct)
        else:
            stop_loss = current_price * (1 + self.stop_pct)
            take_profit = current_price * (1 - self.take_profit_pct)

        return TradeSignal(
            strategy_name=self.name,
            direction=direction,
            confidence=min(confidence, 0.85),
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            regime=regime.regime,
            metadata={
                "current_sentiment": current_score,
                "sentiment_shift": shift,
                "history_len": len(self._sentiment_history),
                "synthetic": self.use_synthetic,
            },
        )

    def get_position_size(self, portfolio_value: float, signal: TradeSignal) -> float:
        base_pct = 0.015
        size = portfolio_value * base_pct * signal.confidence * signal.regime.risk_multiplier
        return min(size, portfolio_value * 0.03)

    def should_exit(
        self,
        entry_price: float,
        current_price: float,
        direction: SignalDirection,
        regime: RegimeResult,
    ) -> tuple[bool, str]:
        if len(self._sentiment_history) >= 2:
            _, shift = self._compute_shift()
            current_score = self._sentiment_history[-1][1]
            if direction == SignalDirection.LONG and (shift < -0.2 or current_score < -0.1):
                return True, "sentiment_reversal"
            if direction == SignalDirection.SHORT and (shift > 0.2 or current_score > 0.1):
                return True, "sentiment_reversal"
        return False, ""
