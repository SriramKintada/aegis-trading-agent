# AEGIS Strategy Verdict -- Iteration 9 FINAL
*Generated: 2026-03-22 14:30 IST*

## Primary Backtest (ETH/USD, 2001 bars hourly)
- **Return: +0.0776%** ($10,000 -> $10,007.76)
- Trades: 264 | Wins: 184 | Losses: 80 | WR: 69.7%
- Sharpe (annualized): -0.9179
- Sortino (annualized): -1.0928
- Max Drawdown: -1.1905%
- Profit Factor: 1.021
- Rebalance trades: 33 | Rebal P&L: $6.23
- Cross-check: MATCH

### Per Strategy:
- AdaptiveMomentumStrategy: 31 trades, WR 38.7%, P&L $-23.39
- CrossDexArbStrategy: 103 trades, WR 100.0%, P&L $87.39
- Rebalance: 33 trades, WR 100.0%, P&L $6.23
- SentimentPulseStrategy: 97 trades, WR 37.1%, P&L $-62.48

### Critical Observation:
**Only arb and rebalancing are profitable.** AdaptiveMomentum and SentimentPulse are NET LOSERS.
- Arb: +$87.39 (100% WR -- simulated, carries to both datasets identically)
- Non-arb directional trades: -$85.87 (37.5% WR -- these LOSE money)
- The tiny +0.08% return is entirely from simulated arb noise, not real edge

## Out-of-Sample (BTC/USD, 2001 bars hourly)
- **Return: -0.2982%** ($10,000 -> $9,970.18)
- Trades: 248 | WR: 68.5%
- Sharpe: -5.4454
- Max Drawdown: -0.7958%
- Cross-check: MATCH
- **FAILS out-of-sample test** -- net negative return

## Stress Tests
- **Flash crash (-15% at bar 1000):** PASS -- Return: +0.1591%, survived with $10,015.91
- **Flat market (bars 800-1200):** FAIL -- 57 directional trades in flat period (should be minimal)
- **Double costs (0.2% slip + 0.1% comm):** FAIL -- Return: -2.2804% (catastrophic)
- **Regime flip (swap BULL/BEAR):** FAIL -- Primary: +0.0776% vs Flipped: +1.0993% (flipping IMPROVES performance, meaning regime detection is HURTING, not helping)

Stress tests passed: 1/4

## Statistical Significance
- Bootstrap p-value: **0.5657** (NOT significant -- need <0.05)
- % profitable bootstrap samples: **43.4%** (TERRIBLE -- need >90%)
- 95% CI for Sharpe: [-10.6571, 8.2849] (massive range, includes zero)
- Mean per-trade return: -0.000139 (NEGATIVE)
- 95% CI for mean return: [-0.001548, 0.001283] (includes zero)
- vs Random entry: **AEGIS LOSES to random** by 0.4564%
- Random avg return: +0.5340% | AEGIS: +0.0776%

## VERDICT: **NOT READY** for real money

### The Brutal Truth

This strategy has **no statistically significant edge**. Here's the evidence:

1. **The return is indistinguishable from zero.** The p-value of 0.5657 means there's a 57% chance this return happened by pure luck. Only 43% of bootstrap samples are profitable -- a coin flip is 50%.

2. **AEGIS loses to random entry.** Random trades with the same position sizing return +0.53%, while AEGIS returns +0.08%. The strategy is actively WORSE than random.

3. **The only profitable component is simulated arb.** CrossDexArbStrategy shows 100% WR and +$87 -- but this is from deterministic random noise simulation. It's the same result on ETH and BTC because the noise generator is identical. This is NOT real edge -- it's an artifact of the simulation.

4. **Regime detection is HURTING performance.** When we flip BULL/BEAR labels, performance IMPROVES from +0.08% to +1.10%. This means the HMM regime detector is actively making wrong decisions. The regime filter is worse than useless.

5. **Directional strategies lose money.** AdaptiveMomentum (-$23) and SentimentPulse (-$62) are net losers even with all the improvements (multi-condition entry, asymmetric stops, capital preservation). These strategies do not have predictive power on hourly crypto data.

6. **Cannot survive cost doubling.** At 2x transaction costs (-2.28%), the strategy collapses. Any real-world friction (MEV, wider spreads, failed txns) would push costs above our backtest assumptions.

### What Needs to Change:

1. **Kill the directional strategies.** AdaptiveMomentum and SentimentPulse are value-destructive. They lose money consistently. No amount of filter stacking fixes a strategy with no underlying edge.

2. **Get REAL arb data.** The simulated arb with N(0, 0.15%) noise is meaningless -- it generates identical results regardless of the underlying asset. Real cross-DEX spreads need actual Uniswap/Aerodrome quote data.

3. **Fix or remove the HMM regime detector.** It's currently HURTING returns. Either:
   - Retrain with much more data (6+ months)
   - Use simpler regime detection (e.g., 200-SMA above/below)
   - Remove it entirely and use unconditional strategies

4. **Longer backtest period.** 83 days (2000 hourly bars) is far too short. Need 6-12 months minimum for any statistical confidence.

5. **Consider a fundamentally different approach:**
   - Pure on-chain arb with real DEX integration (the only component that conceptually works)
   - Funding rate arb (perps vs spot) -- proven to work in crypto
   - Simple rebalancing portfolio (the rebalance component was consistently profitable)
   - Abandon directional prediction on hourly crypto -- the market is too efficient at this timeframe

### Estimated Time to Fix:
- Real DEX data integration: 1-2 weeks
- Regime detector overhaul: 1 week
- Extended backtesting with real data: 2 weeks
- Paper trading validation: 30 days minimum
- **Total: 2-3 months before reconsidering real money**

### Bottom Line:
Do NOT deploy real money. The strategy doesn't work. The tiny positive return on ETH is noise, the arb is simulated, and the directional components actively lose money. This is a well-engineered research prototype that has successfully proven the approach needs fundamental changes before it can generate real alpha.

The honest conclusion: **the multi-condition entry stacking, asymmetric stops, and capital preservation improvements all worked as designed (they prevented catastrophic losses), but they cannot create edge where none exists.** Good risk management on a zero-edge strategy produces approximately zero returns -- which is exactly what we see.
