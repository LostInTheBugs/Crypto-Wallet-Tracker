"""
EvmProvider — wraps existing EVM/Blockscout portfolio & transaction logic.

This is a THIN wrapper.  It does NOT copy or rewrite any existing logic.
All portfolio computation and transaction fetching stays in
portfolio_service.py and app.py exactly as before.  EvmProvider simply
delegates through callbacks that are wired up at app startup.

Design constraint (2026.07.21): zero behavior change for EVM addresses.
The same HTTP requests, the same responses, the same errors.
"""

from __future__ import annotations

import re
from typing import Callable, Awaitable, Any, Protocol

from services.providers.base import ChainProvider, register_provider, logger

# Type aliases for the callbacks that EvmProvider delegates to.
PortfolioFn = Callable[[str], Awaitable[dict]]


class TransactionsFn(Protocol):
    """Callback signature for get_transactions.

    Must accept address, wallet, chain, token, direction, event_type,
    limit, offset (all keyword) and return the standard response dict.
    """
    async def __call__(
        self,
        *,
        address: str,
        wallet: str | None = None,
        chain: str | None = None,
        token: str | None = None,
        direction: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict: ...


# ── Wiring state (module-level, set once during app startup) ──────

_portfolio_fn: PortfolioFn | None = None
_tx_fn: TransactionsFn | None = None


def wire_evm(portfolio_fn: PortfolioFn, tx_fn: TransactionsFn) -> None:
    """Wire the real implementations (called once during app startup)."""
    global _portfolio_fn, _tx_fn
    _portfolio_fn = portfolio_fn
    _tx_fn = tx_fn
    logger.info("EvmProvider wired: portfolio_fn=%s tx_fn=%s",
                 getattr(portfolio_fn, "__name__", "?"),
                 getattr(tx_fn, "__name__", "?"))


class EvmProvider(ChainProvider):
    """Ethereum / EVM-compatible chains via Blockscout API.

    Detection: 0x... addresses, 42 characters (20 bytes hex-encoded).
    """

    chain_type = "evm"
    native_symbol = "ETH"

    # ── Detection ────────────────────────────────────────────────

    _EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

    def detect(self, address: str) -> bool:
        """Return True for any 0x...42-char hex address."""
        return bool(self._EVM_RE.match(address))

    # ── Portfolio ────────────────────────────────────────────────

    async def get_portfolio(self, address: str) -> dict:
        """Delegate to _compute_portfolio via the wired callback."""
        if _portfolio_fn is None:
            raise RuntimeError("EvmProvider not wired — no _portfolio_fn set")
        return await _portfolio_fn(address)

    # ── Transactions ─────────────────────────────────────────────

    async def get_transactions(
        self,
        address: str,
        wallet: str | None = None,
        chain: str | None = None,
        token: str | None = None,
        direction: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """Delegate to the wired tx_fn callback."""
        if _tx_fn is None:
            raise RuntimeError("EvmProvider not wired — no _tx_fn set")
        return await _tx_fn(
            address=address,
            wallet=wallet,
            chain=chain,
            token=token,
            direction=direction,
            event_type=event_type,
            limit=limit,
            offset=offset,
        )

    # ── Explorer URLs ────────────────────────────────────────────

    def explorer_url(self, address: str) -> str | None:
        return f"https://eth.blockscout.com/address/{address}"

    def explorer_tx_url(self, tx_hash: str) -> str | None:
        return f"https://eth.blockscout.com/tx/{tx_hash}"


# ── Auto-register ──────────────────────────────────────────────────
evm_provider = EvmProvider()
register_provider(evm_provider)
