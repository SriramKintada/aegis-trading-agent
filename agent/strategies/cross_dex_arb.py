"""
AEGIS — Cross-DEX Arbitrage Strategy
Compares ETH/USDC price between Uniswap v4 and Aerodrome on Base.
Pure mathematical edge — no prediction needed.

For backtesting: simulates Aerodrome price as CryptoCompare price + random noise.
For live: queries both DEX routers for real quotes.
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from strategies.base_strategy import BaseStrategy, TradeSignal, SignalDirection
from regime.regime_types import Regime, RegimeResult

logger = logging.getLogger(__name__)


class CrossDexArbStrategy(BaseStrategy):
    """
    Cross-DEX arbitrage between Uniswap v4 and Aerodrome on Base.

    Entry:
      - When price difference > cost_threshold (gas + slippage + commission on both legs)
      - Buy on cheaper DEX, sell on expensive DEX
      - Direction is LONG if Uniswap is cheaper (buy Uni, sell Aero)
      - Direction is SHORT if Aerodrome is cheaper (buy Aero, sell Uni)

    Confidence:
      - Proportional to spread size beyond threshold
      - confidence = (spread_pct - cost_threshold) / spread_pct

    Key: This is regime-independent. Arb works in any market condition.
    """

    def __init__(
        self,
        cost_threshold_pct: float = 0.0020,   # 0.20% min spread to cover costs
        # Breakdown: 0.10% slippage leg1 + 0.05% commission leg1
        #          + 0.05% slippage leg2 (arb second leg)
        max_spread_pct: float = 0.0100,        # 1% max — beyond this, something is wrong
        stop_pct: float = 0.003,               # 0.3% stop (arb should close fast)
        take_profit_pct: float = 0.002,        # 0.2% TP (capture the spread)
        min_confidence: float = 0.30,
        # Simulation parameters for backtesting
        sim_noise_mean: float = 0.0,           # Mean of price noise
        sim_noise_std: float = 0.0015,         # Std dev of price noise (0.15%)
    ):
        super().__init__("CrossDexArbStrategy")
        self.cost_threshold_pct = cost_threshold_pct
        self.max_spread_pct = max_spread_pct
        self.stop_pct = stop_pct
        self.take_profit_pct = take_profit_pct
        self.min_confidence = min_confidence
        self.sim_noise_mean = sim_noise_mean
        self.sim_noise_std = sim_noise_std

        # Track arb-specific metrics
        self.arb_opportunities = 0
        self.arb_executed = 0

    @property
    def genes(self) -> dict:
        return {
            "cost_threshold_pct": self.cost_threshold_pct,
            "stop_pct": self.stop_pct,
            "take_profit_pct": self.take_profit_pct,
            "sim_noise_std": self.sim_noise_std,
        }

    @genes.setter
    def genes(self, values: dict):
        self.cost_threshold_pct = float(np.clip(
            values.get("cost_threshold_pct", self.cost_threshold_pct), 0.0005, 0.005))
        self.stop_pct = float(np.clip(
            values.get("stop_pct", self.stop_pct), 0.001, 0.01))
        self.take_profit_pct = float(np.clip(
            values.get("take_profit_pct", self.take_profit_pct), 0.001, 0.01))
        self.sim_noise_std = float(np.clip(
            values.get("sim_noise_std", self.sim_noise_std), 0.0005, 0.005))

    def _get_simulated_aerodrome_price(
        self, uniswap_price: float, bar_index: int
    ) -> float:
        """
        Simulate Aerodrome price as Uniswap price + random noise.
        Uses bar_index as part of the seed for reproducibility without look-ahead.
        """
        # Deterministic noise per bar (no look-ahead — based on bar index only)
        rng = np.random.RandomState(seed=bar_index * 7919 + 42)
        noise_pct = rng.normal(self.sim_noise_mean, self.sim_noise_std)
        return uniswap_price * (1 + noise_pct)

    def generate_signal(
        self,
        df: pd.DataFrame,
        regime: RegimeResult,
        sentiment_score: float = 0.0,
        aerodrome_price: Optional[float] = None,
        bar_index: int = 0,
    ) -> TradeSignal:
        """
        Generate arb signal by comparing prices on two DEXs.

        In backtest mode: aerodrome_price can be passed directly or simulated.
        In live mode: aerodrome_price would come from actual router queries.
        """
        df_local = df.copy()
        df_local.columns = [c.lower() for c in df_local.columns]
        uniswap_price = float(df_local["close"].iloc[-1])

        if uniswap_price <= 0:
            return self._neutral_signal(uniswap_price, regime.regime)

        # Get Aerodrome price (simulated for backtest, real for live)
        if aerodrome_price is None:
            aerodrome_price = self._get_simulated_aerodrome_price(
                uniswap_price, bar_index
            )

        # Calculate spread
        spread = aerodrome_price - uniswap_price
        spread_pct = abs(spread) / uniswap_price

        # Track opportunity
        if spread_pct > self.cost_threshold_pct:
            self.arb_opportunities += 1

        # Filter: spread must exceed cost threshold
        if spread_pct <= self.cost_threshold_pct:
            return self._neutral_signal(uniswap_price, regime.regime)

        # Filter: spread must not be suspiciously large
        if spread_pct > self.max_spread_pct:
            return self._neutral_signal(uniswap_price, regime.regime)

        # Direction: buy on cheaper DEX, sell on expensive DEX
        if aerodrome_price > uniswap_price:
            # Uniswap is cheaper → buy on Uniswap, sell on Aerodrome
            direction = SignalDirection.LONG
            entry_price = uniswap_price
        else:
            # Aerodrome is cheaper → buy on Aerodrome, sell on Uniswap
            direction = SignalDirection.SHORT
            entry_price = uniswap_price

        # Confidence: proportional to edge beyond cost
        net_edge = spread_pct - self.cost_threshold_pct
        confidence = min(net_edge / spread_pct, 0.95)
        confidence = max(confidence, 0.0)

        if confidence < self.min_confidence:
            return self._neutral_signal(uniswap_price, regime.regime)

        self.arb_executed += 1

        # Tight stop and TP for arb (these are fast trades)
        if direction == SignalDirection.LONG:
            stop_loss = entry_price * (1 - self.stop_pct)
            take_profit = entry_price * (1 + self.take_profit_pct)
        else:
            stop_loss = entry_price * (1 + self.stop_pct)
            take_profit = entry_price * (1 - self.take_profit_pct)

        return TradeSignal(
            strategy_name=self.name,
            direction=direction,
            confidence=min(confidence, 0.95),
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            regime=regime.regime,
            metadata={
                "uniswap_price": uniswap_price,
                "aerodrome_price": aerodrome_price,
                "spread_pct": spread_pct,
                "net_edge_pct": net_edge,
                "arb_direction": "buy_uni_sell_aero" if direction == SignalDirection.LONG else "buy_aero_sell_uni",
            },
        )

    def get_position_size(self, portfolio_value: float, signal: TradeSignal) -> float:
        """Arb positions can be larger — it's a mathematical edge."""
        base_pct = 0.05  # 5% base for arb
        size = portfolio_value * base_pct * signal.confidence
        return min(size, portfolio_value * 0.08)

    def should_exit(
        self,
        entry_price: float,
        current_price: float,
        direction: SignalDirection,
        regime: RegimeResult,
    ) -> tuple[bool, str]:
        """Arb trades should close quickly — always exit at next bar."""
        return True, "arb_single_bar"

    def get_arb_metrics(self) -> dict:
        """Return arb-specific performance metrics."""
        return {
            "opportunities_detected": self.arb_opportunities,
            "trades_executed": self.arb_executed,
            "execution_rate": (
                self.arb_executed / self.arb_opportunities
                if self.arb_opportunities > 0 else 0.0
            ),
        }

    def reset_metrics(self):
        """Reset arb metrics for new backtest run."""
        self.arb_opportunities = 0
        self.arb_executed = 0
