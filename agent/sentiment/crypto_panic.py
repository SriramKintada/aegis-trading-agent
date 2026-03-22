"""
AEGIS — CryptoPanic API Client
Fetches latest crypto news headlines for sentiment analysis.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://cryptopanic.com/api/v1"


@dataclass
class NewsItem:
    title: str
    url: str
    published_at: str
    source: str
    currencies: list[str]
    sentiment: Optional[str]  # "positive", "negative", "important", or None
    votes: dict               # {"positive": n, "negative": n, ...}


class CryptoPanicClient:
    """
    Async client for the CryptoPanic API.
    Free tier: 100 requests/hour.

    Docs: https://cryptopanic.com/developers/api/
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: list[NewsItem] = []
        self._cache_ts: float = 0
        self._cache_ttl: int = 300  # 5 min cache

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def _request(self, endpoint: str, params: dict) -> dict:
        session = await self._get_session()
        url = f"{BASE_URL}/{endpoint}/"
        if self.api_key:
            params["auth_token"] = self.api_key
        else:
            # Public endpoint (limited, no auth)
            pass

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 429:
                    logger.warning("CryptoPanic rate limited — sleeping 60s")
                    await asyncio.sleep(60)
                    return {}
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientError as e:
            logger.error(f"CryptoPanic request failed: {e}")
            return {}

    def _parse_results(self, data: dict) -> list[NewsItem]:
        items = []
        for result in data.get("results", []):
            currencies = [c.get("code", "") for c in result.get("currencies", [])]
            votes = result.get("votes", {})
            items.append(NewsItem(
                title=result.get("title", ""),
                url=result.get("url", ""),
                published_at=result.get("published_at", ""),
                source=result.get("source", {}).get("title", ""),
                currencies=currencies,
                sentiment=result.get("kind"),   # "news", "media" — filter at caller level
                votes=votes,
            ))
        return items

    async def fetch_news(
        self,
        currencies: list[str] = None,
        filter_: str = "hot",            # "latest", "hot", "bullish", "bearish", "important"
        limit: int = 20,
        use_cache: bool = True,
    ) -> list[NewsItem]:
        """
        Fetch latest news from CryptoPanic.

        Args:
            currencies: List of coin symbols, e.g. ["ETH", "BTC"]
            filter_: API filter type
            limit: Max items to return
            use_cache: Return cached results if within TTL

        Returns:
            List of NewsItem objects
        """
        now = time.time()
        if use_cache and self._cache and (now - self._cache_ts) < self._cache_ttl:
            logger.debug(f"Returning {len(self._cache)} cached news items")
            return self._cache[:limit]

        params: dict = {
            "public": "true",
            "filter": filter_,
        }
        if currencies:
            params["currencies"] = ",".join(currencies)

        data = await self._request("posts", params)
        if not data:
            logger.warning("CryptoPanic returned no data — using cache or empty")
            return self._cache[:limit] if self._cache else []

        items = self._parse_results(data)
        self._cache = items
        self._cache_ts = now
        logger.info(f"Fetched {len(items)} news items from CryptoPanic")
        return items[:limit]

    async def fetch_with_fallback(
        self,
        currencies: list[str] = None,
    ) -> list[NewsItem]:
        """
        Fetch news with fallback to Fear & Greed Index if API fails.
        Returns at least some sentiment-bearing data.
        """
        items = await self.fetch_news(currencies=currencies or ["ETH", "BTC"])
        if items:
            return items

        # Fallback: try Fear & Greed Index as a single "headline"
        logger.info("Falling back to Fear & Greed Index for sentiment")
        try:
            session = await self._get_session()
            async with session.get(
                "https://api.alternative.me/fng/?limit=1",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                fng = await resp.json()
                fng_data = fng.get("data", [{}])[0]
                value = int(fng_data.get("value", 50))
                label = fng_data.get("value_classification", "Neutral")
                # Convert to fake NewsItem for pipeline compatibility
                return [NewsItem(
                    title=f"Fear & Greed Index: {value} ({label})",
                    url="https://alternative.me/crypto/fear-and-greed-index/",
                    published_at=fng_data.get("timestamp", ""),
                    source="alternative.me",
                    currencies=["BTC", "ETH"],
                    sentiment="positive" if value > 55 else ("negative" if value < 45 else None),
                    votes={"fng_value": value},
                )]
        except Exception as e:
            logger.error(f"Fear & Greed fallback also failed: {e}")
            return []

    async def get_headlines(self, currencies: list[str] = None) -> list[str]:
        """Return plain list of headline strings (convenience method)."""
        items = await self.fetch_with_fallback(currencies)
        return [item.title for item in items]

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
