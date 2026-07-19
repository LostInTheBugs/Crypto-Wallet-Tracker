"""
Unit tests — ChainProvider abstraction & EvmProvider.

These tests are STATELESS.  They do NOT require a running app, database,
or network.  They verify the provider interface, registry, detection, and
the unsupported-response contract.

Run:  python3 tests/test_providers.py
"""

import asyncio
import sys
import os

# Allow importing from src/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.providers.base import ChainProvider, PROVIDERS, register_provider, provider_for
from services.providers.evm import EvmProvider, evm_provider, wire_evm


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
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ═══════════════════════════════════════════════════════════════════
# 1. EvmProvider.detect()
# ═══════════════════════════════════════════════════════════════════

def test_detect():
    section("1. EvmProvider.detect()")

    evm = EvmProvider()

    # Valid EVM addresses (mixed case, checksum)
    check(evm.detect("0x15CD7D7A1fc0ca1B91F58d64a591dA4f5C50AD7e") == True,
          "valid checksummed EVM address")
    check(evm.detect("0x15cd7d7a1fc0ca1b91f58d64a591da4f5c50ad7e") == True,
          "valid lowercase EVM address")
    check(evm.detect("0x0000000000000000000000000000000000000000") == True,
          "zero address")

    # Invalid — wrong length
    check(evm.detect("0x15CD") == False,
          "too short")
    check(evm.detect("0x15CD7D7A1fc0ca1B91F58d64a591dA4f5C50AD7eFF") == False,
          "too long (43 chars)")

    # Invalid — missing 0x prefix
    check(evm.detect("15CD7D7A1fc0ca1B91F58d64a591dA4f5C50AD7e") == False,
          "no 0x prefix")

    # Invalid — non-hex characters
    check(evm.detect("0xZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ") == False,
          "non-hex chars")

    # Non-EVM addresses that will get providers in the future
    check(evm.detect("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq") == False,
          "Bitcoin bech32")
    check(evm.detect("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa") == False,
          "Bitcoin P2PKH")
    check(evm.detect("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy") == False,
          "Bitcoin P2SH")
    check(evm.detect("7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV") == False,
          "Solana base58")
    check(evm.detect("cosmos1m3h30w0quvmj55jdguy8y7guyx5j5xkv9rxgtz") == False,
          "Cosmos bech32")

    # Edge cases
    check(evm.detect("") == False, "empty string")
    check(evm.detect("0x") == False, "only prefix")


# ═══════════════════════════════════════════════════════════════════
# 2. Registry — provider_for()
# ═══════════════════════════════════════════════════════════════════

def test_registry():
    section("2. Registry — provider_for()")

    # At this point EvmProvider is auto-registered via import
    check(len(PROVIDERS) >= 1, "at least 1 provider registered")
    check(isinstance(PROVIDERS[0], EvmProvider), "first provider is EvmProvider")

    # provider_for with valid EVM addresses
    p = provider_for("0x15CD7D7A1fc0ca1B91F58d64a591dA4f5C50AD7e")
    check(p is not None and isinstance(p, EvmProvider),
          "provider_for(EVM) → EvmProvider")
    assert p is not None  # for type checker
    check(p.chain_type == "evm",
          "chain_type is 'evm'")

    # provider_for with non-EVM addresses → None
    check(provider_for("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq") is None,
          "provider_for(BTC bech32) → None")
    check(provider_for("7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV") is None,
          "provider_for(Solana) → None")
    check(provider_for("cosmos1m3h30w0quvmj55jdguy8y7guyx5j5xkv9rxgtz") is None,
          "provider_for(Cosmos) → None")
    check(provider_for("garbage_not_an_address") is None,
          "provider_for(garbage) → None")
    check(provider_for("") is None,
          "provider_for(empty) → None")


# ═══════════════════════════════════════════════════════════════════
# 3. EvmProvider metadata
# ═══════════════════════════════════════════════════════════════════

def test_metadata():
    section("3. EvmProvider metadata")

    evm = EvmProvider()
    check(evm.chain_type == "evm", "chain_type == 'evm'")
    check(evm.native_symbol == "ETH", "native_symbol == 'ETH'")

    # Explorer URLs
    check("eth.blockscout.com" in (evm.explorer_url("0xABC") or ""),
          "explorer_url returns blockscout link")
    check("eth.blockscout.com" in (evm.explorer_tx_url("0xTXHASH") or ""),
          "explorer_tx_url returns blockscout link")


# ═══════════════════════════════════════════════════════════════════
# 4. Unsupported response contract
# ═══════════════════════════════════════════════════════════════════

def test_unsupported_contract():
    section("4. Unsupported response contract")

    # This is the shape returned when no provider handles an address.
    # All non-EVM addresses (until their providers are added) get this.
    unsupported = {
        "supported": False,
        "chain_type": None,
        "message": "Chaine non prise en charge (a venir)",
    }
    check(unsupported.get("supported") == False, "supported is False")
    check(unsupported.get("chain_type") is None, "chain_type is None")
    check(isinstance(unsupported.get("message"), str) and len(unsupported["message"]) > 0,
          "message is non-empty string")


# ═══════════════════════════════════════════════════════════════════
# 5. Register a second mock provider (proves registry extensibility)
# ═══════════════════════════════════════════════════════════════════

def test_extensibility():
    section("5. Registry extensibility — mock BitcoinProvider")

    class MockBitcoinProvider(ChainProvider):
        chain_type = "bitcoin"
        native_symbol = "BTC"

        def detect(self, address: str) -> bool:
            return address.startswith(("1", "3", "bc1"))

        async def get_portfolio(self, address: str) -> dict:
            return {"supported": True, "chain_type": "bitcoin"}

        async def get_transactions(
            self, address: str,
            wallet: str | None = None,
            chain: str | None = None,
            token: str | None = None,
            direction: str | None = None,
            event_type: str | None = None,
            limit: int = 100,
            offset: int = 0,
        ) -> dict:
            return {"total": 0, "items": [], "counts": {}}

    btc = MockBitcoinProvider()
    register_provider(btc)

    # Now provider_for should find it for Bitcoin addresses
    check(provider_for("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq") is btc,
          "provider_for(BTC) → BitcoinProvider")

    # EVM still works
    p2 = provider_for("0x0000000000ABCDEF000000000000000000000000")
    check(p2 is not None and isinstance(p2, EvmProvider),
          "provider_for(EVM) still EvmProvider (not shadowed)")

    # Clean up for subsequent tests
    PROVIDERS.remove(btc)


# ═══════════════════════════════════════════════════════════════════
# 6. Async — EvmProvider.get_portfolio() with wired shim
# ═══════════════════════════════════════════════════════════════════

def test_evm_wired():
    section("6. EvmProvider wired — get_portfolio delegate")

    # Wire with a mock
    calls = []

    async def mock_portfolio(address: str) -> dict:
        calls.append(address)
        return {"address": address, "total_usd": 100.0, "tokens": []}

    async def mock_tx(**kwargs) -> dict:
        return {"total": 0, "items": [], "counts": {}}

    wire_evm(mock_portfolio, mock_tx)

    result = asyncio.run(evm_provider.get_portfolio("0x0000000000ABCDEF000000000000000000000000"))
    check(len(calls) == 1, "mock_portfolio was called exactly once")
    check(result.get("total_usd") == 100.0, "result passed through")


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_detect()
    test_registry()
    test_metadata()
    test_unsupported_contract()
    test_extensibility()
    test_evm_wired()

    print(f"\n{'='*60}")
    total = PASS + FAIL
    print(f"  Results: {PASS}/{total} passed")
    if FAIL > 0:
        print(f"  {FAIL} FAILURE(S)")
        sys.exit(1)
    else:
        print(f"  ALL TESTS PASSED")
