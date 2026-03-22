"""Deep trade analysis — find exactly where the edge is (or isn't)."""
import pandas as pd
import numpy as np
import os

results_dir = os.path.join(os.path.dirname(__file__), "results")
path = os.path.join(results_dir, "trades_iter5_final.csv")
t = pd.read_csv(path)
print(f"Analyzing {len(t)} trades from iter5_final\n")

wins = t[t["net_pnl"] > 0]
losses = t[t["net_pnl"] <= 0]

print("=== Win/Loss Profile ===")
print(f"Avg win:  {wins['net_pnl_pct'].mean():.4%} (${wins['net_pnl'].mean():.4f})")
print(f"Avg loss: {losses['net_pnl_pct'].mean():.4%} (${losses['net_pnl'].mean():.4f})")
print(f"W/L size ratio: {abs(wins['net_pnl'].mean() / losses['net_pnl'].mean()):.3f}")
print(f"Need WR > {1/(1+abs(wins['net_pnl'].mean()/losses['net_pnl'].mean())):.1%} to break even")
print()

print("=== By Regime + Direction ===")
for (reg, d), g in t.groupby(["regime", "direction"]):
    pnl = g["net_pnl"].sum()
    wr = (g["net_pnl"] > 0).mean()
    print(f"  {reg:20s} {d:6s} | {len(g):3d} | WR:{wr:.1%} | PnL:${pnl:>7.2f}")
print()

print("=== By Strategy + Regime ===")
for (strat, reg), g in t.groupby(["strategy", "regime"]):
    pnl = g["net_pnl"].sum()
    wr = (g["net_pnl"] > 0).mean()
    print(f"  {strat:30s} {reg:20s} | {len(g):3d} | WR:{wr:.1%} | ${pnl:>7.2f}")
print()

print("=== Transaction Costs ===")
total_slip = t["slippage_cost"].sum()
total_comm = t["commission_cost"].sum()
gross = t["gross_pnl"].sum()
net = t["net_pnl"].sum()
print(f"Gross PnL (before costs): ${gross:.2f}")
print(f"Slippage drag:            ${total_slip:.2f}")
print(f"Commission drag:          ${total_comm:.2f}")
print(f"Net PnL (after costs):    ${net:.2f}")
print(f"Would be profitable without costs? {'YES' if gross > 0 else 'NO'}")
print()

print("=== Confidence Buckets ===")
for lo, hi in [(0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)]:
    g = t[(t["confidence"] >= lo) & (t["confidence"] < hi)]
    if len(g) > 0:
        pnl = g["net_pnl"].sum()
        wr = (g["net_pnl"] > 0).mean()
        print(f"  {lo:.1f}-{hi:.1f}: {len(g):3d} trades | WR:{wr:.1%} | ${pnl:>7.2f}")
print()

print("=== Exit Reasons ===")
for reason, g in t.groupby("exit_reason"):
    pnl = g["net_pnl"].sum()
    wr = (g["net_pnl"] > 0).mean()
    print(f"  {reason:20s}: {len(g):3d} | WR:{wr:.1%} | ${pnl:>7.2f}")
print()

print("=== PROFITABILITY THRESHOLD ===")
for threshold in [0.3, 0.4, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8]:
    filtered = t[t["confidence"] >= threshold]
    if len(filtered) >= 5:
        pnl = filtered["net_pnl"].sum()
        wr = (filtered["net_pnl"] > 0).mean()
        sharpe = filtered["net_pnl_pct"].mean() / filtered["net_pnl_pct"].std() * np.sqrt(252*24) if filtered["net_pnl_pct"].std() > 0 else 0
        print(f"  Conf >= {threshold:.2f}: {len(filtered):3d} trades | WR:{wr:.1%} | ${pnl:>7.2f} | Sharpe:{sharpe:.2f}")
print()

# The key question: is there ANY combination that's profitable?
print("=== HUNTING FOR EDGE ===")
best_combo = None
best_pnl = -999
for strat in t["strategy"].unique():
    for reg in t["regime"].unique():
        for d in t["direction"].unique():
            for conf_min in [0.4, 0.5, 0.6, 0.7]:
                mask = (
                    (t["strategy"] == strat) &
                    (t["regime"] == reg) &
                    (t["direction"] == d) &
                    (t["confidence"] >= conf_min)
                )
                g = t[mask]
                if len(g) >= 5:
                    pnl = g["net_pnl"].sum()
                    wr = (g["net_pnl"] > 0).mean()
                    if pnl > 0:
                        if best_combo is None or pnl > best_pnl:
                            best_pnl = pnl
                            best_combo = (strat, reg, d, conf_min, len(g), wr, pnl)

if best_combo:
    strat, reg, d, conf, n, wr, pnl = best_combo
    print(f"  FOUND EDGE: {strat} | {reg} | {d} | conf>={conf:.1f}")
    print(f"  Trades: {n} | WR: {wr:.1%} | PnL: ${pnl:.2f}")
else:
    print("  NO profitable combination found with >= 5 trades")
    print("  Strategies need fundamental rework or different market conditions")
