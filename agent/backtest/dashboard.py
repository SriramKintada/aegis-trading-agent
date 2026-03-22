"""
AEGIS — Backtest Feedback Dashboard
Comprehensive visual report using rich.
"""

import sys
import json
import math
import warnings
warnings.filterwarnings('ignore')
from pathlib import Path
from typing import List, Dict

sys.path.insert(0, str(Path(__file__).parent.parent))

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.columns import Columns
    from rich import box
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

RESULTS_DIR = Path(__file__).parent / "results"
console = Console(width=120) if HAS_RICH else None


def load_result(tag: str) -> Dict:
    path = RESULTS_DIR / f"backtest_{tag}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def load_trades(tag: str) -> List[Dict]:
    import csv
    path = RESULTS_DIR / f"trades_{tag}.csv"
    if not path.exists():
        return []
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def load_equity(tag: str) -> List[float]:
    import csv
    path = RESULTS_DIR / f"equity_{tag}.csv"
    if not path.exists():
        return []
    with open(path, newline='') as f:
        reader = csv.DictReader(f)
        return [float(row['equity']) for row in reader]


def ascii_equity_curve(equity: List[float], width: int = 80, height: int = 15) -> str:
    """Render equity curve as ASCII art."""
    if not equity or len(equity) < 2:
        return "  [No equity data]\n"

    min_val = min(equity)
    max_val = max(equity)
    val_range = max_val - min_val

    if val_range < 0.01:
        return f"  Flat equity curve (${equity[-1]:,.2f})\n"

    # Downsample to width
    step = max(1, len(equity) // width)
    sampled = equity[::step]
    if len(sampled) > width:
        sampled = sampled[:width]

    lines = []
    # Render height rows, top to bottom
    for row in range(height - 1, -1, -1):
        threshold = min_val + (row / (height - 1)) * val_range
        line_chars = []
        for val in sampled:
            if val >= threshold - val_range / (2 * height):
                line_chars.append('*' if row == int((val - min_val) / val_range * (height - 1)) else '|')
            else:
                line_chars.append(' ')
        # Y-axis label every 5 rows
        if row == height - 1:
            label = f"${max_val:>8,.0f}"
        elif row == 0:
            label = f"${min_val:>8,.0f}"
        elif row == height // 2:
            mid = (max_val + min_val) / 2
            label = f"${mid:>8,.0f}"
        else:
            label = "         "
        lines.append(f"{label} |{''.join(line_chars)}")

    # X-axis
    lines.append("         +" + "-" * len(sampled))
    lines.append("          Start" + " " * (len(sampled) - 20) + "End")

    return "\n".join(lines)


def print_dashboard(tag: str):
    """Print comprehensive dashboard for a backtest run."""
    result = load_result(tag)
    trades = load_trades(tag)
    equity = load_equity(tag)

    if not result:
        print(f"No results found for tag: {tag}")
        return

    print(f"\n{'='*80}")
    print(f"  AEGIS BACKTEST DASHBOARD — {tag.upper()}")
    print(f"{'='*80}")

    # ── Overview ──
    print(f"\n{'─'*80}")
    print("  OVERVIEW")
    print(f"{'─'*80}")
    print(f"  Period:        {result['start_time']} → {result['end_time']}")
    print(f"  Bars:          {result['total_bars']:,}")
    print(f"  Capital:       ${result['initial_capital']:,.2f} → ${result['final_capital']:,.2f}")
    ret = result['total_return_pct']
    ret_sign = '+' if ret >= 0 else ''
    print(f"  Total Return:  {ret_sign}{ret:.4f}%")
    print(f"  Costs:         Slippage {result['slippage_pct']:.2%} | Commission {result['commission_pct']:.2%}")

    # ── Equity Curve ──
    print(f"\n{'─'*80}")
    print("  EQUITY CURVE")
    print(f"{'─'*80}")
    print(ascii_equity_curve(equity, width=70, height=12))

    # ── Trade Statistics ──
    print(f"\n{'─'*80}")
    print("  TRADE STATISTICS")
    print(f"{'─'*80}")
    print(f"  Total Trades:    {result['total_trades']:,}")
    print(f"  Wins / Losses:   {result['winning_trades']} / {result['losing_trades']}")
    print(f"  Win Rate:        {result['win_rate']:.2%}")
    print(f"  Profit Factor:   {result['profit_factor']:.4f}")
    print(f"  Avg Duration:    {result['avg_trade_duration']:.1f} bars")

    # ── Risk Metrics ──
    print(f"\n{'─'*80}")
    print("  RISK METRICS")
    print(f"{'─'*80}")
    print(f"  Sharpe Ratio (ann.):   {result['sharpe_ratio']:.4f}")
    print(f"  Sortino Ratio (ann.):  {result['sortino_ratio']:.4f}")
    print(f"  Max Drawdown:          {result['max_drawdown_pct']:.4f}%")
    print(f"  Calmar Ratio:          {result['calmar_ratio']:.4f}")

    # ── P&L Cross-Check ──
    print(f"\n{'─'*80}")
    print("  P&L CROSS-CHECK")
    print(f"{'─'*80}")
    match_str = "MATCH" if result['pnl_match'] else "MISMATCH"
    print(f"  Trade sum method:    ${result['pnl_sum_method']:,.4f}")
    print(f"  Portfolio method:    ${result['pnl_portfolio_method']:,.4f}")
    print(f"  Status:              {match_str}")

    # ── Per-Strategy ──
    if result.get('strategy_stats'):
        print(f"\n{'─'*80}")
        print("  PER-STRATEGY PERFORMANCE")
        print(f"{'─'*80}")
        print(f"  {'Strategy':<25} {'Trades':>7} {'Win%':>7} {'Total PnL':>12} {'Avg Dur':>9}")
        print(f"  {'─'*65}")
        for name, stats in result['strategy_stats'].items():
            pnl = stats['total_pnl']
            pnl_str = f"${pnl:>+10.2f}"
            print(f"  {name:<25} {stats['trades']:>7} {stats['win_rate']:>6.1%} "
                  f"  {pnl_str} {stats['avg_duration']:>8.1f}b")

    # ── Per-Regime ──
    if result.get('regime_stats'):
        print(f"\n{'─'*80}")
        print("  PER-REGIME PERFORMANCE")
        print(f"{'─'*80}")
        print(f"  {'Regime':<22} {'Trades':>7} {'Total PnL':>12} {'Best Strategy':<25}")
        print(f"  {'─'*70}")
        for name, stats in result['regime_stats'].items():
            pnl = stats['total_pnl']
            pnl_str = f"${pnl:>+10.2f}"
            print(f"  {name:<22} {stats['trades']:>7}   {pnl_str}   {stats['best_strategy']:<25}")

    # ── Recent Trade Log ──
    if trades:
        print(f"\n{'─'*80}")
        print(f"  LAST 10 TRADES")
        print(f"{'─'*80}")
        print(f"  {'#':>4} {'Strategy':<22} {'Dir':>5} {'Entry':>9} {'Exit':>9} {'PnL':>8} {'Exit Reason':<15}")
        print(f"  {'─'*75}")
        for t in trades[-10:]:
            pnl = float(t.get('net_pnl', 0))
            print(f"  {t['trade_id']:>4} {t['strategy']:<22} {t['direction']:>5} "
                  f"${float(t['entry_price']):>8.2f} ${float(t['exit_price']):>8.2f} "
                  f"${pnl:>+7.2f} {t.get('exit_reason',''):<15}")

    # ── Warnings ──
    print(f"\n{'─'*80}")
    print("  WARNINGS & ANALYSIS")
    print(f"{'─'*80}")

    warnings_list = []

    if result['win_rate'] < 0.40:
        warnings_list.append(f"LOW WIN RATE ({result['win_rate']:.1%}) — strategies have poor signal quality")
    if result['profit_factor'] < 1.0:
        warnings_list.append(f"PROFIT FACTOR < 1.0 ({result['profit_factor']:.4f}) — losing money overall")
    if result['sharpe_ratio'] < -1.0:
        warnings_list.append(f"NEGATIVE SHARPE ({result['sharpe_ratio']:.2f}) — risk-adjusted returns are poor")
    if result['total_trades'] < 20:
        warnings_list.append(f"FEW TRADES ({result['total_trades']}) — insufficient sample for statistical significance")
    if result['total_trades'] > 500:
        warnings_list.append(f"OVERTRADING ({result['total_trades']} trades) — excessive costs dragging returns")
    if not result['pnl_match']:
        warnings_list.append("P&L MISMATCH — cross-check failed, review engine logic")

    # Check dominant losing regime
    if result.get('regime_stats'):
        for reg, stats in result['regime_stats'].items():
            if stats['total_pnl'] < -50:
                warnings_list.append(f"REGIME BLEEDING: {reg} losing ${abs(stats['total_pnl']):.2f} — consider disabling strategies in this regime")

    if not warnings_list:
        print("  No critical warnings.")
    else:
        for w in warnings_list:
            print(f"  !! {w}")

    print(f"\n{'='*80}\n")


def compare_iterations(tags: List[str]):
    """Print comparison table across iterations."""
    print(f"\n{'='*80}")
    print("  CROSS-ITERATION COMPARISON")
    print(f"{'='*80}")
    print(f"\n  {'Tag':<25} {'Trades':>7} {'Win%':>7} {'Return%':>10} {'Sharpe':>8} {'MaxDD%':>8} {'PF':>7} {'Match':>6}")
    print(f"  {'─'*78}")

    for tag in tags:
        r = load_result(tag)
        if not r:
            print(f"  {tag:<25} [not found]")
            continue
        print(f"  {tag:<25} {r['total_trades']:>7} {r['win_rate']:>6.1%} "
              f"{r['total_return_pct']:>+9.4f}% {r['sharpe_ratio']:>7.2f} "
              f"{r['max_drawdown_pct']:>7.2f}% {r['profit_factor']:>6.3f} "
              f"{'OK' if r['pnl_match'] else 'MISS':>6}")

    print()


if __name__ == "__main__":
    tags = [
        "iter1_baseline",
        "iter2_tuned",
        "iter3_mr_fixed",
        "iter4_quality",
        "iter5_final",
    ]

    # Print dashboard for best iteration (iter3_mr_fixed by Sharpe)
    print_dashboard("iter3_mr_fixed")

    # Also print iter5_final (our final config)
    print_dashboard("iter5_final")

    # Summary comparison
    compare_iterations(tags)
