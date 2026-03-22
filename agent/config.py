"""
AEGIS Trading Agent — Configuration
Loads all environment variables, chain config, contract addresses.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ─── Chain IDs ──────────────────────────────────────────────────────────────

CHAIN_BASE_MAINNET = 8453
CHAIN_BASE_SEPOLIA = 84532


# ─── Contract Addresses ───────────────────────────────────────────────────────

class BaseAddresses:
    """Deployed contract addresses on Base Mainnet."""
    # Uniswap v4
    POOL_MANAGER = "0x498581fF718922c3f8e6A244956aF099B2652b2b"
    UNIVERSAL_ROUTER = "0x6fF5693b99212Da76ad316178A184AB56D299b43"
    PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
    STATE_VIEW = "0x571291b572ed32ce6751a2cb2cff00b6a4b8e05a"
    POSITION_MANAGER = "0x7C5f5A4bBd8fD63184577525326123B519429bDc"

    # Tokens
    WETH = "0x4200000000000000000000000000000000000006"
    USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    USDT = "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2"
    cbETH = "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22"

    # Chainlink price feeds
    ETH_USD_FEED = "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70"
    BTC_USD_FEED = "0xCCADC697c55bbB68dc5bCdf8d3CBe83CdD4E071e"


class BaseSepoliaAddresses:
    """Deployed contract addresses on Base Sepolia (testnet)."""
    # Uniswap v4
    POOL_MANAGER = "0x05E73354cFDd6745C338b50BcFDfA3Aa6fA4057b"
    UNIVERSAL_ROUTER = "0x492E6456D9528771018DeB9E87ef7750EF184104"
    PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
    STATE_VIEW = "0xD9a7Fd2a5F9E3E20E56C93b02C726eD14A9eBcdb"
    POSITION_MANAGER = "0x4B2c77d209D3405F41a037Ec6c77F7F5b8e2ca80"

    # Tokens (Base Sepolia test tokens)
    WETH = "0x4200000000000000000000000000000000000006"
    USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"

    # AEGIS-deployed contracts (update after deployment)
    AEGIS_CONTROLLER = os.getenv("AEGIS_CONTROLLER_ADDRESS", "")
    AEGIS_VAULT = os.getenv("AEGIS_VAULT_ADDRESS", "")
    AEGIS_REPORTER = os.getenv("AEGIS_REPORTER_ADDRESS", "")


# ─── Config Dataclass ────────────────────────────────────────────────────────

@dataclass
class Config:
    # RPC
    base_mainnet_rpc: str = field(default_factory=lambda: os.getenv(
        "BASE_MAINNET_RPC",
        f"https://base-mainnet.g.alchemy.com/v2/{os.getenv('ALCHEMY_API_KEY', '')}"
    ))
    base_sepolia_rpc: str = field(default_factory=lambda: os.getenv(
        "BASE_SEPOLIA_RPC",
        f"https://base-sepolia.g.alchemy.com/v2/{os.getenv('ALCHEMY_API_KEY', '')}"
    ))

    # Keys
    alchemy_api_key: str = field(default_factory=lambda: os.getenv("ALCHEMY_API_KEY", ""))
    private_key: str = field(default_factory=lambda: os.getenv("PRIVATE_KEY", ""))
    cryptopanic_api_key: str = field(default_factory=lambda: os.getenv("CRYPTOPANIC_API_KEY", ""))
    uniswap_api_key: str = field(default_factory=lambda: os.getenv("UNISWAP_API_KEY", ""))
    pinata_jwt: str = field(default_factory=lambda: os.getenv("PINATA_JWT", ""))
    bond_credit_api_key: str = field(default_factory=lambda: os.getenv("BOND_CREDIT_API_KEY", ""))

    # Network
    chain_id: int = field(default_factory=lambda: int(os.getenv("CHAIN_ID", str(CHAIN_BASE_SEPOLIA))))
    use_testnet: bool = field(default_factory=lambda: os.getenv("USE_TESTNET", "true").lower() == "true")

    # Trading
    max_position_pct: float = 0.05       # 5% of portfolio per trade
    max_daily_loss_pct: float = 0.05     # 5% daily circuit breaker
    max_total_drawdown_pct: float = 0.15 # 15% emergency stop
    max_concurrent_positions: int = 3
    max_price_impact_pct: float = 0.01   # 1% max slippage
    min_gas_profit_multiple: float = 3.0
    default_slippage_bps: int = 50       # 0.5% slippage

    # Agent loop timing
    loop_interval_seconds: int = 900     # 15 minutes
    evolution_interval_hours: int = 24

    # Model
    hmm_lookback_bars: int = 50          # bars fed to HMM for inference
    hmm_training_bars: int = 500         # bars for initial HMM training
    ohlcv_timeframe: str = "1h"

    # Uniswap
    swap_deadline_seconds: int = 300     # 5 min deadline

    @property
    def rpc_url(self) -> str:
        return self.base_sepolia_rpc if self.use_testnet else self.base_mainnet_rpc

    @property
    def addresses(self):
        return BaseSepoliaAddresses if self.use_testnet else BaseAddresses

    def validate(self) -> bool:
        """Return True if critical config is present."""
        missing = []
        if not self.private_key:
            missing.append("PRIVATE_KEY")
        if not self.alchemy_api_key:
            missing.append("ALCHEMY_API_KEY")
        if missing:
            logger.warning(f"Missing config keys: {missing}. Agent will run in read-only mode.")
            return False
        return True


# Singleton
_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
