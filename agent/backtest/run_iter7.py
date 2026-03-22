"""
Iteration 7: Fix the REAL problem — transaction cost drag.

Key insight from deep analysis:
  - Gross PnL is POSITIVE (+$16.41) — strategies DO work
  - 331 trades x $0.65 avg cost = $213 cost drag kills it
  - 209/331 trades hit stop loss — stops too tight
  - Confidence >= 0.80 is profitable (Sharpe +1.74)
  
Changes:
  1. Trade LESS: only confidence >= 0.75
  2. Widen stops: ATR x 3.0 (was 2.0) — stop bleeding from premature exits
  3. Only BEAR/BULL trending (no CHOPPY, no MEAN_REVERTING)
  4. Larger position sizes on fewer, higher-quality trades
  5. Skip MeanReversion entirely
"""

import asyncio
import json
import sys
import os
import numpy as np
import pandas as pd
import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from regime.hmm_detector import HMMRegimeDetector
from regime.regime_types import Regime
from strategies.momentum import MomentumStrategy
from strategies.sentiment_pulse import SentimentPulseStrategy
from strategies.base_strategy import SignalDirection


async def fetch_ohlcv(limit=2000):
    url = "https://min-api.cryptocompare.com/data/v2/histohour"
    params = {"fsym": "ETH", "tsym": "USD", "limit": limit}
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params=params) as r:
            data = await r.json()
            candles = data["Data"]["Data"]
            df = pd.DataFrame(candles)
            df = df.rename(columns={"time": "timestamp", "volumefrom": "volume"})
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
            df = df.set_index("timestamp")
            return df[["open", "high", "low", "close", "volume"]].dropna()


def compute_atr(df, period=14):
    h, l, c = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


async def main():
    print("=" * 70)
    print("=== ITERATION 7: Fewer Trades, Wider Stops, Higher Quality ===")
    print("=" * 70)
    print()
    print("Changes from iter 5:")
    print("  1. Min confidence: 0.75 (was ~0.40)")
    print("  2. ATR stop multiplier: 3.0x (was 2.0x)")
    print("  3. Only BULL_TRENDING + BEAR_TRENDING regimes")
    print("  4. No MeanReversion")
    print("  5. Position size: 4% of portfolio (was ~1.5%)")
    print()

    df = await fetch_ohlcv(2000)
    print(f"Data: {len(df)} bars | {df.index[0]} to {df.index[-1]}")
    print(f"ETH: ${df['close'].min():.2f} - ${df['close'].max():.2f} | Now: ${df['close'].iloc[-1]:.2f}")
    print()

    # Parameters
    TRAIN = 300
    RETRAIN = 100
    SLIPPAGE = 0.001
    COMMISSION = 0.0005
    MIN_CONF = 0.75
    CAPITAL_INIT = 10000.0
    POS_PCT = 0.04
    ATR_STOP_MULT = 3.0
    ATR_TP_MULT = 4.5  # 1.5x risk:reward
    ALLOWED = {Regime.BULL_TRENDING, Regime.BEAR_TRENDING}

    strategies = [
        MomentumStrategy(fast_period=20, slow_period=50),
        SentimentPulseStrategy(shift_threshold=0.25),
    ]
    detector = HMMRegimeDetector()

    capital = CAPITAL_INIT
    peak = capital
    trades = []
    equity = [capital]
    last_train = 0

    for i in range(TRAIN, len(df) - 1):
        # Retrain
        if i == TRAIN or (i - last_train) >= RETRAIN:
            try:
                detector.fit(df.iloc[max(0, i - TRAIN):i])
                last_train = i
            except Exception:
                pass

        # Regime
        try:
            regime = detector.predict(df.iloc[max(0, i - 50):i + 1])
        except Exception:
            equity.append(capital)
            continue

        # Skip if not trending
        if regime.regime not in ALLOWED:
            equity.append(capital)
            continue

        window = df.iloc[max(0, i - 200):i + 1]
        atr_series = compute_atr(window)
        current_atr = atr_series.iloc[-1] if len(atr_series) > 0 else 0

        for strat in strategies:
            try:
                sig = strat.generate_signal(window, regime)
            except Exception:
                continue

            if not sig.is_actionable or sig.confidence < MIN_CONF:
                continue

            # Entry on next bar open
            next_bar = df.iloc[i + 1]
            entry = next_bar["open"]
            if entry <= 0 or current_atr <= 0:
                continue

            # Custom wider stops
            if sig.direction == SignalDirection.LONG:
                stop = entry - ATR_STOP_MULT * current_atr
                tp = entry + ATR_TP_MULT * current_atr
            elif sig.direction == SignalDirection.SHORT:
                stop = entry + ATR_STOP_MULT * current_atr
                tp = entry - ATR_TP_MULT * current_atr
            else:
                continue

            # Position size
            pos_usd = capital * POS_PCT
            if pos_usd < 20:
                continue

            # Simulate using next bar's OHLC (conservative: check if stop/TP hit)
            bar_h = next_bar["high"]
            bar_l = next_bar["low"]
            bar_c = next_bar["close"]

            hit_stop = False
            hit_tp = False

            if sig.direction == SignalDirection.LONG:
                if bar_l <= stop:
                    hit_stop = True
                    exit_price = stop
                elif bar_h >= tp:
                    hit_tp = True
                    exit_price = tp
                else:
                    exit_price = bar_c
                raw_pnl = (exit_price - entry) / entry
            else:  # SHORT
                if bar_h >= stop:
                    hit_stop = True
                    exit_price = stop
                elif bar_l <= tp:
                    hit_tp = True
                    exit_price = tp
                else:
                    exit_price = bar_c
                raw_pnl = (entry - exit_price) / entry

            net_pnl = raw_pnl - SLIPPAGE - COMMISSION
            pnl_usd = pos_usd * net_pnl
            capital += pnl_usd
            peak = max(peak, capital)

            exit_reason = "stop_loss" if hit_stop else ("take_profit" if hit_tp else "bar_close")

            trades.append({
                "bar": i,
                "time": str(df.index[i]),
                "strategy": strat.name,
                "direction": sig.direction.value,
                "confidence": round(sig.confidence, 4),
                "regime": regime.regime.label,
                "entry": round(entry, 2),
                "exit": round(exit_price, 2),
                "stop": round(stop, 2),
                "tp": round(tp, 2),
                "atr": round(current_atr, 2),
                "raw_pnl_pct": round(raw_pnl, 6),
                "net_pnl_pct": round(net_pnl, 6),
                "pos_usd": round(pos_usd, 2),
                "pnl_usd": round(pnl_usd, 4),
                "exit_reason": exit_reason,
                "capital": round(capital, 2),
            })

        equity.append(capital)

        if (i - TRAIN) % 200 == 0:
            pct = (i - TRAIN) / (len(df) - TRAIN - 1) * 100
            print(f"  {pct:5.1f}% | Bar {i}/{len(df)} | ${capital:,.2f} | {len(trades)} trades")

    # === RESULTS ===
    print()
    print("=" * 70)
    print("=== ITERATION 7 RESULTS ===")
    print("=" * 70)
    print()

    n = len(trades)
    if n == 0:
        print("NO TRADES. Need to lower confidence threshold.")
        return

    tdf = pd.DataFrame(trades)
    wins = (tdf["pnl_usd"] > 0).sum()
    losses = n - wins
    wr = wins / n
    total_ret = (capital - CAPITAL_INIT) / CAPITAL_INIT
    gross_w = tdf[tdf["pnl_usd"] > 0]["pnl_usd"].sum()
    gross_l = abs(tdf[tdf["pnl_usd"] <= 0]["pnl_usd"].sum())
    pf = gross_w / gross_l if gross_l > 0 else float("inf")

    rets = tdf["net_pnl_pct"]
    sharpe = rets.mean() / rets.std() * np.sqrt(252 * 24) if rets.std() > 0 else 0
    down = rets[rets < 0]
    sortino = rets.mean() / down.std() * np.sqrt(252 * 24) if len(down) > 0 and down.std() > 0 else 0

    eq = pd.Series(equity)
    max_dd = ((eq - eq.cummax()) / eq.cummax()).min()

    # Cross-check
    sum_pnl = tdf["pnl_usd"].sum()
    delta = capital - CAPITAL_INIT
    match = abs(sum_pnl - delta) < 0.02

    print(f"Period: {df.index[TRAIN]} to {df.index[-1]}")
    print(f"Initial: ${CAPITAL_INIT:,.2f} | Final: ${capital:,.2f}")
    print(f"Return: {total_ret:+.4%}")
    print()
    print(f"Trades: {n} | Wins: {wins} | Losses: {losses} | WR: {wr:.1%}")
    print(f"Profit Factor: {pf:.3f}")
    print(f"Gross Wins: ${gross_w:,.2f} | Gross Losses: ${gross_l:,.2f}")
    print()
    print(f"Sharpe (ann.): {sharpe:.4f}")
    print(f"Sortino (ann.): {sortino:.4f}")
    print(f"Max Drawdown: {max_dd:.4%}")
    print()
    print(f"Cross-check: sum=${sum_pnl:.4f} delta=${delta:.4f} {'MATCH' if match else 'MISMATCH'}")
    print()

    print("--- Per Strategy ---")
    for name, g in tdf.groupby("strategy"):
        w = (g["pnl_usd"] > 0).sum()
        pnl = g["pnl_usd"].sum()
        print(f"  {name:30s} | {len(g):3d} trades | WR:{w/len(g):.1%} | ${pnl:>8.2f}")
    print()

    print("--- Per Regime ---")
    for reg, g in tdf.groupby("regime"):
        pnl = g["pnl_usd"].sum()
        print(f"  {reg:20s} | {len(g):3d} trades | ${pnl:>8.2f}")
    print()

    print("--- Exit Reasons ---")
    for reason, g in tdf.groupby("exit_reason"):
        pnl = g["pnl_usd"].sum()
        print(f"  {reason:15s} | {len(g):3d} trades | ${pnl:>8.2f}")
    print()

    # Compare to iter 5
    print("--- vs Iteration 5 ---")
    print(f"  Iter 5: 331 trades | WR:34.7% | -0.55% | Sharpe:-1.46")
    print(f"  Iter 7: {n:3d} trades | WR:{wr:.1%}  | {total_ret:+.2%}  | Sharpe:{sharpe:.2f}")
    print(f"  {'IMPROVED' if total_ret > -0.0055 else 'WORSE'}")

    # Save
    rd = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(rd, exist_ok=True)
    tdf.to_csv(os.path.join(rd, "trades_iter7.csv"), index=False)
    pd.Series(equity).to_csv(os.path.join(rd, "equity_iter7.csv"), index=False)
    with open(os.path.join(rd, "backtest_iter7.json"), "w") as f:
        json.dump({"trades": n, "win_rate": round(wr, 4), "return_pct": round(total_ret * 100, 4),
                    "sharpe": round(sharpe, 4), "max_dd": round(max_dd * 100, 4),
                    "profit_factor": round(pf, 4), "crosscheck": "MATCH" if match else "MISMATCH"}, f, indent=2)
    print(f"\nSaved to {rd}/")


if __name__ == "__main__":
    asyncio.run(main())
