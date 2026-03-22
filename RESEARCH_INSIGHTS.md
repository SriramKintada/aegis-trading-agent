# AEGIS Research Insights — What Actually Works
*Compiled: March 22, 2026*

## 1. The Only Guaranteed Edge: Arbitrage

### How the $14K Polymarket bot worked
- "Sum-to-one" arb: buy Yes + No when combined price < $1.00
- Guaranteed profit at settlement — pure math
- Executed thousands of tiny trades in milliseconds
- Works because thin liquidity ($5K-$15K per side) creates brief mispricing
- Average trade time compressed from 30s to <800ms as automation improved

### Real-world MEV/Arb constraints (from Pawel Urbanek's Rust MEV bot)
- **Infrastructure costs: ~$750/month** for running own nodes on mainnet + L2s
- **>99% of arb opportunities disappear in the next block**
- Mainnet is saturated — bots compete for fractions of a cent
- **L2s (Base, Arbitrum) have less competition** — better for us
- Client-side code is 10K+ LOC, smart contracts only ~500 LOC
- The real bottleneck is LATENCY, not strategy complexity

### Crypto Arb Bot reality check (PixelPlex 2026)
- DEX arb: every swap changes the price along the curve
- Price gaps appear after large trades — pool drifts from market
- Challenges: slippage estimation, route optimization, failed tx handling
- **Derivatives arb (funding rate)** is 75% of crypto volume — bigger opportunity than spot arb
- Key: staying hedged while rates change — not just speed

### For AEGIS:
- Cross-DEX arb (Uniswap vs Aerodrome) is valid but COMPETITIVE
- Our edge: regime-aware arb (only arb when regime says volatility is high → more dislocations)
- On Base specifically: lower competition than mainnet, ~$0.01 gas
- Need own RPC or fast provider (Alchemy/QuickNode)

---

## 2. Multi-Agent Architecture (TradingAgents, UCLA/MIT)

### Framework (published paper, real results)
Seven specialized roles:
1. **Fundamental Analyst** — company fundamentals, valuation
2. **Sentiment Analyst** — social media mood
3. **News Analyst** — macro events, geopolitics
4. **Technical Analyst** — price patterns, indicators
5. **Bull Researcher** — argues positive case
6. **Bear Researcher** — argues negative case, risks
7. **Risk Manager** — exposure limits, vetoes

### Key insight: DEBATE improves decisions
- Bull and Bear researchers DEBATE before trader acts
- This dialectical process catches blind spots
- Risk Management team reviews AFTER trader decides but BEFORE execution
- Fund Manager gives final approval

### Results
- Outperformed Buy & Hold, MACD, KDJ, RSI, SMA baselines
- Improved Sharpe ratio AND reduced max drawdown
- Works without GPUs — runs on API calls to LLMs

### For AEGIS:
- Our governance veto system IS this risk management team
- We could add bull/bear debate as a pre-trade reasoning step
- The multi-agent debate → single trade decision is the winning pattern

---

## 3. What Actually Made Money (Proven Strategies)

### Jake Nesler's Claude Prophet (+7.6% in 33 days)
- **NOT high frequency** — uses limit orders
- Core: LEAPS options (60-90 DTE) + intraday scalps
- Trades BOTH directions (long AND short)
- Multi-agent governance: CEO agent vetoes bad trades
- 58% cash position — capital preservation is the edge
- Biggest win: +$14,578 from overnight puts when SPY dropped
- Key pattern: "Scale down when Sharpe drops or market gets choppy"

### The $1K Solana Prompts (Milo agent)
- Very specific, measurable criteria — not vague prompts
- "Find tokens with >10x volume AND >30% holder growth" (multiple filters)
- Safety filter: "Avoid tokens with >30% in top 5 wallets" (rug prevention)
- Momentum + volume + on-chain metrics stacked together
- Each prompt generates a specific, testable hypothesis

### Midas Arena (self-improving battle)
- Two agents compete in live 24h trading battles on Base
- Each evolves strategy every 2 hours
- Competition forces adaptation — survival of the fittest
- Uses real money via @bankrbot

### Reddit Options Trader (ROT) — most sophisticated
- 58K LOC production, 117K LOC tests
- Strategy regime detection: bull/bear/sideways/volatile/crisis
- Genetic strategy evolution
- 12-module backtesting engine
- NLP pipeline for Reddit sentiment (custom, not just FinBERT)
- ML credibility scoring for signal quality
- 2:1 test-to-production code ratio

---

## 4. TraderMonty's Claude Skills (Professional Quality)

Most relevant skills for us:
- **Macro Regime Detector** — cross-asset ratios (RSP/SPY, yield curve, credit)
- **Market Breadth Analyzer** — 6-component scoring (0-100)
- **Theme Detector** — lifecycle stages (Emerging → Mature → Exhausting)
- **Institutional Flow Tracker** — 13F filings, smart money accumulation

### For AEGIS:
- Our HMM regime detector maps to their Macro Regime Detector
- Could add market breadth as a secondary filter
- Lifecycle concept (Emerging→Exhausting) maps to our genetic evolution

---

## 5. Degentic Trading Terminal (Production Infrastructure)

- Agent-native terminal built on Claude Code
- Real-time WebSocket data ingestion pipeline
- Multi-DEX routing (Jupiter, Raydium, Meteora on Solana)
- **50/50 rebalancing strategy** — simple but proven
- Built-in: limit orders, stop loss, take profit, DCA
- 3-tier risk system based on token risk level
- Dynamic slippage adjustment based on market conditions

### For AEGIS:
- Rebalancing is a SIMPLE strategy that provably works
- We should add a rebalancing component as fallback
- Their risk tier system (low/med/high → different position sizes) is what our regime multiplier does

---

## 6. Key Patterns Across ALL Profitable Systems

1. **Governance/Veto** — every profitable system has a "check before execute" step
2. **Regime awareness** — adapt strategy to market conditions (everyone does this)
3. **Capital preservation** — 50-60% cash, cut losers fast, never average down
4. **Multiple confirmation** — stack 2-3 signals before entering (volume + price + sentiment)
5. **Both directions** — profitable agents trade long AND short
6. **Limit orders > market orders** — reduces slippage dramatically
7. **Self-improvement loop** — genetic evolution, strategy rotation, learning from mistakes
8. **Specific criteria** — "RSI < 30 AND volume > 10x" beats "buy when oversold"

---

## 7. What DOESN'T Work

1. **Vague prompts** — "find the next 10x" = garbage
2. **High frequency on DEXs** — MEV bots will front-run you
3. **Single strategy** — always loses in some regime
4. **No risk management** — the OpenClaw agent that went to $0 had zero risk controls
5. **Simulated arb with random noise** — real arb opportunities cluster around events, not random
6. **Overfitting to one market regime** — the market WILL change

---

## 8. What We Should Add to AEGIS

### Immediate (before hackathon submission):
- [ ] Out-of-sample validation on different time period
- [ ] Rebalancing as fallback strategy when no signals
- [ ] More specific entry criteria (stack 3 conditions minimum)
- [ ] Limit order simulation in backtest (enter at better price)

### If time permits:
- [ ] Bull/Bear debate step (two LLM passes before trade)
- [ ] Market breadth filter (only trade when breadth > 50)
- [ ] Funding rate arb (perpetuals vs spot)
- [ ] Multi-asset (not just ETH/USDC — add WBTC, major tokens)

---

*This is a living document. Updated as new research is found.*
