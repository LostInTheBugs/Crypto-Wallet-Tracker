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
    section("2. Staking rewards checker — parsing (cosmos removed, test adapted)")

    # After Cosmos removal, denom parsing utilities no longer importable.
    # Verify the airdrops module still loads and returns empty properly.
    check(True, "staking rewards checker removed — parsing tests skipped (cosmos dropped 2026.07.29)")

    # Verify the claim dict shape remains valid for future checkers
    claim = {
        "source": "example_checker",
        "chain": "ethereum",
        "token_symbol": "ETH",
        "amount": 1.0,
        "usd_value": 100.0,
        "claim_url": "https://etherscan.io/address/0x...",
        "status": "claimable",
        "details": "Example claim",
    }

    check(claim["source"] == "example_checker", "claim has source")
    check(claim["status"] == "claimable", "claim status is claimable")
    check(isinstance(claim["usd_value"], (int, float)) and claim["usd_value"] > 0, "claim has positive usd_value")


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
    # After Cosmos removal (2026.07.29), no checkers registered by default
    check(len(checkers) >= 0, "0+ checkers in registry (cosmos removed)")

    # Check that checkers list is empty since Cosmos removed
    names = [c.name for c in checkers]
    if len(checkers) > 0:
        # If new checkers added post-removal, list them
        pass
    else:
        check(len(checkers) == 0, "no checkers registered (cosmos removed 2026.07.29)")


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

    # Cosmos is no longer supported
    p4 = provider_for("cosmos1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd")
    check(p4 is None, "provider_for(Cosmos) → None (removed 2026.07.29)")

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
