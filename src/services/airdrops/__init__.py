"""
Airdrops — best-effort claimable airdrop detection via extensible checker registry.

Each checker is a self-contained module that knows how to query a specific
source (staking rewards, airdrop eligibility endpoints, etc.) for one or more
chain types.  The registry routes an address to the right checkers by matching
the provider's chain_type against each checker's chain_types list.

Design principles:
- NO API key or fragile scraping checkers.  Only free, public endpoints.
- Defensive: one broken checker never blocks others — timeouts and exceptions
  are caught per-checker.
- Extensible: adding a new airdrop source = writing one checker file + dropping
  it in src/services/airdrops/checkers/ (auto-discovered on import).
- Never invent amounts or eligibility.  If not verifiable → no claim returned.

2026.07.25 — Phase 2 airdrop detection.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable

logger = logging.getLogger("crypto.airdrops")

# ═══════════════════════════════════════════════════════════════════
# Data model
# ═══════════════════════════════════════════════════════════════════

# AirdropClaim is a plain dict with these keys:
#
#   source:       str   — checker name (e.g. "cosmos_staking_rewards")
#   chain:        str   — chain label (e.g. "cosmos-cosmos", "ethereum")
#   token_symbol: str   — e.g. "ATOM", "OSMO"
#   amount:       float — human-readable amount (e.g. 0.123456)
#   usd_value:    float — best-effort USD value, 0 if unknown
#   claim_url:    str   — URL where the user can claim (e.g. Mintscan wallet page)
#   status:       str   — "claimable" | "pending" | "info"
#   details:      str   — human-readable extra info


# ═══════════════════════════════════════════════════════════════════
# Interface
# ═══════════════════════════════════════════════════════════════════


class AirdropChecker(ABC):
    """Abstract interface for an airdrop/claim checker.

    Subclasses must set:
      - name: unique short identifier (e.g. "cosmos_staking_rewards")
      - chain_types: list of chain_type strings this checker handles
                     (e.g. ["cosmos"], ["evm"], ["solana"])

    And implement:
      - async check(address) -> list[dict]  (AirdropClaim dicts)
    """

    name: str = ""
    chain_types: list[str] = []

    @abstractmethod
    async def check(self, address: str) -> list[dict]:
        """Return claimable airdrop dicts for `address`.

        Must be defensive: NEVER raise.  Return [] on any error.
        """
        ...


# ═══════════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════════

_AIRDROP_CHECKERS: list[AirdropChecker] = []


def register_checker(checker: AirdropChecker) -> None:
    """Add a checker to the registry."""
    if checker not in _AIRDROP_CHECKERS:
        _AIRDROP_CHECKERS.append(checker)
        logger.info(
            "Airdrop checker registered: %s (chain_types=%s)",
            checker.name,
            checker.chain_types,
        )


def get_checkers() -> list[AirdropChecker]:
    """Return all registered checkers (useful for test introspection)."""
    return list(_AIRDROP_CHECKERS)


# ═══════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════

_CHECKER_TIMEOUT = 15.0  # per-checker timeout in seconds


async def get_claimable_airdrops(
    address: str,
    chain_type: str,
) -> list[dict]:
    """Run all checkers whose chain_types include `chain_type`.

    Returns aggregated list of AirdropClaim dicts.  A broken checker (exception,
    timeout) is silently skipped — it never blocks the others.

    Args:
        address: The wallet address to check.
        chain_type: The chain type from provider_for(address).chain_type
                    (e.g. "evm", "cosmos", "bitcoin", "solana").
    """
    claims: list[dict] = []

    for checker in _AIRDROP_CHECKERS:
        if chain_type not in checker.chain_types:
            continue

        try:
            result = await asyncio.wait_for(
                checker.check(address),
                timeout=_CHECKER_TIMEOUT,
            )
            if isinstance(result, list):
                claims.extend(result)
        except asyncio.TimeoutError:
            logger.debug(
                "Airdrop checker %s timed out for %s...",
                checker.name, address[:20],
            )
        except Exception as e:
            logger.debug(
                "Airdrop checker %s failed for %s...: %s",
                checker.name, address[:20], e,
            )

    return claims


# ═══════════════════════════════════════════════════════════════════
# Auto-discover checkers on import
# ═══════════════════════════════════════════════════════════════════

# Import all checker modules so they self-register.
# New checkers just need to be added here.
import services.airdrops.checkers.staking_rewards  # noqa: F401,E402 — auto-registers
