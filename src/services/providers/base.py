"""
ChainProvider — abstract interface for multi-chain portfolio providers.

Each provider handles one chain type (EVM, Bitcoin, Solana, Cosmos, etc.).
The registry maps addresses to the right provider via detect().

2026.07.21 — Foundation for Phase 2 multi-chain support.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

import logging

logger = logging.getLogger("crypto.providers")


class ChainProvider(ABC):
    """Abstract base for chain-specific portfolio/transaction providers.

    To add a new chain type (Bitcoin, Solana, Cosmos...):
      1. Subclass ChainProvider
      2. Implement all abstract methods
      3. Register it (call _register() or add to PROVIDERS list)
    """

    # ── Subclass MUST set these ──────────────────────────────────
    chain_type: str            # e.g. "evm", "bitcoin", "solana"
    native_symbol: str = ""    # e.g. "ETH", "BTC", "SOL"

    # ── Detection ────────────────────────────────────────────────

    @abstractmethod
    def detect(self, address: str) -> bool:
        """Return True if `address` belongs to this chain type.

        Must be pure (no I/O), fast (<1ms), and deterministic.
        """
        ...

    # ── Portfolio ────────────────────────────────────────────────

    @abstractmethod
    async def get_portfolio(self, address: str) -> dict:
        """Return the portfolio for `address`.

        Return shape MUST match the existing /api/portfolio response:
            {
                "address": str,
                "total_usd": float,
                "token_count": int,
                "chain_count": int,
                "chains": {...},
                "tokens": [{...}],
                "errors": [...],
            }
        For unsupported addresses, return:
            {
                "supported": False,
                "chain_type": self.chain_type,
                "message": "Chaine non prise en charge (a venir)",
            }
        """
        ...

    # ── Transactions ─────────────────────────────────────────────

    @abstractmethod
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
        """Return paginated transaction events.

        Return shape MUST match the existing /api/transactions response:
            {"total": int, "items": [...], "counts": {...}}
        """
        ...

    # ── Explorer URL (optional) ──────────────────────────────────

    def explorer_url(self, address: str) -> str | None:
        """Return a human-readable explorer URL for the address, or None."""
        return None

    def explorer_tx_url(self, tx_hash: str) -> str | None:
        """Return a human-readable explorer URL for the transaction, or None."""
        return None


# ═══════════════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════════════

PROVIDERS: list[ChainProvider] = []


def register_provider(provider: ChainProvider) -> None:
    """Add a provider to the registry. Called on import / startup."""
    if provider not in PROVIDERS:
        PROVIDERS.append(provider)
        logger.info(
            "Provider registered: %s (chain_type=%s)",
            type(provider).__name__,
            provider.chain_type,
        )


def provider_for(address: str) -> ChainProvider | None:
    """Return the first registered provider whose detect() matches.

    Returns None if no provider can handle the address.
    """
    for p in PROVIDERS:
        try:
            if p.detect(address):
                return p
        except Exception:
            # A broken detect() must never crash the registry
            logger.debug(
                "Provider %s raised on detect(%s), skipping",
                type(p).__name__, address[:20],
            )
            continue
    return None
