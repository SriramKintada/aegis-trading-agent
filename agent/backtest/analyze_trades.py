"""Deep analysis of why trades are losing."""
import pandas as pd
import numpy as np
import os

results_dir = os.path.join(os.path.dirname(__file__), "results")

# Load best iteration
for fname in ["trades_iter5_final.csv", "trades_iter3_mr_fixed.csv", "trades_iter1_baseline.csv"]:
    path = os.path.join(results_dir, fname)
    if os.path.exists(path):
        trades = pd.read_csv(path)
        print(f"Analyzing: {fname} ({len(trades)} trades)\n")
        break

wins = trades[trades["net_pnl"] > 0]
losses = trades[trades["net_pnl"] <= 0]

print("=== Win/Loss Profile ===")
print(f"Avg winning trade: {wins['net_pnl_pct'].mean():.4%}")
print(f"Avg losing trade:  {losses['net_pnl_pct'].mean():.4%}")
print(f"Avg win USD:  ${wins['net_pnl'].mean():.4f}")
print(f"Avg loss USD: ${losses['net_pnl'].mean():.4f}")
print(f"Win/Loss ratio: {abs(wins['net_pnl'].mean() / losses['net_pnl'].mean()):.2f}")
print(f"Median confidence (wins):   {wins['confidence'].median():.3f}")
print(f"Median confidence (losses): {losses['confidence'].median():.3f}")
print()

print("=== By Regime + Direction ===")
for (regime, direction), grp in trades.groupby(["regime", "direction"]):
    pnl = grp["pnl_usd"].sum()
    wr = (grp["pnl_usd"] > 0).mean()
    n = len(grp)
    print(f"  {regime:20s} {direction:6s} | {n:3d} trades | WR: {wr:.1%} | PnL: ${pnl:>8.2f}")
print()

print("=== By Strategy + Regime ===")
for (strat, regime), grp in trades.groupby(["strategy", "regime"]):
    pnl = grp["pnl_usd"].sum()
    wr = (grp["pnl_usd"] > 0).mean()
    n = len(grp)
    print(f"  {strat:30s} {regime:20s} | {n:3d} | WR: {wr:.1%} | ${pnl:>8.2f}")
print()

print("=== Transaction Cost Drag ===")
avg_size = trades["position_usd"].mean()
total_volume = trades["position_usd"].sum()
cost_per_trade = avg_size * 0.0015  # slippage + commission
total_cost = total_volume * 0.0015
raw_pnl = trades["raw_pnl_pct"].apply(lambda x: x * avg_size).sum()
print(f"Total trades: {len(trades)}")
print(f"Avg trade size: ${avg_size:.2f}")
print(f"Total volume: ${total_volume:,.2f}")
print(f"Cost per trade: ${cost_per_trade:.4f}")
print(f"Total cost drag: ${total_cost:.2f}")
print(f"Raw PnL (before costs): ${raw_pnl:.2f}")
print(f"Net PnL (after costs):  ${trades['pnl_usd'].sum():.2f}")
print(f"Costs as % of gross: {total_cost / max(abs(trades['pnl_usd'].sum()), 1) * 100:.1f}%")
print()

print("=== Confidence Distribution ===")
for bucket in [(0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0)]:
    mask = (trades["confidence"] >= bucket[0]) & (trades["confidence"] < bucket[1])
    grp = trades[mask]
    if len(grp) > 0:
        pnl = grp["pnl_usd"].sum()
        wr = (grp["pnl_usd"] > 0).mean()
        print(f"  Conf {bucket[0]:.1f}-{bucket[1]:.1f}: {len(grp):4d} trades | WR: {wr:.1%} | PnL: ${pnl:>8.2f}")

print()
print("=== KEY INSIGHT ===")
# What confidence threshold makes the strategy profitable?
for threshold in [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8]:
    filtered = trades[trades["confidence"] >= threshold]
    if len(filtered) > 5:
        pnl = filtered["pnl_usd"].sum()
        wr = (filtered["pnl_usd"] > 0).mean()
        print(f"  Conf >= {threshold:.2f}: {len(filtered):4d} trades | WR: {wr:.1%} | PnL: ${pnl:>8.2f}")
