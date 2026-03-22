"""
AEGIS — Stop Loss Manager
ATR-based dynamic stops, regime-adaptive, with trailing stop for winners.
"""

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from regime.regime_types import Regime, RegimeResult
from strategies.base_strategy import SignalDirection

logger = logging.getLogger(__name__)


@dataclass
class StopState:
    """Live state of stops for an open position."""
    entry_price: float
    direction: SignalDirection
    initial_stop: float
    current_stop: float
    take_profit: float
    trailing_active: bool = False
    highest_favorable: float = 0.0   # Peak favorable price seen since entry
    atr_at_entry: float = 0.0


class StopManager:
    """
    Manages stop losses and take-profit levels per open position.

    Features:
      - ATR-based initial stop (regime-adaptive multiplier)
      - Trailing stop: activates once position gains > trail_activation_pct
      - Hard stop at initial_stop (never moves against position)
    """

    # ATR multipliers by regime
    REGIME_ATR_MULT: dict[Regime, float] = {
        Regime.BULL_TRENDING: 2.5,
        Regime.BEAR_TRENDING: 2.5,
        Regime.HIGH_VOL_CHOPPY: 1.5,   # Tighter in volatile regimes
        Regime.MEAN_REVERTING: 2.0,
    }

    def __init__(
        self,
        atr_period: int = 14,
        trail_activation_pct: float = 0.01,   # Trail kicks in at 1% gain
        trail_step_atr_mult: float = 1.0,     # Trail moves by 1× ATR
    ):
        self.atr_period = atr_period
        self.trail_activation_pct = trail_activation_pct
        self.trail_step_atr_mult = trail_step_atr_mult

    def _compute_atr(self, df: pd.DataFrame) -> float:
        """Compute current ATR from OHLCV data."""
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.ewm(com=self.atr_period - 1, min_periods=self.atr_period).mean()
        val = float(atr.iloc[-1])
        return val if not np.isnan(val) else float(df["close"].std() * 0.01)

    def create_stop(
        self,
        entry_price: float,
        direction: SignalDirection,
        regime: Regime,
        df: pd.DataFrame,
        override_take_profit: Optional[float] = None,
    ) -> StopState:
        """
        Create initial stop state for a new position.

        Args:
            entry_price: Trade entry price
            direction: LONG or SHORT
            regime: Current market regime
            df: Recent OHLCV data for ATR calculation
            override_take_profit: Use specific TP if provided by strategy

        Returns:
            StopState tracking this position's levels
        """
        atr = self._compute_atr(df)
        mult = self.REGIME_ATR_MULT.get(regime, 2.0)

        if direction == SignalDirection.LONG:
            stop = entry_price - mult * atr
            # Default TP: 3× ATR above entry
            tp = override_take_profit if override_take_profit else entry_price + 3 * mult * atr
        else:
            stop = entry_price + mult * atr
            tp = override_take_profit if override_take_profit else entry_price - 3 * mult * atr

        state = StopState(
            entry_price=entry_price,
            direction=direction,
            initial_stop=stop,
            current_stop=stop,
            take_profit=tp,
            highest_favorable=entry_price,
            atr_at_entry=atr,
        )
        logger.debug(
            f"Stop created | {direction.value} @ {entry_price:.4f} | "
            f"stop={stop:.4f} | tp={tp:.4f} | ATR={atr:.4f} | mult={mult}"
        )
        return state

    def update(
        self,
        state: StopState,
        current_price: float,
        df: pd.DataFrame,
        regime: RegimeResult,
    ) -> StopState:
        """
        Update stop state with current price.
        May activate or move trailing stop if position is a winner.
        """
        if state.direction == SignalDirection.LONG:
            # Update peak favorable price
            if current_price > state.highest_favorable:
                state.highest_favorable = current_price

            # Check if trailing should activate
            gain_pct = (state.highest_favorable - state.entry_price) / state.entry_price
            if gain_pct >= self.trail_activation_pct:
                state.trailing_active = True
                # Trail: current_stop = peak - 1× ATR
                atr_now = self._compute_atr(df)
                trail_stop = state.highest_favorable - self.trail_step_atr_mult * atr_now
                # Only move stop UP (never down against position)
                if trail_stop > state.current_stop:
                    state.current_stop = trail_stop
                    logger.debug(
                        f"Trailing stop moved UP: {state.current_stop:.4f} "
                        f"(peak={state.highest_favorable:.4f})"
                    )

        elif state.direction == SignalDirection.SHORT:
            if current_price < state.highest_favorable or state.highest_favorable == state.entry_price:
                state.highest_favorable = current_price

            gain_pct = (state.entry_price - state.highest_favorable) / state.entry_price
            if gain_pct >= self.trail_activation_pct:
                state.trailing_active = True
                atr_now = self._compute_atr(df)
                trail_stop = state.highest_favorable + self.trail_step_atr_mult * atr_now
                if trail_stop < state.current_stop:
                    state.current_stop = trail_stop
                    logger.debug(
                        f"Trailing stop moved DOWN: {state.current_stop:.4f} "
                        f"(trough={state.highest_favorable:.4f})"
                    )

        return state

    def should_stop(self, state: StopState, current_price: float) -> tuple[bool, str]:
        """
        Check if position should be closed.

        Returns:
            (should_close: bool, reason: str)
        """
        if state.direction == SignalDirection.LONG:
            if current_price <= state.current_stop:
                return True, "stop_loss"
            if current_price >= state.take_profit:
                return True, "take_profit"
        elif state.direction == SignalDirection.SHORT:
            if current_price >= state.current_stop:
                return True, "stop_loss"
            if current_price <= state.take_profit:
                return True, "take_profit"
        return False, ""
