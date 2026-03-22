"""
AEGIS — Uniswap v4 Router
Handles swap quoting and execution via Uniswap Trading API + Universal Router.
Targets Base Mainnet and Base Sepolia.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp
from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

logger = logging.getLogger(__name__)

# ─── ABIs (minimal fragments) ─────────────────────────────────────────────────

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

UNIVERSAL_ROUTER_ABI = [
    {
        "name": "execute",
        "type": "function",
        "inputs": [
            {"name": "commands", "type": "bytes"},
            {"name": "inputs", "type": "bytes[]"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [],
        "stateMutability": "payable",
    },
]

# Uniswap Universal Router command bytes
COMMAND_V3_SWAP_EXACT_IN = 0x00
COMMAND_V4_SWAP = 0x10
COMMAND_WRAP_ETH = 0x0b
COMMAND_UNWRAP_WETH = 0x0c


@dataclass
class QuoteResult:
    token_in: str
    token_out: str
    amount_in: int              # Wei
    amount_out: int             # Wei (expected output)
    amount_out_min: int         # Wei (min after slippage)
    price_impact_pct: float
    gas_estimate: int
    route: list[str]            # Pool addresses in route
    calldata: str               # Ready-to-submit calldata (from Trading API)
    quote_id: str


@dataclass
class SwapResult:
    tx_hash: str
    status: str                 # "success", "pending", "failed"
    amount_in: int
    amount_out: int
    gas_used: int
    gas_price: int
    timestamp: int


class UniswapRouter:
    """
    Uniswap v4 swap executor for Base chain.

    Uses:
      1. Uniswap Trading API for quotes + calldata (preferred)
      2. Fallback to QuoterV2 contract if API unavailable
    """

    TRADING_API_BASE = "https://trading-api-labs.interface.gateway.uniswap.org/v1"
    ROUTING_API_BASE = "https://api.uniswap.org/v2"

    def __init__(
        self,
        rpc_url: str,
        private_key: str,
        universal_router: str,
        weth: str,
        usdc: str,
        chain_id: int = 84532,
        slippage_bps: int = 50,
        deadline_seconds: int = 300,
        uniswap_api_key: str = "",
    ):
        self.rpc_url = rpc_url
        self.private_key = private_key
        self.universal_router = Web3.to_checksum_address(universal_router)
        self.weth = Web3.to_checksum_address(weth)
        self.usdc = Web3.to_checksum_address(usdc)
        self.chain_id = chain_id
        self.slippage_bps = slippage_bps
        self.deadline_seconds = deadline_seconds
        self.uniswap_api_key = uniswap_api_key

        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        if private_key:
            self.account = Account.from_key(private_key)
            self.wallet = self.account.address
        else:
            self.account = None
            self.wallet = None

        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {}
            if self.uniswap_api_key:
                headers["x-api-key"] = self.uniswap_api_key
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    # ─── Quoting ──────────────────────────────────────────────────────────────

    async def get_quote(
        self,
        token_in: str,
        token_out: str,
        amount_in_wei: int,
    ) -> Optional[QuoteResult]:
        """
        Get a swap quote via Uniswap Trading API.
        Falls back to routing API if Trading API fails.
        """
        token_in = Web3.to_checksum_address(token_in)
        token_out = Web3.to_checksum_address(token_out)

        quote = await self._quote_trading_api(token_in, token_out, amount_in_wei)
        if quote:
            return quote

        # Fallback: routing API
        quote = await self._quote_routing_api(token_in, token_out, amount_in_wei)
        return quote

    async def _quote_trading_api(
        self,
        token_in: str,
        token_out: str,
        amount_in_wei: int,
    ) -> Optional[QuoteResult]:
        """Uniswap Trading API v1 quote."""
        session = await self._get_session()
        payload = {
            "tokenIn": {"chainId": self.chain_id, "address": token_in},
            "tokenOut": {"chainId": self.chain_id, "address": token_out},
            "amount": str(amount_in_wei),
            "type": "EXACT_INPUT",
            "slippageTolerance": self.slippage_bps / 10000,
            "deadline": int(time.time()) + self.deadline_seconds,
            "sendPortionEnabled": False,
        }
        try:
            async with session.post(
                f"{self.TRADING_API_BASE}/quote",
                json=payload,
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"Trading API returned {resp.status}")
                    return None
                data = await resp.json()

            quote_data = data.get("quote", data)
            amount_out = int(quote_data.get("output", {}).get("amount", 0))
            if not amount_out:
                return None

            slippage_mult = 1 - self.slippage_bps / 10000
            amount_out_min = int(amount_out * slippage_mult)

            return QuoteResult(
                token_in=token_in,
                token_out=token_out,
                amount_in=amount_in_wei,
                amount_out=amount_out,
                amount_out_min=amount_out_min,
                price_impact_pct=float(quote_data.get("priceImpact", 0)),
                gas_estimate=int(quote_data.get("gasUseEstimate", 250000)),
                route=[],
                calldata=data.get("swap", {}).get("calldata", ""),
                quote_id=data.get("quote", {}).get("quoteId", ""),
            )
        except Exception as e:
            logger.warning(f"Trading API quote failed: {e}")
            return None

    async def _quote_routing_api(
        self,
        token_in: str,
        token_out: str,
        amount_in_wei: int,
    ) -> Optional[QuoteResult]:
        """Uniswap Routing API v2 quote (fallback)."""
        session = await self._get_session()
        params = {
            "tokenInAddress": token_in,
            "tokenInChainId": str(self.chain_id),
            "tokenOutAddress": token_out,
            "tokenOutChainId": str(self.chain_id),
            "amount": str(amount_in_wei),
            "type": "exactIn",
        }
        try:
            async with session.get(
                f"{self.ROUTING_API_BASE}/quote",
                params=params,
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

            amount_out = int(data.get("quoteDecimals", "0").replace(".", "").split(".")[0])
            if not amount_out:
                # Try raw
                amount_out = int(float(data.get("quote", "0")) * 1e6)  # assume USDC 6 dec

            slippage_mult = 1 - self.slippage_bps / 10000
            amount_out_min = int(amount_out * slippage_mult)

            return QuoteResult(
                token_in=token_in,
                token_out=token_out,
                amount_in=amount_in_wei,
                amount_out=amount_out,
                amount_out_min=amount_out_min,
                price_impact_pct=float(data.get("priceImpact", 0)),
                gas_estimate=int(data.get("estimatedGasUsed", 250000)),
                route=[],
                calldata="",
                quote_id=data.get("quoteId", ""),
            )
        except Exception as e:
            logger.warning(f"Routing API quote failed: {e}")
            return None

    # ─── Execution ────────────────────────────────────────────────────────────

    async def execute_swap(
        self,
        quote: QuoteResult,
        dry_run: bool = False,
    ) -> Optional[SwapResult]:
        """
        Execute a swap using a previously obtained quote.

        Args:
            quote: QuoteResult from get_quote()
            dry_run: If True, build but don't send transaction

        Returns:
            SwapResult on success, None on failure
        """
        if not self.account:
            logger.error("No private key configured — cannot execute swap")
            return None

        if quote.price_impact_pct > 1.0:
            logger.warning(
                f"Price impact {quote.price_impact_pct:.2%} exceeds 1% — aborting swap"
            )
            return None

        # Approve token_in if not native ETH
        if quote.token_in.lower() != self.weth.lower():
            approved = await self._ensure_allowance(
                quote.token_in, self.universal_router, quote.amount_in
            )
            if not approved:
                logger.error("Token approval failed")
                return None

        try:
            nonce = self.w3.eth.get_transaction_count(self.wallet)
            gas_price_info = await self._get_eip1559_gas()

            deadline = int(time.time()) + self.deadline_seconds

            # Use calldata from Trading API if available, otherwise build manually
            if quote.calldata and len(quote.calldata) > 2:
                tx = {
                    "from": self.wallet,
                    "to": self.universal_router,
                    "data": quote.calldata,
                    "value": quote.amount_in if quote.token_in.lower() == "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee" else 0,
                    "nonce": nonce,
                    "chainId": self.chain_id,
                    "gas": min(quote.gas_estimate * 2, 1_000_000),
                    **gas_price_info,
                }
            else:
                # Build V3 swap calldata manually as fallback
                tx = await self._build_v3_swap_tx(quote, nonce, gas_price_info, deadline)

            if dry_run:
                logger.info(f"DRY RUN: Would send tx: {tx}")
                return SwapResult(
                    tx_hash="0x" + "0" * 64,
                    status="dry_run",
                    amount_in=quote.amount_in,
                    amount_out=quote.amount_out,
                    gas_used=0,
                    gas_price=gas_price_info.get("maxFeePerGas", 0),
                    timestamp=int(time.time()),
                )

            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_hash_hex = "0x" + tx_hash.hex()
            logger.info(f"Swap submitted: {tx_hash_hex}")

            # Wait for receipt with timeout
            receipt = await self._wait_for_receipt(tx_hash_hex, timeout=120)
            status = "success" if receipt and receipt.get("status") == 1 else "failed"

            gas_used = receipt.get("gasUsed", 0) if receipt else 0
            effective_gas = receipt.get("effectiveGasPrice", 0) if receipt else 0

            return SwapResult(
                tx_hash=tx_hash_hex,
                status=status,
                amount_in=quote.amount_in,
                amount_out=quote.amount_out,
                gas_used=gas_used,
                gas_price=effective_gas,
                timestamp=int(time.time()),
            )

        except Exception as e:
            logger.error(f"Swap execution failed: {e}", exc_info=True)
            return None

    async def _build_v3_swap_tx(
        self, quote: QuoteResult, nonce: int, gas_info: dict, deadline: int
    ) -> dict:
        """Build a V3-style exactInput swap transaction manually."""
        from eth_abi import encode

        path = encode(
            ["address", "uint24", "address"],
            [Web3.to_checksum_address(quote.token_in), 3000, Web3.to_checksum_address(quote.token_out)],
        )
        params_encoded = encode(
            ["bytes", "address", "uint256", "uint256", "uint256"],
            [path, self.wallet, deadline, quote.amount_in, quote.amount_out_min],
        )
        selector = Web3.keccak(text="exactInput((bytes,address,uint256,uint256,uint256))")[:4]
        calldata = selector + params_encoded

        router_contract = self.w3.eth.contract(
            address=self.universal_router, abi=UNIVERSAL_ROUTER_ABI
        )
        return {
            "from": self.wallet,
            "to": self.universal_router,
            "data": calldata.hex(),
            "value": 0,
            "nonce": nonce,
            "chainId": self.chain_id,
            "gas": 400_000,
            **gas_info,
        }

    async def _ensure_allowance(
        self, token: str, spender: str, amount: int
    ) -> bool:
        """Approve ERC-20 token if allowance is insufficient."""
        try:
            contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(token), abi=ERC20_ABI
            )
            current = contract.functions.allowance(self.wallet, Web3.to_checksum_address(spender)).call()
            if current >= amount:
                return True

            # Approve max uint256
            nonce = self.w3.eth.get_transaction_count(self.wallet)
            gas_info = await self._get_eip1559_gas()
            tx = contract.functions.approve(
                Web3.to_checksum_address(spender),
                2**256 - 1,
            ).build_transaction({
                "from": self.wallet,
                "nonce": nonce,
                "gas": 60_000,
                "chainId": self.chain_id,
                **gas_info,
            })
            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = await self._wait_for_receipt("0x" + tx_hash.hex(), timeout=60)
            return receipt is not None and receipt.get("status") == 1
        except Exception as e:
            logger.error(f"Allowance/approve failed: {e}")
            return False

    async def _get_eip1559_gas(self) -> dict:
        """Get EIP-1559 gas parameters for Base chain."""
        try:
            fee_history = self.w3.eth.fee_history(1, "latest", [25])
            base_fee = fee_history["baseFeePerGas"][-1]
            priority_fee = Web3.to_wei(0.001, "gwei")  # Base is cheap
            max_fee = base_fee * 2 + priority_fee
            return {
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": priority_fee,
            }
        except Exception:
            # Legacy gas fallback
            gas_price = self.w3.eth.gas_price
            return {"gasPrice": gas_price}

    async def _wait_for_receipt(
        self, tx_hash: str, timeout: int = 120
    ) -> Optional[dict]:
        """Poll for transaction receipt with timeout."""
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

    async def get_token_balance(self, token: str, wallet: Optional[str] = None) -> float:
        """Return token balance in human-readable units."""
        wallet = wallet or self.wallet
        if not wallet:
            return 0.0
        try:
            contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(token), abi=ERC20_ABI
            )
            balance = contract.functions.balanceOf(wallet).call()
            decimals = contract.functions.decimals().call()
            return balance / (10 ** decimals)
        except Exception as e:
            logger.error(f"Balance check failed for {token}: {e}")
            return 0.0

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
