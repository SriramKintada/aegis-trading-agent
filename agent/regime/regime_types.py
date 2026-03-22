"""
AEGIS — Market Regime Types
"""

from enum import Enum, auto


class Regime(Enum):
    """Four market regimes detected by HMM."""
    BULL_TRENDING = 0     # Strong upward trend, lower volatility
    BEAR_TRENDING = 1     # Strong downward trend, lower volatility
    HIGH_VOL_CHOPPY = 2   # High volatility, no clear direction
    MEAN_REVERTING = 3    # Low volatility, ranging, ideal for MR strategies

    @property
    def is_trending(self) -> bool:
        return self in (Regime.BULL_TRENDING, Regime.BEAR_TRENDING)

    @property
    def is_high_vol(self) -> bool:
        return self == Regime.HIGH_VOL_CHOPPY

    @property
    def is_mean_reverting(self) -> bool:
        return self == Regime.MEAN_REVERTING

    @property
    def risk_multiplier(self) -> float:
        """Capital allocation multiplier by regime."""
        return {
            Regime.BULL_TRENDING: 1.2,
            Regime.BEAR_TRENDING: 0.6,
            Regime.HIGH_VOL_CHOPPY: 0.3,
            Regime.MEAN_REVERTING: 1.0,
        }[self]

    @property
    def label(self) -> str:
        return self.name


class RegimeResult:
    """Output of HMM regime prediction."""

    def __init__(self, regime: Regime, confidence: float, probabilities: dict):
        self.regime = regime
        self.confidence = confidence          # Probability of predicted regime
        self.probabilities = probabilities    # {Regime: probability}

    def __repr__(self) -> str:
        return (
            f"RegimeResult(regime={self.regime.label}, "
            f"confidence={self.confidence:.2%})"
        )
