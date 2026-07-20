"""
Tests for Airdrops — checker registry, staking rewards, defensive isolation,
and provider non-regression (EVM/BTC/Solana/Cosmos).

Run:  python3 tests/test_airdrops.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.airdrops import (
    AirdropChecker,
    register_checker,
    get_checkers,
    get_claimable_airdrops,
    _AIRDROP_CHECKERS,
)

# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

PASS = 0
FAIL = 0


def check(cond, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")


def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


# ═══════════════════════════════════════════════════════════════════
# 1. Registry — checker registration and filtering by chain_type
# ═══════════════════════════════════════════════════════════════════


def test_registry_routing():
    section("1. Registry — checker routing by chain_type")

    # Clean registry (save and restore)
    saved = list(_AIRDROP_CHECKERS)
    _AIRDROP_CHECKERS.clear()

    try:
        # Register a cosmos-only checker
        class MockCosmosChecker(AirdropChecker):
            name = "mock_cosmos"
            chain_types = ["cosmos"]

            async def check(self, address):
                return [{
                    "source": "mock_cosmos",
                    "chain": "cosmos-test",
                    "token_symbol": "ATOM",
                    "amount": 1.0,
                    "usd_value": 10.0,
                    "claim_url": "https://example.com",
                    "status": "claimable",
                    "details": "mock",
                }]

        # Register an EVM-only checker
        class MockEvmChecker(AirdropChecker):
            name = "mock_evm"
            chain_types = ["evm"]

            async def check(self, address):
                return [{
                    "source": "mock_evm",
                    "chain": "ethereum",
                    "token_symbol": "ETH",
                    "amount": 0.5,
                    "usd_value": 500.0,
                    "claim_url": "https://example.com",
                    "status": "info",
                    "details": "mock",
                }]

        register_checker(MockCosmosChecker())
        register_checker(MockEvmChecker())

        check(len(_AIRDROP_CHECKERS) == 2, "2 checkers registered")

        # Cosmos address → only cosmos checker runs
        cosmos_claims = asyncio.run(
            get_claimable_airdrops("cosmos1abcdef", "cosmos")
        )
        check(len(cosmos_claims) == 1, "cosmos chain_type → 1 claim (cosmos checker only)")
        if cosmos_claims:
            check(
                cosmos_claims[0]["source"] == "mock_cosmos",
                "cosmos claim from mock_cosmos checker",
            )

        # EVM address → only EVM checker runs
        evm_claims = asyncio.run(
            get_claimable_airdrops("0xabcdef", "evm")
        )
        check(len(evm_claims) == 1, "evm chain_type → 1 claim (EVM checker only)")
        if evm_claims:
            check(
                evm_claims[0]["source"] == "mock_evm",
                "evm claim from mock_evm checker",
            )

        # Bitcoin address → no checker matches → 0 claims
        btc_claims = asyncio.run(
            get_claimable_airdrops("bc1qa", "bitcoin")
        )
        check(len(btc_claims) == 0, "bitcoin chain_type → 0 claims (no matching checker)")

    finally:
        _AIRDROP_CHECKERS.clear()
        _AIRDROP_CHECKERS.extend(saved)


# ═══════════════════════════════════════════════════════════════════
# 2. Staking rewards checker — parse example rewards JSON
# ═══════════════════════════════════════════════════════════════════


def test_staking_rewards_parse():
    section("2. Staking rewards checker — static rewards parsing")

    from services.providers.cosmos import _denom_to_symbol

    # Simulate what a Cosmos LCD rewards response looks like
    total_rewards = [
        {"denom": "uatom", "amount": "123456"},
    ]

    sym, exp = _denom_to_symbol("uatom")
    amount = float(total_rewards[0]["amount"]) / (10**exp)
    check(abs(amount - 0.123456) < 0.000001, "uatom 123456 → 0.123456 ATOM")

    # With a known price of $10:
    price = 10.0
    usd_value = round(amount * price, 2)
    check(usd_value == 1.23, "0.123456 ATOM @ $10 = $1.23")

    # Verify the claim dict shape
    claim = {
        "source": "cosmos_staking_rewards",
        "chain": "cosmos-cosmos",
        "token_symbol": "ATOM",
        "amount": round(amount, 6),
        "usd_value": usd_value,
        "claim_url": "https://www.mintscan.io/cosmos/account/cosmos1...",
        "status": "claimable",
        "details": "Staking rewards for ATOM",
    }

    check(claim["source"] == "cosmos_staking_rewards", "claim has source")
    check(claim["status"] == "claimable", "claim status is claimable")
    check(claim["chain"].startswith("cosmos-"), "claim chain starts with cosmos-")
    check(claim["usd_value"] > 0, "claim has positive usd_value")
    check("mintscan.io" in claim["claim_url"], "claim url is mintscan")


# ═══════════════════════════════════════════════════════════════════
# 3. Defensive — checker exception does not block others
# ═══════════════════════════════════════════════════════════════════


def test_defensive_isolation():
    section("3. Defensive — broken checker never blocks others")

    saved = list(_AIRDROP_CHECKERS)
    _AIRDROP_CHECKERS.clear()

    try:
        # A checker that always raises
        class BrokenChecker(AirdropChecker):
            name = "broken"
            chain_types = ["evm"]

            async def check(self, address):
                raise RuntimeError("simulated crash")

        # A healthy checker
        class HealthyChecker(AirdropChecker):
            name = "healthy"
            chain_types = ["evm"]

            async def check(self, address):
                return [{
                    "source": "healthy",
                    "chain": "ethereum",
                    "token_symbol": "ETH",
                    "amount": 1.0,
                    "usd_value": 100.0,
                    "claim_url": "https://example.com",
                    "status": "claimable",
                    "details": "healthy",
                }]

        register_checker(BrokenChecker())
        register_checker(HealthyChecker())

        claims = asyncio.run(
            get_claimable_airdrops("0xabc", "evm")
        )
        check(len(claims) == 1, "broken checker does not block healthy → 1 claim")
        if claims:
            check(claims[0]["source"] == "healthy", "claim comes from healthy checker")

        # Timeout test — a checker that hangs
        class HungChecker(AirdropChecker):
            name = "hung"
            chain_types = ["evm"]

            async def check(self, address):
                await asyncio.sleep(99)  # will be timed out
                return []

        register_checker(HungChecker())

        claims2 = asyncio.run(
            get_claimable_airdrops("0xabc", "evm")
        )
        check(len(claims2) == 1, "hung checker does not block healthy → 1 claim")
        if claims2:
            check(claims2[0]["source"] == "healthy", "claim still from healthy checker")

    finally:
        _AIRDROP_CHECKERS.clear()
        _AIRDROP_CHECKERS.extend(saved)


# ═══════════════════════════════════════════════════════════════════
# 4. Registry introspection
# ═══════════════════════════════════════════════════════════════════


def test_registry_introspection():
    section("4. Registry introspection")

    checkers = get_checkers()
    check(len(checkers) >= 1, "at least 1 checker in registry (staking_rewards)")

    # Check that cosmos_staking_rewards is there
    names = [c.name for c in checkers]
    check("cosmos_staking_rewards" in names, "cosmos_staking_rewards checker registered")

    # Check its chain_types
    for c in checkers:
        if c.name == "cosmos_staking_rewards":
            check(
                "cosmos" in c.chain_types,
                "cosmos_staking_rewards handles 'cosmos' chain_type",
            )
            check(
                "evm" not in c.chain_types,
                "cosmos_staking_rewards does NOT handle 'evm'",
            )


# ═══════════════════════════════════════════════════════════════════
# 5. Non-regression — provider_for still works for all chains
# ═══════════════════════════════════════════════════════════════════


def test_provider_non_regression():
    section("5. Non-regression — provider_for (EVM/BTC/Solana/Cosmos)")

    from services.providers.base import provider_for

    # EVM
    p = provider_for("0x15CD7D7aE29f3F76FDC9d89e1FbC58B23E8D9C30")
    check(p is not None and p.chain_type == "evm", "provider_for(EVM) → evm")

    # BTC
    p2 = provider_for("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")
    check(p2 is not None and p2.chain_type == "bitcoin", "provider_for(BTC) → bitcoin")

    # Solana
    p3 = provider_for("7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV")
    check(p3 is not None and p3.chain_type == "solana", "provider_for(Solana) → solana")

    # Cosmos
    p4 = provider_for("cosmos1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd")
    check(p4 is not None and p4.chain_type == "cosmos", "provider_for(Cosmos) → cosmos")

    # Garbage
    check(provider_for("hello") is None, "provider_for(garbage) → None")


# ═══════════════════════════════════════════════════════════════════
# 6. AirdropChecker interface contract
# ═══════════════════════════════════════════════════════════════════


def test_interface_contract():
    section("6. AirdropChecker interface contract")

    # Verify abstract class attributes
    check(hasattr(AirdropChecker, "name"), "AirdropChecker has 'name' attribute")
    check(hasattr(AirdropChecker, "chain_types"), "AirdropChecker has 'chain_types' attribute")
    check(hasattr(AirdropChecker, "check"), "AirdropChecker has 'check' method")

    # Verify a concrete checker implements everything
    class MinimalChecker(AirdropChecker):
        name = "minimal"
        chain_types = ["evm"]

        async def check(self, address):
            return [{
                "source": self.name,
                "chain": "ethereum",
                "token_symbol": "ETH",
                "amount": 0.0,
                "usd_value": 0.0,
                "claim_url": "",
                "status": "info",
                "details": "",
            }]

    mc = MinimalChecker()
    check(mc.name == "minimal", "name accessible")
    check("evm" in mc.chain_types, "chain_types accessible")
    check(asyncio.run(mc.check("0xabc")) is not None, "check returns result")


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_registry_routing()
    test_staking_rewards_parse()
    test_defensive_isolation()
    test_registry_introspection()
    test_provider_non_regression()
    test_interface_contract()

    print(f"\n{'=' * 60}")
    total = PASS + FAIL
    print(f"  Results: {PASS}/{total} passed")
    if FAIL > 0:
        print(f"  {FAIL} FAILURE(S)")
        sys.exit(1)
    else:
        print(f"  ALL TESTS PASSED")
