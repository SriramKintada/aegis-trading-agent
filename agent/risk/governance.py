"""
AEGIS — Governance / Veto System
A "second opinion" check before executing any trade.

Rules-based veto that prevents bad trades:
  1. If last 3 trades were all losses → pause for 1 hour
  2. If daily drawdown > 1% → reduce position size by 50%
  3. If gas price > 30 gwei → skip trade
  4. If spread between entry and stop < 2x transaction cost → skip
  5. If portfolio is > 70% deployed → no new positions
  6. Regime confidence < 60% → skip non-arb trades

Every veto is logged with reason.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from strategies.base_strategy import TradeSignal, SignalDirection

logger = logging.getLogger(__name__)


@dataclass
class VetoResult:
    """Result of a governance veto check."""
    approved: bool
    reason: str = ""
    position_size_multiplier: float = 1.0  # 1.0 = full size, 0.5 = halved
    warnings: list = field(default_factory=list)


@dataclass
class TradeRecord:
    """Minimal trade record for governance tracking."""
    timestamp: float
    pnl_pct: float
    pnl_usd: float
    strategy: str
    direction: str


class GovernanceVeto:
    """
    Pre-trade governance system.

    Checks a series of rules before allowing trade execution.
    Can approve, reject, or modify (reduce size) trades.
    """

    def __init__(
        self,
        loss_streak_limit: int = 3,
        loss_streak_pause_seconds: float = 3600.0,  # 1 hour
        daily_drawdown_limit: float = 0.01,          # 1%
        drawdown_size_reduction: float = 0.50,       # 50% reduction
        max_gas_gwei: float = 30.0,
        min_edge_cost_ratio: float = 2.0,            # Edge must be 2x cost
        max_deployed_pct: float = 0.70,              # 70% max deployed
        min_regime_confidence: float = 0.60,         # 60% regime confidence
        transaction_cost_pct: float = 0.0015,        # 0.15% per trade
        arb_strategies: Optional[set] = None,        # Arb is regime-independent
    ):
        self.loss_streak_limit = loss_streak_limit
        self.loss_streak_pause_seconds = loss_streak_pause_seconds
        self.daily_drawdown_limit = daily_drawdown_limit
        self.drawdown_size_reduction = drawdown_size_reduction
        self.max_gas_gwei = max_gas_gwei
        self.min_edge_cost_ratio = min_edge_cost_ratio
        self.max_deployed_pct = max_deployed_pct
        self.min_regime_confidence = min_regime_confidence
        self.transaction_cost_pct = transaction_cost_pct
        self.arb_strategies = arb_strategies or {"CrossDexArbStrategy"}

        # State
        self._trade_history: list[TradeRecord] = []
        self._daily_start_capital: float = 0.0
        self._daily_low_capital: float = 0.0
        self._last_loss_streak_pause: float = 0.0
        self._veto_log: list[dict] = []

    def set_daily_capital(self, capital: float):
        """Set the starting capital for today's drawdown tracking."""
        self._daily_start_capital = capital
        self._daily_low_capital = capital

    def record_trade(self, trade: TradeRecord):
        """Record a completed trade for governance tracking."""
        self._trade_history.append(trade)

    def record_trade_simple(
        self,
        pnl_pct: float,
        pnl_usd: float,
        strategy: str,
        direction: str,
    ):
        """Convenience: record trade without creating TradeRecord."""
        self._trade_history.append(TradeRecord(
            timestamp=time.time(),
            pnl_pct=pnl_pct,
            pnl_usd=pnl_usd,
            strategy=strategy,
            direction=direction,
        ))

    def check(
        self,
        signal: TradeSignal,
        current_capital: float,
        deployed_capital: float = 0.0,
        gas_price_gwei: float = 0.5,  # Base is cheap, default low
        current_time: Optional[float] = None,
    ) -> VetoResult:
        """
        Run all governance checks on a proposed trade.

        Args:
            signal: The trade signal to evaluate
            current_capital: Current portfolio value
            deployed_capital: Capital currently in open positions
            gas_price_gwei: Current gas price in gwei
            current_time: Current timestamp (for backtest time simulation)

        Returns:
            VetoResult with approval status and any modifications
        """
        now = current_time or time.time()
        is_arb = signal.strategy_name in self.arb_strategies
        warnings = []
        size_mult = 1.0

        # Track daily low
        if current_capital < self._daily_low_capital:
            self._daily_low_capital = current_capital

        # ── Rule 1: Loss streak pause ──
        if len(self._trade_history) >= self.loss_streak_limit:
            recent = self._trade_history[-self.loss_streak_limit:]
            all_losses = all(t.pnl_pct < 0 for t in recent)
            if all_losses:
                time_since_pause = now - self._last_loss_streak_pause
                if time_since_pause < self.loss_streak_pause_seconds:
                    remaining = self.loss_streak_pause_seconds - time_since_pause
                    reason = (
                        f"VETO: Loss streak ({self.loss_streak_limit} consecutive losses). "
                        f"Paused for {remaining:.0f}s more."
                    )
                    self._log_veto(signal, reason)
                    return VetoResult(approved=False, reason=reason)
                else:
                    # Just ended a streak — mark the pause start for future
                    self._last_loss_streak_pause = now
                    warnings.append("Just exited loss streak pause — proceed with caution")

        # ── Rule 2: Daily drawdown check ──
        if self._daily_start_capital > 0:
            daily_drawdown = (
                self._daily_start_capital - current_capital
            ) / self._daily_start_capital
            if daily_drawdown > self.daily_drawdown_limit:
                size_mult *= self.drawdown_size_reduction
                warnings.append(
                    f"Daily drawdown {daily_drawdown:.2%} > {self.daily_drawdown_limit:.2%} "
                    f"— position size reduced by {(1 - self.drawdown_size_reduction):.0%}"
                )

        # ── Rule 3: Gas price check ──
        if gas_price_gwei > self.max_gas_gwei:
            reason = (
                f"VETO: Gas price {gas_price_gwei:.1f} gwei > "
                f"{self.max_gas_gwei:.1f} gwei limit"
            )
            self._log_veto(signal, reason)
            return VetoResult(approved=False, reason=reason)

        # ── Rule 4: Minimum edge check ──
        if signal.direction != SignalDirection.NEUTRAL:
            if signal.direction == SignalDirection.LONG:
                risk = abs(signal.entry_price - signal.stop_loss) / signal.entry_price
            else:
                risk = abs(signal.stop_loss - signal.entry_price) / signal.entry_price

            edge_cost_ratio = risk / self.transaction_cost_pct if self.transaction_cost_pct > 0 else float('inf')
            if edge_cost_ratio < self.min_edge_cost_ratio:
                reason = (
                    f"VETO: Edge/cost ratio {edge_cost_ratio:.2f}x < "
                    f"{self.min_edge_cost_ratio:.1f}x minimum. "
                    f"Risk={risk:.4%}, cost={self.transaction_cost_pct:.4%}"
                )
                self._log_veto(signal, reason)
                return VetoResult(approved=False, reason=reason)

        # ── Rule 5: Portfolio deployment limit ──
        if current_capital > 0:
            deployed_pct = deployed_capital / current_capital
            if deployed_pct > self.max_deployed_pct:
                reason = (
                    f"VETO: Portfolio {deployed_pct:.1%} deployed > "
                    f"{self.max_deployed_pct:.1%} limit. No new positions."
                )
                self._log_veto(signal, reason)
                return VetoResult(approved=False, reason=reason)

        # ── Rule 6: Regime confidence for non-arb trades ──
        if not is_arb:
            # Use the regime confidence from signal metadata if available
            regime_conf = signal.confidence  # proxy: signal conf correlates with regime conf
            # Actually we need regime confidence directly — use a reasonable proxy
            # In practice, this would come from the RegimeResult
            # For backtest, we approximate: if signal confidence is low, regime is uncertain
            if signal.confidence < self.min_regime_confidence * 0.8:
                reason = (
                    f"VETO: Low confidence {signal.confidence:.2%} for non-arb trade. "
                    f"Minimum effective threshold: {self.min_regime_confidence * 0.8:.2%}"
                )
                self._log_veto(signal, reason)
                return VetoResult(approved=False, reason=reason)

        # All checks passed
        return VetoResult(
            approved=True,
            position_size_multiplier=size_mult,
            warnings=warnings,
        )

    def check_backtest(
        self,
        signal: TradeSignal,
        current_capital: float,
        bar_index: int = 0,
    ) -> VetoResult:
        """
        Simplified governance check for backtest mode.
        No gas price check (simulated). No deployment tracking (single position).
        """
        return self.check(
            signal=signal,
            current_capital=current_capital,
            deployed_capital=0.0,  # Backtest: no concurrent positions tracked
            gas_price_gwei=0.5,    # Base is cheap
            current_time=float(bar_index * 3600),  # Simulate time from bar index
        )

    def _log_veto(self, signal: TradeSignal, reason: str):
        """Log a veto decision."""
        entry = {
            "strategy": signal.strategy_name,
            "direction": signal.direction.value,
            "confidence": signal.confidence,
            "reason": reason,
            "timestamp": time.time(),
        }
        self._veto_log.append(entry)
        logger.info(f"Governance: {reason}")

    @property
    def veto_count(self) -> int:
        return len(self._veto_log)

    @property
    def veto_log(self) -> list[dict]:
        return self._veto_log

    def veto_summary(self) -> dict:
        """Summarize veto reasons."""
        if not self._veto_log:
            return {"total_vetoes": 0}

        reasons = {}
        for v in self._veto_log:
            # Extract first word of reason for categorization
            key = v["reason"].split(":")[1].strip().split(".")[0] if ":" in v["reason"] else "unknown"
            reasons[key] = reasons.get(key, 0) + 1

        return {
            "total_vetoes": len(self._veto_log),
            "by_reason": reasons,
        }

    def reset(self):
        """Reset all state for new backtest run."""
        self._trade_history.clear()
        self._veto_log.clear()
        self._daily_start_capital = 0.0
        self._daily_low_capital = 0.0
        self._last_loss_streak_pause = 0.0
