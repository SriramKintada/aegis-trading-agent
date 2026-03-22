# AEGIS — Autonomous Epistemic Genesis Intelligence System

> *An autonomous on-chain trading agent with HMM regime detection, FinBERT sentiment, Kelly position sizing, and genetic strategy evolution.*

Built for **The Synthesis Hackathon** — targeting Base, Uniswap, and bond.credit tracks.

---

## What Makes AEGIS Different

| Feature | Generic Bots | AEGIS |
|---------|-------------|-------|
| Market context | Fixed logic always | HMM detects regime; strategy adapts |
| Sentiment | Keyword bag | FinBERT (financial-domain transformer) |
| Strategy selection | Rule-based | Regime-conditioned signal scoring |
| Self-improvement | Manual | Genetic evolution every 24h |
| Identity | Anonymous wallet | ERC-8004 verifiable on-chain identity |
| Capital path | Fixed initial only | bond.credit score → future leverage |
| Risk | Fixed % stops | Kelly criterion + ATR-adaptive stops |

---

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

### Regimes (HMM States)

| Regime | Strategy Active | Risk Multiplier |
|--------|----------------|-----------------|
| `BULL_TRENDING` | Momentum (Long) | 1.2× |
| `BEAR_TRENDING` | Momentum (Short) | 0.6× |
| `HIGH_VOL_CHOPPY` | Reduced position | 0.3× |
| `MEAN_REVERTING` | Mean Reversion | 1.0× |

---

## Setup

### Prerequisites

- Python 3.11+
- Burner wallet (never use main wallet!)
- Base Sepolia ETH (from [base-faucet.vercel.app](https://base-faucet.vercel.app))
- Alchemy API key (free at [alchemy.com](https://alchemy.com))

### Installation

```bash
# Clone / navigate to project
cd projects/synthesis-trading-agent

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# For CPU-only PyTorch (lighter):
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

### Configuration

```bash
# Copy example env
cp .env.example .env

# Fill in your values:
ALCHEMY_API_KEY=your_key
PRIVATE_KEY=0xyour_burner_key   # BURNER WALLET ONLY!
CRYPTOPANIC_API_KEY=your_key
USE_TESTNET=true
CHAIN_ID=84532
```

### Generate a burner wallet

```python
from eth_account import Account
a = Account.create()
print("Private key:", a.key.hex())
print("Address:", a.address)
# Fund this address with testnet ETH from the faucet
```

### Run the agent

```bash
cd agent
python main.py
```

The agent will:
1. Register ERC-8004 identity on Base Sepolia
2. Train HMM regime detector on historical ETH data
3. Begin 15-minute autonomous cycles (DISCOVER → PLAN → EXECUTE → VERIFY → LEARN)
4. Log all transactions to `aegis.log`

---

## Module Reference

```
agent/
├── main.py                    # Autonomous agent loop
├── config.py                  # All config + chain addresses
│
├── regime/
│   ├── hmm_detector.py        # GaussianHMM, 4 states, 5 features
│   └── regime_types.py        # Regime enum + RegimeResult
│
├── strategies/
│   ├── base_strategy.py       # Abstract base (generate_signal, get_position_size, should_exit)
│   ├── momentum.py            # SMA crossover + RSI, BULL/BEAR regimes
│   ├── mean_reversion.py      # Bollinger Bands + VWAP, MEAN_REVERTING
│   └── sentiment_pulse.py     # FinBERT shift >0.3 → trade, any regime
│
├── risk/
│   ├── position_sizer.py      # Kelly criterion, regime-adjusted, 5% max cap
│   └── stop_manager.py        # ATR stops, trailing, regime-adaptive
│
├── sentiment/
│   ├── crypto_panic.py        # CryptoPanic API + Fear&Greed fallback
│   └── finbert_scorer.py      # ProsusAI/finbert + keyword fallback
│
├── execution/
│   ├── uniswap_router.py      # Uniswap Trading API + Universal Router
│   └── gas_optimizer.py       # EIP-1559 gas, 50 gwei ceiling
│
├── identity/
│   ├── erc8004.py             # ERC-8004 registration, IPFS agent card
│   └── bond_credit.py         # Performance reporting, P&L tracking
│
└── evolution/
    └── genetic.py             # Population, crossover, mutation, fitness
```

---

## Key Addresses (Base Sepolia)

| Contract | Address |
|---------|---------|
| Uniswap PoolManager | `0x05E73354cFDd6745C338b50BcFDfA3Aa6fA4057b` |
| Universal Router | `0x492E6456D9528771018DeB9E87ef7750EF184104` |
| WETH | `0x4200000000000000000000000000000000000006` |
| USDC | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` |
| AEGIS Controller | *(deploy and update)* |

---

## Risk Controls

| Limit | Value |
|-------|-------|
| Max position size | 5% of portfolio |
| Max daily loss | 5% → 1h pause |
| Max total drawdown | 15% → emergency stop |
| Max price impact | 1% |
| Max gas | 50 gwei |
| Max concurrent positions | 3 |
| Min gas profit multiple | 3× |

---

## Hackathon Tracks

- **Base (Autonomous Trading)** — Regime-adaptive autonomous agent executing real swaps on Uniswap v4 on Base Sepolia/Mainnet
- **Uniswap (Agentic Finance)** — Using Uniswap Trading API for quotes + Universal Router for execution
- **bond.credit (Agents that Pay)** — ERC-8004 identity + P&L reporting to ACE for credit score accrual

---

## License

MIT — open source for the hackathon.
