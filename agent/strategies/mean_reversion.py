"""
AEGIS - Mean Reversion Strategy
Bollinger Bands + rolling VWAP anchor.
Regime-aware: trades in MEAN_REVERTING and HIGH_VOL_CHOPPY regimes.

FIXED: 
- Use rolling VWAP (not cumulative) to keep anchor relevant
- Relaxed entry conditions: OR logic instead of AND for BB/VWAP
- Lower BB std (1.5) for more entries
- Added RSI overbought/oversold confirmation
- Also trades in HIGH_VOL_CHOPPY when BB width expanding
"""

import logging
import numpy as np
import pandas as pd

from strategies.base_strategy import BaseStrategy, TradeSignal, SignalDirection
from regime.regime_types import Regime, RegimeResult

logger = logging.getLogger(__name__)


def _bollinger_bands(
    series: pd.Series, period: int = 20, n_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (upper, mid, lower) Bollinger Bands."""
    mid = series.rolling(period, min_periods=period).mean()
    std = series.rolling(period, min_periods=period).std()
    upper = mid + n_std * std
    lower = mid - n_std * std
    return upper, mid, lower


def _vwap(df: pd.DataFrame, period: int = 50) -> pd.Series:
    """
    Compute ROLLING VWAP over a window.
    Rolling keeps the anchor relevant to recent price action,
    unlike cumulative VWAP which drifts to historical levels.
    """
    tp = (df["high"] + df["low"] + df["close"]) / 3
    tp_vol = tp * df["volume"]
    cum_tp_vol = tp_vol.rolling(period, min_periods=1).sum()
    cum_vol = df["volume"].rolling(period, min_periods=1).sum()
    return cum_tp_vol / cum_vol.replace(0, 1e-9)


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI indicator."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


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


class MeanReversionStrategy(BaseStrategy):
    """
    Mean reversion strategy using Bollinger Bands, RSI, and rolling VWAP.

    Entry:
      - LONG:  close < lower_band OR (close near lower_band AND RSI < 30)
      - SHORT: close > upper_band OR (close near upper_band AND RSI > 70)

    Active in: MEAN_REVERTING regime (primary), HIGH_VOL_CHOPPY (when BB expanding)
    Target: fade back to midline (mid BB)
    Stop: beyond outer band
    """

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 1.5,       # Lowered from 2.0 for more entries
        atr_period: int = 14,
        rsi_period: int = 14,
        stop_atr_mult: float = 1.5,
        band_proximity_pct: float = 0.003,  # Within 0.3% of band triggers
        min_confidence: float = 0.40,        # Lowered from 0.50
        vwap_period: int = 50,
    ):
        super().__init__("MeanReversionStrategy")
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.atr_period = atr_period
        self.rsi_period = rsi_period
        self.stop_atr_mult = stop_atr_mult
        self.band_proximity_pct = band_proximity_pct
        self.min_confidence = min_confidence
        self.vwap_period = vwap_period

    @property
    def genes(self) -> dict:
        return {
            "bb_period": self.bb_period,
            "bb_std": self.bb_std,
            "stop_atr_mult": self.stop_atr_mult,
            "band_proximity_pct": self.band_proximity_pct,
        }

    @genes.setter
    def genes(self, values: dict):
        self.bb_period = max(10, int(values.get("bb_period", self.bb_period)))
        self.bb_std = float(np.clip(values.get("bb_std", self.bb_std), 1.0, 4.0))
        self.stop_atr_mult = float(np.clip(values.get("stop_atr_mult", self.stop_atr_mult), 0.5, 4.0))
        self.band_proximity_pct = float(np.clip(values.get("band_proximity_pct", self.band_proximity_pct), 0.0, 0.02))

    def generate_signal(
        self,
        df: pd.DataFrame,
        regime: RegimeResult,
        sentiment_score: float = 0.0,
    ) -> TradeSignal:
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        current_price = float(df["close"].iloc[-1])

        # Trade in MEAN_REVERTING (primary) and HIGH_VOL_CHOPPY (secondary)
        is_mr = regime.regime.is_mean_reverting
        is_choppy = regime.regime == Regime.HIGH_VOL_CHOPPY

        if not (is_mr or is_choppy):
            return self._neutral_signal(current_price, regime.regime)

        required = max(self.bb_period, self.atr_period, self.rsi_period, self.vwap_period) + 5
        if len(df) < required:
            return self._neutral_signal(current_price, regime.regime)

        upper, mid, lower = _bollinger_bands(df["close"], self.bb_period, self.bb_std)
        vwap = _vwap(df, self.vwap_period)
        rsi = _rsi(df["close"], self.rsi_period)
        atr = _atr(df, self.atr_period)

        curr_upper = float(upper.iloc[-1])
        curr_mid = float(mid.iloc[-1])
        curr_lower = float(lower.iloc[-1])
        curr_vwap = float(vwap.iloc[-1])
        curr_rsi = float(rsi.iloc[-1])
        curr_atr = float(atr.iloc[-1])

        if any(np.isnan(v) for v in [curr_upper, curr_mid, curr_lower, curr_vwap, curr_rsi, curr_atr]):
            return self._neutral_signal(current_price, regime.regime)

        # Bandwidth: how wide are the bands relative to mid
        bandwidth = (curr_upper - curr_lower) / curr_mid if curr_mid > 0 else 0

        # For HIGH_VOL_CHOPPY, only trade when BB is wide enough (expanding)
        if is_choppy:
            # Check if bandwidth is above median (expanding)
            hist_bw = ((upper - lower) / mid).dropna()
            if len(hist_bw) < 10:
                return self._neutral_signal(current_price, regime.regime)
            median_bw = float(hist_bw.median())
            if bandwidth < median_bw:
                return self._neutral_signal(current_price, regime.regime)

        direction = SignalDirection.NEUTRAL
        confidence = 0.0
        stop_loss = current_price
        take_profit = current_price

        # ── LONG conditions (price near/below lower band) ──
        pct_below_lower = (curr_lower - current_price) / curr_lower if curr_lower > 0 else 0
        near_lower = current_price <= curr_lower * (1 + self.band_proximity_pct)
        rsi_oversold = curr_rsi < 35

        # Primary: price at or below lower band
        # Secondary: price near lower band AND RSI confirms oversold
        if near_lower or (pct_below_lower > -0.005 and rsi_oversold):
            if current_price < curr_lower:
                # Below band — strong signal
                confidence = 0.55 + min(pct_below_lower * 15, 0.25) + 0.10 * regime.confidence
            elif near_lower and rsi_oversold:
                # Near band + RSI confirms
                confidence = 0.45 + 0.15 * regime.confidence
                # Bonus if also below VWAP
                if current_price < curr_vwap:
                    confidence += 0.05
            elif near_lower:
                # Just near the band
                confidence = 0.40 + 0.10 * regime.confidence

            if confidence > 0:
                direction = SignalDirection.LONG

        # ── SHORT conditions (price near/above upper band) ──
        pct_above_upper = (current_price - curr_upper) / curr_upper if curr_upper > 0 else 0
        near_upper = current_price >= curr_upper * (1 - self.band_proximity_pct)
        rsi_overbought = curr_rsi > 65

        if direction == SignalDirection.NEUTRAL:
            if near_upper or (pct_above_upper > -0.005 and rsi_overbought):
                if current_price > curr_upper:
                    confidence = 0.55 + min(pct_above_upper * 15, 0.25) + 0.10 * regime.confidence
                elif near_upper and rsi_overbought:
                    confidence = 0.45 + 0.15 * regime.confidence
                    if current_price > curr_vwap:
                        confidence += 0.05
                elif near_upper:
                    confidence = 0.40 + 0.10 * regime.confidence

                if confidence > 0:
                    direction = SignalDirection.SHORT

        # Reduce confidence in choppy regime (higher risk)
        if is_choppy:
            confidence *= 0.85

        # Sentiment adjustment
        if direction == SignalDirection.LONG and sentiment_score < -0.3:
            confidence *= 0.8
        elif direction == SignalDirection.SHORT and sentiment_score > 0.3:
            confidence *= 0.8

        if direction == SignalDirection.NEUTRAL or confidence < self.min_confidence:
            return self._neutral_signal(current_price, regime.regime)

        # Stop loss and take profit
        if direction == SignalDirection.LONG:
            stop_loss = current_price - self.stop_atr_mult * curr_atr
            take_profit = curr_mid  # target midline
        else:
            stop_loss = current_price + self.stop_atr_mult * curr_atr
            take_profit = curr_mid  # target midline

        # Ensure minimum R:R and that TP is on the right side of entry
        if direction == SignalDirection.LONG:
            risk = current_price - stop_loss
            reward = take_profit - current_price
        else:
            risk = stop_loss - current_price
            reward = current_price - take_profit

        # If TP is wrong side (e.g. price already past midline), skip
        if reward <= 0:
            return self._neutral_signal(current_price, regime.regime)

        # Minimum R:R of 0.6 (we accept near-band entries with modest reward)
        if risk <= 0 or reward / risk < 0.6:
            return self._neutral_signal(current_price, regime.regime)

        return TradeSignal(
            strategy_name=self.name,
            direction=direction,
            confidence=min(confidence, 0.90),
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            regime=regime.regime,
            metadata={
                "bb_upper": curr_upper,
                "bb_mid": curr_mid,
                "bb_lower": curr_lower,
                "vwap": curr_vwap,
                "rsi": curr_rsi,
                "atr": curr_atr,
                "bandwidth": bandwidth,
            },
        )

    def get_position_size(self, portfolio_value: float, signal: TradeSignal) -> float:
        base_pct = 0.025
        size = portfolio_value * base_pct * signal.confidence * signal.regime.risk_multiplier
        return min(size, portfolio_value * 0.05)

    def should_exit(
        self,
        entry_price: float,
        current_price: float,
        direction: SignalDirection,
        regime: RegimeResult,
    ) -> tuple[bool, str]:
        if not (regime.regime.is_mean_reverting or regime.regime == Regime.HIGH_VOL_CHOPPY):
            return True, "regime_change"
        return False, ""
