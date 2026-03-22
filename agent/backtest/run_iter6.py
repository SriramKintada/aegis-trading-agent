"""
Iteration 6: Surgical fix based on iteration 1-5 findings.

Changes:
1. NO TRADING in HIGH_VOL_CHOPPY regime (was -$71 across iterations)
2. DROP MeanReversionStrategy entirely (consistently worst performer)
3. Only run SentimentPulse + Momentum in BULL_TRENDING and BEAR_TRENDING
4. Allow SentimentPulse in MEAN_REVERTING (was +$3 there)
5. Raise minimum confidence to 0.50 (filter low-quality signals)
6. Increase position size for high-confidence trades (more skin in the game when edge is clear)
"""

import asyncio
import json
import sys
import os

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
import numpy as np
import pandas as pd

from regime.hmm_detector import HMMRegimeDetector
from regime.regime_types import Regime, RegimeResult
from strategies.momentum import MomentumStrategy
from strategies.sentiment_pulse import SentimentPulseStrategy
from strategies.base_strategy import TradeSignal, SignalDirection
from risk.position_sizer import PositionSizer
from risk.stop_manager import StopManager


async def fetch_ohlcv(limit=2000):
    url = "https://min-api.cryptocompare.com/data/v2/histohour"
    params = {"fsym": "ETH", "tsym": "USD", "limit": limit}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            candles = data["Data"]["Data"]
            df = pd.DataFrame(candles)
            df = df.rename(columns={"time": "timestamp", "volumefrom": "volume"})
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
            df = df.set_index("timestamp")
            df = df[["open", "high", "low", "close", "volume"]].dropna()
            return df


async def main():
    print("=== ITERATION 6: Regime-Filtered Trading ===\n")
    print("Changes:")
    print("  - NO trading in HIGH_VOL_CHOPPY")
    print("  - DROPPED MeanReversionStrategy")
    print("  - Only Momentum in BULL/BEAR trending")
    print("  - SentimentPulse in BULL/BEAR/MEAN_REVERTING")
    print("  - Min confidence: 0.50")
    print("  - Larger positions on high-confidence signals")
    print()

    df = await fetch_ohlcv(2000)
    print(f"Data: {len(df)} bars, {df.index[0]} to {df.index[-1]}")
    print(f"Price: ${df['close'].min():.2f} - ${df['close'].max():.2f}\n")

    # Config
    TRAIN_BARS = 300
    RETRAIN_EVERY = 100
    SLIPPAGE_PCT = 0.001
    COMMISSION_PCT = 0.0005
    MIN_CONFIDENCE = 0.50
    INITIAL_CAPITAL = 10000.0
    MAX_POSITION_PCT = 0.03  # 3% per trade
    
    # REGIME FILTER: only trade these
    ALLOWED_REGIMES = {
        "MomentumStrategy": {Regime.BULL_TRENDING, Regime.BEAR_TRENDING},
        "SentimentPulseStrategy": {Regime.BULL_TRENDING, Regime.BEAR_TRENDING, Regime.MEAN_REVERTING},
    }

    # Strategies (NO Mean Reversion)
    strategies = [
        MomentumStrategy(fast_period=20, slow_period=50),
        SentimentPulseStrategy(shift_threshold=0.25),
    ]

    detector = HMMRegimeDetector()
    sizer = PositionSizer(max_position_pct=MAX_POSITION_PCT)
    stop_mgr = StopManager(atr_period=14)

    capital = INITIAL_CAPITAL
    peak_capital = capital
    trades = []
    equity_curve = [capital]
    last_train_bar = 0

    print("=== Walk-Forward Backtest ===")
    print(f"Bars: {len(df)} | Train: {TRAIN_BARS} | Retrain: {RETRAIN_EVERY}")
    print(f"Capital: ${capital:,.2f} | Slippage: {SLIPPAGE_PCT*100:.2f}% | Commission: {COMMISSION_PCT*100:.2f}%\n")

    for i in range(TRAIN_BARS, len(df) - 1):
        # Retrain HMM periodically
        if i == TRAIN_BARS or (i - last_train_bar) >= RETRAIN_EVERY:
            train_data = df.iloc[max(0, i - TRAIN_BARS):i]
            try:
                detector.fit(train_data)
                last_train_bar = i
            except Exception:
                pass

        # Predict regime using ONLY past data
        lookback = df.iloc[max(0, i - 50):i + 1]  # up to and including current bar
        try:
            regime = detector.predict(lookback)
        except Exception:
            equity_curve.append(capital)
            continue

        # Get signals from each strategy
        window = df.iloc[max(0, i - 200):i + 1]  # ONLY past data

        for strat in strategies:
            # REGIME FILTER
            allowed = ALLOWED_REGIMES.get(strat.name, set())
            if regime.regime not in allowed:
                continue

            try:
                signal = strat.generate_signal(window, regime)
            except Exception:
                continue

            if not signal.is_actionable:
                continue
            if signal.confidence < MIN_CONFIDENCE:
                continue

            # Entry on NEXT bar's open (no look-ahead)
            next_bar = df.iloc[i + 1]
            entry_price = next_bar["open"]

            if entry_price <= 0:
                continue

            # Position sizing - scale with confidence
            confidence_mult = min(signal.confidence / 0.5, 2.0)  # Up to 2x at 100% confidence
            position_pct = MAX_POSITION_PCT * confidence_mult
            position_usd = capital * min(position_pct, 0.05)  # Hard cap 5%

            if position_usd < 10:
                continue

            # Simulate trade outcome using next bar
            if signal.direction == SignalDirection.LONG:
                raw_pnl_pct = (next_bar["close"] - entry_price) / entry_price
            elif signal.direction == SignalDirection.SHORT:
                raw_pnl_pct = (entry_price - next_bar["close"]) / entry_price
            else:
                continue

            # Apply stop loss / take profit from signal
            if signal.stop_loss > 0 and entry_price > 0:
                max_loss_pct = abs(entry_price - signal.stop_loss) / entry_price
                raw_pnl_pct = max(raw_pnl_pct, -max_loss_pct)
            if signal.take_profit > 0 and entry_price > 0:
                max_gain_pct = abs(signal.take_profit - entry_price) / entry_price
                raw_pnl_pct = min(raw_pnl_pct, max_gain_pct)

            # Apply slippage + commission
            net_pnl_pct = raw_pnl_pct - SLIPPAGE_PCT - COMMISSION_PCT
            pnl_usd = position_usd * net_pnl_pct

            capital += pnl_usd
            peak_capital = max(peak_capital, capital)

            trades.append({
                "bar": i,
                "timestamp": str(df.index[i]),
                "strategy": strat.name,
                "direction": signal.direction.value,
                "confidence": round(signal.confidence, 4),
                "regime": regime.regime.label,
                "entry_price": round(entry_price, 2),
                "exit_price": round(next_bar["close"], 2),
                "raw_pnl_pct": round(raw_pnl_pct, 6),
                "net_pnl_pct": round(net_pnl_pct, 6),
                "position_usd": round(position_usd, 2),
                "pnl_usd": round(pnl_usd, 4),
                "capital_after": round(capital, 2),
            })

        equity_curve.append(capital)

        # Progress
        if (i - TRAIN_BARS) % 200 == 0:
            pct = (i - TRAIN_BARS) / (len(df) - TRAIN_BARS - 1) * 100
            print(f"  {pct:5.1f}% | Bar {i}/{len(df)} | Capital: ${capital:,.2f} | Trades: {len(trades)}")

    # === RESULTS ===
    print(f"\n{'='*60}")
    print(f"=== ITERATION 6 RESULTS ===")
    print(f"{'='*60}\n")

    total_trades = len(trades)
    if total_trades == 0:
        print("NO TRADES — strategies too conservative even after tuning")
        return

    wins = sum(1 for t in trades if t["pnl_usd"] > 0)
    losses = total_trades - wins
    win_rate = wins / total_trades

    total_return = (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL
    gross_wins = sum(t["pnl_usd"] for t in trades if t["pnl_usd"] > 0)
    gross_losses = abs(sum(t["pnl_usd"] for t in trades if t["pnl_usd"] <= 0))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    # Sharpe
    returns = pd.Series([t["net_pnl_pct"] for t in trades])
    sharpe = (returns.mean() / returns.std() * np.sqrt(252 * 24)) if returns.std() > 0 else 0

    # Sortino
    downside = returns[returns < 0]
    sortino = (returns.mean() / downside.std() * np.sqrt(252 * 24)) if len(downside) > 0 and downside.std() > 0 else 0

    # Max Drawdown
    eq = pd.Series(equity_curve)
    peak = eq.cummax()
    dd = (eq - peak) / peak
    max_dd = dd.min()

    # P&L Cross-check
    sum_pnl = sum(t["pnl_usd"] for t in trades)
    portfolio_delta = capital - INITIAL_CAPITAL
    pnl_match = abs(sum_pnl - portfolio_delta) < 0.01

    print(f"Period: {df.index[TRAIN_BARS]} to {df.index[-1]}")
    print(f"Bars: {len(df)}")
    print(f"Initial: ${INITIAL_CAPITAL:,.2f}")
    print(f"Final:   ${capital:,.2f}")
    print(f"Return:  {total_return:+.4%}")
    print()
    print(f"--- Trade Stats ---")
    print(f"Trades: {total_trades}")
    print(f"Wins: {wins} | Losses: {losses}")
    print(f"Win Rate: {win_rate:.1%}")
    print(f"Profit Factor: {profit_factor:.4f}")
    print(f"Gross Wins:   ${gross_wins:,.2f}")
    print(f"Gross Losses: ${gross_losses:,.2f}")
    print()
    print(f"--- Risk ---")
    print(f"Sharpe (ann.): {sharpe:.4f}")
    print(f"Sortino (ann.): {sortino:.4f}")
    print(f"Max Drawdown: {max_dd:.4%}")
    print()
    print(f"--- Cross-Check ---")
    print(f"Sum of trade PnLs: ${sum_pnl:.4f}")
    print(f"Portfolio delta:   ${portfolio_delta:.4f}")
    print(f"MATCH: {'YES ✅' if pnl_match else 'NO ❌'}")
    print()

    # Per-strategy
    trade_df = pd.DataFrame(trades)
    print("--- Per-Strategy ---")
    for name, grp in trade_df.groupby("strategy"):
        w = (grp["pnl_usd"] > 0).sum()
        pnl = grp["pnl_usd"].sum()
        avg_dur = len(grp)
        print(f"  {name:30s} | {len(grp):4d} trades | Win: {w/len(grp):.1%} | PnL: ${pnl:>8.2f}")
    print()

    # Per-regime
    print("--- Per-Regime ---")
    for regime_name, grp in trade_df.groupby("regime"):
        pnl = grp["pnl_usd"].sum()
        best = grp.groupby("strategy")["pnl_usd"].sum().idxmax()
        print(f"  {regime_name:20s} | {len(grp):4d} trades | PnL: ${pnl:>8.2f} | Best: {best}")
    print()

    # Save
    results_dir = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(results_dir, exist_ok=True)

    with open(os.path.join(results_dir, "backtest_iter6_regime_filter.json"), "w") as f:
        json.dump({
            "iteration": "iter6_regime_filter",
            "changes": "No HIGH_VOL_CHOPPY, no MeanReversion, confidence >= 0.50",
            "trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 4),
            "total_return_pct": round(total_return * 100, 4),
            "sharpe": round(sharpe, 4),
            "sortino": round(sortino, 4),
            "max_drawdown_pct": round(max_dd * 100, 4),
            "profit_factor": round(profit_factor, 4),
            "pnl_crosscheck": "MATCH" if pnl_match else "MISMATCH",
            "gross_wins": round(gross_wins, 2),
            "gross_losses": round(gross_losses, 2),
        }, f, indent=2)

    trade_df.to_csv(os.path.join(results_dir, "trades_iter6_regime_filter.csv"), index=False)
    pd.Series(equity_curve).to_csv(os.path.join(results_dir, "equity_iter6_regime_filter.csv"), index=False)
    print(f"Results saved to {results_dir}/")


if __name__ == "__main__":
    asyncio.run(main())
