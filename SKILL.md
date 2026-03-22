# AEGIS Trading Agent — Build Skill

## Overview
AEGIS (Autonomous Epistemic Genesis Intelligence System) is an on-chain autonomous trading agent for The Synthesis hackathon on Base chain. It combines institutional quant methods (HMM regime detection, FinBERT sentiment, RL portfolio optimization) with DeFi execution (Uniswap v4 on Base).

## Target Tracks
1. **Autonomous Trading Agent (Base)** — 3 × $1,667
2. **Agentic Finance (Uniswap)** — $2,500
3. **Agents that pay (bond.credit)** — $1,000

## Architecture
```
DISCOVER → PLAN → EXECUTE → VERIFY → LEARN
   │          │         │          │        │
   ▼          ▼         ▼          ▼        ▼
 Prices    HMM      Uniswap    Confirm   Evolve
 Sentiment Regime   v4 Swap    P&L       Strategies
 DEX data  Select   Position   Bond      Genetic
           Strategy  Size      Score     Algorithm
```

## Tech Stack
- **Agent Core:** Python 3.11+ (asyncio)
- **Contracts:** Solidity + Foundry
- **Chain:** Base (mainnet + Sepolia testnet)
- **DEX:** Uniswap v4 (Universal Router, Trading API)
- **ML:** hmmlearn (regime), transformers/FinBERT (sentiment), torch (RL)
- **Web3:** web3.py, ethers.js (for contract deployment)
- **Identity:** ERC-8004 on Base

## Key Differentiators vs Competitors
1. **HMM Regime Awareness** — Agent adapts strategy to market regime (trending/mean-reverting/volatile). Most bots run same logic in all markets.
2. **Institutional Sentiment** — FinBERT scoring similar to what Alpha Alternatives (India's largest alt manager) uses.
3. **Genetic Strategy Evolution** — Strategies compete; only fittest get capital.
4. **bond.credit Score** — Agent builds on-chain credit to access leveraged capital.

## Build Sequence (48-hour sprint)
### Phase 1: Foundation (0-8h)
- Foundry project + minimal Solidity controller
- Deploy to Base Sepolia
- First swap TxID captured

### Phase 2: Strategy Engine (8-16h)  
- HMM regime detector (port from existing code)
- 2 strategies: Momentum + Mean Reversion
- Uniswap API integration + execution

### Phase 3: Autonomy (16-24h)
- Main agent loop (discover → plan → execute → verify → learn)
- Sentiment integration (CryptoPanic + FinBERT)
- ERC-8004 identity registration

### Phase 4: Enhancement (24-40h)
- bond.credit integration
- Additional strategies (Sentiment Pulse, Cross-Pool Arb)
- Genetic evolution of strategy parameters
- Mainnet deployment if profitable on testnet

### Phase 5: Ship (40-48h)
- README with all TxIDs, architecture, metrics
- 3-min demo video
- Submission to all 3 tracks

## Critical Rules
1. NEVER use main wallet — burner wallet only
2. Test EVERYTHING on Sepolia before mainnet
3. Capture every TxID — they're proof of work
4. Keep positions small ($5-20 per trade)
5. Stop loss on every position (regime-adaptive)
6. Document conversation log for submission

## File Structure
```
projects/synthesis-trading-agent/
├── ARCHITECTURE.md
├── TODO.md
├── SKILL.md (this file)
├── contracts/
│   ├── src/
│   │   ├── AEGISController.sol
│   │   ├── AEGISVault.sol
│   │   └── AEGISReporter.sol
│   ├── script/
│   │   └── Deploy.s.sol
│   └── foundry.toml
├── agent/
│   ├── main.py
│   ├── config.py
│   ├── regime/
│   │   ├── hmm_detector.py
│   │   └── regime_types.py
│   ├── strategies/
│   │   ├── base_strategy.py
│   │   ├── momentum.py
│   │   ├── mean_reversion.py
│   │   └── sentiment_pulse.py
│   ├── execution/
│   │   ├── uniswap_router.py
│   │   └── gas_optimizer.py
│   ├── risk/
│   │   ├── position_sizer.py
│   │   └── stop_manager.py
│   ├── sentiment/
│   │   ├── crypto_panic.py
│   │   └── finbert_scorer.py
│   ├── evolution/
│   │   └── genetic.py
│   └── identity/
│       ├── erc8004.py
│       └── bond_credit.py
├── tests/
├── .env.example
└── README.md
```
