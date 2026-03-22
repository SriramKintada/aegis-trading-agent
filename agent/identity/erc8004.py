"""
AEGIS — ERC-8004 Agent Identity
Registers agent identity on-chain via ERC-8004 Identity Registry.
Stores agent metadata (name, description, capabilities) on IPFS.
"""

import json
import logging
from dataclasses import dataclass, asdict
from typing import Optional

import aiohttp
from web3 import Web3

logger = logging.getLogger(__name__)

# ERC-8004 Identity Registry on Base Sepolia
# Note: Update this address after registry is deployed/confirmed for the hackathon
ERC8004_REGISTRY_SEPOLIA = "0x0000000000000000000000000000000000000000"  # TBD

ERC8004_REGISTRY_ABI = [
    {
        "name": "register",
        "type": "function",
        "inputs": [{"name": "agentCardURI", "type": "string"}],
        "outputs": [{"name": "tokenId", "type": "uint256"}],
        "stateMutability": "nonpayable",
    },
    {
        "name": "getAgentCard",
        "type": "function",
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "outputs": [{"name": "agentCardURI", "type": "string"}],
        "stateMutability": "view",
    },
    {
        "name": "ownerOf",
        "type": "function",
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
    },
]

PINATA_API = "https://api.pinata.cloud"


@dataclass
class AgentCard:
    """ERC-8004 Agent Card — JSON metadata pinned to IPFS."""
    name: str = "AEGIS-v1"
    description: str = (
        "Autonomous HMM-guided trading agent on Base. "
        "Uses regime detection, FinBERT sentiment, Kelly position sizing, "
        "and genetic strategy evolution."
    )
    version: str = "1.0.0"
    capabilities: list = None
    protocols: list = None
    creator: str = ""
    created: str = ""

    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = [
                "trading",
                "portfolio-management",
                "sentiment-analysis",
                "regime-detection",
                "genetic-evolution",
            ]
        if self.protocols is None:
            self.protocols = ["https", "a2a"]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


class ERC8004Identity:
    """
    Manages AEGIS agent identity on Base chain via ERC-8004.

    Flow:
      1. Build AgentCard JSON
      2. Pin to IPFS (Pinata)
      3. Register with on-chain registry → get tokenId
      4. Use tokenId in all trade events for provenance
    """

    def __init__(
        self,
        w3: Web3,
        wallet: str,
        account,
        chain_id: int = 84532,
        registry_address: str = ERC8004_REGISTRY_SEPOLIA,
        pinata_jwt: str = "",
    ):
        self.w3 = w3
        self.wallet = wallet
        self.account = account
        self.chain_id = chain_id
        self.registry_address = Web3.to_checksum_address(registry_address) if registry_address and registry_address != "0x" + "0" * 40 else None
        self.pinata_jwt = pinata_jwt

        self._token_id: Optional[int] = None
        self._ipfs_hash: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {}
            if self.pinata_jwt:
                headers["Authorization"] = f"Bearer {self.pinata_jwt}"
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    async def pin_to_ipfs(self, agent_card: AgentCard) -> Optional[str]:
        """
        Pin agent card JSON to IPFS via Pinata.
        Returns IPFS hash (CID) or None on failure.
        """
        if not self.pinata_jwt:
            logger.warning("PINATA_JWT not configured — skipping IPFS pin")
            return None

        session = await self._get_session()
        payload = {
            "pinataContent": json.loads(agent_card.to_json()),
            "pinataMetadata": {"name": f"aegis-agent-card-{agent_card.version}"},
        }

        try:
            async with session.post(
                f"{PINATA_API}/pinning/pinJSONToIPFS",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                cid = data.get("IpfsHash")
                self._ipfs_hash = cid
                logger.info(f"Agent card pinned to IPFS: ipfs://{cid}")
                return cid
        except Exception as e:
            logger.error(f"IPFS pin failed: {e}")
            return None

    async def register(self, agent_card: Optional[AgentCard] = None) -> Optional[int]:
        """
        Register agent identity on-chain.

        Returns:
            tokenId if successful, None otherwise
        """
        from datetime import datetime, timezone

        if agent_card is None:
            agent_card = AgentCard(
                creator=self.wallet,
                created=datetime.now(timezone.utc).isoformat(),
            )

        # Pin to IPFS first
        cid = await self.pin_to_ipfs(agent_card)
        if cid:
            agent_card_uri = f"ipfs://{cid}"
        else:
            # Fallback: use inline JSON as data URI
            import base64
            encoded = base64.b64encode(agent_card.to_json().encode()).decode()
            agent_card_uri = f"data:application/json;base64,{encoded}"

        # Register on-chain
        if not self.registry_address:
            logger.warning("ERC-8004 registry address not set — skipping on-chain registration")
            logger.info(f"Agent card URI: {agent_card_uri}")
            return None

        if not self.account:
            logger.warning("No private key — cannot register on-chain")
            return None

        try:
            registry = self.w3.eth.contract(
                address=self.registry_address,
                abi=ERC8004_REGISTRY_ABI,
            )
            nonce = self.w3.eth.get_transaction_count(self.wallet)
            gas_price = self.w3.eth.gas_price

            tx = registry.functions.register(agent_card_uri).build_transaction({
                "from": self.wallet,
                "nonce": nonce,
                "gas": 200_000,
                "gasPrice": gas_price,
                "chainId": self.chain_id,
            })
            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            logger.info(f"Registration tx: 0x{tx_hash.hex()}")

            # Wait for receipt
            import time, asyncio
            for _ in range(30):
                try:
                    receipt = self.w3.eth.get_transaction_receipt(tx_hash)
                    if receipt:
                        # Parse tokenId from logs
                        # ERC-721 Transfer event: topics[3] = tokenId
                        if receipt.get("logs"):
                            log = receipt["logs"][-1]
                            topics = log.get("topics", [])
                            if len(topics) >= 4:
                                token_id = int(topics[3].hex(), 16)
                                self._token_id = token_id
                                logger.info(f"ERC-8004 Agent registered! Token ID: {token_id}")
                                return token_id
                        break
                except Exception:
                    pass
                await asyncio.sleep(3)

        except Exception as e:
            logger.error(f"On-chain registration failed: {e}")

        return None

    @property
    def token_id(self) -> Optional[int]:
        return self._token_id

    @property
    def ipfs_hash(self) -> Optional[str]:
        return self._ipfs_hash

    def get_identity_summary(self) -> dict:
        return {
            "token_id": self._token_id,
            "ipfs_hash": self._ipfs_hash,
            "wallet": self.wallet,
            "chain_id": self.chain_id,
            "registry": self.registry_address,
        }

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
