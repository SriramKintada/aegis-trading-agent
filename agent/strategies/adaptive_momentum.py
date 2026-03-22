"""
AEGIS — Adaptive Momentum Strategy
Inspired by Jake Nesler's Claude Prophet approach.

Key differences from basic MomentumStrategy:
  1. Shorter EMAs (5/15) instead of SMAs (20/50) — faster reaction
  2. Trades BOTH directions aggressively (not just with regime)
  3. Volatility breakout: enter when price moves > 1.5 ATR in single bar
  4. Multi-timeframe: hourly signal + 4h trend alignment
  5. Limit order simulation: enter at better price than open (open - 0.05% for longs)
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from strategies.base_strategy import BaseStrategy, TradeSignal, SignalDirection
from regime.regime_types import Regime, RegimeResult

logger = logging.getLogger(__name__)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, min_periods=period).mean()


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


class AdaptiveMomentumStrategy(BaseStrategy):
    """
    Adaptive momentum with EMA crossovers, volatility breakouts,
    and multi-timeframe confirmation.

    Generates signals more aggressively than MomentumStrategy:
    - Fast 5/15 EMA crossovers
    - Volatility breakout component (> 1.5 ATR single-bar move)
    - 4h trend alignment via 60-bar EMA (4 * 15 bars ≈ 4h on hourly data)
    - Limit order simulation for better fills in backtest
    """

    def __init__(
        self,
        fast_ema: int = 5,
        slow_ema: int = 15,
        trend_ema: int = 60,           # 4h trend on hourly data
        atr_period: int = 14,
        atr_breakout_mult: float = 1.5, # Breakout threshold
        rsi_period: int = 14,
        stop_atr_mult: float = 2.5,
        tp_atr_mult: float = 4.0,
        limit_fill_improvement: float = 0.0005,  # 0.05% better fill
        min_confidence: float = 0.45,
    ):
        super().__init__("AdaptiveMomentumStrategy")
        self.fast_ema = fast_ema
        self.slow_ema = slow_ema
        self.trend_ema = trend_ema
        self.atr_period = atr_period
        self.atr_breakout_mult = atr_breakout_mult
        self.rsi_period = rsi_period
        self.stop_atr_mult = stop_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.limit_fill_improvement = limit_fill_improvement
        self.min_confidence = min_confidence

    @property
    def genes(self) -> dict:
        return {
            "fast_ema": self.fast_ema,
            "slow_ema": self.slow_ema,
            "trend_ema": self.trend_ema,
            "atr_breakout_mult": self.atr_breakout_mult,
            "stop_atr_mult": self.stop_atr_mult,
            "tp_atr_mult": self.tp_atr_mult,
            "limit_fill_improvement": self.limit_fill_improvement,
        }

    @genes.setter
    def genes(self, values: dict):
        self.fast_ema = max(3, int(values.get("fast_ema", self.fast_ema)))
        self.slow_ema = max(
            self.fast_ema + 3,
            int(values.get("slow_ema", self.slow_ema))
        )
        self.trend_ema = max(30, int(values.get("trend_ema", self.trend_ema)))
        self.atr_breakout_mult = float(np.clip(
            values.get("atr_breakout_mult", self.atr_breakout_mult), 1.0, 3.0))
        self.stop_atr_mult = float(np.clip(
            values.get("stop_atr_mult", self.stop_atr_mult), 1.0, 5.0))
        self.tp_atr_mult = float(np.clip(
            values.get("tp_atr_mult", self.tp_atr_mult), 1.5, 8.0))
        self.limit_fill_improvement = float(np.clip(
            values.get("limit_fill_improvement", self.limit_fill_improvement),
            0.0, 0.002))

    def generate_signal(
        self,
        df: pd.DataFrame,
        regime: RegimeResult,
        sentiment_score: float = 0.0,
    ) -> TradeSignal:
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        current_price = float(df["close"].iloc[-1])

        required = max(self.trend_ema, self.slow_ema) + self.atr_period + 5
        if len(df) < required:
            return self._neutral_signal(current_price, regime.regime)

        # Calculate indicators
        close = df["close"]
        fast = _ema(close, self.fast_ema)
        slow = _ema(close, self.slow_ema)
        trend = _ema(close, self.trend_ema)
        atr = _atr(df, self.atr_period)
        rsi = _rsi(close, self.rsi_period)

        curr_fast = fast.iloc[-1]
        curr_slow = slow.iloc[-1]
        prev_fast = fast.iloc[-2]
        prev_slow = slow.iloc[-2]
        curr_trend = trend.iloc[-1]
        curr_atr = atr.iloc[-1]
        curr_rsi = rsi.iloc[-1]

        if any(np.isnan(v) for v in [curr_fast, curr_slow, curr_trend, curr_atr, curr_rsi]):
            return self._neutral_signal(current_price, regime.regime)

        # ── Signal Components ──

        # 1. EMA crossover (fast/slow)
        bullish_cross = (prev_fast <= prev_slow) and (curr_fast > curr_slow)
        bearish_cross = (prev_fast >= prev_slow) and (curr_fast < curr_slow)
        bullish_trend_state = curr_fast > curr_slow
        bearish_trend_state = curr_fast < curr_slow

        # 2. Volatility breakout
        bar_range = float(df["close"].iloc[-1] - df["open"].iloc[-1])
        breakout_up = bar_range > self.atr_breakout_mult * curr_atr
        breakout_down = bar_range < -self.atr_breakout_mult * curr_atr

        # 3. Multi-timeframe: 4h trend alignment
        above_trend = current_price > curr_trend
        below_trend = current_price < curr_trend

        # ── Combine into signal ──
        direction = SignalDirection.NEUTRAL
        confidence = 0.0
        signal_type = ""

        # Priority 1: EMA crossover + trend alignment
        if bullish_cross and above_trend:
            direction = SignalDirection.LONG
            confidence = 0.65 + 0.10 * regime.confidence
            signal_type = "ema_cross_aligned"
        elif bearish_cross and below_trend:
            direction = SignalDirection.SHORT
            confidence = 0.65 + 0.10 * regime.confidence
            signal_type = "ema_cross_aligned"

        # Priority 2: Volatility breakout (strong single-bar move)
        elif breakout_up:
            direction = SignalDirection.LONG
            breakout_strength = abs(bar_range) / (self.atr_breakout_mult * curr_atr)
            confidence = 0.50 + min(breakout_strength * 0.15, 0.25)
            signal_type = "volatility_breakout"
        elif breakout_down:
            direction = SignalDirection.SHORT
            breakout_strength = abs(bar_range) / (self.atr_breakout_mult * curr_atr)
            confidence = 0.50 + min(breakout_strength * 0.15, 0.25)
            signal_type = "volatility_breakout"

        # Priority 3: EMA crossover without trend alignment (weaker)
        elif bullish_cross:
            direction = SignalDirection.LONG
            confidence = 0.45 + 0.10 * regime.confidence
            signal_type = "ema_cross"
        elif bearish_cross:
            direction = SignalDirection.SHORT
            confidence = 0.45 + 0.10 * regime.confidence
            signal_type = "ema_cross"

        # Priority 4: Strong trend riding + RSI confirmation
        elif bullish_trend_state and above_trend and 45 < curr_rsi < 70:
            ema_spread = (curr_fast - curr_slow) / curr_slow
            if ema_spread > 0.002:  # Meaningful spread
                direction = SignalDirection.LONG
                confidence = 0.40 + min(ema_spread * 20, 0.20) + 0.05 * regime.confidence
                signal_type = "trend_ride"
        elif bearish_trend_state and below_trend and 30 < curr_rsi < 55:
            ema_spread = (curr_slow - curr_fast) / curr_slow
            if ema_spread > 0.002:
                direction = SignalDirection.SHORT
                confidence = 0.40 + min(ema_spread * 20, 0.20) + 0.05 * regime.confidence
                signal_type = "trend_ride"

        # RSI extreme filter: don't go long above 80 or short below 20
        if direction == SignalDirection.LONG and curr_rsi > 80:
            confidence *= 0.5
        elif direction == SignalDirection.SHORT and curr_rsi < 20:
            confidence *= 0.5

        # Sentiment boost
        if direction == SignalDirection.LONG and sentiment_score > 0.2:
            confidence = min(confidence + 0.05, 0.95)
        elif direction == SignalDirection.SHORT and sentiment_score < -0.2:
            confidence = min(confidence + 0.05, 0.95)

        if direction == SignalDirection.NEUTRAL or confidence < self.min_confidence:
            return self._neutral_signal(current_price, regime.regime)

        # Apply limit order improvement for entry
        if direction == SignalDirection.LONG:
            entry_price = current_price * (1 - self.limit_fill_improvement)
            stop_loss = entry_price - self.stop_atr_mult * curr_atr
            take_profit = entry_price + self.tp_atr_mult * curr_atr
        else:
            entry_price = current_price * (1 + self.limit_fill_improvement)
            stop_loss = entry_price + self.stop_atr_mult * curr_atr
            take_profit = entry_price - self.tp_atr_mult * curr_atr

        return TradeSignal(
            strategy_name=self.name,
            direction=direction,
            confidence=min(confidence, 0.90),
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            regime=regime.regime,
            metadata={
                "signal_type": signal_type,
                "fast_ema": curr_fast,
                "slow_ema": curr_slow,
                "trend_ema": curr_trend,
                "atr": curr_atr,
                "rsi": curr_rsi,
                "bar_range_atr": abs(bar_range) / curr_atr if curr_atr > 0 else 0,
                "above_trend": above_trend,
                "limit_fill_improvement": self.limit_fill_improvement,
            },
        )

    def get_position_size(self, portfolio_value: float, signal: TradeSignal) -> float:
        base_pct = 0.035
        size = portfolio_value * base_pct * signal.confidence
        return min(size, portfolio_value * 0.06)

    def should_exit(
        self,
        entry_price: float,
        current_price: float,
        direction: SignalDirection,
        regime: RegimeResult,
    ) -> tuple[bool, str]:
        # Exit if regime changes dramatically against position
        if direction == SignalDirection.LONG and regime.regime == Regime.BEAR_TRENDING:
            if regime.confidence > 0.7:
                return True, "strong_regime_reversal"
        if direction == SignalDirection.SHORT and regime.regime == Regime.BULL_TRENDING:
            if regime.confidence > 0.7:
                return True, "strong_regime_reversal"
        return False, ""
