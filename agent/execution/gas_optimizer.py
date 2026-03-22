"""
AEGIS — Gas Optimizer
Monitors Base chain gas prices and blocks high-cost trades.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import aiohttp
from web3 import Web3

logger = logging.getLogger(__name__)

MAX_GAS_GWEI = 50.0       # Max gas price we'll trade at (gwei)
MIN_PROFIT_MULTIPLE = 3.0  # Trade must profit at least 3× its gas cost


@dataclass
class GasInfo:
    base_fee_gwei: float
    priority_fee_gwei: float
    max_fee_gwei: float
    is_acceptable: bool        # True if gas is within budget

    @property
    def total_gwei(self) -> float:
        return self.max_fee_gwei


class GasOptimizer:
    """
    Monitors Base L2 gas prices and provides EIP-1559 fee recommendations.

    Base chain is very cheap (typically < 1 gwei). This guard is mainly to
    catch rare spikes during high-activity periods.
    """

    def __init__(
        self,
        rpc_url: str,
        max_gas_gwei: float = MAX_GAS_GWEI,
        priority_fee_gwei: float = 0.001,    # Base is OP Stack, very low tips
        cache_ttl_seconds: int = 30,
    ):
        self.rpc_url = rpc_url
        self.max_gas_gwei = max_gas_gwei
        self.priority_fee_gwei = priority_fee_gwei
        self.cache_ttl = cache_ttl_seconds

        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self._cached: Optional[GasInfo] = None
        self._cache_ts: float = 0

    async def get_gas_info(self, force_refresh: bool = False) -> GasInfo:
        """
        Get current gas info, using cache if fresh.
        """
        import time
        now = time.time()
        if not force_refresh and self._cached and (now - self._cache_ts) < self.cache_ttl:
            return self._cached

        info = await self._fetch_gas()
        self._cached = info
        self._cache_ts = now
        return info

    async def _fetch_gas(self) -> GasInfo:
        """Fetch current gas from node."""
        try:
            fee_history = self.w3.eth.fee_history(5, "latest", [25, 50])
            base_fees = fee_history.get("baseFeePerGas", [])
            if base_fees:
                base_fee_wei = base_fees[-1]
            else:
                base_fee_wei = self.w3.eth.gas_price

            rewards = fee_history.get("reward", [[0]])
            # Use median priority fee from history
            priority_fees = [r[0] for r in rewards if r]
            priority_wei = int(sum(priority_fees) / max(len(priority_fees), 1)) if priority_fees else Web3.to_wei(self.priority_fee_gwei, "gwei")

            max_fee_wei = base_fee_wei * 2 + priority_wei

            base_gwei = Web3.from_wei(base_fee_wei, "gwei")
            priority_gwei = Web3.from_wei(priority_wei, "gwei")
            max_gwei = Web3.from_wei(max_fee_wei, "gwei")

            is_acceptable = float(max_gwei) <= self.max_gas_gwei

            if not is_acceptable:
                logger.warning(f"Gas too high: {max_gwei:.3f} gwei (max: {self.max_gas_gwei} gwei)")

            return GasInfo(
                base_fee_gwei=float(base_gwei),
                priority_fee_gwei=float(priority_gwei),
                max_fee_gwei=float(max_gwei),
                is_acceptable=is_acceptable,
            )

        except Exception as e:
            logger.error(f"Gas fetch failed: {e}")
            # Default: assume acceptable (Base is typically <1 gwei)
            return GasInfo(
                base_fee_gwei=0.1,
                priority_fee_gwei=0.001,
                max_fee_gwei=0.201,
                is_acceptable=True,
            )

    def estimate_trade_cost_usd(
        self,
        gas_info: GasInfo,
        gas_units: int,
        eth_price_usd: float,
    ) -> float:
        """
        Estimate total gas cost in USD for a trade.

        Args:
            gas_info: Current gas info
            gas_units: Estimated gas units for the swap (typically 150k-300k)
            eth_price_usd: Current ETH price in USD

        Returns:
            Estimated cost in USD
        """
        cost_eth = (gas_info.max_fee_gwei * 1e-9) * gas_units
        return cost_eth * eth_price_usd

    def is_trade_profitable_after_gas(
        self,
        expected_profit_usd: float,
        gas_cost_usd: float,
        min_multiple: float = MIN_PROFIT_MULTIPLE,
    ) -> bool:
        """
        Returns True if expected profit exceeds gas cost by min_multiple.
        """
        if gas_cost_usd <= 0:
            return True
        return expected_profit_usd >= min_multiple * gas_cost_usd

    async def wait_for_acceptable_gas(
        self,
        max_wait_seconds: int = 300,
        poll_interval: int = 30,
    ) -> bool:
        """
        Wait until gas drops below threshold.
        Returns True if gas became acceptable, False on timeout.
        """
        waited = 0
        while waited < max_wait_seconds:
            info = await self.get_gas_info(force_refresh=True)
            if info.is_acceptable:
                return True
            logger.info(
                f"Gas too high ({info.max_fee_gwei:.3f} gwei) — "
                f"waiting {poll_interval}s ({waited}/{max_wait_seconds}s)"
            )
            await asyncio.sleep(poll_interval)
            waited += poll_interval
        return False

    def get_eip1559_params(self, gas_info: GasInfo) -> dict:
        """Return web3.py-compatible EIP-1559 gas params."""
        return {
            "maxFeePerGas": Web3.to_wei(gas_info.max_fee_gwei, "gwei"),
            "maxPriorityFeePerGas": Web3.to_wei(gas_info.priority_fee_gwei, "gwei"),
        }
