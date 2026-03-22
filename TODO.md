# AEGIS — Hackathon TODO List
*Prioritized for 48-hour execution*

---

## 🔴 CRITICAL — Do First (Hours 0-8)

### Setup & Infrastructure
- [ ] Create burner wallet (NEVER use main wallet for agent trades)
- [ ] Get Alchemy API key → create Base Mainnet + Base Sepolia apps
- [ ] Get Base Sepolia testnet ETH from faucet
- [ ] Initialize Foundry project: `forge init aegis-contracts`
- [ ] Initialize Python project: `uv init aegis-agent` or `pip` venv

### First On-Chain Proof (Highest Priority)
- [ ] Write `AEGISController.sol` minimal version (execute swap + emit event)
- [ ] Deploy to Base Sepolia via `forge script`
- [ ] Execute first swap through contract (WETH → USDC on Uniswap v4)
- [ ] **Capture first TxID** — paste into README immediately
- [ ] Verify on Basescan (basescan.org)

### Python Foundation
- [ ] Set up web3.py + connect to Base Sepolia
- [ ] Listen to Uniswap v4 PoolManager swap events
- [ ] Build minimal OHLCV aggregator (1m candles from swap events)
- [ ] Test: can we get last 100 candles of WETH/USDC on Base?

---

## 🟠 HIGH PRIORITY — Strategy Engine (Hours 8-16)

### Regime Detection (Port existing work)
- [ ] Extract HMM regime detector from existing AI hedge fund codebase
- [ ] Adapt `hmm_detector.py` to use Base chain OHLCV (vs Indian equity data)
- [ ] Test: run regime detection on last 7 days of WETH/USDC data
- [ ] Log regimes: what regime are we in right now?

### Strategies (Start with 2)
- [ ] Implement `MomentumStrategy` (20/50 MA crossover + RSI)
- [ ] Implement `MeanReversionStrategy` (Bollinger Bands + VWAP)
- [ ] Add `RiskManager`: Kelly sizing + stop loss calculations
- [ ] Backtest both strategies on Base Sepolia mock data or mainnet history

### Uniswap API Integration
- [ ] Register on portal.uniswap.org for Trading API key (or use free tier)
- [ ] Test `GET /quote` for WETH/USDC on Base
- [ ] Build `uniswap_router.py`: calldata builder for Universal Router
- [ ] End-to-end test: Python → get quote → build tx → send via AEGISController

---

## 🟡 MEDIUM PRIORITY — Autonomy Loop (Hours 16-24)

### Main Agent Loop
- [ ] Write `main.py` with async loop:
  ```
  while True:
      regime = detect_regime()
      signals = generate_signals(regime)
      if signal.confidence > threshold:
          trade = plan_trade(signal)
          if risk_manager.approve(trade):
              txid = execute(trade)
              verify(txid)
              record_to_bond_credit(txid)
      await asyncio.sleep(15 * 60)  # 15-minute cycle
  ```
- [ ] Add simple terminal dashboard (rich library)
- [ ] Run first autonomous cycle on Sepolia → capture TxID #2, #3, #4...

### Sentiment Integration
- [ ] Register CryptoPanic API (free): api.cryptopanic.com
- [ ] Set up FinBERT inference (local or HuggingFace free API)
- [ ] Test: score last 10 crypto news headlines
- [ ] Integrate sentiment score into strategy signal strength

### ERC-8004 Identity
- [ ] Read ERC-8004 spec at eips.ethereum.org/EIPS/eip-8004
- [ ] Write agent card JSON → pin to IPFS (Pinata free tier)
- [ ] Call ERC-8004 Identity Registry to register agent (Base Sepolia)
- [ ] Capture agent identity tokenId → use in all trade events

---

## 🟢 STANDARD — Enhancement (Hours 24-40)

### Bond.Credit Integration
- [ ] Visit bond.credit → find hackathon contact/API docs
- [ ] Register for ACE (Agentic Credit Engine) access
- [ ] Implement `bond_credit.py` reporter
- [ ] After 24h of trades → submit performance report
- [ ] Document credit score accrual in README

### Additional Strategies
- [ ] Implement `SentimentPulseStrategy` (trade on FinBERT score shifts)
- [ ] Implement `CrossPoolArbStrategy` (Uniswap v4 vs Aerodrome price diff)
- [ ] Add strategy to RL optimizer (adapt from existing actor-critic code)

### Genetic Evolution
- [ ] Implement `genetic_evolver.py` (keep it simple: parameter mutation only)
- [ ] Run one evolution cycle → show strategy parameters improved
- [ ] Log evolution in `AEGISReporter.sol`

### Mainnet Deployment (If Time Permits)
- [ ] Deploy contracts to Base mainnet
- [ ] Fund with $50-100 USDC for live trades
- [ ] Execute 5+ mainnet trades → capture mainnet TxIDs
- [ ] Document mainnet performance in README

---

## ⚪ NICE TO HAVE — Polish (Hours 40-48)

### Documentation
- [ ] Write README.md with:
  - [ ] Architecture diagram (ASCII or draw.io)
  - [ ] Setup instructions
  - [ ] All TxIDs table
  - [ ] Performance metrics (Sharpe, win rate, P&L %)
  - [ ] What makes this different section

### Demo Prep
- [ ] Record 3-minute screen capture showing:
  1. Agent auto-detecting regime change
  2. Strategy selection adjusting
  3. Live trade executing → TxID appearing
  4. Basescan verification
  5. bond.credit score updating
- [ ] Write submission blurbs for each track (Base, Uniswap, bond.credit)

### GitHub
- [ ] Push to public repo: `github.com/sriramkintada/aegis-agent`
- [ ] Add MIT license
- [ ] Add `.env.example` with all required keys listed

---

## 📋 API Keys To Get (Do on Day 1)

| Service | URL | Notes |
|---------|-----|-------|
| Alchemy | alchemy.com | Base Mainnet + Sepolia RPC |
| CryptoPanic | cryptopanic.com/developers/api | Free, 100 req/hr |
| Pinata | pinata.cloud | IPFS for agent card, free tier |
| Uniswap Portal | portal.uniswap.org | Trading API key (may have free tier) |
| bond.credit | bond.credit | Contact via hackathon Discord for API access |
| Fear & Greed | alternative.me/crypto/fear-and-greed-index/api | No key needed |

---

## 🚨 Risk Flags

- **Testnet gas** can be flaky — have backup RPC (Infura or QuickNode)
- **FinBERT** is 400MB model — test inference speed early; use HuggingFace Inference API if too slow
- **Uniswap v4 on Base Sepolia** — verify liquidity exists before building swap logic around specific pairs
- **ERC-8004 registry address on Base Sepolia** — find the exact deployed address (check eips.ethereum.org or hackathon Discord)
- **bond.credit API** — may require hackathon registration/Discord access; do this Day 1

---

## 🏆 Minimum Viable Submission Checklist

To win ANY prize, we need ALL of these:
- [ ] Working code on GitHub (open source)
- [ ] Deployed contracts on Base (testnet or mainnet)
- [ ] Minimum 10 real TxIDs showing autonomous trades
- [ ] At least 1 strategy showing positive P&L (even on testnet)
- [ ] Uniswap v4 API used for quotes/routing (Uniswap track)
- [ ] bond.credit performance report submitted (bond.credit track)
- [ ] ERC-8004 agent identity registered (preferred for all tracks)
- [ ] 3-minute demo video
- [ ] README with architecture explanation

*Last updated: 2026-03-22*
