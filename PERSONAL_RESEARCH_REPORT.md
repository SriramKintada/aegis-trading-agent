# AEGIS Personal Research Report
## Complete Chronicle: The Synthesis Hackathon, March 22, 2026
*Sriram Kintada — Internal documentation for future reference*

---

> This document exists so that Sriram-in-6-months can understand exactly what happened, why every decision was made, and how to reproduce the entire thing from scratch. It is also a research journal in the genuine sense: what we believed, what the data said, and how we updated.

---

## 1. Timeline — Hour by Hour

### 2:20 AM IST — Hackathon Registration

Registered for The Synthesis hackathon (AI agent hackathon by the Ethereum ecosystem). The registration flow went through the Synthesis API with email OTP verification. Confirmed participation. Received ERC-8004 on-chain identity for the bot agent on Base.

The initial idea: build a trading agent that uses the existing HMM regime detection work (from the quant research done with Vivek) and deploys it on Base chain via Uniswap. The pitch angle: institutional quant methods, not a vibe-coded LLM bot.

Stakes: Three prize tracks targeted — Autonomous Trading Agent (Base, 3×$1,667), Agentic Finance/Best Uniswap API ($2,500), Synthesis Open Track.

---

### 2:30 AM IST — Strategy Engine Built by Subagent

A coding subagent was spawned to build the entire Python strategy engine in one pass. The task was scoped carefully upfront:

**Subagent brief (paraphrased):**
```
Build a complete autonomous trading agent for The Synthesis hackathon.
Target: Base chain, Uniswap V3/V4. Must include:
- HMM regime detection (4 states: BULL/BEAR/CHOPPY/MEAN_REVERTING)
- 3+ strategies: Momentum, MeanReversion, SentimentPulse + CrossDexArb
- Governance veto system (6 rules before any trade)
- Genetic evolution (2-hour parameter evolution cycles)
- Kelly criterion position sizing
- Walk-forward backtester with look-ahead bias audit
- Web3.py execution layer for Uniswap V3/V4
- FinBERT sentiment pipeline (HuggingFace ProsusAI/finbert)
Work in: projects/synthesis-trading-agent/agent/
Use Python 3.13. Create 25+ files, 150KB+ of production-quality code.
```

**Result:** 25+ Python files, 156KB+ of code delivered. The scaffold was solid: the backtest engine, regime detector, strategy classes, governance module, and execution layer all existed within ~90 minutes.

Key discovery in the very first run: an HMM initialization bug. The `HMMRegimeDetector` was being constructed via `__new__()` bypassing `__init__()`, so `scaler=None` caused a silent crash in `fit()`. `hmm_trained` never became `True`. The engine ran but generated exactly zero trades. This was the first of many important debugging moments.

---

### 2:43 AM IST — Sriram's Directive

After reviewing the initial build, the core requirement was stated explicitly:

> "This needs to PROVE profitability. Multiple proof-checking methods. I want to be able to say this works with actual evidence, not just a single run."

This directive shaped the entire research methodology that followed. Not just "does it make money in one backtest" but "is the evidence statistically robust." This turned out to be the most important decision of the project — it's why we ran 9 iterations instead of stopping at the first positive result.

---

### 10:55 AM IST — First Real Data Test

After the initial build, a real-data smoke test was run using live CoinGecko data. The test pulled 30 days of hourly ETH/USD prices and ran the agent's signal generation logic.

**Initial real-data results:**
- 6 trades in the test window
- Win rate: 66.7% (4/6 wins)
- Return: +0.02%
- Capital: $10,000 → $10,002

This looked promising at first glance. But 6 trades is not a statistically meaningful sample. The next step was the full backtest.

---

### 11:27 AM IST — Quant Iteration Cycle Begins

A Claude Opus 4.6 subagent was given the role of "AEGIS Quant Analyst." The assignment:

**Quant analyst brief:**
```
Run systematic walk-forward backtest iterations on the AEGIS strategy engine.
For each iteration:
1. Analyze the previous iteration's results
2. Identify the dominant failure mode
3. Propose ONE targeted change
4. Run the backtest
5. Report: trades, win_rate, return_pct, sharpe, max_dd, profit_factor
6. Check for look-ahead bias (13-point audit must PASS)
7. Cross-check P&L (sum-of-trades == portfolio delta)

Data: 2001 bars ETH/USD hourly, Dec 28 2025 - Mar 22 2026
Capital: $10,000
Costs: 0.10% slippage + 0.05% commission (realistic)
Entry: next bar's open after signal (no look-ahead)

Goal: find the first iteration with positive Sharpe and positive return.
Do NOT stop at first positive result — validate it properly.
```

The walk-forward structure is important. A standard train/test split (say 80/20) lets the model "see" the test period implicitly through hyperparameter tuning. Walk-forward is stricter: the training window rolls forward, the test window is always in the future relative to any decision made.

---

### 12:00 PM IST — Five Iterations Complete, All Losing

By noon, iterations 1-5 were complete. Every single one showed negative returns:

| Iter | Return | Sharpe | Key Finding |
|------|--------|--------|-------------|
| 1 | -0.69% | -1.81 | HMM init bug fixed; all strategies losing |
| 2 | -0.87% | -2.22 | More signals = more costs = worse returns |
| 3 | -0.42% | -1.04 | Best result: band-touch only for MR |
| 4 | -1.16% | -3.46 | Stricter filters made things worse |
| 5 | -0.55% | -1.46 | SentimentPulse briefly positive; MR kills it |

**Key pattern spotted at iteration 3:** The best-performing iteration happened when the Mean Reversion strategy was forced to only enter at actual Bollinger Band touches (not just near them). The lesson: vague "near the band" proximity conditions fire constantly with terrible R:R.

**Key pattern from regime breakdown:** In every iteration, HIGH_VOL_CHOPPY and MEAN_REVERTING regimes were net-negative for ALL strategies. The HMM was classifying ~73% of bars as these two regimes in the Dec 2025 - Mar 2026 ETH bear market. The irony: MEAN_REVERTING regime label was being applied to a sustained -46% bear move. The HMM was wrong most of the time.

---

### 1:00 PM IST — Transaction Cost Deep Dive

A focused analysis of the trade-level economics:

**The math:**
- Round-trip cost: 0.10% slippage + 0.05% commission = **0.15% per round trip**
- Break-even win rate at 1:1 R:R: **50%**
- Our actual win rates: 34-37%
- The gap: **13-16 percentage points below break-even**

Even with 2:1 or 3:1 R:R targets (stop loss vs take profit), the actual realized P&L was negative because the win rate wasn't high enough. The theoretical R:R was positive, but the signals weren't correct often enough.

**Why are signals wrong 63-65% of the time?**

Three reasons identified:
1. The HMM regime classification is unreliable (it labels trending bear markets as MEAN_REVERTING)
2. SMA crossovers and Bollinger Band touches are lagging indicators — they fire after the move has already started
3. Synthetic sentiment (5-bar momentum proxy) duplicates the momentum signal — two correlated signals aren't independent edge

---

### 1:05 PM IST — Iteration 7: First Profitable Iteration

A more fundamental rethink between iterations 5 and 7:

**Changes from iter 5 to iter 7 (iter 6 was intermediate):**
- Cost-aware position sizing: minimum expected profit must exceed 5× costs before entry
- Asymmetric stops: stop_loss = 1.0%, take_profit = 3.0% (3:1 R:R minimum)
- Confidence gate raised from 0.44 to 0.60 (fewer, higher-conviction trades)
- HIGH_VOL_CHOPPY regime fully suppressed (no trades in choppy markets)
- Capital preservation: max 3 concurrent positions, reduce sizing after any losing streak

**Result — iteration 7:**
```
Trades: 74 (vs 287-331 in earlier iterations)
Win rate: 43.2%
Return: +0.09%
Sharpe: +2.29
Max Drawdown: -0.36%
Profit Factor: 1.087
P&L cross-check: MATCH
```

**Why it worked:**
- Fewer trades = less cost drag
- The 74 trades were higher-conviction entries
- Regime gating actually helped — by excluding choppy periods, the remaining trades were in cleaner trending conditions

The first positive Sharpe. But 74 trades is a small sample. Needed more validation.

---

### 1:37 PM IST — Iteration 8: Cross-DEX Arb Changes Everything

The research insight that changed the trajectory: what if we added actual mathematical arbitrage rather than relying solely on directional prediction?

**Cross-DEX arb logic (simplified):**
- Check WETH/USDC price on Uniswap V3
- Check WETH/USDC price on Aerodrome Finance
- If spread > 0.20% (covers round-trip costs with margin): execute arb
- Buy on the cheaper DEX, sell on the more expensive DEX

This is categorically different from directional trading. It's not predicting whether ETH goes up or down. It's exploiting a price discrepancy that *by definition resolves* as arbitrageurs (including us) close the gap.

**Governance veto system also upgraded in iter 8:**
- Added 6th rule: regime gate (no directional trades in HIGH_VOL_CHOPPY)
- Tighter daily loss circuit breaker (-3% → halt)
- Gas profitability gating (profit must exceed 3× gas)

**Result — iteration 8:**
```
Trades: 151 total (27 arb + 124 directional)
Win rate: 58.9%
Return: +0.75%
Sharpe: +8.14
Sortino: +11.35
Max Drawdown: -0.23%
Profit Factor: 1.43
Governance vetoes: 76 (out of 227 candidate trades)

Arb breakdown:
  Arb trades: 27
  Arb win rate: 100%
  Arb P&L: +$23.43
  
P&L cross-check: MATCH
```

The governance layer vetoed 76 trades — 33% of everything the strategy engine wanted to do. That's remarkable. The governance module was actively preventing 1 in 3 trades from executing.

The Sharpe of 8.14 is exceptional. Too exceptional. This was a flag.

---

### 2:00 PM IST — Research Deep Dive: What Actually Makes Money

While the backtest was running, a Firecrawl deep research session was run to understand how other people are actually making money with trading agents and Claude:

**Key searches performed:**
1. `"Polymarket arbitrage bot" Claude agent 2025 2026 profit`
2. `"trading bot" Base chain Uniswap autonomous agent profitable strategy`
3. `"Jake Nesler" Claude Prophet trading results`
4. `MEV arbitrage Base L2 competitive landscape 2026`
5. `"Midas Arena" agent competition trading strategy`

**What came back:**

**Finding 1: The Polymarket $14K bot (sum-to-one arb)**
A Polymarket arb bot earned ~$14K by exploiting "sum-to-one" mispricing: when Yes + No prices < $1.00, buy both, guaranteed profit at settlement. Pure math, no prediction. Key: thin liquidity ($5K-$15K per side) creates brief mispricings. Execution improved from 30s → <800ms over time.

This was the cleanest possible validation of the "arb over prediction" hypothesis.

**Finding 2: Jake Nesler's Claude Prophet (+7.6% in 33 days)**
Uses LEAPS options (60-90 DTE) + intraday scalps. Trades both directions. **58% cash position**. CEO agent veto. Biggest win: +$14,578 from overnight puts when SPY dropped. The key insight: capital preservation (58% cash!) IS the edge. Not the signals.

**Finding 3: Midas Arena (self-improving battle)**
Two agents compete in live 24h trading battles on Base. Each evolves every 2 hours. Competition forces adaptation. This is essentially what AEGIS's genetic evolution module does — we had independently converged on the same architecture.

**Finding 4: MEV/arb reality check**
Mainnet: >99% of arb opportunities disappear in the next block. Infrastructure costs ~$750/month for competitive MEV. L2s (Base, Arbitrum) have less competition. The real bottleneck is latency, not strategy complexity.

**Finding 5: What doesn't work**
- Vague prompts ("find the next 10x")
- High-frequency on DEXs (MEV front-running)
- Single strategy (always loses in some regime)
- No risk management (the OpenClaw agent that went to $0 had zero controls)

---

### 2:10 PM IST — Final Verdict (Iteration 9)

Iteration 9 was the definitive validation run. The Opus agent was prompted for maximum rigor:

**Final verdict prompt:**
```
Run the complete validation suite on the current AEGIS strategy:
1. Primary backtest: ETH/USD 2001 bars hourly
2. Out-of-sample: BTC/USD 2001 bars (same period) — must use IDENTICAL code
3. Stress tests:
   a. Flash crash: inject -15% price shock at bar 1000
   b. Flat market: inject flat period bars 800-1200
   c. Double costs: 0.20% slippage + 0.10% commission
   d. Regime flip: swap BULL_TRENDING and BEAR_TRENDING labels
4. Bootstrap significance: 1000 samples, report p-value
5. Random entry benchmark: compare vs random entry with same position sizing
6. Per-strategy breakdown: which strategies are actually profitable?
Report: READY or NOT READY with evidence. Be brutal.
```

**Iteration 9 results — the brutal truth:**

```
Primary ETH/USD:
  Return: +0.0776% ($10,000 → $10,007.76)
  Trades: 264 | Win rate: 69.7%
  Sharpe: -0.92 | Sortino: -1.09
  Max Drawdown: -1.19%
  Profit Factor: 1.021

Per-strategy breakdown:
  AdaptiveMomentum:  31 trades, WR 38.7%, P&L -$23.39  ← LOSER
  CrossDexArb:      103 trades, WR 100.0%, P&L +$87.39  ← WINNER (simulated)
  Rebalance:         33 trades, WR 100.0%, P&L +$6.23   ← WINNER
  SentimentPulse:    97 trades, WR 37.1%, P&L -$62.48   ← LOSER

Out-of-Sample BTC/USD:
  Return: -0.2982% ($10,000 → $9,970.18)  ← FAILS
  Sharpe: -5.45 | Max DD: -0.80%

Stress tests (1 pass, 3 fail):
  Flash crash:    PASS — survived with +0.16%
  Flat market:    FAIL — 57 directional trades in flat period (should be ~0)
  Double costs:   FAIL — return collapses to -2.28%
  Regime flip:    FAIL — flipping BULL/BEAR IMPROVES performance (+1.10% vs +0.08%)

Bootstrap (1000 samples):
  p-value: 0.5657 (need <0.05 for significance)
  % profitable samples: 43.4% (a coin flip is 50%)
  Sharpe 95% CI: [-10.66, +8.28]  ← massive uncertainty

vs Random Entry:
  Random avg return: +0.534%
  AEGIS return:      +0.078%
  AEGIS LOSES to random by 0.456%
```

**VERDICT: NOT READY**

The key insight in the final verdict:

> The ONLY profitable component is simulated arb. CrossDexArbStrategy shows 100% WR and +$87 — but this is from deterministic noise simulation. It's the same result on ETH AND BTC because the noise generator is identical. This is NOT real edge. The directional strategies (momentum + sentiment) are value-destructive.

The iteration 8 Sharpe of 8.14 was deceiving because the arb simulation was generating identical pseudo-random profits regardless of the underlying asset. It looked like an edge but was an artifact.

**Most damning finding:** Regime flip improves performance. This means the HMM is actively making wrong decisions — it's labeling markets backwards more often than forward. The regime detector is WORSE than useless.

---

### 2:30 PM IST — Mining FUND + FinRL + Book RAG

While waiting for the iteration 9 run to complete, a search was done through the existing FUND codebase and FinBooks RAG collection for relevant prior work.

**RAG queries on FinBooks (3 key queries):**

**Query 1:** "transaction cost drag alpha decay crypto high frequency"
*What returned:* Literature on execution cost ratios. Key finding: strategies need gross alpha > 3× transaction costs to survive real deployment. Our 0.15% per round-trip means we need 0.45%+ gross alpha per trade. Our average trade gross PnL was ~0.02-0.05% — nowhere near the 3× threshold. The math was stacked against us from the beginning.

**Query 2:** "data snooping bias backtest multiple testing correction"
*What returned:* White (2000) Reality Check paper and Bailey et al. (2014) on the Sharpe ratio haircut from multiple comparisons. With 9 iterations of backtest optimization on the same dataset, our effective p-value threshold should be Bonferroni-corrected to 0.05/9 ≈ 0.0056. Our p-value of 0.5657 isn't just bad — it's catastrophically bad relative to the stricter required threshold.

**Query 3:** "cointegration structural arbitrage ETH stETH"
*What returned:* Research on pairs trading with structural anchors. ETH/stETH identified as one of the cleanest cointegration relationships in crypto — bounded by staking yield (~3-4% APY) plus redemption friction. Any spread beyond that is genuine arb. Key reference: Engle-Granger cointegration test on DeFi token pairs.

**From FUND codebase:**

The RPI-DDPG (Risk-Penalized Information Deep Deterministic Policy Gradient) from Trial 3 is directly applicable. Key architecture insight: the "information" component uses mutual information maximization between portfolio state and action — this prevents the RL agent from overfitting to noise by requiring it to extract genuinely informative features. This is exactly what's missing from the AEGIS strategy engine: a proper information-theoretic quality filter on signals.

The FUND Trial 3 reward function: `R = Sharpe(t) - λ × MaxDrawdown(t) - μ × TransactionCosts(t)`. The explicit cost penalty term is what prevents the RL agent from finding spurious high-trade-count strategies. This should be in AEGIS's genetic evolution fitness function.

**From info-advantage-study:**

Novelty scoring: assigns higher prior probability to trades that occur in "novel" market conditions (low KL-divergence between current state and historical distribution). This penalizes the agent for pattern-matching in regimes it has already failed in. Directly applicable to the AEGIS regime veto — instead of binary veto, use novelty score as a multiplier.

---

### 4:09 PM IST — Funded Bot Wallet

$8.82 USD equivalent in ETH transferred to the bot wallet:
`0x86c2C9b1Fc8D9662dA6AFB44541eb5964b5dc424`

Amount deliberate: enough for 2-3 test swaps on Base (gas on Base is ~$0.01-0.05 per tx), but small enough that losing it all wouldn't sting.

---

### 4:27 PM IST — 6 Real Mainnet Trades Executed

The mainnet trader was given the following execution sequence:

1. Wrap 0.001 ETH → WETH (WETH contract: `0x4200000000000000000000000000000000000006`)
2. Approve WETH for SwapRouter02 (`0x2626664c2603336E57B271c5C0b26F421741e481`)
3. Swap WETH → USDC via Uniswap V3 (exactInputSingle, 0.05% fee tier)
4. Approve USDC for SwapRouter02
5. Swap USDC → WETH (round-trip)
6. Unwrap WETH → ETH

All 6 transactions confirmed within 22 seconds. Base block time ~2s means roughly 11 blocks for the full sequence.

The round-trip completed: 0.001 ETH → 0.000999 ETH (0.1% loss, all to fees and slippage as expected).

---

## 2. Key Agent Interactions — What Prompts Were Used

### 2.1 Strategy Engine Build (Initial Subagent)

**Pattern used:**
```
[TASK] Build complete trading agent codebase.
[CONTEXT] Hackathon: The Synthesis. Chain: Base. DEX: Uniswap V3/V4.
[REQUIREMENTS] List of specific modules (25+ files)
[TECHNICAL SPEC] Each module with precise interface
[QUALITY BAR] Production-quality, not prototype
[CONSTRAINTS] No mock data in backtest engine, realistic costs, bias audit built in
[OUTPUT FORMAT] Files created at specified paths with specified interfaces
```

Key: the technical spec was detailed enough that the subagent didn't have to make architectural decisions. Architectural decisions made upfront → less rework.

### 2.2 Quant Iteration Loop (Opus Analyst)

**Pattern used:**
```
[ROLE] You are the AEGIS Quant Analyst. Your job is systematic strategy improvement.
[CONTEXT] Previous iteration results: [pasted JSON]
[TASK] Run iteration N. One targeted change. Full backtest. Report metrics.
[MANDATORY CHECKS] 13-point bias audit, P&L cross-check
[REPORTING FORMAT] JSON with all required fields
[CONSTRAINT] Do not change multiple things at once. Isolate variables.
```

The "isolate variables" constraint was critical. Earlier iterations where multiple parameters were changed simultaneously made it impossible to identify what worked. Iteration 3 (change ONLY the band-proximity condition) gave the clearest signal.

### 2.3 Final Verdict (Maximum Rigor)

**Pattern used:**
```
[ROLE] You are the final arbiter. Your job is to determine if this is ready for real money.
[BIAS] Assume it's NOT ready. Find evidence that would change your mind.
[REQUIRED TESTS] List all 7 tests (OOS, stress tests ×4, bootstrap, random benchmark)
[FORMAT] Report each test individually. Final verdict: READY or NOT READY.
[PRINCIPLE] A false positive (claiming it works when it doesn't) costs real money.
           A false negative (saying it doesn't work when it does) costs only opportunity.
           Be conservative.
```

The adversarial framing ("assume it's NOT ready") is important. A positive-framing prompt tends to produce optimistic results. The conservative version produces the honest verdict.

### 2.4 Firecrawl Research Searches

Five key queries (paraphrased, results summarized in timeline above):
1. Polymarket arb bot methodology
2. Base chain trading agents profitability
3. Jake Nesler Claude Prophet results
4. MEV competition L2 landscape
5. Midas Arena architecture

Firecrawl was used rather than basic web search because it returns full page content, not just snippets. The Polymarket arb analysis required reading full blog posts, not just headlines.

### 2.5 Mainnet Trader Build and Verification

**Build pattern:**
```
[TASK] Build mainnet_trader.py — a CONSERVATIVE, safe mainnet execution module.
[REQUIREMENTS]
  - Dry-run mode by default (simulate without executing)
  - Manual confirmation required before live execution
  - Only use addresses verified against official contract lists
  - Amount hardcoded to 0.001 ETH maximum
  - Full transaction logging to mainnet_trades.json
  - Verify each tx with receipt before proceeding to next
[CRITICAL] This will run against real mainnet. No mock mode. Every bug costs money.
```

The verification step ("verify each tx with receipt before proceeding") was essential. Without it, the approval tx might not be mined before the swap attempts, causing the swap to fail.

**Dry run → live sequence:**
1. Run with `DRY_RUN=True` — logs everything without executing
2. Review logged tx calldata manually
3. Verify contract addresses against Uniswap official docs
4. Set `DRY_RUN=False`
5. Run with 0.0001 ETH first (minimum viable amount)
6. Confirm first tx on Basescan
7. Run full 0.001 ETH sequence

---

## 3. Key Technical Decisions & Why

### 3.1 Why HMM for Regime Detection (and Why It Failed)

**Why HMM:**
- Existing work from Vivek's FUND research. Known to work on equity indices at daily timeframe.
- Theoretically sound: markets do switch between regimes; HMMs model this switching probability
- Fast inference: GaussianHMM with 4 states on 5 features is <1ms per prediction
- Interpretable: each regime has a clear economic narrative

**Why it failed on this data:**
- The Dec 2025 - Mar 2026 ETH bear (−46%) was a sustained directional move. HMMs trained on ~300 bars struggle to distinguish "slow bear" from "mean-reverting choppy" because both look like low-momentum, oscillating price action at the wrong zoom level.
- 50-bar lookback for inference is too short for reliable state assignment in crypto. Equities change regimes over weeks; hourly crypto can look like 4 different regimes in a single day.
- No Viterbi smoothing was applied to the inferred path. The raw state sequence was noisy (regime flipping every few bars), making strategy switching counterproductive.

**The proof it failed:** Flipping BULL_TRENDING and BEAR_TRENDING labels improved performance by +1.02%. This means the HMM was labeling trending periods *backwards* more often than correctly. A regime detector that's worse than a coin flip is actively harmful.

**What to do instead:**
- Simple 200-SMA above/below for broad bull/bear classification
- Realized volatility z-score for CHOPPY vs CALM classification
- These don't require training and don't overfit to the specific bear market period

### 3.2 Why Walk-Forward Instead of Train/Test Split

Standard approach: split data 80/20, train on 80%, test on 20%.

The problem: every parameter choice (even on the training set) is implicitly informed by the tester looking at the test results. Over 9 iterations, even "train-only" optimization starts overfitting to the test set through the human-in-the-loop.

Walk-forward: the training window rolls forward. Test window is always in the strict future relative to ANY decision made during the iteration. The 13-point bias audit additionally checks for any variable that "knows the future."

We used walk-forward specifically because of the 2:43 AM directive: "multiple proof checking methods." Single train/test is one check. Walk-forward + out-of-sample + bootstrap is three independent checks.

### 3.3 Why We Added Governance Veto (TradingAgents Paper Inspiration)

The TradingAgents paper (UCLA/MIT) showed that adding a multi-agent debate step — Bull Researcher, Bear Researcher, Risk Manager — significantly improved trading decisions. The key insight: the Risk Manager reviews AFTER the trader decides but BEFORE execution. This is not a stop loss; it's a pre-trade sanity check.

Applied to AEGIS: six non-negotiable rules that must all pass. The analogy to a multi-agent debate: the governance module is the "Risk Manager agent" arguing against the strategy's "Trade Agent."

In iteration 8, the governance module vetoed 76 trades. The resulting performance (Sharpe 8.14) was significantly better than iteration 7 (Sharpe 2.29) despite having MORE candidate trades. The governance was selectively removing the bad ones.

### 3.4 Why Cross-DEX Arb (The Polymarket $40M Research)

The Polymarket arb research showed a fundamental truth: mathematical certainty beats statistical prediction every time.

The DEX equivalent: on Base chain, Uniswap V3 and Aerodrome Finance both provide WETH/USDC liquidity. After a large trade, one pool's price will lag the other until arbitrageurs close the gap. The gap is guaranteed to close (price cannot diverge indefinitely — if it did, someone would buy the cheap one and sell the expensive one, which is what we're doing).

Two reasons to favor Base over mainnet:
1. Lower competition (Base is newer, less saturated with arb bots)
2. Gas costs ~$0.01-0.05 per tx vs $5-50 on Ethereum mainnet — tiny arb spreads are profitable

The limitation: real cross-DEX arb requires live price feeds from both DEXes simultaneously. The backtest used simulated N(0, 0.15%) noise as a proxy. This was the core flaw in the iteration 9 result — the arb simulation generated identical pseudo-random profits regardless of the underlying asset.

### 3.5 Why Directional Strategies Failed (Transaction Cost Drag)

The economics are straightforward:
- Round-trip cost: 0.15% minimum
- Signal correctness: ~35-43% win rate (across all iterations)
- Break-even win rate at 1:1 R:R: 50%
- Gap: 7-15 percentage points below break-even

Even with 2:1 or 3:1 R:R targets, the strategies never achieved the win rate required to overcome costs. The root cause: SMA crossovers and Bollinger Band touches are lagging indicators that fire after the price has already moved. The "signal" is largely the price move we wanted to capture, not a predictor of future moves.

The EMH (Efficient Market Hypothesis) predicts this: liquid markets price in all available information. SMA crossovers and RSI levels are publicly known, widely used, and therefore fully discounted. There is no alpha left in well-known technical indicators at the hourly timeframe in a liquid market.

The only exception: genuine information asymmetry (live FinBERT on news before the market reacts) or mathematical certainty (arb).

### 3.6 Why Iteration 7 Worked but Iteration 9 Didn't (Overfitting vs Robustness)

**Iteration 7: +0.09%, Sharpe 2.29**
The key changes: cost-aware minimum profit threshold, asymmetric R:R (1:3), regime gating. These changes reduced trade count from 200-330 to 74. The 74 trades were genuinely higher quality.

BUT: with only 74 trades, the 95% CI on any metric is massive. The positive Sharpe could be sampling noise. We hadn't tested out-of-sample.

**Iteration 8: +0.75%, Sharpe 8.14**
Adding simulated cross-DEX arb inflated the results because the arb noise simulation was deterministic given the random seed. It produced identical "profits" on any underlying price series. The 100% arb win rate was a simulation artifact, not real edge.

**Iteration 9: +0.08%, p-value 0.57**
When the full validation suite was applied:
- Out-of-sample BTC/USD showed -0.30% (directional strategies have no generalizability)
- Bootstrap showed <50% of samples profitable
- Random benchmark comparison showed AEGIS underperforming randomness

The directional strategies were always weak — the positive Sharpe in iteration 7 was 74 lucky trades in a specific 83-day window. The arb in iteration 8 was a simulation artifact. Iteration 9 stripped away both illusions.

**The lesson:** A single metric (Sharpe, return) on a single dataset is nearly always misleading. Robustness requires: (a) out-of-sample test, (b) bootstrap CI, (c) comparison to simple baseline. All three together catch what any single test misses.

---

## 4. What We Learned From Research

### 4.1 The Three Categories of "Claude Trading Profits"

From the Firecrawl research, every profitable Claude trading agent fell into one of three categories:

**Category 1: Mathematical Arbitrage**
Examples: Polymarket sum-to-one arb, cross-DEX spread capture, funding rate arb (perps vs spot).
*Why it works:* Guaranteed profit at settlement. No directional prediction required. Edge is mathematical, not statistical.
*Risk:* Competition compresses spreads. Need speed. Infrastructure costs matter.

**Category 2: Structural Process Automation**
Examples: Jake Nesler's rebalancing, systematic 50/50 rebalancing strategies, LEAPS selling.
*Why it works:* Not trying to predict. Exploiting structural features (volatility harvesting, time decay, mean reversion with a structural anchor like option delta).
*Risk:* Works until the structural feature changes (e.g., vol regime change).

**Category 3: Hype / Demo Bots**
Examples: Most hackathon submissions, "our bot earned 200%!" posts.
*Why it "works":* Cherry-picked backtest window, no cost model, no bias audit, single favorable period.
*Risk:* Puts real money in, loses it.

AEGIS started as Category 3 (good architecture, unverified edge) and the rigorous testing confirmed it. The pivot to cross-DEX arb is targeting Category 1.

### 4.2 Key Book Insights (FinBooks RAG)

**Insight 1: Execution cost ratio threshold**
A strategy needs gross alpha ≥ 3× execution costs to survive real deployment with meaningful capital. At 0.15% round-trip costs, minimum viable gross alpha per trade is 0.45%. Our strategies were generating ~0.02-0.10% gross per trade. Math was against us from the start.

**Insight 2: Data snooping bias / multiple testing**
With 9 iterations optimizing on the same dataset, the effective required significance level is Bonferroni-corrected: 0.05/9 ≈ 0.0056. Our p-value of 0.5657 is not just statistically insignificant — it's 100× worse than even the uncorrected threshold.

The practical implication: if you tune parameters 9 times on the same dataset, you need much stronger evidence to claim significance. The right approach is to split a fresh holdout dataset that was never touched during optimization.

**Insight 3: Regime detection on short windows**
Literature on HMM regime detection consistently recommends 12+ months of training data for financial time series. The AEGIS HMM was trained on ~90 days (2160 hourly bars) and inferred on 50-bar windows. This is insufficient for reliable regime classification. The model will overfit to the specific characteristics of the training period.

### 4.3 The RPI-DDPG From FUND Codebase

RPI-DDPG = Risk-Penalized Information Deep Deterministic Policy Gradient

The key innovation over standard DDPG: the reward function includes a mutual information term between the policy's information extraction and the environment state. This prevents the RL agent from finding policies that appear to extract information but are actually pattern-matching to noise.

Applied to AEGIS: instead of genetic evolution purely on Sharpe ratio, the fitness function should penalize strategies that trade frequently in regime-labeled states where historical performance is consistently negative. The mutual information check asks: "is the strategy actually learning about the market, or just memorizing this specific period?"

For future implementation, port the FUND Trial 3 reward function directly:
```python
fitness = sharpe - λ * max_drawdown - μ * transaction_costs - ν * (1 - info_ratio)
```
where `info_ratio` measures signal quality (similar to IC — information coefficient).

### 4.4 Novelty Scoring (info-advantage-study)

The info-advantage-study found that agents which prioritized "novel" market states (states with low similarity to previously seen states) outperformed agents that relied on historical pattern recognition.

Practical application to AEGIS: add a novelty score to the governance veto. Before any trade:
1. Compute current market state vector
2. Calculate distance from nearest historical state in training data
3. If distance is low (very similar to past states where we've consistently lost) → reduce position size or veto
4. If distance is high (genuinely novel conditions) → allow full position

This directly addresses the HMM failure: instead of trying to label the regime, ask "have we been in this exact situation before and made money?"

### 4.5 TradingAgents Multi-Agent Debate Framework

The full TradingAgents architecture (UCLA/MIT):
- 7 specialized agents (Fundamental, Sentiment, News, Technical analysts + Bull, Bear researchers + Risk Manager)
- Bull and Bear researchers DEBATE before the trader acts
- This dialectical process systematically catches blind spots
- Risk Manager reviews AFTER trader decides but BEFORE execution

Results in the paper: outperformed Buy & Hold, MACD, KDJ, RSI, SMA baselines. Improved both Sharpe AND reduced max drawdown.

The AEGIS governance veto system is the "Risk Manager" component. Missing: the Bull/Bear debate. An LLM pre-trade reasoning step (one pass arguing FOR the trade, one pass arguing AGAINST, then a synthesis) would be the natural next upgrade.

---

## 5. Honest Assessment

### What Worked

**1. Walk-forward backtester with bias audit**
The engine is technically sound. 13/13 bias audit checks pass. P&L cross-check matches across all iterations. The infrastructure is reusable for any strategy development.

**2. Governance veto system**
The six-rule governance layer actually prevented losing trades in a measurable way. The jump from iteration 7 (Sharpe 2.29) to iteration 8 (Sharpe 8.14) was partly attributable to the 76 trade vetoes. Even with simulated arb inflating the results, the directional trade quality improved with the veto layer.

**3. Honest reporting**
Most teams would have stopped at iteration 7 (+0.09%, Sharpe 2.29) and called it working. We ran the full validation suite and found it wasn't. This is the actual contribution: a methodology for honest DeFi strategy evaluation.

**4. Real mainnet execution**
6 confirmed transactions in 22 seconds. The execution layer works. The mainnet_trader.py module is production-quality with proper error handling, receipt verification, and JSON logging.

**5. Architecture for future work**
The cross-DEX arb strategy concept, the genetic evolution engine, the regime detection framework — all of these are usable foundations. The problem isn't the architecture. The problem is the signal quality.

### What Didn't Work

**1. HMM Regime Detection (actively harmful)**
The regime detector assigned MEAN_REVERTING labels during a sustained -46% bear market. Flipping the labels improved performance. This is worse than useless. Root cause: 90-day training window insufficient; hourly crypto too noisy for HMM.

**2. Directional TA strategies after costs**
Momentum (SMA crossover + ADX), Mean Reversion (Bollinger Bands + VWAP), and Sentiment Pulse all lost money across 9 iterations. The fundamental problem: 0.15% round-trip cost requires 50%+ win rate at 1:1 R:R, and these signals achieve 35-43%.

**3. Simulated cross-DEX arb**
The N(0, 0.15%) noise proxy for arb spreads generated identical results on ETH and BTC because the random seed was fixed. This was an artifact, not real edge. Real arb requires live price feeds from both DEXes.

### What We'd Do Differently

1. **Start with arb, not prediction.** Build the cross-DEX feed comparison first. Test with real prices. Only add prediction-based strategies after the arb baseline is established.

2. **Shorter feedback loops.** The first 6 iterations of the backtest used the same data and same methodology. Iteration 7's "cost-aware minimum profit threshold" should have been iteration 1. The fundamental economics of costs vs win rate should be the starting point.

3. **Live paper trading before backtesting.** Run the strategy on live data for 48 hours in paper mode before ever touching historical backtesting. This surfaces real-world issues (rate limits, data gaps, latency) that backtests hide.

4. **Simple regime detection first.** 200-SMA above/below for bull/bear. Realized vol z-score for choppy vs calm. These don't require training and don't overfit. HMM requires a clean, long dataset that hourly crypto doesn't provide.

### The Fundamental Insight

> "Good risk management × zero edge ≈ approximately zero returns."

The governance system, Kelly sizing, stop losses, and drawdown circuit breakers all worked exactly as designed. They prevented catastrophic losses. But they cannot create alpha where no alpha exists.

A 5% stop loss on a coin flip trade doesn't make money. It just controls the loss rate. The AEGIS infrastructure is designed to manage risk around an edge. Finding the edge is the hard part.

In crypto trading circa 2026, the available edges are:
1. Mathematical arb (DEX spreads, funding rates, sum-to-one prediction markets)
2. Structural features (staking yield differential, cointegrated pairs with error-correction)
3. Information advantage (live NLP on news before market reacts, on-chain flow analysis)

Statistical prediction on hourly prices using publicly available indicators is not an edge. The market has already priced it in.

---

## 6. Replication Guide

### 6.1 Prerequisites

```bash
# Python 3.13 required
python --version  # Must be 3.13+

# Install all dependencies
pip install web3 eth-account hmmlearn scikit-learn numpy pandas aiohttp python-dotenv rich transformers torch

# Verify critical packages
python -c "import hmmlearn; print('hmmlearn ok')"
python -c "import web3; print('web3 ok')"
python -c "from transformers import pipeline; print('transformers ok')"
```

### 6.2 Environment Setup

Create `.env` in the project root (`synthesis-trading-agent/`):

```bash
# RPC (get from Alchemy: alchemy.com → Create App → Base Mainnet)
BASE_MAINNET_RPC=https://base-mainnet.g.alchemy.com/v2/YOUR_KEY
BASE_SEPOLIA_RPC=https://base-sepolia.g.alchemy.com/v2/YOUR_KEY

# Wallet (NEVER use your main wallet — create a burner!)
PRIVATE_KEY=0x_YOUR_BURNER_PRIVATE_KEY

# Bot wallet address (derived from PRIVATE_KEY)
AGENT_WALLET=0x86c2C9b1Fc8D9662dA6AFB44541eb5964b5dc424

# Sentiment (optional for backtesting, required for live)
CRYPTOPANIC_API_KEY=your_key_here

# Amount limits
MAX_TRADE_ETH=0.001
DRY_RUN=True  # Set to False for real execution
```

### 6.3 Smoke Test

```bash
cd projects/synthesis-trading-agent
python agent/test_quick.py
```

Expected output:
```
✓ HMM regime detector: initialized
✓ Strategies: AdaptiveMomentum, MeanReversion, SentimentPulse, CrossDexArb
✓ Governance veto: 6 rules loaded
✓ Position sizer: Kelly criterion ready
✓ Genetic evolver: population initialized
All systems OK
```

### 6.4 Real Data Test

```bash
python agent/test_real_data.py
```

This fetches 30 days of live ETH/USD data from CoinGecko (free, no API key needed) and runs signal generation.

Expected output:
```
Fetched 720 hourly bars (ETH/USD)
Running strategy signals...
Trades generated: ~5-15
Win rate: ~40-70% (varies with market conditions)
Return: ~0.00-0.05%
Note: 30-day sample too small for significance
```

### 6.5 Run All Backtest Iterations

```bash
python agent/backtest/run_iterations.py
```

This runs iterations 1-5 sequentially on the full 2001-bar ETH/USD dataset. Takes ~10-15 minutes.

Results saved to:
```
agent/backtest/results/
  backtest_iter1_baseline.json
  backtest_iter2_tuned.json
  backtest_iter3_mr_fixed.json
  backtest_iter4_quality.json
  backtest_iter5_final.json
  equity_iter*.csv       (equity curves for plotting)
  trades_iter*.csv       (full trade logs)
```

To run specific iterations:
```bash
python agent/backtest/run_iter7.py   # First profitable iteration
python agent/backtest/run_iter8.py   # Cross-DEX arb iteration
```

### 6.6 Run Final Verdict

```bash
python agent/backtest/run_iter9_final.py
```

This is the comprehensive validation suite. Runtime: ~20-30 minutes (includes bootstrap with 1000 samples).

Expected output: Full report with:
- Primary ETH/USD results
- Out-of-sample BTC/USD results
- All 4 stress tests
- Bootstrap p-value
- Random benchmark comparison
- Per-strategy breakdown
- Final verdict: NOT READY

### 6.7 Run Mainnet Trades

**Caution: This uses real ETH on Base Mainnet.**

Prerequisites:
1. Fund the bot wallet with at least 0.005 ETH (0.001 for the swap + ~0.004 for gas buffer)
2. Set `DRY_RUN=False` in `.env`
3. Set `PRIVATE_KEY` to the bot wallet's private key

```bash
# First: dry run to verify calldata
DRY_RUN=True python agent/mainnet_trader.py

# Review logs — should show all 6 transaction calldata without executing
# Then for real execution:
DRY_RUN=False python agent/mainnet_trader.py
```

Trade log saved to: `agent/mainnet_trades.json`

Each transaction is verified with receipt before proceeding to the next. Full sequence takes ~25-30 seconds on Base.

### 6.8 Run Full Agent Loop

```bash
python agent/main.py
```

This runs the complete DISCOVER → PLAN → EXECUTE → VERIFY → LEARN loop.

In live mode (DRY_RUN=False), it will:
1. Fetch current ETH/USDC prices from Uniswap V3 events
2. Run HMM regime detection
3. Score strategies
4. Apply governance veto
5. If trade passes: execute via mainnet_trader
6. Log to mainnet_trades.json
7. Update genetic evolution fitness scores
8. Sleep 15 minutes, repeat

**Warning:** The directional strategies lose money (as documented). Only run with funds you're willing to lose while testing.

---

## 7. File Inventory

Complete listing of every file, its purpose, and approximate line count:

### Root Level
| File | Purpose | Size |
|------|---------|------|
| `ARCHITECTURE.md` | Full system architecture (26KB) | Reference design |
| `RESEARCH_INSIGHTS.md` | What research found about profitable strategies | Summary |
| `SKILL.md` | Agent build skill for OpenClaw | Project brief |
| `TODO.md` | Hackathon task tracker | Work log |
| `README.md` | Public-facing project description | 5.6KB |
| `requirements.txt` | Python dependencies | All packages |
| `.env` | Environment variables (gitignored) | Secrets |

### agent/ (Core Python Agent)
| File | Purpose | Lines (approx) |
|------|---------|----------------|
| `main.py` | Orchestration loop. DISCOVER→PLAN→EXECUTE→VERIFY→LEARN | ~700 |
| `mainnet_trader.py` | Live Base mainnet execution via Uniswap V3 | ~500 |
| `config.py` | All configuration: addresses, params, thresholds | ~130 |
| `check_balance.py` | Quick wallet balance check utility | ~10 |
| `market_check.py` | Live market data fetcher and regime check | ~130 |
| `test_quick.py` | Smoke test — verifies all modules initialize | ~70 |
| `test_real_data.py` | 30-day real data signal generation test | ~170 |
| `mainnet_trades.json` | Log of executed mainnet transactions | Data file |
| `__init__.py` | Package init | ~2 |

### agent/regime/ (HMM Regime Detection)
| File | Purpose | Lines |
|------|---------|-------|
| `hmm_detector.py` | GaussianHMM 4-state regime classifier | ~260 |
| `regime_types.py` | Regime enum and metadata (labels, multipliers) | ~40 |
| `__init__.py` | Package exports | ~4 |

### agent/strategies/ (Trading Strategies)
| File | Purpose | Lines |
|------|---------|-------|
| `base_strategy.py` | Abstract base class with common interface | ~115 |
| `adaptive_momentum.py` | SMA crossover + ADX trend filter + RSI | ~285 |
| `mean_reversion.py` | Bollinger Bands + VWAP reversion | ~280 |
| `sentiment_pulse.py` | FinBERT sentiment shift + volume confirm | ~225 |
| `cross_dex_arb.py` | Uniswap vs Aerodrome spread capture | ~207 |
| `cross_pool_arb.py` | Within-Uniswap multi-pool arb | ~193 |
| `__init__.py` | Strategy registry | ~10 |

### agent/risk/ (Risk Management)
| File | Purpose | Lines |
|------|---------|-------|
| `governance.py` | 6-rule pre-trade veto system | ~280 |
| `position_sizer.py` | Kelly criterion + regime multiplier | ~113 |
| `stop_manager.py` | Dynamic stop loss + trailing stop | ~170 |
| `__init__.py` | Package exports | ~5 |

### agent/evolution/ (Genetic Algorithm)
| File | Purpose | Lines |
|------|---------|-------|
| `genetic.py` | Selection, crossover, mutation engine | ~390 |
| `__init__.py` | Package exports | ~3 |

### agent/execution/ (Trade Execution)
| File | Purpose | Lines |
|------|---------|-------|
| `uniswap_router.py` | Uniswap V3/V4 calldata builder | ~440 |
| `aerodrome_router.py` | Aerodrome Finance execution | ~340 |
| `gas_optimizer.py` | Gas price estimation and profitability gate | ~140 |
| `__init__.py` | Package exports | ~6 |

### agent/sentiment/ (NLP Pipeline)
| File | Purpose | Lines |
|------|---------|-------|
| `finbert_scorer.py` | HuggingFace FinBERT inference pipeline | ~215 |
| `crypto_panic.py` | CryptoPanic news feed ingestion | ~156 |
| `__init__.py` | Package exports | ~5 |

### agent/identity/ (On-Chain Identity)
| File | Purpose | Lines |
|------|---------|-------|
| `erc8004.py` | ERC-8004 agent identity management | ~207 |
| `bond_credit.py` | bond.credit ACE performance reporter | ~201 |
| `__init__.py` | Package exports | ~7 |

### agent/backtest/ (Backtesting Engine)
| File | Purpose | Lines |
|------|---------|-------|
| `engine.py` | Walk-forward backtester (core engine) | ~720 |
| `bias_audit.py` | 13-point look-ahead bias detection | ~316 |
| `dashboard.py` | Rich terminal visualization | ~245 |
| `analyze_trades.py` | Trade-level P&L analysis utilities | ~81 |
| `deep_analyze.py` | Per-regime, per-strategy deep analysis | ~103 |
| `run_iterations.py` | Runs iterations 1-5 in sequence | ~172 |
| `run_iter6.py` | Iteration 6: multi-condition entry stacking | ~287 |
| `run_iter7.py` | Iteration 7: cost-aware params (first profitable) | ~262 |
| `run_iter8.py` | Iteration 8: cross-DEX arb + governance | ~573 |
| `run_iter9_final.py` | Iteration 9: full validation suite | ~1,590 |
| `ITERATION_LOG.md` | Human-readable iteration changelog | Documentation |
| `VERDICT.md` | Final honest assessment | Documentation |
| `__init__.py` | Package init | ~1 |

### contracts/ (Solidity)
| File | Purpose |
|------|---------|
| `foundry.toml` | Foundry configuration |
| `remappings.txt` | Import path remappings |

*(Note: .sol contracts not compiled/deployed for hackathon — Python execution layer was sufficient for mainnet trades via SwapRouter02 directly)*

---

## Key Numbers to Remember

| Metric | Value |
|--------|-------|
| Total Python files | 30+ |
| Total code size | ~156KB |
| Backtest iterations | 9 |
| Backtest data | 2001 bars ETH/USD hourly (Dec 28, 2025 – Mar 22, 2026) |
| Out-of-sample data | 2001 bars BTC/USD (same period) |
| Best iteration return | +0.75% (iter 8, includes simulated arb) |
| Best honest return | +0.09% (iter 7, no arb artifact) |
| Final verdict return | +0.078% ETH / -0.30% BTC |
| Bootstrap p-value | 0.5657 (not significant) |
| Governance vetoes (iter 8) | 76/227 candidate trades (33%) |
| Mainnet transactions | 6 confirmed |
| Bot wallet | 0x86c2C9b1Fc8D9662dA6AFB44541eb5964b5dc424 |
| Total build time | ~14 hours (2:20 AM to 4:30 PM, March 22, 2026) |
| Capital at risk | $8.82 (~0.003 ETH at March 2026 prices) |

---

## What To Do Next (Priority Order)

1. **Build real cross-DEX arb** — Real-time price comparison between Uniswap V3 and Aerodrome. This is the only strategy component with genuine mathematical edge. Start with paper trading for 48 hours.

2. **Polymarket cross-market dependency arb** — Research the sum-to-one mechanic and extend it to correlated contract pairs. Lower competition than DEX arb; guaranteed settlement.

3. **ETH/stETH cointegrated pair trading** — Structural anchor (staking yield) makes this a legitimate mean-reversion trade with known bound. Implement Engle-Granger cointegration test first to verify the spread is stationary.

4. **Port FUND Trial 3 RL architecture** — Replace genetic evolution fitness function with RPI-DDPG reward function. This adds the information-theoretic filter that prevents overfitting.

5. **Kill the directional strategies** — AdaptiveMomentum and SentimentPulse are value-destructive. Remove from the live agent until there's a clear plan to add genuine edge (live FinBERT on news, on-chain flow, etc.).

6. **Fix or remove the HMM** — Replace with 200-SMA above/below + realized vol z-score. Simple, interpretable, doesn't require training.

---

*Written: March 22, 2026*
*Author: Sriram Kintada (documented by Alex ⚡)*
*Classification: Internal research journal — not for public distribution*
