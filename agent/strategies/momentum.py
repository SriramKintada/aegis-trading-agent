"""
AEGIS - Momentum Strategy
Fast/Slow SMA crossover + RSI + ADX confirmation.
Regime-aware: trades in BULL_TRENDING, BEAR_TRENDING, and trend-like setups.

TUNED:
- Shorter SMA periods (10/30) for more signals
- Lower min_confidence to 0.40
- Added ADX filter for trend strength
- Also fires in MEAN_REVERTING when strong trend develops
"""

import logging
from typing import Optional
import numpy as np
import pandas as pd

from strategies.base_strategy import BaseStrategy, TradeSignal, SignalDirection
from regime.regime_types import Regime, RegimeResult

logger = logging.getLogger(__name__)


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, min_periods=period).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index — measures trend strength."""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # +DM and -DM
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)

    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Smoothed
    atr = tr.ewm(span=period, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(span=period, min_periods=period).mean() / atr.replace(0, 1e-9)
    minus_di = 100 * minus_dm.ewm(span=period, min_periods=period).mean() / atr.replace(0, 1e-9)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
    adx = dx.ewm(span=period, min_periods=period).mean()

    return adx


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


class MomentumStrategy(BaseStrategy):
    """
    Momentum strategy: SMA crossover + RSI + ADX trend strength.

    Entry:
      - LONG: fast_sma > slow_sma AND (RSI > 50 OR bullish crossover) AND ADX > 20
      - SHORT: fast_sma < slow_sma AND (RSI < 50 OR bearish crossover) AND ADX > 20
      - Works in trending regimes AND when ADX signals trend in any regime

    TUNED: shorter periods, lower thresholds for more frequent trades.
    """

    def __init__(
        self,
        fast_period: int = 10,       # Shortened from 20
        slow_period: int = 30,       # Shortened from 50
        rsi_period: int = 14,
        adx_period: int = 14,
        rsi_oversold: float = 40.0,
        rsi_overbought: float = 60.0,
        adx_threshold: float = 20.0,  # Minimum ADX for trend confirmation
        atr_period: int = 14,
        stop_atr_mult: float = 2.0,
        tp_atr_mult: float = 3.0,
        min_confidence: float = 0.40,  # Lowered from 0.55
    ):
        super().__init__("MomentumStrategy")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.rsi_period = rsi_period
        self.adx_period = adx_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.adx_threshold = adx_threshold
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.min_confidence = min_confidence

    @property
    def genes(self) -> dict:
        return {
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "rsi_period": self.rsi_period,
            "rsi_oversold": self.rsi_oversold,
            "rsi_overbought": self.rsi_overbought,
            "stop_atr_mult": self.stop_atr_mult,
            "tp_atr_mult": self.tp_atr_mult,
        }

    @genes.setter
    def genes(self, values: dict):
        self.fast_period = max(5, int(values.get("fast_period", self.fast_period)))
        self.slow_period = max(self.fast_period + 5, int(values.get("slow_period", self.slow_period)))
        self.rsi_period = max(5, int(values.get("rsi_period", self.rsi_period)))
        self.rsi_oversold = float(np.clip(values.get("rsi_oversold", self.rsi_oversold), 20, 50))
        self.rsi_overbought = float(np.clip(values.get("rsi_overbought", self.rsi_overbought), 50, 80))
        self.stop_atr_mult = float(np.clip(values.get("stop_atr_mult", self.stop_atr_mult), 0.5, 5.0))
        self.tp_atr_mult = float(np.clip(values.get("tp_atr_mult", self.tp_atr_mult), 1.0, 8.0))

    def generate_signal(
        self,
        df: pd.DataFrame,
        regime: RegimeResult,
        sentiment_score: float = 0.0,
    ) -> TradeSignal:
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        current_price = float(df["close"].iloc[-1])

        required = self.slow_period + self.rsi_period + 5
        if len(df) < required:
            logger.debug(f"Not enough bars ({len(df)} < {required})")
            return self._neutral_signal(current_price, regime.regime)

        fast = _sma(df["close"], self.fast_period)
        slow = _sma(df["close"], self.slow_period)
        rsi = _rsi(df["close"], self.rsi_period)
        adx = _adx(df, self.adx_period)
        atr = _atr(df, self.atr_period)

        curr_fast = fast.iloc[-1]
        curr_slow = slow.iloc[-1]
        prev_fast = fast.iloc[-2]
        prev_slow = slow.iloc[-2]
        curr_rsi = rsi.iloc[-1]
        curr_adx = adx.iloc[-1]
        curr_atr = atr.iloc[-1]

        if any(np.isnan(v) for v in [curr_fast, curr_slow, curr_rsi, curr_adx, curr_atr]):
            return self._neutral_signal(current_price, regime.regime)

        is_trending = regime.regime.is_trending
        has_adx_trend = curr_adx > self.adx_threshold

        # Allow trading in any regime if ADX confirms a trend
        if not is_trending and not has_adx_trend:
            return self._neutral_signal(current_price, regime.regime)

        direction = SignalDirection.NEUTRAL
        confidence = 0.0

        # ── Crossover signals ──
        bullish_cross = (prev_fast < prev_slow) and (curr_fast > curr_slow)
        bearish_cross = (prev_fast > prev_slow) and (curr_fast < curr_slow)

        # ── Trend-riding signals ──
        bullish_trend = (curr_fast > curr_slow)
        bearish_trend = (curr_fast < curr_slow)

        # SMA spread (strength of trend)
        sma_spread = abs(curr_fast - curr_slow) / curr_slow

        # ── LONG signals ──
        if bullish_cross and curr_rsi < self.rsi_overbought:
            # Fresh crossover — strongest signal
            confidence = 0.60 + 0.20 * regime.confidence
            if has_adx_trend:
                confidence += 0.05
            direction = SignalDirection.LONG

        elif bullish_trend and curr_rsi > 45 and curr_rsi < self.rsi_overbought:
            # Trend riding — moderate signal
            confidence = 0.40 + 0.15 * regime.confidence + min(sma_spread * 10, 0.10)
            if has_adx_trend:
                confidence += 0.05
            direction = SignalDirection.LONG

        # ── SHORT signals ──
        elif bearish_cross and curr_rsi > self.rsi_oversold:
            confidence = 0.55 + 0.20 * regime.confidence
            if has_adx_trend:
                confidence += 0.05
            direction = SignalDirection.SHORT

        elif bearish_trend and curr_rsi < 55 and curr_rsi > self.rsi_oversold:
            confidence = 0.40 + 0.15 * regime.confidence + min(sma_spread * 10, 0.10)
            if has_adx_trend:
                confidence += 0.05
            direction = SignalDirection.SHORT

        # Reduce confidence if regime opposes direction
        if direction == SignalDirection.LONG and regime.regime == Regime.BEAR_TRENDING:
            confidence *= 0.7
        elif direction == SignalDirection.SHORT and regime.regime == Regime.BULL_TRENDING:
            confidence *= 0.7

        # Sentiment boost
        if direction == SignalDirection.LONG and sentiment_score > 0.2:
            confidence = min(confidence + 0.05, 0.95)
        elif direction == SignalDirection.SHORT and sentiment_score < -0.2:
            confidence = min(confidence + 0.05, 0.95)

        if direction == SignalDirection.NEUTRAL or confidence < self.min_confidence:
            return self._neutral_signal(current_price, regime.regime)

        # Stop loss and take profit
        if direction == SignalDirection.LONG:
            stop_loss = current_price - self.stop_atr_mult * curr_atr
            take_profit = current_price + self.tp_atr_mult * curr_atr
        else:
            stop_loss = current_price + self.stop_atr_mult * curr_atr
            take_profit = current_price - self.tp_atr_mult * curr_atr

        return TradeSignal(
            strategy_name=self.name,
            direction=direction,
            confidence=min(confidence, 0.90),
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            regime=regime.regime,
            metadata={
                "fast_sma": curr_fast,
                "slow_sma": curr_slow,
                "rsi": curr_rsi,
                "adx": curr_adx,
                "atr": curr_atr,
                "sma_spread": sma_spread,
                "bullish_cross": bullish_cross,
                "bearish_cross": bearish_cross,
            },
        )

    def get_position_size(self, portfolio_value: float, signal: TradeSignal) -> float:
        base_pct = 0.03
        size = portfolio_value * base_pct * signal.confidence * signal.regime.risk_multiplier
        return min(size, portfolio_value * 0.05)

    def should_exit(
        self,
        entry_price: float,
        current_price: float,
        direction: SignalDirection,
        regime: RegimeResult,
    ) -> tuple[bool, str]:
        if direction == SignalDirection.LONG and regime.regime == Regime.BEAR_TRENDING:
            return True, "trend_reversal"
        if direction == SignalDirection.SHORT and regime.regime == Regime.BULL_TRENDING:
            return True, "trend_reversal"
        return False, ""
