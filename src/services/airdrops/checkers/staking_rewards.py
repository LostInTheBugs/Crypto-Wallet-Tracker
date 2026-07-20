"""
Cosmos staking rewards checker — reuses the existing CosmosProvider
to detect pending staking rewards and surface them as claimable airdrops.

This is the most reliable checker because:
1. Staking rewards are REAL, on-chain, verifiable via public LCD.
2. CosmosProvider already fetches them (no duplicated logic).
3. The user can claim them directly via any Cosmos wallet or Mintscan.

2026.07.25 — Phase 2 airdrop detection.
"""

from __future__ import annotations

import asyncio
import logging

from services.airdrops import AirdropChecker, register_checker

logger = logging.getLogger("crypto.airdrops.staking")


# ── Helper to avoid circular imports at module level ──────────────


def _get_cosmos_provider():
    """Lazy-import CosmosProvider to avoid circular imports."""
    from services.providers.cosmos import (
        CosmosProvider,
        _identify_hrp,
        _hrp_info,
        _get_balances,
        _get_delegations,
        _get_rewards,
        _get_cosmos_price_usd,
        _denom_to_symbol,
    )
    return (
        CosmosProvider,
        _identify_hrp,
        _hrp_info,
        _get_balances,
        _get_delegations,
        _get_rewards,
        _get_cosmos_price_usd,
        _denom_to_symbol,
    )


class CosmosStakingRewardsChecker(AirdropChecker):
    """Detect claimable Cosmos staking rewards.

    Reuses CosmosProvider's existing LCD calls to fetch pending
    distribution rewards.  Each reward denom becomes an AirdropClaim
    with status "claimable" and a link to Mintscan wallet page.
    """

    name = "cosmos_staking_rewards"
    chain_types = ["cosmos"]

    async def check(self, address: str) -> list[dict]:
        """Return claimable staking rewards for a Cosmos address."""
        (
            _CosmosProvider,
            _identify_hrp,
            _hrp_info,
            _get_balances,
            _get_delegations,
            _get_rewards,
            _get_cosmos_price_usd,
            _denom_to_symbol,
        ) = _get_cosmos_provider()

        hrp = _identify_hrp(address)
        if hrp is None:
            return []

        info = _hrp_info(hrp)
        if info is None:
            return []

        lcd = info["lcd"]
        native_sym = info["symbol"]
        cg_id = info["coingecko_id"]
        explorer_prefix = info.get("explorer_prefix", hrp)

        # Fetch rewards + price in parallel
        try:
            rewards, price = await asyncio.gather(
                _get_rewards(lcd, address),
                _get_cosmos_price_usd(cg_id),
            )
        except Exception as e:
            logger.debug(
                "Cosmos rewards fetch failed for %s: %s", address[:20], e
            )
            return []

        price = price or 0.0
        claim_url = (
            f"https://www.mintscan.io/{explorer_prefix}/account/{address}"
        )

        claims: list[dict] = []
        for r in rewards:
            denom = r["denom"]
            amt_base = r["amount"]
            sym, exp = _denom_to_symbol(denom)
            divisor = 10**exp
            amount = amt_base / divisor
            is_native = sym.upper() == native_sym.upper()
            token_price = price if is_native else 0.0
            usd_value = round(amount * token_price, 2)

            claims.append({
                "source": self.name,
                "chain": f"cosmos-{hrp}",
                "token_symbol": sym,
                "amount": round(amount, 6),
                "usd_value": usd_value,
                "claim_url": claim_url,
                "status": "claimable",
                "details": (
                    f"Staking rewards for {native_sym}"
                    if is_native
                    else f"Pending rewards ({denom})"
                ),
            })

        return claims


# ── Auto-register on import ───────────────────────────────────────

cosmos_staking_rewards_checker = CosmosStakingRewardsChecker()
register_checker(cosmos_staking_rewards_checker)
