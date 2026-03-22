"""
AEGIS — Abstract Base Strategy
All concrete strategies inherit from this.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import pandas as pd

from regime.regime_types import Regime, RegimeResult


class SignalDirection(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


@dataclass
class TradeSignal:
    """Unified signal output from any strategy."""
    strategy_name: str
    direction: SignalDirection
    confidence: float            # [0, 1]
    entry_price: float
    stop_loss: float
    take_profit: float
    regime: Regime
    metadata: dict = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.direction != SignalDirection.NEUTRAL and self.confidence > 0.0

    @property
    def risk_reward(self) -> float:
        if self.direction == SignalDirection.LONG:
            risk = self.entry_price - self.stop_loss
            reward = self.take_profit - self.entry_price
        elif self.direction == SignalDirection.SHORT:
            risk = self.stop_loss - self.entry_price
            reward = self.entry_price - self.take_profit
        else:
            return 0.0
        return reward / risk if risk > 0 else 0.0

    def __repr__(self) -> str:
        return (
            f"Signal({self.strategy_name} | {self.direction.value} | "
            f"conf={self.confidence:.2%} | entry={self.entry_price:.4f} | "
            f"R:R={self.risk_reward:.2f})"
        )


class BaseStrategy(ABC):
    """
    Abstract base for all AEGIS trading strategies.

    Subclasses must implement:
        - generate_signal()
        - get_position_size()
        - should_exit()
    """

    def __init__(self, name: str):
        self.name = name
        self._win_rate: float = 0.5
        self._avg_win: float = 0.02
        self._avg_loss: float = 0.01
        self._trade_count: int = 0

    @abstractmethod
    def generate_signal(
        self,
        df: pd.DataFrame,
        regime: RegimeResult,
        sentiment_score: float = 0.0,
    ) -> TradeSignal:
        """
        Analyze market data and return a trade signal.

        Args:
            df: OHLCV DataFrame (columns: open, high, low, close, volume)
            regime: Current regime from HMM detector
            sentiment_score: Current FinBERT composite score [-1, 1]

        Returns:
            TradeSignal (may have direction=NEUTRAL if no opportunity)
        """
        ...

    @abstractmethod
    def get_position_size(self, portfolio_value: float, signal: TradeSignal) -> float:
        """
        Return dollar position size for this signal.

        Args:
            portfolio_value: Total portfolio value in USD
            signal: The trade signal to size

        Returns:
            Dollar amount to deploy
        """
        ...

    @abstractmethod
    def should_exit(
        self,
        entry_price: float,
        current_price: float,
        direction: SignalDirection,
        regime: RegimeResult,
    ) -> tuple[bool, str]:
        """
        Determine if open position should be exited.

        Returns:
            (should_exit: bool, reason: str)
        """
        ...

    def update_performance(self, won: bool, pnl_pct: float):
        """Update running win rate and P&L stats after a trade closes."""
        self._trade_count += 1
        alpha = 1 / max(self._trade_count, 10)  # EMA-style update
        if won:
            self._win_rate = (1 - alpha) * self._win_rate + alpha * 1.0
            self._avg_win = (1 - alpha) * self._avg_win + alpha * abs(pnl_pct)
        else:
            self._win_rate = (1 - alpha) * self._win_rate + alpha * 0.0
            self._avg_loss = (1 - alpha) * self._avg_loss + alpha * abs(pnl_pct)

    @property
    def sharpe_proxy(self) -> float:
        """Quick Sharpe proxy: expected return / expected loss."""
        expected_return = self._win_rate * self._avg_win - (1 - self._win_rate) * self._avg_loss
        if self._avg_loss == 0:
            return 0.0
        return expected_return / self._avg_loss

    def _neutral_signal(self, current_price: float, regime: Regime) -> TradeSignal:
        """Helper: return a neutral (no-trade) signal."""
        return TradeSignal(
            strategy_name=self.name,
            direction=SignalDirection.NEUTRAL,
            confidence=0.0,
            entry_price=current_price,
            stop_loss=current_price,
            take_profit=current_price,
            regime=regime,
        )
