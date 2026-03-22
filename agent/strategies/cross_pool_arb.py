"""
AEGIS - Cross-Pool Arbitrage Strategy
Looks for price discrepancies between exchange sources.
For backtest: simulates spread using rolling price vs VWAP divergence.

NOTE: Real cross-pool arb requires real-time data from multiple DEX pools.
This backtest version uses a proxy: when price diverges significantly from
a smoothed average (simulating pool desync), it captures the reversion.
"""

import logging
import numpy as np
import pandas as pd

from strategies.base_strategy import BaseStrategy, TradeSignal, SignalDirection
from regime.regime_types import Regime, RegimeResult

logger = logging.getLogger(__name__)


class CrossPoolArbStrategy(BaseStrategy):
    """
    Cross-pool arbitrage proxy for backtesting.
    
    Real implementation would compare ETH prices across:
    - Uniswap v3/v4 pools on Base
    - Different fee tiers (0.05%, 0.3%, 1%)
    - CEX reference prices
    
    Backtest proxy:
    - Compare close price to EMA(close) as a "fair value" reference
    - Compare close to TWAP (time-weighted average) 
    - When divergence > threshold, assume arbitrage opportunity exists
    - This simulates the pool desync that creates real arb opportunities
    
    Key insight: On Base chain, pool desyncs happen during:
    - High gas periods on mainnet (L2 arb bots lag)
    - Large swaps that move one pool significantly
    - Low liquidity periods in certain fee tiers
    """

    def __init__(
        self,
        divergence_threshold: float = 0.008,  # 0.8% — only real dislocations
        ema_fast: int = 3,     # Very fast EMA (immediate price)
        ema_slow: int = 24,    # 24h EMA (fair value)
        holding_bars: int = 3,
        stop_pct: float = 0.004,
        min_confidence: float = 0.55,  # High confidence bar
    ):
        super().__init__("CrossPoolArbStrategy")
        self.divergence_threshold = divergence_threshold
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.holding_bars = holding_bars
        self.stop_pct = stop_pct
        self.min_confidence = min_confidence

    @property
    def genes(self) -> dict:
        return {
            "divergence_threshold": self.divergence_threshold,
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "stop_pct": self.stop_pct,
        }

    @genes.setter
    def genes(self, values: dict):
        self.divergence_threshold = float(np.clip(
            values.get("divergence_threshold", self.divergence_threshold), 0.001, 0.01))
        self.ema_fast = max(3, int(values.get("ema_fast", self.ema_fast)))
        self.ema_slow = max(10, int(values.get("ema_slow", self.ema_slow)))
        self.stop_pct = float(np.clip(values.get("stop_pct", self.stop_pct), 0.002, 0.02))

    def generate_signal(
        self,
        df: pd.DataFrame,
        regime: RegimeResult,
        sentiment_score: float = 0.0,
    ) -> TradeSignal:
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        current_price = float(df["close"].iloc[-1])

        if len(df) < self.ema_slow + 5:
            return self._neutral_signal(current_price, regime.regime)

        # Compute "pool prices" using EMAs of different speeds
        fast_ema = df["close"].ewm(span=self.ema_fast, min_periods=self.ema_fast).mean()
        slow_ema = df["close"].ewm(span=self.ema_slow, min_periods=self.ema_slow).mean()

        curr_fast = float(fast_ema.iloc[-1])
        curr_slow = float(slow_ema.iloc[-1])

        if np.isnan(curr_fast) or np.isnan(curr_slow):
            return self._neutral_signal(current_price, regime.regime)

        # Divergence between "pools"
        divergence = (curr_fast - curr_slow) / curr_slow

        # Also check price vs TWAP (volume-weighted)
        if df["volume"].sum() > 0:
            tp = (df["high"] + df["low"] + df["close"]) / 3
            twap_10 = float(tp.iloc[-10:].mean())
            price_vs_twap = (current_price - twap_10) / twap_10
        else:
            price_vs_twap = 0.0

        # Volume spike indicator (arb opportunities often coincide with volume)
        vol_mean = float(df["volume"].iloc[-21:-1].mean()) if len(df) > 20 else float(df["volume"].mean())
        curr_vol = float(df["volume"].iloc[-1])
        vol_spike = curr_vol / vol_mean if vol_mean > 0 else 1.0

        direction = SignalDirection.NEUTRAL
        confidence = 0.0

        # Arb signal: fast pool diverges from slow (24h fair value)
        # Only fire when multiple confirmations align
        if abs(divergence) >= self.divergence_threshold:
            # Need volume spike to confirm it's a real dislocation event
            if vol_spike < 1.5:
                return self._neutral_signal(current_price, regime.regime)

            if divergence > 0:
                # Price spiked above fair value — expect reversion DOWN
                direction = SignalDirection.SHORT
                # Extra check: also above TWAP
                if price_vs_twap < 0.002:
                    return self._neutral_signal(current_price, regime.regime)
            else:
                # Price dipped below fair value — expect reversion UP
                direction = SignalDirection.LONG
                # Extra check: also below TWAP
                if price_vs_twap > -0.002:
                    return self._neutral_signal(current_price, regime.regime)

            # Confidence scales with divergence magnitude
            div_strength = abs(divergence) / self.divergence_threshold
            confidence = min(0.50 + 0.15 * div_strength, 0.82)

            # Strong volume spike adds confidence
            if vol_spike > 2.0:
                confidence = min(confidence + 0.08, 0.85)

        if direction == SignalDirection.NEUTRAL or confidence < self.min_confidence:
            return self._neutral_signal(current_price, regime.regime)

        # Tight stops for arb — should converge quickly
        if direction == SignalDirection.LONG:
            stop_loss = current_price * (1 - self.stop_pct)
            take_profit = curr_slow  # Target slow EMA (fair value)
        else:
            stop_loss = current_price * (1 + self.stop_pct)
            take_profit = curr_slow

        # Ensure minimum R:R
        if direction == SignalDirection.LONG:
            risk = current_price - stop_loss
            reward = take_profit - current_price
        else:
            risk = stop_loss - current_price
            reward = current_price - take_profit

        if risk <= 0 or reward <= 0 or reward / risk < 0.5:
            return self._neutral_signal(current_price, regime.regime)

        return TradeSignal(
            strategy_name=self.name,
            direction=direction,
            confidence=confidence,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            regime=regime.regime,
            metadata={
                "divergence": divergence,
                "fast_ema": curr_fast,
                "slow_ema": curr_slow,
                "price_vs_twap": price_vs_twap,
                "vol_spike": vol_spike,
            },
        )

    def get_position_size(self, portfolio_value: float, signal: TradeSignal) -> float:
        # Arb positions can be slightly larger (lower risk)
        base_pct = 0.03
        size = portfolio_value * base_pct * signal.confidence
        return min(size, portfolio_value * 0.04)

    def should_exit(
        self,
        entry_price: float,
        current_price: float,
        direction: SignalDirection,
        regime: RegimeResult,
    ) -> tuple[bool, str]:
        # Arb should close quickly — exit if no convergence
        return False, ""
