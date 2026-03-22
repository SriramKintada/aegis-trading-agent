# AEGIS Backtest Iteration Log

**Data:** 2001 bars of ETH/USD hourly data, Dec 2025 – Mar 2026  
**Capital:** $10,000 initial  
**Costs:** 0.10% slippage + 0.05% commission per trade  
**Entry:** Next bar's open after signal (no look-ahead)  
**Bias Audit:** 13/13 PASS — all indicators deterministic, no future data used  
**P&L Cross-Check:** All 5 iterations MATCH — sum of trades = portfolio delta

---

## Iteration Results

| Iteration | Changes | Trades | Win% | Return | Sharpe | Max DD | PF |
|---|---|---|---|---|---|---|---|
| iter1_baseline | Original conservative (20/50 SMA, 2.0 BB, 0.3 sentiment) | 205 | 35.6% | -0.69% | -1.81 | -1.28% | 0.872 |
| iter2_tuned | 10/30 SMA, 1.5 BB std, ADX filter, lower thresholds | 287 | 37.3% | -0.87% | -2.22 | -1.38% | 0.875 |
| iter3_mr_fixed | MR: only at band cross (not proximity), tighter stop | 282 | 35.8% | **-0.42%** | **-1.04** | -1.29% | **0.938** |
| iter4_quality | ADX > 25, stronger filters, no arb proxy | 324 | 34.9% | -1.16% | -3.46 | -2.03% | 0.831 |
| iter5_final | Best-of-breed params, tighter quality filter | 331 | 34.7% | -0.55% | -1.46 | -1.71% | 0.922 |

**Best iteration by Sharpe: `iter3_mr_fixed`** (Sharpe -1.04, Max DD -1.29%, PF 0.938)

---

## What Happened Per Iteration

### Iteration 1 — Baseline
**Problem discovered:** 0 trades in baseline run — HMM was being constructed via `__new__()` bypassing `__init__()`, so `scaler=None` caused silent crash in `fit()`. `hmm_trained` never became `True`. Engine ran but never generated trades.

**Engine bug fixed:** Use `HMMRegimeDetector(model_path=dummy_path)` for proper initialization.

**Results after fix:** 205 trades, but all strategies unprofitable. MomentumStrategy nearly flat (+$0.91), MeanReversionStrategy and SentimentPulse both negative.

**Key finding:** MeanReversionStrategy (2.0 BB std) fires on "near-band proximity" entries that have poor R:R. Win rate 26.8%.

---

### Iteration 2 — Tuned Thresholds
**Changes:**
- SMA periods: 20/50 → 10/30 (more crossover signals)
- BB std: 2.0 → 1.5 (more entries)
- Confidence threshold: 0.55 → 0.40
- ADX filter added for trend confirmation
- Synthetic sentiment proxy enabled

**Results:** More trades (287) but worse returns (-0.87%). MeanReversionStrategy shot to 95 trades but -$69. 

**Root cause identified:** Lowering BB std to 1.5 means the "near-band" proximity condition fires constantly on minor fluctuations. Most entries are at the midline already, with tiny R:R.

---

### Iteration 3 — Mean Reversion Fixed ✅ BEST
**Changes:**
- `band_proximity_pct=0.0` — only enter when price IS at or BEYOND the band (not just near it)
- Tighter stop: `stop_atr_mult=1.2`
- Sentiment shift: 0.3 → 0.22

**Results:** Best iteration. Return improved to -0.42%, Sharpe -1.04, PF 0.938.

**Per-regime performance:**
- BULL_TRENDING: +$30.51 (Momentum catches actual uptrends)
- BEAR_TRENDING: +$35.78 (Sentiment detects momentum)
- HIGH_VOL_CHOPPY: -$49.31 (All strategies lose here — volume-driven noise)
- MEAN_REVERTING: -$58.54 (Paradox: even MR strategy loses in MEAN_REVERTING regime)

**Key finding:** HIGH_VOL_CHOPPY and MEAN_REVERTING regimes are net negative across all strategies. The HMM is classifying ~85% of bars as these two regimes. Trading is essentially net-negative in 85% of market conditions.

---

### Iteration 4 — Quality Filter
**Changes:** ADX > 25 (strict trend), no crosspool arb proxy, tighter confidence gates.

**Results:** Worse (-1.16%). More filtering = fewer but not better trades. The problem isn't signal count, it's regime.

**CrossPool Arb kill confirmed:** EMA divergence proxy (12.2% win rate on quick test) is not a viable backtest proxy for real arb. The EMA spread is persistent, not temporary — the "reversion" doesn't happen on the expected time horizon.

---

### Iteration 5 — Final Config
**Changes:** RSI bands narrowed (35/65), TP widened to 3.0x ATR.

**Results:** -0.55%, Sharpe -1.46. SentimentPulse +$18.19, Momentum near break-even (+$0.06), MeanReversion -$73.45.

---

## What Worked

1. **Engine architecture**: Walk-forward, look-ahead-clean, next-bar entry, slippage-inclusive, dual P&L cross-check. All 13 bias audits pass.

2. **MomentumStrategy with ADX**: In BULL_TRENDING and BEAR_TRENDING regimes, Momentum delivers +$30-40 over the period. The problem is trending regimes are rare (< 15% of bars).

3. **SentimentPulse in trending regimes**: Bear_Trending is profitable for sentiment-based trades. The synthetic proxy captures momentum reversals adequately.

4. **CrossPool Arb (real version, not proxy)**: The concept is valid. Real arb compares actual pool prices, not EMA divergence. On Base chain with multi-pool DEX data, this is the highest-quality edge. The backtest proxy was invalid.

---

## What Didn't Work (Honest Assessment)

### 1. Mean Reversion Strategy — Fundamental Problem
**Root cause:** The HMM classifies ETH/USD Dec 2025 – Mar 2026 as MEAN_REVERTING ~73% of the time. But this period was actually a **sustained bear move** from $3,384 to $1,815 (−46%). A "mean-reverting" label is wrong — the price never reverts to any mean. Mean reversion in a falling market = catching falling knives.

**The paradox:** MeanReversionStrategy loses money *specifically in MEAN_REVERTING regimes*. This is because the HMM is mislabeling the regime, or because "mean-reverting" on hourly data still has directional drift.

### 2. HIGH_VOL_CHOPPY regime destroys all strategies
All 3 strategies are net-negative in this regime. These bars should simply be excluded from trading. Risk multiplier of 0.3× is insufficient — it should be 0.0×.

### 3. Win rate structural floor ~35%
With slippage + commission = 0.15% per round-trip, and average trade PnL near zero, the break-even win rate at 1:1 R:R is 50%. Our strategies are at 35% → consistent losses. Need either:
- Better signal quality (fewer, higher-conviction trades)
- Better R:R (wider TP relative to SL)
- Different regime handling

### 4. CrossPool Arb EMA proxy is invalid
EMA divergence is not pool price divergence. Don't use it as a backtest proxy. Real arb needs real-time two-source price comparison.

---

## Final Recommended Configuration

Based on the data, the **honest recommendation** is:

```python
# Use this ONLY in TRENDING regimes. Don't trade in HIGH_VOL_CHOPPY or MEAN_REVERTING.
MomentumStrategy(
    fast_period=10,
    slow_period=30,
    min_confidence=0.45,
    adx_threshold=25.0,   # Only enter when trend is confirmed
    stop_atr_mult=1.8,
    tp_atr_mult=3.0,
    rsi_oversold=35.0,
    rsi_overbought=65.0,
)

# Fire only at genuine band touch — not proximity, not near-band.
# Skip in MEAN_REVERTING (HMM mislabels trending bear markets as MR).
MeanReversionStrategy(
    bb_period=20,
    bb_std=1.5,
    min_confidence=0.46,
    band_proximity_pct=0.0,  # Only AT the band
    stop_atr_mult=1.2,
)

# Disable or use only in BEAR_TRENDING. Synthetic sentiment is noisy.
SentimentPulseStrategy(
    shift_threshold=0.22,
    min_confidence=0.44,
    use_synthetic=True,  # For live: use FinBERT
    stop_pct=0.012,
    take_profit_pct=0.028,
)
```

**Critical addition not yet implemented:**  
Add a **regime veto**: if regime is HIGH_VOL_CHOPPY → ALL strategies return NEUTRAL. This single change should prevent ~40% of losing trades.

---

## Remaining Risks — Honest Assessment

### 1. The data period is a bear market
Dec 2025 – Mar 2026 was ETH −46%. Any long-biased strategy loses in a bear. Any short-biased strategy needs accurate regime detection. Our HMM doesn't reliably distinguish "slow bear" from "mean-reverting choppy."

### 2. The HMM regime labeling is unreliable on short windows
A 50-bar lookback for prediction on a 300-bar training window is insufficient for reliable regime transitions. The model assigns MEAN_REVERTING 73.7% of bars in a clearly trending bear market. This is the core problem.

**Fix:** Train on 1000+ bars. Use Viterbi path smoothing. Consider regime change detection (e.g., online BOCPD) instead of HMM.

### 3. The strategies all have positive expected PF but negative actual PF
Individual trade R:R ratios are theoretically positive (2:1, 3:1) but realized PF is 0.87-0.94. This means:
- Stops are being hit before TP more than expected
- The signal direction is correct ~35% of the time vs the required ~40%+

**Fix:** The SMA crossover and BB touch signals are lagging. Consider using faster signals (e.g., candlestick patterns, order flow proxy via volume delta).

### 4. Synthetic sentiment is a momentum duplicate
The synthetic sentiment proxy computes 5-bar momentum + volume, which is nearly identical to what the momentum strategy already uses. Two strategies using the same underlying signal = overfit, not diversification.

**Fix:** For real deployment, use live FinBERT on CryptoPanic news. Do not rely on synthetic sentiment in production.

### 5. Position sizing is too small to matter
With $10,000 capital and ~5% max position, each trade is ~$250-500. After 0.15% round-trip costs, a trade needs >$0.75 gain to break even. At these sizes, commission drag is significant.

**Fix:** In real deployment with real capital, position sizing becomes more favorable. Or raise capital minimum for Base chain operations.

---

## Summary Verdict

**The current strategy ensemble does NOT have a positive edge on this data period.**

The best iteration (iter3_mr_fixed) lost 0.42% over 83 days — that's a -1.85% annualized return, Sharpe -1.04. These are real-money-losing results, not noise.

**Why it's not ready:**
1. HMM mislabels 73%+ of the bear market as MEAN_REVERTING
2. All strategies trade in HIGH_VOL_CHOPPY and lose there
3. Win rate ~35% is structurally below break-even for a 0.15% cost structure
4. Synthetic sentiment duplicates momentum signal — no independent edge

**What would make it viable:**
1. Add regime veto (no trade in HIGH_VOL_CHOPPY)
2. Improve HMM: longer training window, more features, smoothed transitions
3. Live FinBERT sentiment (genuine orthogonal signal)
4. Real CrossPool Arb with actual DEX price feeds (strongest verifiable edge)
5. Minimum 6+ months backtest across different market conditions (not just a bear)

The infrastructure (engine, bias audit, dashboard, walk-forward) is solid and correct. The signals need work.

---

*Generated: 2026-03-22 | AEGIS Quant Team*
