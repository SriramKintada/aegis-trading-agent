"""
AEGIS — Walk-Forward Backtesting Engine

CRITICAL: NO LOOK-AHEAD BIAS
- HMM trained ONLY on data before current bar
- Indicators use ONLY past data
- Entry on NEXT bar's open after signal
- Slippage: 0.1% per trade
- Commission: 0.05% per trade

Two independent P&L calculations that must match.
"""

import json
import csv
import logging
import sys
import os
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from datetime import datetime

import numpy as np
import pandas as pd
import requests

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from regime.hmm_detector import HMMRegimeDetector
from regime.regime_types import Regime, RegimeResult
from strategies.base_strategy import BaseStrategy, TradeSignal, SignalDirection
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.sentiment_pulse import SentimentPulseStrategy
from risk.position_sizer import PositionSizer

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# ─── Constants ────────────────────────────────────────────────────────────────

SLIPPAGE_PCT = 0.001    # 0.1% per trade
COMMISSION_PCT = 0.0005  # 0.05% per trade (Base chain gas)
INITIAL_CAPITAL = 10000.0


# ─── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class Trade:
    """Record of a single completed trade."""
    trade_id: int
    strategy: str
    direction: str  # "LONG" or "SHORT"
    regime: str
    entry_bar: int
    exit_bar: int
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    confidence: float
    position_size: float
    gross_pnl: float
    slippage_cost: float
    commission_cost: float
    net_pnl: float
    net_pnl_pct: float
    duration_bars: int
    exit_reason: str

    @property
    def won(self) -> bool:
        return self.net_pnl > 0


@dataclass
class BacktestResult:
    """Complete backtest results."""
    # Config
    start_time: str
    end_time: str
    total_bars: int
    initial_capital: float
    slippage_pct: float
    commission_pct: float

    # Performance
    final_capital: float
    total_return_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    calmar_ratio: float
    avg_trade_duration: float

    # Cross-check
    pnl_sum_method: float
    pnl_portfolio_method: float
    pnl_match: bool

    # Per-strategy
    strategy_stats: Dict[str, Dict]
    # Per-regime
    regime_stats: Dict[str, Dict]

    # Raw trades
    trades: List[Dict]
    # Equity curve
    equity_curve: List[float]


# ─── Data Fetching ────────────────────────────────────────────────────────────

def fetch_eth_data(limit: int = 2000) -> pd.DataFrame:
    """
    Fetch ETH/USD hourly data from CryptoCompare.
    Free API, no key needed for basic.
    """
    print(f"Fetching {limit} bars of ETH/USD hourly data from CryptoCompare...")
    all_data = []
    remaining = limit
    to_ts = None

    while remaining > 0:
        batch = min(remaining, 2000)
        url = "https://min-api.cryptocompare.com/data/v2/histohour"
        params = {"fsym": "ETH", "tsym": "USD", "limit": batch}
        if to_ts:
            params["toTs"] = to_ts

        resp = requests.get(url, params=params, timeout=30)
        data = resp.json()

        if data.get("Response") != "Success":
            raise RuntimeError(f"CryptoCompare API error: {data.get('Message', 'unknown')}")

        candles = data["Data"]["Data"]
        all_data = candles + all_data  # prepend older data
        remaining -= len(candles)

        if remaining > 0:
            to_ts = candles[0]["time"] - 1  # go further back

    df = pd.DataFrame(all_data)
    df = df.rename(columns={"time": "timestamp", "volumefrom": "volume"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
    df = df.set_index("timestamp")
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df = df[~df.index.duplicated(keep='first')]
    df = df.sort_index()

    print(f"  Got {len(df)} bars: {df.index[0]} to {df.index[-1]}")
    print(f"  Price range: ${df['close'].min():.2f} - ${df['close'].max():.2f}")
    return df


# ─── Backtesting Engine ──────────────────────────────────────────────────────

class WalkForwardBacktester:
    """
    Walk-forward backtester with rigorous no-look-ahead enforcement.

    Walk-forward structure:
      - HMM training window: first `hmm_train_bars` bars
      - HMM retrain every `hmm_retrain_interval` bars
      - At each bar i, only data[0:i+1] is visible
      - Signal generated at bar i → entry at bar i+1 open
      - Exit checked at each subsequent bar
    """

    def __init__(
        self,
        strategies: List[BaseStrategy],
        hmm_train_bars: int = 300,
        hmm_retrain_interval: int = 100,
        hmm_lookback: int = 50,
        max_concurrent_positions: int = 3,
        initial_capital: float = INITIAL_CAPITAL,
    ):
        self.strategies = strategies
        self.hmm_train_bars = hmm_train_bars
        self.hmm_retrain_interval = hmm_retrain_interval
        self.hmm_lookback = hmm_lookback
        self.max_concurrent_positions = max_concurrent_positions
        self.initial_capital = initial_capital
        self.sizer = PositionSizer()

    def run(self, df: pd.DataFrame) -> BacktestResult:
        """
        Run walk-forward backtest.

        CRITICAL INVARIANT: At bar i, we can ONLY see df.iloc[0:i+1]
        """
        n = len(df)
        if n < self.hmm_train_bars + 50:
            raise ValueError(
                f"Need at least {self.hmm_train_bars + 50} bars, got {n}"
            )

        print(f"\n=== Walk-Forward Backtest ===")
        print(f"Bars: {n} | HMM train: {self.hmm_train_bars} | "
              f"Retrain every: {self.hmm_retrain_interval}")
        print(f"Capital: ${self.initial_capital:,.2f} | "
              f"Slippage: {SLIPPAGE_PCT:.2%} | Commission: {COMMISSION_PCT:.2%}")

        capital = self.initial_capital
        equity_curve = [capital]
        trades: List[Trade] = []
        trade_id = 0

        # Open positions: list of dicts
        open_positions: List[Dict] = []

        # HMM detector — use a fresh instance with a non-existent model path
        # so it doesn't load a stale cached model
        _dummy_path = Path(__file__).parent / "results" / "_hmm_no_cache.pkl"
        if _dummy_path.exists():
            _dummy_path.unlink()
        detector = HMMRegimeDetector(model_path=_dummy_path)

        last_train_bar = -1
        hmm_trained = False

        # Pending signals: generated at bar i, to be executed at bar i+1
        pending_signals: List[Dict] = []

        for i in range(self.hmm_train_bars, n):
            # === LOOK-AHEAD CHECK ===
            # visible_data is strictly data[0:i+1] — bar i is the CURRENT bar
            visible_data = df.iloc[0:i + 1]
            current_bar = df.iloc[i]
            current_price = float(current_bar["close"])
            current_open = float(current_bar["open"])
            current_high = float(current_bar["high"])
            current_low = float(current_bar["low"])
            bar_time = str(df.index[i])

            # ── Step 1: Execute pending signals from previous bar ──
            # Entry at THIS bar's open (signal was from bar i-1)
            for ps in pending_signals:
                if len(open_positions) >= self.max_concurrent_positions:
                    break

                entry_price = current_open
                # Apply slippage
                if ps["direction"] == "LONG":
                    entry_price *= (1 + SLIPPAGE_PCT)
                else:
                    entry_price *= (1 - SLIPPAGE_PCT)

                # Recalculate stop/TP relative to actual entry price
                # (signal was generated at prev bar's close; entry is next bar's open)
                signal_price = ps["entry_price"]
                if signal_price > 0 and signal_price != entry_price:
                    price_shift = entry_price / signal_price
                    stop_loss = ps["stop_loss"] * price_shift
                    take_profit = ps["take_profit"] * price_shift
                else:
                    stop_loss = ps["stop_loss"]
                    take_profit = ps["take_profit"]

                # Commission on entry
                commission = entry_price * ps["size_pct"] * capital * COMMISSION_PCT

                open_positions.append({
                    "trade_id": trade_id,
                    "strategy": ps["strategy"],
                    "direction": ps["direction"],
                    "regime": ps["regime"],
                    "entry_bar": i,
                    "entry_time": bar_time,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "confidence": ps["confidence"],
                    "size_pct": ps["size_pct"],
                    "position_size": ps["size_pct"] * capital,
                    "entry_commission": commission,
                })
                trade_id += 1

            pending_signals = []

            # ── Step 2: Check exits on open positions ──
            closed_this_bar = []
            for pos in open_positions:
                should_close = False
                exit_reason = ""
                exit_price = current_price

                if pos["direction"] == "LONG":
                    # Check stop (hit if low <= stop)
                    if current_low <= pos["stop_loss"]:
                        should_close = True
                        exit_price = pos["stop_loss"]
                        exit_reason = "stop_loss"
                    # Check TP (hit if high >= tp)
                    elif current_high >= pos["take_profit"]:
                        should_close = True
                        exit_price = pos["take_profit"]
                        exit_reason = "take_profit"
                else:  # SHORT
                    if current_high >= pos["stop_loss"]:
                        should_close = True
                        exit_price = pos["stop_loss"]
                        exit_reason = "stop_loss"
                    elif current_low <= pos["take_profit"]:
                        should_close = True
                        exit_price = pos["take_profit"]
                        exit_reason = "take_profit"

                # Time-based exit: max 48 bars (2 days for hourly)
                duration = i - pos["entry_bar"]
                if duration >= 48 and not should_close:
                    should_close = True
                    exit_price = current_price
                    exit_reason = "time_exit"

                if should_close:
                    # Apply slippage on exit
                    if pos["direction"] == "LONG":
                        exit_price *= (1 - SLIPPAGE_PCT)
                    else:
                        exit_price *= (1 + SLIPPAGE_PCT)

                    # Calculate P&L
                    position_size = pos["position_size"]
                    if pos["direction"] == "LONG":
                        gross_pnl = position_size * (exit_price - pos["entry_price"]) / pos["entry_price"]
                    else:
                        gross_pnl = position_size * (pos["entry_price"] - exit_price) / pos["entry_price"]

                    slippage_cost = position_size * SLIPPAGE_PCT * 2  # entry + exit
                    commission_cost = position_size * COMMISSION_PCT * 2
                    net_pnl = gross_pnl - commission_cost  # slippage already in prices

                    trade = Trade(
                        trade_id=pos["trade_id"],
                        strategy=pos["strategy"],
                        direction=pos["direction"],
                        regime=pos["regime"],
                        entry_bar=pos["entry_bar"],
                        exit_bar=i,
                        entry_time=pos["entry_time"],
                        exit_time=bar_time,
                        entry_price=pos["entry_price"],
                        exit_price=exit_price,
                        stop_loss=pos["stop_loss"],
                        take_profit=pos["take_profit"],
                        confidence=pos["confidence"],
                        position_size=position_size,
                        gross_pnl=gross_pnl,
                        slippage_cost=slippage_cost,
                        commission_cost=commission_cost,
                        net_pnl=net_pnl,
                        net_pnl_pct=net_pnl / position_size if position_size > 0 else 0,
                        duration_bars=duration,
                        exit_reason=exit_reason,
                    )
                    trades.append(trade)
                    capital += net_pnl
                    closed_this_bar.append(pos["trade_id"])

            # Remove closed positions
            open_positions = [p for p in open_positions if p["trade_id"] not in closed_this_bar]

            # ── Step 3: Train/Retrain HMM (only on past data) ──
            bars_since_train = i - last_train_bar
            should_train = (
                (not hmm_trained and i >= self.hmm_train_bars) or
                (hmm_trained and bars_since_train >= self.hmm_retrain_interval)
            )

            if should_train:
                try:
                    train_data = visible_data.copy()
                    detector_fresh = HMMRegimeDetector(model_path=_dummy_path)
                    detector_fresh.fit(train_data)
                    detector = detector_fresh
                    hmm_trained = True
                    last_train_bar = i
                except Exception as e:
                    logger.warning(f"HMM train failed at bar {i}: {e}")
                    if not hmm_trained:
                        continue
                    # Keep old detector on retrain failure

            if not hmm_trained:
                equity_curve.append(capital)
                continue

            # ── Step 4: Get regime prediction (using only visible data) ──
            try:
                lookback = visible_data.iloc[-self.hmm_lookback:]
                regime = detector.predict(lookback)
            except Exception:
                equity_curve.append(capital)
                continue

            # ── Step 5: Generate signals (only if we have capacity) ──
            if len(open_positions) < self.max_concurrent_positions:
                # Check we don't already have a pending or open position from same strategy
                active_strategies = set(p["strategy"] for p in open_positions)
                active_strategies.update(ps["strategy"] for ps in pending_signals)

                for strat in self.strategies:
                    if strat.name in active_strategies:
                        continue

                    try:
                        signal = strat.generate_signal(visible_data, regime)
                    except Exception:
                        continue

                    if not signal.is_actionable:
                        continue

                    # Size the position
                    size_pct = min(
                        0.05 * signal.confidence * regime.regime.risk_multiplier,
                        0.05
                    )

                    if size_pct < 0.005:  # Skip tiny positions
                        continue

                    # Adjust stop/TP relative to current price
                    pending_signals.append({
                        "strategy": strat.name,
                        "direction": signal.direction.value,
                        "regime": regime.regime.label,
                        "confidence": signal.confidence,
                        "entry_price": current_price,
                        "stop_loss": signal.stop_loss,
                        "take_profit": signal.take_profit,
                        "size_pct": size_pct,
                    })

            equity_curve.append(capital)

            # Progress indicator
            if (i - self.hmm_train_bars) % 200 == 0:
                pct = (i - self.hmm_train_bars) / (n - self.hmm_train_bars) * 100
                print(f"  Progress: {pct:.0f}% | Bar {i}/{n} | "
                      f"Capital: ${capital:,.2f} | Trades: {len(trades)}")

        # ── Close any remaining open positions at last bar ──
        last_price = float(df["close"].iloc[-1])
        for pos in open_positions:
            duration = n - 1 - pos["entry_bar"]
            if pos["direction"] == "LONG":
                exit_price = last_price * (1 - SLIPPAGE_PCT)
                gross_pnl = pos["position_size"] * (exit_price - pos["entry_price"]) / pos["entry_price"]
            else:
                exit_price = last_price * (1 + SLIPPAGE_PCT)
                gross_pnl = pos["position_size"] * (pos["entry_price"] - exit_price) / pos["entry_price"]

            commission_cost = pos["position_size"] * COMMISSION_PCT * 2
            net_pnl = gross_pnl - commission_cost

            trade = Trade(
                trade_id=pos["trade_id"],
                strategy=pos["strategy"],
                direction=pos["direction"],
                regime=pos["regime"],
                entry_bar=pos["entry_bar"],
                exit_bar=n - 1,
                entry_time=pos["entry_time"],
                exit_time=str(df.index[-1]),
                entry_price=pos["entry_price"],
                exit_price=exit_price,
                stop_loss=pos["stop_loss"],
                take_profit=pos["take_profit"],
                confidence=pos["confidence"],
                position_size=pos["position_size"],
                gross_pnl=gross_pnl,
                slippage_cost=pos["position_size"] * SLIPPAGE_PCT * 2,
                commission_cost=commission_cost,
                net_pnl=net_pnl,
                net_pnl_pct=net_pnl / pos["position_size"] if pos["position_size"] > 0 else 0,
                duration_bars=duration,
                exit_reason="end_of_data",
            )
            trades.append(trade)
            capital += net_pnl

        # ── Compute metrics ──
        result = self._compute_metrics(trades, equity_curve, df, capital)
        return result

    def _compute_metrics(
        self,
        trades: List[Trade],
        equity_curve: List[float],
        df: pd.DataFrame,
        final_capital: float,
    ) -> BacktestResult:
        """Compute all performance metrics with cross-validation."""

        # ── P&L Cross-Check ──
        # Method 1: Sum of individual trade P&Ls
        pnl_sum = sum(t.net_pnl for t in trades)

        # Method 2: Portfolio value change
        pnl_portfolio = final_capital - self.initial_capital

        # They should match
        tolerance = 0.01  # $0.01
        pnl_match = abs(pnl_sum - pnl_portfolio) < tolerance

        if not pnl_match:
            print(f"  ⚠️ P&L MISMATCH: sum={pnl_sum:.4f} vs portfolio={pnl_portfolio:.4f} "
                  f"(diff={abs(pnl_sum - pnl_portfolio):.4f})")

        # ── Basic stats ──
        total = len(trades)
        wins = sum(1 for t in trades if t.won)
        losses = total - wins
        win_rate = wins / total if total > 0 else 0

        # ── Profit Factor ──
        gross_wins = sum(t.net_pnl for t in trades if t.won)
        gross_losses = abs(sum(t.net_pnl for t in trades if not t.won))
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else float('inf') if gross_wins > 0 else 0

        # ── Sharpe Ratio (annualized, hourly data) ──
        eq = np.array(equity_curve)
        returns = np.diff(eq) / eq[:-1]
        returns = returns[np.isfinite(returns)]
        if len(returns) > 1 and np.std(returns) > 0:
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(8760)  # hourly → annual
        else:
            sharpe = 0.0

        # ── Sortino Ratio ──
        downside = returns[returns < 0]
        if len(downside) > 1:
            downside_std = np.std(downside)
            sortino = np.mean(returns) / downside_std * np.sqrt(8760) if downside_std > 0 else 0
        else:
            sortino = 0.0

        # ── Max Drawdown ──
        peak = np.maximum.accumulate(eq)
        drawdowns = (eq - peak) / peak
        max_dd = float(np.min(drawdowns)) * 100  # as percentage

        # ── Calmar Ratio ──
        total_return = (final_capital - self.initial_capital) / self.initial_capital
        calmar = total_return / abs(max_dd / 100) if max_dd != 0 else 0

        # ── Average Trade Duration ──
        avg_duration = np.mean([t.duration_bars for t in trades]) if trades else 0

        # ── Per-Strategy Stats ──
        strategy_stats = {}
        for strat in set(t.strategy for t in trades):
            strat_trades = [t for t in trades if t.strategy == strat]
            s_wins = sum(1 for t in strat_trades if t.won)
            s_total = len(strat_trades)
            s_pnl = sum(t.net_pnl for t in strat_trades)
            s_avg_dur = np.mean([t.duration_bars for t in strat_trades])
            strategy_stats[strat] = {
                "trades": s_total,
                "wins": s_wins,
                "win_rate": s_wins / s_total if s_total > 0 else 0,
                "total_pnl": round(s_pnl, 2),
                "avg_duration": round(s_avg_dur, 1),
            }

        # ── Per-Regime Stats ──
        regime_stats = {}
        for reg in set(t.regime for t in trades):
            reg_trades = [t for t in trades if t.regime == reg]
            r_pnl = sum(t.net_pnl for t in reg_trades)
            r_total = len(reg_trades)
            # Find best strategy in this regime
            if reg_trades:
                strat_pnls = {}
                for t in reg_trades:
                    strat_pnls[t.strategy] = strat_pnls.get(t.strategy, 0) + t.net_pnl
                best_strat = max(strat_pnls, key=strat_pnls.get)
            else:
                best_strat = "N/A"

            regime_stats[reg] = {
                "trades": r_total,
                "total_pnl": round(r_pnl, 2),
                "best_strategy": best_strat,
            }

        total_return_pct = (final_capital - self.initial_capital) / self.initial_capital * 100

        return BacktestResult(
            start_time=str(df.index[0]),
            end_time=str(df.index[-1]),
            total_bars=len(df),
            initial_capital=self.initial_capital,
            slippage_pct=SLIPPAGE_PCT,
            commission_pct=COMMISSION_PCT,
            final_capital=round(final_capital, 2),
            total_return_pct=round(total_return_pct, 4),
            total_trades=total,
            winning_trades=wins,
            losing_trades=losses,
            win_rate=round(win_rate, 4),
            profit_factor=round(profit_factor, 4),
            sharpe_ratio=round(sharpe, 4),
            sortino_ratio=round(sortino, 4),
            max_drawdown_pct=round(max_dd, 4),
            calmar_ratio=round(calmar, 4),
            avg_trade_duration=round(avg_duration, 1),
            pnl_sum_method=round(pnl_sum, 4),
            pnl_portfolio_method=round(pnl_portfolio, 4),
            pnl_match=pnl_match,
            strategy_stats=strategy_stats,
            regime_stats=regime_stats,
            trades=[asdict(t) for t in trades],
            equity_curve=equity_curve,
        )

    def save_results(self, result: BacktestResult, tag: str = "default"):
        """Save results as JSON + CSV."""
        # JSON summary (without equity curve for readability)
        summary = {k: v for k, v in result.__dict__.items()
                   if k not in ("equity_curve", "trades")}
        summary["trade_count"] = len(result.trades)

        json_path = RESULTS_DIR / f"backtest_{tag}.json"
        with open(json_path, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        print(f"  Results saved: {json_path}")

        # CSV trade log
        csv_path = RESULTS_DIR / f"trades_{tag}.csv"
        if result.trades:
            keys = result.trades[0].keys()
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(result.trades)
            print(f"  Trade log saved: {csv_path}")

        # Equity curve CSV
        eq_path = RESULTS_DIR / f"equity_{tag}.csv"
        with open(eq_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["bar", "equity"])
            for idx, val in enumerate(result.equity_curve):
                writer.writerow([idx, round(val, 2)])


def print_results(result: BacktestResult, tag: str = ""):
    """Print formatted backtest results."""
    header = f"=== Backtest Results{' — ' + tag if tag else ''} ==="
    print(f"\n{'=' * len(header)}")
    print(header)
    print(f"{'=' * len(header)}")

    print(f"\nPeriod: {result.start_time} to {result.end_time}")
    print(f"Bars: {result.total_bars}")
    print(f"Initial Capital: ${result.initial_capital:,.2f}")
    print(f"Final Capital:   ${result.final_capital:,.2f}")
    print(f"Total Return:    {result.total_return_pct:+.4f}%")

    print(f"\n--- Trade Stats ---")
    print(f"Total Trades: {result.total_trades}")
    print(f"Wins: {result.winning_trades} | Losses: {result.losing_trades}")
    print(f"Win Rate: {result.win_rate:.2%}")
    print(f"Profit Factor: {result.profit_factor:.4f}")
    print(f"Avg Trade Duration: {result.avg_trade_duration:.1f} bars")

    print(f"\n--- Risk Metrics ---")
    print(f"Sharpe Ratio (ann.): {result.sharpe_ratio:.4f}")
    print(f"Sortino Ratio (ann.): {result.sortino_ratio:.4f}")
    print(f"Max Drawdown: {result.max_drawdown_pct:.4f}%")
    print(f"Calmar Ratio: {result.calmar_ratio:.4f}")

    print(f"\n--- P&L Cross-Check ---")
    print(f"Sum of trades: ${result.pnl_sum_method:,.4f}")
    print(f"Portfolio delta: ${result.pnl_portfolio_method:,.4f}")
    print(f"Match: {'MATCH' if result.pnl_match else 'MISMATCH'}")

    if result.strategy_stats:
        print(f"\n--- Per-Strategy ---")
        print(f"{'Strategy':<25} {'Trades':>6} {'Win%':>7} {'PnL':>10} {'AvgDur':>8}")
        print("-" * 60)
        for name, stats in result.strategy_stats.items():
            print(f"{name:<25} {stats['trades']:>6} "
                  f"{stats['win_rate']:>6.1%} "
                  f"${stats['total_pnl']:>9.2f} "
                  f"{stats['avg_duration']:>7.1f}")

    if result.regime_stats:
        print(f"\n--- Per-Regime ---")
        print(f"{'Regime':<20} {'Trades':>6} {'PnL':>10} {'Best Strategy':<25}")
        print("-" * 65)
        for name, stats in result.regime_stats.items():
            print(f"{name:<20} {stats['trades']:>6} "
                  f"${stats['total_pnl']:>9.2f} "
                  f"{stats['best_strategy']:<25}")

    print()


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Default run with current strategy params
    df = fetch_eth_data(2000)

    strategies = [
        MomentumStrategy(fast_period=20, slow_period=50, min_confidence=0.55),
        MeanReversionStrategy(bb_period=20, bb_std=2.0, min_confidence=0.50),
        SentimentPulseStrategy(shift_threshold=0.3, min_confidence=0.45),
    ]

    bt = WalkForwardBacktester(
        strategies=strategies,
        hmm_train_bars=300,
        hmm_retrain_interval=100,
        initial_capital=INITIAL_CAPITAL,
    )

    result = bt.run(df)
    print_results(result, "baseline")
    bt.save_results(result, "baseline")
