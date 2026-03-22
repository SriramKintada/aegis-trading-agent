"""
AEGIS — Aerodrome Router (Base Chain)
Handles swap quoting and execution via Aerodrome DEX on Base.

Aerodrome is the largest DEX on Base by TVL.
Used for cross-DEX arbitrage with Uniswap v4.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

logger = logging.getLogger(__name__)

# ─── Aerodrome ABIs (minimal) ─────────────────────────────────────────────────

AERODROME_ROUTER_ABI = [
    {
        "name": "getAmountsOut",
        "type": "function",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {
                "name": "routes",
                "type": "tuple[]",
                "components": [
                    {"name": "from", "type": "address"},
                    {"name": "to", "type": "address"},
                    {"name": "stable", "type": "bool"},
                    {"name": "factory", "type": "address"},
                ],
            },
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
    },
    {
        "name": "swapExactTokensForTokens",
        "type": "function",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {
                "name": "routes",
                "type": "tuple[]",
                "components": [
                    {"name": "from", "type": "address"},
                    {"name": "to", "type": "address"},
                    {"name": "stable", "type": "bool"},
                    {"name": "factory", "type": "address"},
                ],
            },
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
    },
]

ERC20_ABI = [
    {
        "name": "approve",
        "type": "function",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
    },
    {
        "name": "allowance",
        "type": "function",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "name": "balanceOf",
        "type": "function",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "name": "decimals",
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
    },
]

# ─── Aerodrome Addresses on Base ──────────────────────────────────────────────

AERODROME_ROUTER = "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43"
AERODROME_FACTORY = "0x420DD381b31aEf6683db6B902084cB0FFECe40Da"
# Common WETH/USDC volatile pool
WETH_USDC_POOL = "0xcdaC0d6c6C59727a65F871236188350531885C43"


@dataclass
class AerodromeQuote:
    """Result from Aerodrome getAmountsOut."""
    token_in: str
    token_out: str
    amount_in: int
    amount_out: int
    amount_out_min: int
    price: float           # Implied price (token_out per token_in)
    stable: bool
    route: list


@dataclass
class AerodromeSwapResult:
    """Result of an executed Aerodrome swap."""
    tx_hash: str
    status: str
    amount_in: int
    amount_out: int
    gas_used: int
    gas_price: int
    timestamp: int


class AerodromeRouter:
    """
    Aerodrome DEX router for Base chain.

    Supports:
      1. Getting quotes via getAmountsOut
      2. Executing swaps via swapExactTokensForTokens
      3. Price comparison with Uniswap for arb detection
    """

    def __init__(
        self,
        rpc_url: str,
        private_key: str = "",
        weth: str = "0x4200000000000000000000000000000000000006",
        usdc: str = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        chain_id: int = 8453,
        slippage_bps: int = 50,
        deadline_seconds: int = 300,
    ):
        self.rpc_url = rpc_url
        self.private_key = private_key
        self.weth = Web3.to_checksum_address(weth)
        self.usdc = Web3.to_checksum_address(usdc)
        self.chain_id = chain_id
        self.slippage_bps = slippage_bps
        self.deadline_seconds = deadline_seconds

        self.router_address = Web3.to_checksum_address(AERODROME_ROUTER)
        self.factory_address = Web3.to_checksum_address(AERODROME_FACTORY)

        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        self.router_contract = self.w3.eth.contract(
            address=self.router_address, abi=AERODROME_ROUTER_ABI
        )

        if private_key:
            from eth_account import Account
            self.account = Account.from_key(private_key)
            self.wallet = self.account.address
        else:
            self.account = None
            self.wallet = None

    async def get_quote(
        self,
        token_in: str,
        token_out: str,
        amount_in_wei: int,
        stable: bool = False,
    ) -> Optional[AerodromeQuote]:
        """
        Get a swap quote from Aerodrome via getAmountsOut.

        Args:
            token_in: Input token address
            token_out: Output token address
            amount_in_wei: Amount of input token in wei
            stable: Whether to use stable pool (for stablecoin pairs)

        Returns:
            AerodromeQuote or None on failure
        """
        token_in = Web3.to_checksum_address(token_in)
        token_out = Web3.to_checksum_address(token_out)

        route = [{
            "from": token_in,
            "to": token_out,
            "stable": stable,
            "factory": self.factory_address,
        }]

        try:
            amounts = self.router_contract.functions.getAmountsOut(
                amount_in_wei, route
            ).call()

            amount_out = amounts[-1]
            if amount_out <= 0:
                return None

            slippage_mult = 1 - self.slippage_bps / 10000
            amount_out_min = int(amount_out * slippage_mult)

            # Calculate implied price
            # For WETH→USDC: price = amount_out_usdc / amount_in_weth
            # Adjust for decimals (WETH=18, USDC=6)
            if token_in.lower() == self.weth.lower():
                price = (amount_out / 1e6) / (amount_in_wei / 1e18)
            elif token_out.lower() == self.weth.lower():
                price = (amount_in_wei / 1e6) / (amount_out / 1e18)
            else:
                price = amount_out / amount_in_wei

            return AerodromeQuote(
                token_in=token_in,
                token_out=token_out,
                amount_in=amount_in_wei,
                amount_out=amount_out,
                amount_out_min=amount_out_min,
                price=price,
                stable=stable,
                route=route,
            )

        except Exception as e:
            logger.warning(f"Aerodrome quote failed: {e}")
            return None

    async def execute_swap(
        self,
        quote: AerodromeQuote,
        dry_run: bool = False,
    ) -> Optional[AerodromeSwapResult]:
        """
        Execute a swap on Aerodrome.

        Args:
            quote: AerodromeQuote from get_quote()
            dry_run: If True, build but don't send transaction

        Returns:
            AerodromeSwapResult on success, None on failure
        """
        if not self.account:
            logger.error("No private key configured — cannot execute swap")
            return None

        try:
            # Ensure allowance
            approved = await self._ensure_allowance(
                quote.token_in, self.router_address, quote.amount_in
            )
            if not approved:
                logger.error("Token approval failed for Aerodrome")
                return None

            deadline = int(time.time()) + self.deadline_seconds
            nonce = self.w3.eth.get_transaction_count(self.wallet)

            tx = self.router_contract.functions.swapExactTokensForTokens(
                quote.amount_in,
                quote.amount_out_min,
                quote.route,
                self.wallet,
                deadline,
            ).build_transaction({
                "from": self.wallet,
                "nonce": nonce,
                "gas": 300_000,
                "chainId": self.chain_id,
            })

            if dry_run:
                logger.info(f"DRY RUN Aerodrome: Would send tx: {tx}")
                return AerodromeSwapResult(
                    tx_hash="0x" + "0" * 64,
                    status="dry_run",
                    amount_in=quote.amount_in,
                    amount_out=quote.amount_out,
                    gas_used=0,
                    gas_price=0,
                    timestamp=int(time.time()),
                )

            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_hash_hex = "0x" + tx_hash.hex()
            logger.info(f"Aerodrome swap submitted: {tx_hash_hex}")

            receipt = await self._wait_for_receipt(tx_hash_hex, timeout=120)
            status = "success" if receipt and receipt.get("status") == 1 else "failed"

            return AerodromeSwapResult(
                tx_hash=tx_hash_hex,
                status=status,
                amount_in=quote.amount_in,
                amount_out=quote.amount_out,
                gas_used=receipt.get("gasUsed", 0) if receipt else 0,
                gas_price=receipt.get("effectiveGasPrice", 0) if receipt else 0,
                timestamp=int(time.time()),
            )

        except Exception as e:
            logger.error(f"Aerodrome swap execution failed: {e}", exc_info=True)
            return None

    async def get_eth_usdc_price(self, amount_eth_wei: int = 10**18) -> Optional[float]:
        """Get ETH/USDC price on Aerodrome (1 ETH → ? USDC)."""
        quote = await self.get_quote(self.weth, self.usdc, amount_eth_wei)
        return quote.price if quote else None

    async def compare_with_uniswap(
        self,
        uniswap_price: float,
        amount_eth_wei: int = 10**18,
    ) -> Optional[dict]:
        """
        Compare Aerodrome price with Uniswap price.

        Returns dict with spread info, or None if quote fails.
        """
        aero_price = await self.get_eth_usdc_price(amount_eth_wei)
        if aero_price is None:
            return None

        spread = abs(aero_price - uniswap_price)
        spread_pct = spread / uniswap_price

        cheaper_dex = "aerodrome" if aero_price < uniswap_price else "uniswap"

        return {
            "uniswap_price": uniswap_price,
            "aerodrome_price": aero_price,
            "spread": spread,
            "spread_pct": spread_pct,
            "cheaper_dex": cheaper_dex,
            "profitable": spread_pct > 0.0020,  # > 0.20% after costs
        }

    async def _ensure_allowance(
        self, token: str, spender: str, amount: int
    ) -> bool:
        """Approve ERC-20 token if allowance is insufficient."""
        try:
            contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(token), abi=ERC20_ABI
            )
            current = contract.functions.allowance(
                self.wallet, Web3.to_checksum_address(spender)
            ).call()
            if current >= amount:
                return True

            nonce = self.w3.eth.get_transaction_count(self.wallet)
            tx = contract.functions.approve(
                Web3.to_checksum_address(spender), 2**256 - 1
            ).build_transaction({
                "from": self.wallet,
                "nonce": nonce,
                "gas": 60_000,
                "chainId": self.chain_id,
            })
            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = await self._wait_for_receipt("0x" + tx_hash.hex(), timeout=60)
            return receipt is not None and receipt.get("status") == 1
        except Exception as e:
            logger.error(f"Aerodrome allowance/approve failed: {e}")
            return False

    async def _wait_for_receipt(
        self, tx_hash: str, timeout: int = 120
    ) -> Optional[dict]:
        """Poll for transaction receipt."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                receipt = self.w3.eth.get_transaction_receipt(tx_hash)
                if receipt is not None:
                    return dict(receipt)
            except Exception:
                pass
            await asyncio.sleep(2)
        logger.warning(f"Receipt timeout for {tx_hash}")
        return None

    async def close(self):
        """Cleanup."""
        pass
