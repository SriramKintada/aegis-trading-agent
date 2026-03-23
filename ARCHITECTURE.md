# AEGIS — Autonomous Epistemic Trading Agent
### The Synthesis Hackathon | Architecture Document
*Sriram Kintada | March 2026*

---

## Executive Summary

**AEGIS** (Autonomous Epistemic Genesis Intelligence System) is an on-chain trading agent built on Base that combines institutional-grade quantitative finance with autonomous AI decision-making. It leverages Sriram's existing work on HMM regime detection, GBM strategy scoring, and RL portfolio optimization — deployed fully on-chain, executing real trades on Uniswap v4 on Base.

**What makes AEGIS different from a generic trading bot:**
1. **HMM Regime Awareness** — The agent knows *what market it's in*, not just what price is doing. Hidden Markov Models classify the current regime (trending/mean-reverting/volatile/choppy) and the entire strategy stack adapts accordingly. Most bots run the same logic in all regimes and blow up.
2. **FinBERT Sentiment Layer** — Institutional-grade NLP scoring of news + social feeds, similar to what top quantitative asset managers use in production.
3. **Genetic Strategy Evolution** — Strategies compete in a simulation tournament; only the fittest get capital. Population evolves every 24h.
4. **ERC-8004 Identity** — The agent has a verifiable on-chain identity, reputation, and trackable history — not just a wallet, but a *certified agent*.
5. **bond.credit Score** — The agent's profitable track record accrues an on-chain credit score, enabling it to eventually access leveraged capital from the Agentic Credit Vaults.

**Target Prizes:**
- Base: "Autonomous Trading Agent" — 3 × $1,667
- Uniswap: "Agentic Finance / Best Uniswap API Integration" — $2,500
- bond.credit: "Agents that Pay" — $1,000

---

## 1. System Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                     AEGIS AGENT LOOP                          │
│                                                               │
│  ┌─────────┐   ┌──────────┐   ┌───────────┐   ┌──────────┐  │
│  │DISCOVER │──▶│  PLAN    │──▶│  EXECUTE  │──▶│  VERIFY  │  │
│  │         │   │          │   │           │   │          │  │
│  │- Price  │   │- HMM     │   │- Uniswap  │   │- TxID    │  │
│  │  feeds  │   │  regime  │   │  v4 swap  │   │  confirm │  │
│  │- DEX    │   │- Strategy│   │- Position │   │- P&L     │  │
│  │  events │   │  select  │   │  sizing   │   │  record  │  │
│  │- Senti- │   │- Risk    │   │- Gas opt  │   │- Bond    │  │
│  │  ment   │   │  calc    │   │           │   │  score   │  │
│  └─────────┘   └──────────┘   └───────────┘   └────┬─────┘  │
│       ▲                                             │         │
│       └─────────────── LEARN ◀──────────────────────┘        │
│                    (Strategy fitness                          │
│                     update, genetic                          │
│                     evolution trigger)                       │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Data Sources Layer

### 2.1 On-Chain Price Feeds (Real-Time)

| Source | Data | Latency | Use |
|--------|------|---------|-----|
| Uniswap v4 PoolManager events | Tick-by-tick prices for major pairs | ~1 block (~2s on Base) | Primary price feed |
| Aerodrome Finance subgraph | Base-native token prices, liquidity | ~5s | Liquidity depth context |
| Chainlink Price Feeds (Base) | ETH/USD, BTC/USD, major assets | ~10s | Regime anchor prices |
| Uniswap Subgraph API (GraphQL) | Historical OHLCV, volume, liquidity | 30s batch | Regime training data |
| Uniswap Trading API | Real-time quotes + routes | ~100ms | Pre-trade pricing |

**OHLCV Construction:**
- Aggregate Uniswap swap events into 1m/5m/1h candles on-the-fly
- Store rolling 200-bar window in Redis for regime model inference
- Persist to PostgreSQL for backtesting and evolution

### 2.2 Sentiment Data Sources

| Source | Type | API/Method |
|--------|------|------------|
| CryptoPanic API | News headlines | REST, free tier 100 req/hr |
| Nitter/Twitter scraping | Social sentiment | RSS feed parsing |
| Reddit (r/ethfinance, r/defi) | Community signals | Reddit API |
| Santiment | On-chain social volume | API ($) or free tier |
| Fear & Greed Index | Market sentiment | alternative.me API |

**Processing Pipeline:**
1. Fetch raw text every 15 minutes
2. Run FinBERT inference (pretrained `ProsusAI/finbert` or fine-tuned on crypto)
3. Compute rolling 4h weighted sentiment score: `S_t = Σ(w_i × finbert_score_i) / Σw_i`
4. Classify: Bearish < -0.2 | Neutral [-0.2, 0.2] | Bullish > 0.2

### 2.3 On-Chain Intelligence Signals

- **Whale wallet monitoring:** Track top-10 Uniswap liquidity providers on Base for position changes
- **Gas price oracle:** Base `eth_gasPrice` — spike in gas = high MEV activity = avoid trading
- **Pool liquidity depth:** `StateView` contract queries for tick-level liquidity in Uniswap v4
- **Volume anomaly detection:** Z-score on 20-period volume rolling mean

---

## 3. Strategy Engine

### 3.1 HMM Regime Detection

**The Core Intelligence Layer** — adapted from Sriram's existing work.

```python
# Regime states
REGIMES = {
    0: "BULL_TRENDING",      # Strong upward trend, low volatility
    1: "BEAR_TRENDING",      # Strong downward trend, low volatility  
    2: "HIGH_VOL_CHOPPY",    # High volatility, no clear direction
    3: "MEAN_REVERTING",     # Low volatility, ranging — ideal for grid/arb
}

# Features fed to HMM (computed from 1h OHLCV)
features = [
    log_returns,             # 1h log returns
    realized_volatility,     # 24h rolling vol
    volume_z_score,          # Volume anomaly
    price_momentum_20,       # 20-period momentum
    sentiment_score,         # FinBERT composite
]

# Model: GaussianHMM with 4 states
# Training: 90 days of historical data
# Inference: Every 15 minutes on latest 50 candles
# Output: Current regime + confidence score
```

**Implementation:**
- Python: `hmmlearn.GaussianHMM` (existing Sriram code, port to production)
- Persist trained model weights in IPFS for on-chain proof of training
- Regime state logged on-chain via event emission in `AEGISController.sol`

### 3.2 Strategy Portfolio (Regime-Aware)

Each strategy is scored by GBM (Gradient Boosting Model on backtest metrics) and selected by the RL optimizer based on current regime.

| Strategy | Best Regime | Description | Risk Level |
|----------|-------------|-------------|------------|
| **Momentum Swing** | BULL_TRENDING | Enter on 20/50 MA crossover + sentiment bullish; exit on RSI>70 | Medium |
| **Mean Reversion** | MEAN_REVERTING | Trade Bollinger Band breakouts back to mean; VWAP anchored | Medium |
| **Sentiment Pulse** | Any transitioning | Trade 30min lag on large sentiment shifts (±0.4 change) | High |
| **Cross-Pool Arb** | HIGH_VOL_CHOPPY | Exploit price differences between Uniswap v4 and Aerodrome | Low |
| **Liquidity Provision** | MEAN_REVERTING | Passive LP on tight Uniswap v4 range, auto-rebalance | Low |
| **Breakout Capture** | BULL/BEAR trending | Trade first confirmed breakout + volume confirmation | High |

### 3.3 RL Portfolio Optimizer (Actor-Critic)

The existing actor-critic architecture allocates capital across strategies:

```python
# State space
state = [
    current_regime,           # One-hot encoded (4 regimes)
    strategy_recent_pnl,      # Last 10 trades per strategy (6 strategies)
    portfolio_drawdown,        # Current drawdown from peak
    sentiment_score,           # Current composite sentiment
    market_volatility,         # Realized vol
    base_gas_price,            # Current gas costs
]

# Action space: capital allocation weights [0, 1] per strategy (sum = 1)
# Reward: Sharpe ratio on 24h window minus transaction costs

# Training: PPO on historical data + paper trading
# Live: Re-run every 4h; weights update portfolio allocations
```

### 3.4 Genetic Strategy Evolution

Every 24 hours, the agent runs a mini-evolution cycle:

1. **Population:** 20 strategy variants (different parameter combinations)
2. **Fitness:** Sharpe ratio × win rate × (1 - max_drawdown) on last 24h
3. **Selection:** Top 8 survive
4. **Crossover:** Parameter blending between pairs of survivors
5. **Mutation:** 5% random parameter perturbation
6. **Result:** Replace bottom 4 strategies with evolved children

Parameters evolved per strategy:
- MA periods (fast/slow window)
- RSI overbought/oversold thresholds
- Stop loss / take profit percentages
- Entry confidence threshold
- Position sizing multiplier

---

## 4. Execution Layer

### 4.1 Uniswap v4 Integration (PRIMARY — Hackathon Track)

**Why v4 over v3:**
- Custom hooks enable agent-specific logic (e.g., slippage protection hook)
- Single `PoolManager.sol` contract = gas savings
- Better programmatic control for autonomous agents

**Swap Execution Flow:**
```
AEGISController.executeSwap()
    → Uniswap Trading API → get quote + calldata
    → Validate: price impact < 1%, gas < budget
    → Universal Router.execute(commands, inputs)
    → Verify swap event emitted
    → Update position ledger
    → Emit AEGISTradeEvent (captured by monitor)
```

**Key Contracts Interacted With:**
- `PoolManager` (0x...): Core v4 contract
- `UniversalRouter`: Swap execution
- `StateView`: Pool state queries (tick, liquidity, sqrtPriceX96)
- `Permit2`: Token approvals (gasless via signature)

**Trading Pairs (Base Mainnet/Testnet):**
- Primary: WETH/USDC (deepest liquidity)
- Secondary: cbETH/WETH, WETH/USDT
- Testnet: All above on Base Sepolia

### 4.2 Gas Optimization

```python
# Gas strategy
def should_execute_trade(trade, base_gas_price):
    estimated_cost = gas_estimate * base_gas_price * eth_price
    estimated_profit = trade.expected_pnl
    
    # Only execute if profit > 3× gas cost
    if estimated_profit > 3 * estimated_cost:
        return True, "profitable"
    
    # Exception: sentiment pulse trades are time-sensitive
    if trade.type == "SENTIMENT_PULSE" and trade.urgency == "HIGH":
        return True, "time_sensitive"
    
    return False, "uneconomical"
```

### 4.3 Position Management

```python
# Risk-adjusted position sizing (Kelly-inspired)
def calculate_position_size(signal_confidence, regime, portfolio_value):
    base_size = portfolio_value * MAX_POSITION_PCT  # 10% max per trade
    
    # Kelly fraction: f = (p*b - q) / b
    # p = win_probability, b = avg_win/avg_loss, q = 1-p
    kelly_f = calculate_kelly(signal_confidence, historical_win_rate)
    
    # Half-Kelly for risk management
    kelly_size = portfolio_value * (kelly_f / 2)
    
    # Regime multiplier
    regime_multiplier = {
        "BULL_TRENDING": 1.2,
        "BEAR_TRENDING": 0.6,
        "HIGH_VOL_CHOPPY": 0.4,
        "MEAN_REVERTING": 1.0,
    }[regime]
    
    return min(base_size, kelly_size) * regime_multiplier
```

---

## 5. Risk Management

### 5.1 Hard Limits (Non-negotiable)

| Limit | Value | Override |
|-------|-------|---------|
| Max single trade size | 10% of portfolio | None |
| Max daily loss (drawdown circuit breaker) | 5% of portfolio | None |
| Max total drawdown (emergency stop) | 15% | Manual only |
| Max price impact per trade | 1% | None |
| Min gas profit multiple | 3× | Overrideable for sentiment |
| Max concurrent open positions | 3 | None |

### 5.2 Stop Loss Logic

```python
# Per-trade dynamic stop loss
stop_loss_pct = base_stop_loss * (1 + regime_vol_adjustment)
# Base: 2% for trending, 1.5% for mean-reverting
# Vol adjustment: +50% in HIGH_VOL_CHOPPY regime

# Trailing stop
if unrealized_pnl > take_profit_1:
    stop_loss = entry_price * (1 + 0.5 * initial_stop_offset)  # Trail up
```

### 5.3 Drawdown Circuit Breaker

```python
# Monitored in main loop every 5 minutes
def check_drawdown_breaker():
    daily_pnl = get_daily_pnl()
    total_drawdown = (peak_portfolio - current_portfolio) / peak_portfolio
    
    if daily_pnl < -0.05:  # -5% daily
        pause_trading(duration_minutes=60)
        alert("Daily circuit breaker triggered")
    
    if total_drawdown > 0.15:  # -15% total
        emergency_stop()
        alert("Emergency stop: max drawdown exceeded")
```

---

## 6. Agent Identity & Bond.Credit Integration

### 6.1 ERC-8004 Identity

**Agent Card (JSON, stored on IPFS):**
```json
{
  "name": "AEGIS-v1",
  "description": "Autonomous HMM-guided trading agent on Base",
  "version": "1.0.0",
  "capabilities": ["trading", "portfolio-management", "sentiment-analysis"],
  "protocols": ["https", "a2a"],
  "endpoints": {
    "status": "https://aegis.agent/status",
    "trades": "https://aegis.agent/trades"
  },
  "creator": "0x<sriram_wallet>",
  "created": "2026-03-22T00:00:00Z"
}
```

**On-Chain Registration:**
```solidity
// Register agent identity via ERC-8004 Identity Registry
IIdentityRegistry registry = IIdentityRegistry(ERC8004_REGISTRY);
uint256 tokenId = registry.register(agentCardIPFSHash);
// Agent now has a verifiable identity NFT (ERC-721)
```

### 6.2 Bond.Credit Agentic Credit Engine (ACE)

**How AEGIS builds an on-chain credit score:**

1. **Performance tracking:** Every executed trade emits an event captured by ACE
2. **Score dimensions:** Performance + Risk (max drawdown) + Stability (Sharpe) + Provenance (ERC-8004 ID)
3. **Score accrual:** Profitable 7-day track record → ACE mints credit score NFT
4. **Future utility:** Score enables access to Agentic Credit Vaults → leveraged capital

**Integration:**
```python
# After each trade cycle (every 24h)
bond_credit_api.submit_performance_report({
    "agent_id": ERC8004_TOKEN_ID,
    "period": "24h",
    "trades": all_txids,
    "pnl": daily_pnl_pct,
    "sharpe": rolling_sharpe,
    "max_drawdown": period_max_drawdown,
    "regime_accuracy": hmm_regime_accuracy,
})
```

---

## 7. Smart Contract Architecture

### 7.1 `AEGISController.sol` (Main Controller)

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

contract AEGISController {
    // State
    address public owner;           // Sriram's wallet
    address public agentWallet;     // Hot wallet for trades
    uint256 public erc8004TokenId;  // Agent identity
    
    // Uniswap v4 references  
    IPoolManager public poolManager;
    IUniversalRouter public universalRouter;
    
    // Position tracking
    mapping(bytes32 => Position) public positions;
    
    // Events (for bond.credit ACE and verification)
    event TradeExecuted(
        bytes32 indexed tradeId,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 amountOut,
        string regime,
        string strategy,
        uint256 timestamp
    );
    event RegimeChanged(string oldRegime, string newRegime, uint256 timestamp);
    event CircuitBreakerTriggered(string reason, uint256 timestamp);
    
    // Execute swap via Universal Router
    function executeSwap(
        address tokenIn,
        address tokenOut, 
        uint256 amountIn,
        uint256 amountOutMin,
        bytes calldata routerCalldata,
        string calldata regime,
        string calldata strategy
    ) external onlyAgent {
        // Pre-trade risk check
        require(!circuitBreakerActive, "Circuit breaker active");
        require(amountIn <= maxPositionSize(), "Position too large");
        
        // Execute via Universal Router
        IERC20(tokenIn).approve(address(universalRouter), amountIn);
        universalRouter.execute(routerCalldata);
        
        // Record trade for bond.credit
        bytes32 tradeId = keccak256(abi.encode(block.timestamp, tokenIn, tokenOut));
        emit TradeExecuted(tradeId, tokenIn, tokenOut, amountIn, amountOut, regime, strategy, block.timestamp);
    }
    
    // Emergency stop
    function emergencyStop() external onlyOwner {
        circuitBreakerActive = true;
        emit CircuitBreakerTriggered("Manual emergency stop", block.timestamp);
    }
}
```

### 7.2 `AEGISVault.sol` (Fund Custody)

```solidity
contract AEGISVault {
    // Holds trading capital
    // Only AEGISController can initiate withdrawals for trades
    // Owner can deposit/withdraw
    // All movements emit events (auditable for bond.credit)
}
```

### 7.3 `AEGISReporter.sol` (On-Chain Journal)

```solidity
contract AEGISReporter {
    // Records regime state changes
    // Records strategy activation/deactivation
    // Records genetic evolution outcomes
    // Queryable by bond.credit ACE
    // Verifiable on-chain audit trail
}
```

---

## 8. Tech Stack

### 8.1 Off-Chain (Python Agent)

```
aegis/
├── main.py                    # Agent orchestration loop
├── regime/
│   ├── hmm_detector.py        # HMM model (adapted from existing work)
│   ├── feature_engineer.py    # OHLCV → feature vectors
│   └── models/                # Saved model weights
├── strategies/
│   ├── base_strategy.py       # Abstract base class
│   ├── momentum.py
│   ├── mean_reversion.py
│   ├── sentiment_pulse.py
│   ├── cross_pool_arb.py
│   └── lp_passive.py
├── portfolio/
│   ├── rl_optimizer.py        # Actor-critic from existing work
│   ├── risk_manager.py        # Position sizing, drawdown checks
│   └── genetic_evolver.py     # Strategy evolution
├── data/
│   ├── price_feed.py          # Uniswap v4 event listener (web3.py)
│   ├── sentiment.py           # FinBERT pipeline
│   ├── uniswap_api.py         # Trading API, Routing API, Subgraph
│   └── chainlink.py           # Price oracle fallback
├── execution/
│   ├── onchain.py             # Web3 tx building and submission
│   ├── uniswap_router.py      # Universal Router calldata builder
│   └── gas_oracle.py          # Gas price estimation
├── identity/
│   ├── erc8004.py             # Agent identity management
│   └── bond_credit.py         # Bond.credit ACE reporter
└── monitor/
    ├── verifier.py            # Post-trade verification
    └── dashboard.py           # Simple terminal dashboard
```

### 8.2 On-Chain (Solidity + Foundry)

```
contracts/
├── src/
│   ├── AEGISController.sol
│   ├── AEGISVault.sol
│   └── AEGISReporter.sol
├── test/
│   └── AEGISController.t.sol
├── script/
│   └── Deploy.s.sol
└── foundry.toml
```

### 8.3 Dependencies

**Python:**
```
web3==7.x                  # Ethereum interaction
hmmlearn==0.3.2            # HMM regime detection
transformers==4.40.0       # FinBERT
torch==2.3.0               # RL optimizer
scikit-learn==1.5.0        # GBM strategy scoring
pandas, numpy              # Data processing
aiohttp, asyncio           # Async event loops
rich                       # Terminal dashboard
python-dotenv              # Config management
```

**Solidity:**
```
forge-std                  # Testing framework
@uniswap/v4-core           # PoolManager, hooks interfaces
@uniswap/v4-periphery      # Universal Router, StateView
@openzeppelin/contracts    # ERC721, Ownable, ReentrancyGuard
```

### 8.4 Infrastructure

- **RPC:** Alchemy (Base Mainnet + Base Sepolia) — free tier sufficient for hackathon
- **Subgraph:** Uniswap hosted subgraph (The Graph) for historical data
- **Sentiment:** CryptoPanic free tier, Fear & Greed API
- **Storage:** IPFS (Pinata free tier) for agent card + model hashes
- **Monitoring:** Local PostgreSQL + Redis (Docker) — or hosted Railway.app

---

## 9. What Makes AEGIS Unique

### Versus Generic Trading Bots:

| Feature | Generic Bot | AEGIS |
|---------|------------|-------|
| Market context | Runs same logic always | HMM detects regime; strategy adapts |
| Sentiment | Maybe Twitter sentiment, bag-of-words | FinBERT (transformer, trained on financial text) |
| Strategy selection | Fixed or rule-based | RL actor-critic with regime conditioning |
| Strategy improvement | Manual | Genetic evolution every 24h — self-improving |
| Identity | Anonymous wallet | ERC-8004 verifiable identity + reputation |
| Capital access | Fixed initial capital | Building bond.credit score → future leverage |
| Risk management | Fixed % stop loss | Regime-adaptive Kelly sizing + volatility-aware stops |
| Proof | Claims of profit | Full on-chain audit trail, all TxIDs verifiable |

### Versus Other Hackathon Submissions:

- Most submissions will be **LLM-prompted bots** ("buy when sentiment is bullish")
- AEGIS uses **principled quantitative methods** (HMM, RL, Kelly criterion) that mirror institutional practice
- The **bond.credit integration** creates a compelling narrative: the agent builds its own credit score and could access leverage — an autonomous, self-bootstrapping financial entity
- **Genetic evolution** means the agent presented at demo time is literally better than the one at 9 AM — visible, live improvement

---

## 10. 48-Hour Build Timeline

### Hour 0-8: Foundation (Highest Priority)

- [ ] Set up Foundry project + deploy `AEGISController.sol` to Base Sepolia
- [ ] Get Base Sepolia testnet ETH (Alchemy faucet or base-faucet.vercel.app)
- [ ] Connect to Uniswap v4 on Base Sepolia
- [ ] Execute first manual swap via contract → capture TxID
- [ ] Python: Set up web3.py connection, listen for PoolManager events
- [ ] Python: Build OHLCV aggregator from swap events

### Hour 8-16: Strategy Engine

- [ ] Port HMM regime detector from existing code → Python production module
- [ ] Implement 2 strategies: Momentum + Mean Reversion (simplest, highest reliability)
- [ ] Build risk manager: position sizing + stop loss checks
- [ ] Connect Uniswap Trading API for quotes
- [ ] Run backtest on last 7 days of WETH/USDC data → document results

### Hour 16-24: Autonomy Loop

- [ ] Implement `main.py` agent loop: discover → plan → execute → verify
- [ ] Add sentiment fetcher (CryptoPanic) + simple FinBERT inference
- [ ] Add bond.credit performance reporter
- [ ] Register ERC-8004 agent identity on Base Sepolia
- [ ] First fully autonomous 15-minute trade cycle running

### Hour 24-32: Live Trades + Genetic Evolution

- [ ] Run 24h paper-trading simulation with full logging
- [ ] Implement basic genetic evolver (2-4 strategies)
- [ ] Deploy to mainnet with $50 test capital (or continue Sepolia if preferred)
- [ ] Capture 10+ real TxIDs for demo

### Hour 32-40: Polish + Monitoring

- [ ] Terminal dashboard showing live: regime, sentiment, positions, P&L, TxIDs
- [ ] Add 2 more strategies (Sentiment Pulse + Cross-Pool Arb)
- [ ] Write README, architecture diagram
- [ ] Record demo video showing autonomous loop + on-chain TxIDs

### Hour 40-48: Submission

- [ ] Final code cleanup + open source push to GitHub
- [ ] Write project description for each track (Base, Uniswap, bond.credit)
- [ ] Prepare 3-minute demo video
- [ ] Submit to HackQuest/Synthesis platform

---

## 11. API Keys & Setup Checklist

```bash
# Required before coding starts
ALCHEMY_API_KEY=           # Alchemy.com → Create Base Mainnet + Sepolia app
PRIVATE_KEY=               # Burner wallet for agent (never use main wallet!)
CRYPTOPANIC_API_KEY=       # cryptopanic.com → free registration
UNISWAP_API_KEY=           # (optional) portal.uniswap.org → trading API
PINATA_JWT=                # pinata.cloud → IPFS pinning for agent card
BOND_CREDIT_API_KEY=       # bond.credit → contact for hackathon access

# RPC endpoints
BASE_MAINNET_RPC=https://base-mainnet.g.alchemy.com/v2/${ALCHEMY_API_KEY}
BASE_SEPOLIA_RPC=https://base-sepolia.g.alchemy.com/v2/${ALCHEMY_API_KEY}
```

---

## 12. Deployed Contract Addresses (To Be Updated)

| Contract | Network | Address |
|---------|---------|---------|
| AEGISController | Base Sepolia | TBD |
| AEGISVault | Base Sepolia | TBD |
| AEGISReporter | Base Sepolia | TBD |
| ERC-8004 Agent Identity | Base Sepolia | TBD |

---

## 13. Key Research References

- **Uniswap v4:** https://docs.uniswap.org/contracts/v4/overview
- **Uniswap Trading API:** https://docs.uniswap.org/api/overview
- **Base chain docs:** https://docs.base.org
- **ERC-8004 standard:** https://eips.ethereum.org/EIPS/eip-8004
- **bond.credit:** https://www.bond.credit
- **Synthesis hackathon:** https://synthesis.md/hack/
- **FinBERT:** https://huggingface.co/ProsusAI/finbert
- **Aerodrome (Base's main DEX):** https://aerodrome.finance
- **Coinbase Based Agent (reference):** https://github.com/coinbase/based-agent

---

## 14. Pitch Narrative

> "Every profitable hedge fund knows: the strategy you use in a trending market will lose money in a choppy market. AEGIS knows what market it's in.
>
> Using the same Hidden Markov Models that institutional quant desks use for regime detection — and the same FinBERT-based sentiment analysis that India's largest alternative asset manager uses in production — AEGIS autonomously discovers opportunities, sizes positions with Kelly criterion, and evolves its own strategies genetically, every 24 hours.
>
> It's not just a trading bot. It's an autonomous economic agent with a verifiable on-chain identity, a growing bond.credit score, and — eventually — the ability to borrow its own capital. The agent earns its own leverage."

---

*Document Version: 1.0 | Created: 2026-03-22 | Status: Active*
