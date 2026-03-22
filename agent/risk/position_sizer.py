"""
AEGIS — Position Sizer
Kelly criterion-based position sizing with regime adjustment.
"""

import logging
import numpy as np
from dataclasses import dataclass

from regime.regime_types import Regime

logger = logging.getLogger(__name__)


@dataclass
class SizingResult:
    dollar_size: float         # Dollar amount to deploy
    kelly_fraction: float      # Raw Kelly fraction
    regime_multiplier: float
    capped: bool               # True if max position cap applied

    @property
    def is_zero(self) -> bool:
        return self.dollar_size <= 0


class PositionSizer:
    """
    Kelly criterion-based position sizer with risk controls.

    Full Kelly: f* = (p * b - q) / b
      p = probability of winning
      b = avg_win / avg_loss ratio
      q = 1 - p (probability of losing)

    We use Half-Kelly (f*/2) by default to account for estimation error.
    """

    def __init__(
        self,
        max_position_pct: float = 0.05,   # 5% of portfolio max per trade
        kelly_fraction: float = 0.5,       # Half-Kelly
        min_win_rate: float = 0.30,        # Below this, no trade
        min_rr_ratio: float = 1.0,         # Minimum risk:reward required
    ):
        self.max_position_pct = max_position_pct
        self.kelly_fraction = kelly_fraction
        self.min_win_rate = min_win_rate
        self.min_rr_ratio = min_rr_ratio

    def calculate(
        self,
        portfolio_value: float,
        win_rate: float,
        avg_win_pct: float,
        avg_loss_pct: float,
        regime: Regime,
        signal_confidence: float = 1.0,
    ) -> SizingResult:
        """
        Calculate position size.

        Args:
            portfolio_value: Total portfolio value in USD
            win_rate: Historical win rate [0, 1]
            avg_win_pct: Average winning trade size as decimal (e.g. 0.02 = 2%)
            avg_loss_pct: Average losing trade size as decimal (e.g. 0.01 = 1%)
            regime: Current market regime
            signal_confidence: Strategy confidence [0, 1]

        Returns:
            SizingResult with dollar_size to deploy
        """
        # Safety checks
        win_rate = float(np.clip(win_rate, 0.01, 0.99))
        avg_win_pct = max(avg_win_pct, 1e-6)
        avg_loss_pct = max(avg_loss_pct, 1e-6)

        if win_rate < self.min_win_rate:
            logger.debug(f"Win rate {win_rate:.2%} below minimum {self.min_win_rate:.2%} — no trade")
            return SizingResult(0.0, 0.0, 1.0, False)

        rr = avg_win_pct / avg_loss_pct
        if rr < self.min_rr_ratio:
            logger.debug(f"R:R {rr:.2f} below minimum {self.min_rr_ratio:.2f} — no trade")
            return SizingResult(0.0, 0.0, 1.0, False)

        # Full Kelly: f* = (p * b - q) / b
        # b = avg_win / avg_loss, p = win_rate, q = 1 - win_rate
        b = rr
        p = win_rate
        q = 1 - p
        full_kelly = (p * b - q) / b

        if full_kelly <= 0:
            logger.debug(f"Kelly fraction {full_kelly:.4f} <= 0 — expected negative EV, no trade")
            return SizingResult(0.0, full_kelly, 1.0, False)

        # Apply fractional Kelly
        kelly_f = full_kelly * self.kelly_fraction

        # Scale by signal confidence (lower confidence → smaller bet)
        kelly_f *= signal_confidence

        # Regime multiplier
        regime_mult = regime.risk_multiplier

        # Final fraction of portfolio
        final_fraction = kelly_f * regime_mult

        # Dollar size
        raw_size = portfolio_value * final_fraction
        max_size = portfolio_value * self.max_position_pct
        capped = raw_size > max_size
        dollar_size = min(raw_size, max_size)

        logger.debug(
            f"Kelly sizing: win_rate={win_rate:.2%} rr={rr:.2f} full_kelly={full_kelly:.4f} "
            f"f={kelly_f:.4f} regime_mult={regime_mult:.2f} size=${dollar_size:.2f}"
        )

        return SizingResult(
            dollar_size=dollar_size,
            kelly_fraction=full_kelly,
            regime_multiplier=regime_mult,
            capped=capped,
        )

    def quick_size(
        self,
        portfolio_value: float,
        regime: Regime,
        confidence: float,
        max_pct: float = 0.05,
    ) -> float:
        """
        Simplified sizing without historical stats.
        Falls back to confidence × regime_multiplier × max_pct.
        """
        raw = portfolio_value * max_pct * confidence * regime.risk_multiplier
        return min(raw, portfolio_value * max_pct)
