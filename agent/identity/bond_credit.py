"""
AEGIS — bond.credit ACE Integration
Reports trade performance to the Agentic Credit Engine for credit score accrual.
"""

import json
import logging
import time
from dataclasses import dataclass, asdict
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

BOND_CREDIT_API = "https://api.bond.credit/v1"


@dataclass
class TradeRecord:
    tx_hash: str
    token_in: str
    token_out: str
    amount_in_usd: float
    amount_out_usd: float
    pnl_usd: float
    pnl_pct: float
    strategy: str
    regime: str
    timestamp: int
    gas_cost_usd: float


@dataclass
class PerformanceReport:
    agent_id: str               # ERC-8004 token ID or wallet address
    period_start: int           # Unix timestamp
    period_end: int
    trades: list[TradeRecord]
    total_pnl_usd: float
    total_pnl_pct: float
    win_rate: float
    sharpe_ratio: float
    max_drawdown_pct: float
    num_trades: int
    regime_accuracy: float


class BondCreditReporter:
    """
    Submits AEGIS performance data to bond.credit ACE.
    Builds on-chain credit score through verified profitable trading.

    If API is unavailable (no key), logs locally and prepares report for
    manual submission.
    """

    def __init__(
        self,
        api_key: str = "",
        agent_id: str = "",
        wallet: str = "",
    ):
        self.api_key = api_key
        self.agent_id = agent_id or wallet
        self.wallet = wallet

        self._session: Optional[aiohttp.ClientSession] = None
        self._trade_log: list[TradeRecord] = []
        self._session_start = int(time.time())

        # Running stats
        self._peak_portfolio: float = 0.0
        self._current_portfolio: float = 0.0
        self._max_drawdown: float = 0.0
        self._returns: list[float] = []

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    def record_trade(
        self,
        tx_hash: str,
        token_in: str,
        token_out: str,
        amount_in_usd: float,
        amount_out_usd: float,
        gas_cost_usd: float,
        strategy: str,
        regime: str,
    ):
        """Record a completed trade locally."""
        pnl_usd = amount_out_usd - amount_in_usd - gas_cost_usd
        pnl_pct = pnl_usd / amount_in_usd if amount_in_usd > 0 else 0.0

        record = TradeRecord(
            tx_hash=tx_hash,
            token_in=token_in,
            token_out=token_out,
            amount_in_usd=amount_in_usd,
            amount_out_usd=amount_out_usd,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            strategy=strategy,
            regime=regime,
            timestamp=int(time.time()),
            gas_cost_usd=gas_cost_usd,
        )
        self._trade_log.append(record)
        self._returns.append(pnl_pct)

        # Update drawdown tracking
        self._current_portfolio += pnl_usd
        if self._peak_portfolio == 0:
            self._peak_portfolio = max(amount_in_usd, 1.0)
        self._peak_portfolio = max(self._peak_portfolio, self._current_portfolio + amount_in_usd)
        if self._peak_portfolio > 0:
            dd = (self._peak_portfolio - self._current_portfolio) / self._peak_portfolio
            self._max_drawdown = max(self._max_drawdown, dd)

        logger.info(
            f"Trade recorded: {tx_hash[:12]}... | "
            f"P&L: ${pnl_usd:.2f} ({pnl_pct:.2%}) | "
            f"Strategy: {strategy}"
        )

    def _compute_sharpe(self) -> float:
        """Compute simplified Sharpe ratio from return history."""
        import numpy as np
        if len(self._returns) < 3:
            return 0.0
        arr = np.array(self._returns)
        mean = arr.mean()
        std = arr.std()
        if std == 0:
            return 0.0
        # Annualize: assuming 15-min cycles → ~35040 cycles/year
        annualization = (35040 ** 0.5)
        return float(mean / std * annualization)

    def build_report(self) -> PerformanceReport:
        """Build performance report from logged trades."""
        if not self._trade_log:
            return PerformanceReport(
                agent_id=self.agent_id,
                period_start=self._session_start,
                period_end=int(time.time()),
                trades=[],
                total_pnl_usd=0.0,
                total_pnl_pct=0.0,
                win_rate=0.0,
                sharpe_ratio=0.0,
                max_drawdown_pct=0.0,
                num_trades=0,
                regime_accuracy=0.0,
            )

        wins = [t for t in self._trade_log if t.pnl_usd > 0]
        total_pnl = sum(t.pnl_usd for t in self._trade_log)
        total_invested = sum(t.amount_in_usd for t in self._trade_log)

        return PerformanceReport(
            agent_id=self.agent_id,
            period_start=self._session_start,
            period_end=int(time.time()),
            trades=self._trade_log,
            total_pnl_usd=total_pnl,
            total_pnl_pct=total_pnl / total_invested if total_invested > 0 else 0.0,
            win_rate=len(wins) / len(self._trade_log),
            sharpe_ratio=self._compute_sharpe(),
            max_drawdown_pct=self._max_drawdown,
            num_trades=len(self._trade_log),
            regime_accuracy=0.0,  # TODO: track predicted vs actual regime performance
        )

    async def submit_report(self, report: Optional[PerformanceReport] = None) -> bool:
        """
        Submit performance report to bond.credit API.
        Returns True on success.
        """
        report = report or self.build_report()

        # Always log locally
        report_dict = {
            "agent_id": report.agent_id,
            "period_start": report.period_start,
            "period_end": report.period_end,
            "num_trades": report.num_trades,
            "total_pnl_usd": round(report.total_pnl_usd, 4),
            "total_pnl_pct": round(report.total_pnl_pct, 6),
            "win_rate": round(report.win_rate, 4),
            "sharpe_ratio": round(report.sharpe_ratio, 4),
            "max_drawdown_pct": round(report.max_drawdown_pct, 4),
            "regime_accuracy": report.regime_accuracy,
            "trade_hashes": [t.tx_hash for t in report.trades],
        }

        logger.info(f"Performance report: {json.dumps(report_dict, indent=2)}")

        if not self.api_key:
            logger.warning("BOND_CREDIT_API_KEY not set — report logged locally only")
            return False

        try:
            session = await self._get_session()
            async with session.post(
                f"{BOND_CREDIT_API}/performance",
                json=report_dict,
            ) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    logger.info(
                        f"Performance submitted to bond.credit | "
                        f"Score: {data.get('score', 'N/A')} | "
                        f"Response: {data}"
                    )
                    return True
                else:
                    body = await resp.text()
                    logger.warning(f"bond.credit API returned {resp.status}: {body}")
                    return False
        except Exception as e:
            logger.error(f"bond.credit submission failed: {e}")
            return False

    @property
    def trade_count(self) -> int:
        return len(self._trade_log)

    @property
    def cumulative_pnl_usd(self) -> float:
        return sum(t.pnl_usd for t in self._trade_log)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
