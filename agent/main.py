"""
AEGIS — Autonomous Trading Agent
Main orchestration loop: DISCOVER → PLAN → EXECUTE → VERIFY → LEARN

Run:
    python main.py

Requires:
    .env file with PRIVATE_KEY, ALCHEMY_API_KEY, CRYPTOPANIC_API_KEY
"""

import asyncio
import logging
import sys
import time
from datetime import datetime
from typing import Optional

import aiohttp
import numpy as np
import pandas as pd
from web3 import Web3

# ─── Local imports ────────────────────────────────────────────────────────────
from config import get_config, BaseSepoliaAddresses
from regime.hmm_detector import HMMRegimeDetector
from regime.regime_types import Regime, RegimeResult
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.sentiment_pulse import SentimentPulseStrategy
from strategies.base_strategy import TradeSignal, SignalDirection
from risk.position_sizer import PositionSizer
from risk.stop_manager import StopManager, StopState
from sentiment.crypto_panic import CryptoPanicClient
from sentiment.finbert_scorer import FinBERTScorer
from execution.uniswap_router import UniswapRouter, QuoteResult
from execution.gas_optimizer import GasOptimizer
from identity.erc8004 import ERC8004Identity, AgentCard
from identity.bond_credit import BondCreditReporter
from evolution.genetic import GeneticEvolver

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("aegis.log", mode="a"),
    ],
)
logger = logging.getLogger("aegis.main")


# ─── OHLCV Fetcher ────────────────────────────────────────────────────────────

async def fetch_ohlcv_from_api(
    session: aiohttp.ClientSession,
    symbol: str = "ETHUSD",
    limit: int = 200,
    timeframe: str = "1h",
) -> pd.DataFrame:
    """
    Fetch OHLCV data from CryptoCompare (free, no key for basic).
    Falls back to synthetic data generation if API fails.
    """
    try:
        url = "https://min-api.cryptocompare.com/data/v2/histohour"
        params = {
            "fsym": "ETH",
            "tsym": "USD",
            "limit": limit,
        }
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.json()
                candles = data.get("Data", {}).get("Data", [])
                if candles:
                    df = pd.DataFrame(candles)
                    df = df.rename(columns={
                        "time": "timestamp",
                        "open": "open",
                        "high": "high",
                        "low": "low",
                        "close": "close",
                        "volumefrom": "volume",
                    })
                    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
                    df = df.set_index("timestamp")
                    df = df[["open", "high", "low", "close", "volume"]].dropna()
                    logger.debug(f"Fetched {len(df)} OHLCV bars from CryptoCompare")
                    return df
    except Exception as e:
        logger.warning(f"OHLCV fetch failed: {e}")

    # Fallback: generate synthetic OHLCV (GBM)
    logger.warning("Using synthetic OHLCV data for testing")
    return _generate_synthetic_ohlcv(limit)


def _generate_synthetic_ohlcv(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV using Geometric Brownian Motion."""
    rng = np.random.default_rng(seed + int(time.time()) % 1000)
    price = 3500.0
    prices = [price]
    for _ in range(n - 1):
        ret = rng.normal(0.0001, 0.008)
        price *= (1 + ret)
        prices.append(price)

    closes = np.array(prices)
    highs = closes * (1 + np.abs(rng.normal(0, 0.003, n)))
    lows = closes * (1 - np.abs(rng.normal(0, 0.003, n)))
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    volumes = rng.exponential(1000, n) * 100

    idx = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="1h")
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    }, index=idx)


async def get_eth_price_usd(session: aiohttp.ClientSession) -> float:
    """Get current ETH price in USD."""
    try:
        url = "https://min-api.cryptocompare.com/data/price"
        params = {"fsym": "ETH", "tsyms": "USD"}
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()
            return float(data.get("USD", 3500.0))
    except Exception:
        return 3500.0


# ─── Portfolio State ──────────────────────────────────────────────────────────

class PortfolioState:
    """Track open positions and portfolio value."""

    def __init__(self, initial_value_usd: float):
        self.initial_value = initial_value_usd
        self.current_value = initial_value_usd
        self.peak_value = initial_value_usd
        self.open_positions: list[dict] = []   # {signal, stop_state, size_usd, entry_time}
        self.daily_pnl: float = 0.0
        self.daily_start_value: float = initial_value_usd
        self.circuit_breaker_active: bool = False
        self.circuit_breaker_until: float = 0.0
        self.tx_count: int = 0
        self.total_pnl: float = 0.0

    @property
    def available_usd(self) -> float:
        deployed = sum(p["size_usd"] for p in self.open_positions)
        return max(0.0, self.current_value - deployed)

    @property
    def drawdown_pct(self) -> float:
        if self.peak_value <= 0:
            return 0.0
        return (self.peak_value - self.current_value) / self.peak_value

    def update_value(self, pnl_usd: float):
        self.current_value += pnl_usd
        self.daily_pnl += pnl_usd
        self.total_pnl += pnl_usd
        self.peak_value = max(self.peak_value, self.current_value)

    def daily_loss_pct(self) -> float:
        if self.daily_start_value <= 0:
            return 0.0
        return (self.daily_start_value - self.current_value) / self.daily_start_value

    def reset_daily(self):
        self.daily_pnl = 0.0
        self.daily_start_value = self.current_value


# ─── Signal Selector ─────────────────────────────────────────────────────────

def select_best_signal(
    signals: list[Optional[TradeSignal]],
    regime: RegimeResult,
) -> Optional[TradeSignal]:
    """
    Select the highest-quality actionable signal from all strategies.
    Regime-adjusts confidence and filters neutral signals.
    """
    actionable = [s for s in signals if s and s.is_actionable]
    if not actionable:
        return None

    # Prefer regime-appropriate strategies
    preferred = [s for s in actionable if s.regime == regime.regime]
    if preferred:
        actionable = preferred

    # Pick by confidence × regime_multiplier
    best = max(actionable, key=lambda s: s.confidence * s.regime.risk_multiplier)
    return best


# ─── Risk Approval ────────────────────────────────────────────────────────────

def risk_approve(
    signal: TradeSignal,
    portfolio: PortfolioState,
    config,
    min_rr: float = 1.2,
) -> tuple[bool, str]:
    """Check if a signal passes risk controls."""

    if portfolio.circuit_breaker_active:
        if time.time() < portfolio.circuit_breaker_until:
            return False, "circuit_breaker_active"
        else:
            portfolio.circuit_breaker_active = False
            logger.info("Circuit breaker reset")

    if len(portfolio.open_positions) >= config.max_concurrent_positions:
        return False, "max_positions_reached"

    if portfolio.daily_loss_pct() >= config.max_daily_loss_pct:
        portfolio.circuit_breaker_active = True
        portfolio.circuit_breaker_until = time.time() + 3600  # 1h pause
        logger.warning("Daily circuit breaker triggered!")
        return False, "daily_loss_limit"

    if portfolio.drawdown_pct >= config.max_total_drawdown_pct:
        logger.critical("Emergency stop: max drawdown exceeded!")
        raise SystemExit("EMERGENCY STOP: Max drawdown exceeded")

    if signal.risk_reward < min_rr:
        return False, f"risk_reward_too_low ({signal.risk_reward:.2f} < {min_rr})"

    if signal.confidence < 0.45:
        return False, f"confidence_too_low ({signal.confidence:.2%})"

    if portfolio.available_usd < 10.0:
        return False, "insufficient_capital"

    return True, "approved"


# ─── Main Agent Loop ──────────────────────────────────────────────────────────

class AEGISAgent:
    """The AEGIS autonomous trading agent."""

    def __init__(self):
        self.config = get_config()
        self.config.validate()

        # ── Components ────────────────────────────────────────────────────────
        self.regime_detector = HMMRegimeDetector()
        self.finbert = FinBERTScorer(device="cpu")
        self.cryptopanic = CryptoPanicClient(api_key=self.config.cryptopanic_api_key)

        self.strategies = [
            MomentumStrategy(fast_period=20, slow_period=50),
            MeanReversionStrategy(bb_period=20, bb_std=2.0),
            SentimentPulseStrategy(shift_threshold=0.30),
        ]

        self.position_sizer = PositionSizer(max_position_pct=self.config.max_position_pct)
        self.stop_manager = StopManager(atr_period=14)
        self.evolver = GeneticEvolver(self.strategies)
        self.bond_reporter = BondCreditReporter(
            api_key=self.config.bond_credit_api_key,
            wallet=self._get_wallet(),
        )

        addr = self.config.addresses
        self.router = UniswapRouter(
            rpc_url=self.config.rpc_url,
            private_key=self.config.private_key,
            universal_router=addr.UNIVERSAL_ROUTER,
            weth=addr.WETH,
            usdc=addr.USDC,
            chain_id=self.config.chain_id,
            slippage_bps=self.config.default_slippage_bps,
            deadline_seconds=self.config.swap_deadline_seconds,
            uniswap_api_key=self.config.uniswap_api_key,
        )
        self.gas_optimizer = GasOptimizer(rpc_url=self.config.rpc_url)

        # Identity
        if self.config.private_key:
            from eth_account import Account
            account = Account.from_key(self.config.private_key)
            w3 = Web3(Web3.HTTPProvider(self.config.rpc_url))
            self.identity = ERC8004Identity(
                w3=w3,
                wallet=account.address,
                account=account,
                chain_id=self.config.chain_id,
                pinata_jwt=self.config.pinata_jwt,
            )
        else:
            self.identity = None

        # State
        self.portfolio = PortfolioState(initial_value_usd=100.0)  # Start with $100
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._current_ohlcv: Optional[pd.DataFrame] = None
        self._current_regime: Optional[RegimeResult] = None
        self._current_sentiment: float = 0.0
        self._running = True
        self._cycle_count = 0
        self._last_daily_reset = time.time()

    def _get_wallet(self) -> str:
        if self.config.private_key:
            try:
                from eth_account import Account
                return Account.from_key(self.config.private_key).address
            except Exception:
                pass
        return ""

    async def _get_http_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    # ─── DISCOVER ─────────────────────────────────────────────────────────────

    async def discover(self) -> tuple[pd.DataFrame, float]:
        """
        Fetch latest market data and sentiment.
        Returns (ohlcv_df, composite_sentiment_score).
        """
        session = await self._get_http_session()

        # Fetch OHLCV
        ohlcv = await fetch_ohlcv_from_api(
            session,
            limit=self.config.hmm_training_bars,
            timeframe=self.config.ohlcv_timeframe,
        )

        # Fetch + score news
        headlines = await self.cryptopanic.get_headlines(["ETH", "BTC"])
        if headlines:
            self.finbert.update_with_headlines(headlines)

        sentiment = self.finbert.composite_score

        logger.info(
            f"[DISCOVER] Bars: {len(ohlcv)} | "
            f"Sentiment: {sentiment:.3f} ({self.finbert.sentiment_label}) | "
            f"Headlines: {len(headlines)}"
        )
        return ohlcv, sentiment

    # ─── PLAN ─────────────────────────────────────────────────────────────────

    async def plan(
        self,
        ohlcv: pd.DataFrame,
        sentiment: float,
    ) -> Optional[TradeSignal]:
        """
        Detect regime and generate strategy signals.
        Returns best signal or None.
        """
        # Fit HMM if not yet trained
        if not self.regime_detector._is_fitted:
            logger.info("Training HMM regime detector...")
            self.regime_detector.fit(ohlcv, sentiment)

        # Predict regime
        recent = ohlcv.iloc[-self.config.hmm_lookback_bars:]
        regime = self.regime_detector.predict(recent, sentiment)
        self._current_regime = regime

        logger.info(
            f"[PLAN] Regime: {regime.regime.label} "
            f"({regime.confidence:.0%} confidence)"
        )

        # Generate signals from all strategies
        signals = []
        for strategy in self.strategies:
            try:
                signal = strategy.generate_signal(ohlcv, regime, sentiment)
                signals.append(signal)
                if signal.is_actionable:
                    logger.info(f"  Strategy signal: {signal}")
            except Exception as e:
                logger.error(f"Strategy {strategy.name} error: {e}", exc_info=True)
                signals.append(None)

        return select_best_signal(signals, regime)

    # ─── EXECUTE ──────────────────────────────────────────────────────────────

    async def execute(self, signal: TradeSignal) -> Optional[dict]:
        """
        Size position, check gas, execute swap.
        Returns trade record or None.
        """
        # Gas check
        gas_info = await self.gas_optimizer.get_gas_info()
        if not gas_info.is_acceptable:
            logger.warning(f"Gas too high ({gas_info.max_fee_gwei:.2f} gwei) — skipping trade")
            return None

        # Size position
        eth_price = await get_eth_price_usd(await self._get_http_session())
        size_result = self.position_sizer.quick_size(
            portfolio_value=self.portfolio.available_usd,
            regime=signal.regime,
            confidence=signal.confidence,
        )

        if size_result < 5.0:
            logger.info(f"Position too small (${size_result:.2f}) — skipping")
            return None

        # Determine swap direction
        # LONG WETH/USDC: buy WETH with USDC (bet ETH goes up)
        # SHORT: buy USDC with WETH (bet ETH goes down)
        addr = self.config.addresses

        if signal.direction == SignalDirection.LONG:
            token_in = addr.USDC
            token_out = addr.WETH
            amount_in_human = size_result
            amount_in_wei = int(size_result * 1e6)  # USDC has 6 decimals
        else:
            token_in = addr.WETH
            token_out = addr.USDC
            # Convert USD → WETH amount
            weth_amount = size_result / eth_price
            amount_in_wei = int(weth_amount * 1e18)
            amount_in_human = size_result

        logger.info(
            f"[EXECUTE] {signal.direction.value} {signal.strategy_name} | "
            f"Size: ${amount_in_human:.2f} | "
            f"Confidence: {signal.confidence:.2%} | "
            f"Gas: {gas_info.max_fee_gwei:.3f} gwei"
        )

        # Get quote
        quote = await self.router.get_quote(token_in, token_out, amount_in_wei)
        if not quote:
            logger.error("Failed to get swap quote")
            return None

        # Check price impact
        if quote.price_impact_pct > self.config.max_price_impact_pct * 100:
            logger.warning(f"Price impact too high: {quote.price_impact_pct:.2%}")
            return None

        # Execute swap
        dry_run = not self.config.private_key or not self.config.validate()
        result = await self.router.execute_swap(quote, dry_run=dry_run)

        if not result:
            logger.error("Swap execution failed")
            return None

        # Create stop state
        stop_state = self.stop_manager.create_stop(
            entry_price=signal.entry_price,
            direction=signal.direction,
            regime=signal.regime,
            df=self._current_ohlcv if self._current_ohlcv is not None else pd.DataFrame(),
            override_take_profit=signal.take_profit,
        )

        # Track position
        position = {
            "signal": signal,
            "stop_state": stop_state,
            "size_usd": amount_in_human,
            "entry_time": time.time(),
            "tx_hash": result.tx_hash,
            "amount_in_wei": amount_in_wei,
            "token_in": token_in,
            "token_out": token_out,
            "eth_price_at_entry": eth_price,
        }
        self.portfolio.open_positions.append(position)
        self.portfolio.tx_count += 1

        logger.info(
            f"✅ Trade executed: {result.tx_hash} | "
            f"Status: {result.status} | "
            f"Amount: ${amount_in_human:.2f}"
        )

        return position

    # ─── VERIFY ───────────────────────────────────────────────────────────────

    async def verify(self, positions: list[dict]):
        """
        Check open positions for stop loss / take profit hits.
        Close positions that should exit.
        """
        if not positions:
            return

        session = await self._get_http_session()
        current_price = await get_eth_price_usd(session)

        positions_to_close = []
        for pos in self.portfolio.open_positions:
            signal: TradeSignal = pos["signal"]
            stop_state: StopState = pos["stop_state"]

            # Update trailing stop
            if self._current_ohlcv is not None:
                stop_state = self.stop_manager.update(
                    stop_state, current_price, self._current_ohlcv,
                    self._current_regime,
                )
                pos["stop_state"] = stop_state

            # Check stop/TP
            should_close, reason = self.stop_manager.should_stop(stop_state, current_price)
            if not should_close and self._current_regime:
                should_close, reason = signal.direction != SignalDirection.NEUTRAL and (
                    False, ""
                ) if True else (False, "")
                # Also ask strategy
                for strategy in self.strategies:
                    if strategy.name == signal.strategy_name:
                        should_close, reason = strategy.should_exit(
                            stop_state.entry_price, current_price,
                            signal.direction, self._current_regime,
                        )
                        break

            if should_close:
                positions_to_close.append((pos, reason, current_price))

        # Close positions
        for pos, reason, close_price in positions_to_close:
            await self._close_position(pos, reason, close_price)

    async def _close_position(self, pos: dict, reason: str, close_price: float):
        """Close a position and record P&L."""
        signal: TradeSignal = pos["signal"]
        entry_price = pos["stop_state"].entry_price
        size_usd = pos["size_usd"]
        eth_price_entry = pos.get("eth_price_at_entry", close_price)

        # Compute P&L
        if signal.direction == SignalDirection.LONG:
            pnl_pct = (close_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - close_price) / entry_price

        pnl_usd = size_usd * pnl_pct
        won = pnl_usd > 0

        self.portfolio.update_value(pnl_usd)
        self.portfolio.open_positions.remove(pos)

        # Update strategy performance
        for strategy in self.strategies:
            if strategy.name == signal.strategy_name:
                strategy.update_performance(won, abs(pnl_pct))
                break

        # Gas cost estimate
        gas_usd = 0.01 * close_price / eth_price_entry  # ~0.01 ETH gas on Base Sepolia

        # Record for bond.credit
        self.bond_reporter.record_trade(
            tx_hash=pos.get("tx_hash", ""),
            token_in=pos.get("token_in", ""),
            token_out=pos.get("token_out", ""),
            amount_in_usd=size_usd,
            amount_out_usd=size_usd + pnl_usd,
            gas_cost_usd=gas_usd,
            strategy=signal.strategy_name,
            regime=signal.regime.label,
        )

        logger.info(
            f"📊 Position closed [{reason}] | "
            f"{signal.direction.value} {signal.strategy_name} | "
            f"P&L: ${pnl_usd:.2f} ({pnl_pct:.2%}) | "
            f"Portfolio: ${self.portfolio.current_value:.2f}"
        )

    # ─── LEARN ────────────────────────────────────────────────────────────────

    async def learn(self):
        """
        Genetic evolution + bond.credit reporting.
        Runs every 24h.
        """
        if self.evolver.should_evolve(interval_hours=self.config.evolution_interval_hours):
            logger.info("🧬 Running genetic evolution cycle...")
            best = self.evolver.evolve()
            summary = self.evolver.evolution_summary()
            logger.info(f"Evolution summary: {summary}")

        # Submit bond.credit report daily
        if self.bond_reporter.trade_count > 0 and self._cycle_count % 96 == 0:  # every 24h
            logger.info("📈 Submitting bond.credit performance report...")
            await self.bond_reporter.submit_report()

    # ─── Agent Status ─────────────────────────────────────────────────────────

    def _print_status(self):
        """Print a compact status line."""
        regime_label = self._current_regime.regime.label if self._current_regime else "UNKNOWN"
        sentiment_label = self.finbert.sentiment_label
        logger.info(
            f"━━━ CYCLE {self._cycle_count} | "
            f"Regime: {regime_label} | "
            f"Sentiment: {sentiment_label} ({self._current_sentiment:.3f}) | "
            f"Portfolio: ${self.portfolio.current_value:.2f} | "
            f"Positions: {len(self.portfolio.open_positions)} | "
            f"TXs: {self.portfolio.tx_count} | "
            f"P&L: ${self.portfolio.total_pnl:.2f} ━━━"
        )

    # ─── Main Loop ────────────────────────────────────────────────────────────

    async def run(self):
        """
        Main autonomous agent loop.
        Runs every 15 minutes (configurable via config.loop_interval_seconds).
        """
        logger.info("🚀 AEGIS Agent starting...")
        logger.info(f"  Network: {'Base Sepolia' if self.config.use_testnet else 'Base Mainnet'}")
        logger.info(f"  Chain ID: {self.config.chain_id}")
        logger.info(f"  Wallet: {self._get_wallet() or 'NO KEY'}")

        # Register identity on startup
        if self.identity:
            logger.info("Registering ERC-8004 identity...")
            token_id = await self.identity.register()
            if token_id:
                self.bond_reporter.agent_id = str(token_id)

        while self._running:
            cycle_start = time.time()
            self._cycle_count += 1

            try:
                # Daily reset
                if time.time() - self._last_daily_reset > 86400:
                    self.portfolio.reset_daily()
                    self._last_daily_reset = time.time()

                # ── DISCOVER ─────────────────────────────────────────────────
                ohlcv, sentiment = await self.discover()
                self._current_ohlcv = ohlcv
                self._current_sentiment = sentiment

                # ── PLAN ─────────────────────────────────────────────────────
                signal = await self.plan(ohlcv, sentiment)

                # ── EXECUTE ──────────────────────────────────────────────────
                if signal:
                    approved, reason = risk_approve(signal, self.portfolio, self.config)
                    if approved:
                        await self.execute(signal)
                    else:
                        logger.info(f"[RISK] Trade rejected: {reason}")

                # ── VERIFY ───────────────────────────────────────────────────
                await self.verify(self.portfolio.open_positions.copy())

                # ── LEARN ────────────────────────────────────────────────────
                await self.learn()

                self._print_status()

            except SystemExit:
                logger.critical("Emergency stop triggered — shutting down")
                self._running = False
                break
            except Exception as e:
                logger.error(f"Cycle {self._cycle_count} error: {e}", exc_info=True)

            # Sleep until next cycle
            elapsed = time.time() - cycle_start
            sleep_time = max(0, self.config.loop_interval_seconds - elapsed)
            logger.info(f"Cycle completed in {elapsed:.1f}s — sleeping {sleep_time:.0f}s")
            await asyncio.sleep(sleep_time)

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down AEGIS...")
        self._running = False

        # Final bond.credit report
        if self.bond_reporter.trade_count > 0:
            await self.bond_reporter.submit_report()

        # Close connections
        await self.cryptopanic.close()
        await self.router.close()
        await self.bond_reporter.close()
        if self.identity:
            await self.identity.close()
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

        logger.info(f"Final portfolio: ${self.portfolio.current_value:.2f}")
        logger.info(f"Total P&L: ${self.portfolio.total_pnl:.2f}")
        logger.info(f"Total TXs: {self.portfolio.tx_count}")


# ─── Entry Point ──────────────────────────────────────────────────────────────

async def main():
    agent = AEGISAgent()
    try:
        await agent.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
