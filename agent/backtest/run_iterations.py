"""
AEGIS — Iteration Runner
Runs multiple backtest iterations, fixing issues found in each.
"""

import sys
import os
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, str(Path(__file__).parent.parent))

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from backtest.engine import (
    WalkForwardBacktester, fetch_eth_data, print_results,
    INITIAL_CAPITAL
)
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.sentiment_pulse import SentimentPulseStrategy
from strategies.cross_pool_arb import CrossPoolArbStrategy


def run_iteration(df, tag, strategies, description, hmm_train=300, hmm_retrain=100):
    print(f"\n{'='*70}")
    print(f"=== ITERATION: {tag} ===")
    print(f"Changes: {description}")
    print(f"{'='*70}")

    bt = WalkForwardBacktester(
        strategies=strategies,
        hmm_train_bars=hmm_train,
        hmm_retrain_interval=hmm_retrain,
        initial_capital=INITIAL_CAPITAL,
        max_concurrent_positions=3,
    )

    result = bt.run(df)
    print_results(result, tag)
    bt.save_results(result, tag)
    return result


def main():
    df = fetch_eth_data(2000)
    results = {}

    # ===== ITERATION 1: Baseline (original conservative) =====
    results["iter1_baseline"] = run_iteration(
        df, "iter1_baseline",
        strategies=[
            MomentumStrategy(fast_period=20, slow_period=50, min_confidence=0.55),
            MeanReversionStrategy(bb_period=20, bb_std=2.0, min_confidence=0.50),
            SentimentPulseStrategy(shift_threshold=0.3, min_confidence=0.45, use_synthetic=True),
        ],
        description="Baseline: 20/50 SMA, 2.0 BB std, 0.3 shift threshold"
    )

    # ===== ITERATION 2: Tuned thresholds + ADX =====
    results["iter2_tuned"] = run_iteration(
        df, "iter2_tuned",
        strategies=[
            MomentumStrategy(
                fast_period=10, slow_period=30,
                min_confidence=0.40, adx_threshold=20.0
            ),
            MeanReversionStrategy(
                bb_period=20, bb_std=1.5,
                min_confidence=0.40, band_proximity_pct=0.003
            ),
            SentimentPulseStrategy(
                shift_threshold=0.20, min_confidence=0.40, use_synthetic=True
            ),
        ],
        description="Tuned: shorter SMA, 1.5 BB, ADX filter, lower thresholds"
    )

    # ===== ITERATION 3: Fix MeanReversion — only fire at band touch/cross =====
    # Insight from iter2: MeanReversion is losing (-$69). 
    # Problem: Near-band entries before price crosses have poor R:R.
    # Fix: Only enter when price actually CROSSES the band (not just near it).
    results["iter3_mr_fixed"] = run_iteration(
        df, "iter3_mr_fixed",
        strategies=[
            MomentumStrategy(
                fast_period=10, slow_period=30,
                min_confidence=0.40, adx_threshold=20.0
            ),
            MeanReversionStrategy(
                bb_period=20, bb_std=1.5,
                min_confidence=0.44,
                band_proximity_pct=0.0,  # Only fire AT or BEYOND the band
                stop_atr_mult=1.2,       # Tighter stop
            ),
            SentimentPulseStrategy(
                shift_threshold=0.22, min_confidence=0.42, use_synthetic=True
            ),
        ],
        description="Fix MR: only enter at band cross (not proximity), tighter stops"
    )

    # ===== ITERATION 4: Momentum only in confirmed trend + remove bad arb =====
    # CrossPoolArb EMA proxy doesn't work — kill it.
    # SentimentPulse marginally profitable — keep with tighter params.
    # Focus momentum on actual trending regimes with ADX confirmation.
    results["iter4_quality"] = run_iteration(
        df, "iter4_quality",
        strategies=[
            MomentumStrategy(
                fast_period=10, slow_period=30,
                min_confidence=0.45,
                adx_threshold=25.0,  # Stronger trend required
                stop_atr_mult=1.8,
                tp_atr_mult=2.8,
            ),
            MeanReversionStrategy(
                bb_period=20, bb_std=1.5,
                min_confidence=0.44,
                band_proximity_pct=0.0,
                stop_atr_mult=1.2,
            ),
            SentimentPulseStrategy(
                shift_threshold=0.22, min_confidence=0.42,
                use_synthetic=True,
                stop_pct=0.012, take_profit_pct=0.025,
            ),
        ],
        description="Quality over quantity: stronger trend filter, no arb proxy",
        hmm_retrain=75,
    )

    # ===== ITERATION 5: Best config + conservative position sizing =====
    # Use best-performing param set, add portfolio-level risk management
    results["iter5_final"] = run_iteration(
        df, "iter5_final",
        strategies=[
            MomentumStrategy(
                fast_period=10, slow_period=30,
                min_confidence=0.45, adx_threshold=25.0,
                stop_atr_mult=1.8, tp_atr_mult=3.0,
                rsi_oversold=35.0, rsi_overbought=65.0,
            ),
            MeanReversionStrategy(
                bb_period=20, bb_std=1.5,
                min_confidence=0.46, band_proximity_pct=0.0,
                stop_atr_mult=1.2,
            ),
            SentimentPulseStrategy(
                shift_threshold=0.22, min_confidence=0.44,
                use_synthetic=True,
                stop_pct=0.012, take_profit_pct=0.028,
                min_abs_score=0.15,
            ),
        ],
        description="Final config: best-of-breed parameters with tighter quality filter",
        hmm_retrain=75,
    )

    # ===== SUMMARY =====
    print(f"\n{'='*80}")
    print("=== ITERATION SUMMARY ===")
    print(f"{'='*80}")
    print(f"\n{'Iteration':<25} {'Trades':>7} {'Win%':>7} {'Return%':>9} {'Sharpe':>8} {'MaxDD%':>8} {'PF':>7} {'PnL?':>6}")
    print("-" * 80)

    for tag, r in results.items():
        print(f"{tag:<25} {r.total_trades:>7} {r.win_rate:>6.1%} "
              f"{r.total_return_pct:>+8.2f}% {r.sharpe_ratio:>7.2f} "
              f"{r.max_drawdown_pct:>7.2f}% {r.profit_factor:>6.3f} "
              f"{'OK' if r.pnl_match else 'MISS':>6}")

    valid = {k: v for k, v in results.items() if v.total_trades >= 10}
    if valid:
        best = max(valid.items(), key=lambda x: x[1].sharpe_ratio)
        print(f"\nBest by Sharpe Ratio: {best[0]}")
        print(f"  Return: {best[1].total_return_pct:+.4f}%")
        print(f"  Sharpe: {best[1].sharpe_ratio:.4f}")
        print(f"  Win Rate: {best[1].win_rate:.1%}")
        print(f"  Max DD: {best[1].max_drawdown_pct:.4f}%")
        print(f"  Trades: {best[1].total_trades}")

    return results


if __name__ == "__main__":
    main()
