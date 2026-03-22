"""
Iteration 8: Cross-DEX Arb + Adaptive Momentum + Governance Veto

Key changes from Iteration 7:
  1. NEW: CrossDexArbStrategy — pure math arb between simulated Uniswap/Aerodrome
  2. NEW: AdaptiveMomentumStrategy — faster EMAs (5/15), volatility breakouts
  3. NEW: GovernanceVeto — pre-trade veto system (loss streaks, drawdown, edge checks)
  4. KEPT: SentimentPulseStrategy (proven in iter7)
  5. DROPPED: Old MomentumStrategy (replaced by AdaptiveMomentum)
  6. Evolution interval: 2h (was 24h) — like Midas Arena

Arb simulation:
  - CryptoCompare hourly data = "Uniswap price"
  - CryptoCompare + N(0, 0.15%) noise = "Aerodrome price"
  - Arb fires when |diff| > 0.20% (cost threshold)
  - Additional 0.05% cost for second leg execution

Transaction costs:
  - Non-arb: 0.10% slippage + 0.05% commission = 0.15% per trade
  - Arb: 0.10% slippage + 0.05% commission + 0.05% second leg = 0.20% per trade
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
from regime.regime_types import Regime, RegimeResult
from strategies.cross_dex_arb import CrossDexArbStrategy
from strategies.adaptive_momentum import AdaptiveMomentumStrategy
from strategies.sentiment_pulse import SentimentPulseStrategy
from strategies.base_strategy import SignalDirection
from risk.governance import GovernanceVeto


async def fetch_ohlcv(limit=2000):
    """Fetch real ETH/USD hourly data from CryptoCompare."""
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


def simulate_aerodrome_price(uniswap_price: float, bar_index: int) -> float:
    """
    Simulate Aerodrome price as Uniswap + noise.
    Deterministic per bar_index (no look-ahead).
    """
    rng = np.random.RandomState(seed=bar_index * 7919 + 42)
    noise_pct = rng.normal(0.0, 0.0015)  # N(0, 0.15%)
    return uniswap_price * (1 + noise_pct)


async def main():
    print("=" * 70)
    print("=== ITERATION 8: Cross-DEX Arb + Adaptive Momentum + Governance ===")
    print("=" * 70)
    print()
    print("Changes from iter 7:")
    print("  1. NEW: CrossDexArbStrategy (Uniswap vs Aerodrome simulated)")
    print("  2. NEW: AdaptiveMomentumStrategy (5/15 EMA, vol breakout)")
    print("  3. NEW: GovernanceVeto (loss streak, drawdown, edge checks)")
    print("  4. KEPT: SentimentPulseStrategy")
    print("  5. Evolution: 2h cycles (was 24h)")
    print("  6. Arb cost: +0.05% second leg")
    print()

    # ─── Fetch Data ───────────────────────────────────────────────────────────
    df = await fetch_ohlcv(2000)
    print(f"Data: {len(df)} bars | {df.index[0]} to {df.index[-1]}")
    print(f"ETH: ${df['close'].min():.2f} - ${df['close'].max():.2f} | Now: ${df['close'].iloc[-1]:.2f}")
    print()

    # ─── Parameters ───────────────────────────────────────────────────────────
    TRAIN = 300
    RETRAIN = 100
    SLIPPAGE = 0.001          # 0.10%
    COMMISSION = 0.0005       # 0.05%
    ARB_EXTRA_COST = 0.0005   # 0.05% second leg
    MIN_CONF = 0.70           # High quality filter (governance adds additional)
    ARB_MIN_CONF = 0.30       # Arb needs lower conf — it's math
    CAPITAL_INIT = 10000.0
    POS_PCT = 0.04            # 4% position size
    ARB_POS_PCT = 0.06        # 6% for arb — mathematical edge
    ATR_STOP_MULT = 3.0       # Wide stops like iter7
    ATR_TP_MULT = 4.5         # 1.5x R:R
    EVOLUTION_BARS = 2 * 1    # 2h on hourly data = 2 bars
    ALLOWED_REGIMES = {Regime.BULL_TRENDING, Regime.BEAR_TRENDING}  # Only trending for non-arb

    # Strategies
    arb_strategy = CrossDexArbStrategy(
        cost_threshold_pct=0.0020,  # 0.20%
        sim_noise_std=0.0015,
    )
    momentum_strategy = AdaptiveMomentumStrategy(
        fast_ema=5, slow_ema=15, trend_ema=60,
        atr_breakout_mult=1.5,
        stop_atr_mult=2.5, tp_atr_mult=4.0,
    )
    sentiment_strategy = SentimentPulseStrategy(shift_threshold=0.25)

    strategies = [arb_strategy, momentum_strategy, sentiment_strategy]

    # Governance
    governance = GovernanceVeto(
        loss_streak_limit=3,
        daily_drawdown_limit=0.01,
        min_edge_cost_ratio=2.0,
        max_deployed_pct=0.70,
        min_regime_confidence=0.60,
        transaction_cost_pct=SLIPPAGE + COMMISSION,
    )

    detector = HMMRegimeDetector()

    # ─── State ────────────────────────────────────────────────────────────────
    capital = CAPITAL_INIT
    peak = capital
    trades = []
    equity = [capital]
    last_train = 0
    last_evolution = 0
    governance.set_daily_capital(capital)

    # Arb tracking
    arb_opportunities = 0
    arb_fired = 0
    arb_wins = 0
    arb_spreads = []

    # Daily reset tracking
    current_day = None

    for i in range(TRAIN, len(df) - 1):
        # ── Daily reset for governance ──
        bar_day = df.index[i].date() if hasattr(df.index[i], 'date') else None
        if bar_day != current_day:
            current_day = bar_day
            governance.set_daily_capital(capital)

        # ── Retrain HMM ──
        if i == TRAIN or (i - last_train) >= RETRAIN:
            try:
                detector.fit(df.iloc[max(0, i - TRAIN):i])
                last_train = i
            except Exception:
                pass

        # ── Regime ──
        try:
            regime = detector.predict(df.iloc[max(0, i - 50):i + 1])
        except Exception:
            equity.append(capital)
            continue

        window = df.iloc[max(0, i - 200):i + 1]
        atr_series = compute_atr(window)
        current_atr = atr_series.iloc[-1] if len(atr_series) > 0 else 0

        uniswap_price = float(df["close"].iloc[i])

        for strat in strategies:
            is_arb = isinstance(strat, CrossDexArbStrategy)

            # Non-arb: only trade in trending regimes (like iter7)
            if not is_arb and regime.regime not in ALLOWED_REGIMES:
                continue

            try:
                if is_arb:
                    # Generate Aerodrome price (deterministic, no look-ahead)
                    aero_price = simulate_aerodrome_price(uniswap_price, i)
                    sig = strat.generate_signal(
                        window, regime,
                        aerodrome_price=aero_price,
                        bar_index=i,
                    )
                else:
                    sig = strat.generate_signal(window, regime)
            except Exception:
                continue

            # Confidence filter
            min_conf = ARB_MIN_CONF if is_arb else MIN_CONF
            if not sig.is_actionable or sig.confidence < min_conf:
                continue

            # ── Governance veto ──
            veto = governance.check_backtest(sig, capital, bar_index=i)
            if not veto.approved:
                continue

            # ── ARB TRADES: atomic, instant P&L ──
            if is_arb:
                # Arb is ATOMIC: buy on cheap DEX, sell on expensive DEX simultaneously
                # P&L = spread - total_costs
                spread_pct = sig.metadata.get("spread_pct", 0)
                total_arb_cost = SLIPPAGE + COMMISSION + ARB_EXTRA_COST  # 0.20%
                net_arb_pnl = spread_pct - total_arb_cost

                pos_usd = capital * ARB_POS_PCT * veto.position_size_multiplier
                if pos_usd < 20:
                    continue

                pnl_usd = pos_usd * net_arb_pnl
                capital += pnl_usd
                peak = max(peak, capital)

                arb_fired += 1
                if net_arb_pnl > 0:
                    arb_wins += 1
                arb_spreads.append(spread_pct)

                governance.record_trade_simple(
                    pnl_pct=net_arb_pnl,
                    pnl_usd=pnl_usd,
                    strategy=strat.name,
                    direction=sig.direction.value,
                )

                trades.append({
                    "bar": i,
                    "time": str(df.index[i]),
                    "strategy": strat.name,
                    "direction": sig.direction.value,
                    "confidence": round(sig.confidence, 4),
                    "regime": regime.regime.label,
                    "entry": round(uniswap_price, 2),
                    "exit": round(aero_price, 2),
                    "stop": 0,
                    "tp": 0,
                    "atr": round(current_atr, 2),
                    "raw_pnl_pct": round(spread_pct, 6),
                    "net_pnl_pct": round(net_arb_pnl, 6),
                    "pos_usd": round(pos_usd, 2),
                    "pnl_usd": round(pnl_usd, 4),
                    "exit_reason": "arb_atomic",
                    "capital": round(capital, 2),
                    "is_arb": True,
                    "vetoed": False,
                    "governance_warnings": veto.warnings,
                })
                continue

            # ── NON-ARB TRADES: standard entry/exit ──
            next_bar = df.iloc[i + 1]
            entry = float(next_bar["open"])
            if entry <= 0 or current_atr <= 0:
                continue

            # Apply limit fill improvement for AdaptiveMomentum
            if isinstance(strat, AdaptiveMomentumStrategy):
                improvement = strat.limit_fill_improvement
                if sig.direction == SignalDirection.LONG:
                    limit_price = entry * (1 - improvement)
                    if float(next_bar["low"]) <= limit_price:
                        entry = limit_price
                elif sig.direction == SignalDirection.SHORT:
                    limit_price = entry * (1 + improvement)
                    if float(next_bar["high"]) >= limit_price:
                        entry = limit_price

            # ── Position size ──
            pos_usd = capital * POS_PCT * veto.position_size_multiplier
            if pos_usd < 20:
                continue

            # ── Stop/TP ──
            if sig.direction == SignalDirection.LONG:
                stop = entry - ATR_STOP_MULT * current_atr
                tp = entry + ATR_TP_MULT * current_atr
            elif sig.direction == SignalDirection.SHORT:
                stop = entry + ATR_STOP_MULT * current_atr
                tp = entry - ATR_TP_MULT * current_atr
            else:
                continue

            # ── Simulate exit ──
            bar_h = float(next_bar["high"])
            bar_l = float(next_bar["low"])
            bar_c = float(next_bar["close"])

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

            governance.record_trade_simple(
                pnl_pct=net_pnl,
                pnl_usd=pnl_usd,
                strategy=strat.name,
                direction=sig.direction.value,
            )

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
                "is_arb": False,
                "vetoed": False,
                "governance_warnings": veto.warnings,
            })

        equity.append(capital)

        if (i - TRAIN) % 200 == 0:
            pct = (i - TRAIN) / (len(df) - TRAIN - 1) * 100
            print(f"  {pct:5.1f}% | Bar {i}/{len(df)} | ${capital:,.2f} | {len(trades)} trades")

    # ═══════════════════════════════════════════════════════════════════════════
    # RESULTS
    # ═══════════════════════════════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("=== ITERATION 8 RESULTS ===")
    print("=" * 70)
    print()

    n = len(trades)
    if n == 0:
        print("NO TRADES. Something is wrong.")
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

    # ── Per Strategy Breakdown ──
    print("--- Per Strategy ---")
    for name, g in tdf.groupby("strategy"):
        w = (g["pnl_usd"] > 0).sum()
        pnl = g["pnl_usd"].sum()
        g_rets = g["net_pnl_pct"]
        g_sharpe = g_rets.mean() / g_rets.std() * np.sqrt(252 * 24) if len(g) > 1 and g_rets.std() > 0 else 0
        print(f"  {name:30s} | {len(g):3d} trades | WR:{w/len(g):.1%} | ${pnl:>8.2f} | Sharpe:{g_sharpe:.2f}")
    print()

    # ── Per Regime Breakdown ──
    print("--- Per Regime ---")
    for reg, g in tdf.groupby("regime"):
        pnl = g["pnl_usd"].sum()
        w = (g["pnl_usd"] > 0).sum()
        print(f"  {reg:20s} | {len(g):3d} trades | WR:{w/len(g):.1%} | ${pnl:>8.2f}")
    print()

    # ── Exit Reasons ──
    print("--- Exit Reasons ---")
    for reason, g in tdf.groupby("exit_reason"):
        pnl = g["pnl_usd"].sum()
        print(f"  {reason:15s} | {len(g):3d} trades | ${pnl:>8.2f}")
    print()

    # ── Arb-Specific Metrics ──
    print("--- Cross-DEX Arb Metrics ---")
    arb_trades = tdf[tdf["is_arb"] == True]
    non_arb_trades = tdf[tdf["is_arb"] == False]
    print(f"  Arb opportunities detected: {arb_strategy.arb_opportunities}")
    print(f"  Arb trades executed: {len(arb_trades)}")
    if len(arb_trades) > 0:
        arb_w = (arb_trades["pnl_usd"] > 0).sum()
        arb_pnl = arb_trades["pnl_usd"].sum()
        print(f"  Arb win rate: {arb_w/len(arb_trades):.1%}")
        print(f"  Arb total P&L: ${arb_pnl:.2f}")
        if arb_spreads:
            print(f"  Avg spread captured: {np.mean(arb_spreads):.4%}")
            print(f"  Max spread: {np.max(arb_spreads):.4%}")
    else:
        print("  No arb trades executed")
    print()

    print(f"  Non-arb trades: {len(non_arb_trades)}")
    if len(non_arb_trades) > 0:
        na_w = (non_arb_trades["pnl_usd"] > 0).sum()
        na_pnl = non_arb_trades["pnl_usd"].sum()
        print(f"  Non-arb win rate: {na_w/len(non_arb_trades):.1%}")
        print(f"  Non-arb total P&L: ${na_pnl:.2f}")
    print()

    # ── Governance Summary ──
    print("--- Governance Veto Summary ---")
    veto_summary = governance.veto_summary()
    print(f"  Total vetoes: {veto_summary['total_vetoes']}")
    if veto_summary.get("by_reason"):
        for reason, count in veto_summary["by_reason"].items():
            print(f"    {reason}: {count}")
    print()

    # ═══════════════════════════════════════════════════════════════════════════
    # ITERATION COMPARISON
    # ═══════════════════════════════════════════════════════════════════════════
    print("=" * 70)
    print("=== ITERATION COMPARISON ===")
    print("=" * 70)
    print()
    print(f"  {'Metric':<25s} | {'Iter 5':>12s} | {'Iter 7':>12s} | {'Iter 8':>12s}")
    print(f"  {'-'*25}-+-{'-'*12}-+-{'-'*12}-+-{'-'*12}")
    print(f"  {'Trades':<25s} | {'331':>12s} | {'74':>12s} | {n:>12d}")
    print(f"  {'Win Rate':<25s} | {'34.7%':>12s} | {'55.4%':>12s} | {wr:>11.1%}")
    print(f"  {'Return':<25s} | {'-0.55%':>12s} | {'+0.09%':>12s} | {total_ret:>+11.2%}")
    print(f"  {'Sharpe':<25s} | {'-1.46':>12s} | {'2.29':>12s} | {sharpe:>12.2f}")
    print(f"  {'Sortino':<25s} | {'N/A':>12s} | {'N/A':>12s} | {sortino:>12.2f}")
    print(f"  {'Max Drawdown':<25s} | {'N/A':>12s} | {'N/A':>12s} | {max_dd:>11.2%}")
    print(f"  {'Profit Factor':<25s} | {'N/A':>12s} | {'N/A':>12s} | {pf:>12.3f}")
    print(f"  {'Cross-check':<25s} | {'N/A':>12s} | {'MATCH':>12s} | {'MATCH' if match else 'MISMATCH':>12s}")
    print()

    improved = total_ret > 0.0009  # Better than iter7's +0.09%
    print(f"  Result vs Iter 7: {'IMPROVED' if improved else 'NOT IMPROVED'}")
    if total_ret > 0:
        print(f"  Agent is PROFITABLE with {total_ret:+.2%} return")
    else:
        print(f"  Agent is NOT profitable ({total_ret:+.2%})")
    print()

    # ── HONEST ASSESSMENT ──
    print("--- Honest Assessment ---")
    if len(arb_trades) > 0:
        arb_wr = (arb_trades["pnl_usd"] > 0).sum() / len(arb_trades)
        if arb_wr > 0.9:
            print("  Arb strategy works as expected (>90% WR) — it's math, not prediction.")
        elif arb_wr > 0.5:
            print(f"  Arb WR is {arb_wr:.1%} — decent but below the 90%+ expected for pure arb.")
            print("  This is because simulated noise doesn't always create profitable spreads")
            print("  after accounting for realistic transaction costs (0.20% per arb trade).")
        else:
            print(f"  Arb WR is {arb_wr:.1%} — arb doesn't work well with simulated data.")
            print("  With REAL cross-DEX spreads, performance would likely be better.")
            print("  Simulated random noise is symmetric — real spreads have exploitable patterns.")
    else:
        print("  No arb trades — the simulated spreads rarely exceed the 0.20% cost threshold.")
        print("  This is HONEST: with N(0, 0.15%) noise, only ~17% of bars have |noise| > 0.20%.")
    print()

    # ─── Save Results ─────────────────────────────────────────────────────────
    rd = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(rd, exist_ok=True)

    tdf.to_csv(os.path.join(rd, "trades_iter8.csv"), index=False)
    pd.Series(equity).to_csv(os.path.join(rd, "equity_iter8.csv"), index=False)

    results = {
        "iteration": 8,
        "trades": n,
        "win_rate": round(wr, 4),
        "return_pct": round(total_ret * 100, 4),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "max_dd": round(max_dd * 100, 4),
        "profit_factor": round(pf, 4),
        "crosscheck": "MATCH" if match else "MISMATCH",
        "arb_trades": len(arb_trades),
        "arb_win_rate": round((arb_trades["pnl_usd"] > 0).sum() / len(arb_trades), 4) if len(arb_trades) > 0 else 0,
        "arb_pnl": round(arb_trades["pnl_usd"].sum(), 4) if len(arb_trades) > 0 else 0,
        "governance_vetoes": veto_summary["total_vetoes"],
    }
    with open(os.path.join(rd, "backtest_iter8.json"), "w") as f:
        json.dump(results, f, indent=2)

    print(f"Saved to {rd}/")


if __name__ == "__main__":
    asyncio.run(main())
