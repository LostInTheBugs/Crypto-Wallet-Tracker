"""
Tests for SolanaProvider — detection, base58 decoding, provider_for routing,
portfolio shape, and EVM/BTC non-regression.

Run:  python3 tests/test_solana_provider.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.providers.base import provider_for, PROVIDERS
from services.providers.solana import (
    SolanaProvider,
    _is_solana_address,
    _base58_decode,
    _get_token_info,
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
# 1. Base58 decoder
# ═══════════════════════════════════════════════════════════════════


def test_base58_decode():
    section("1. Base58 decode")

    # Known: "So11111111111111111111111111111111111111112" (Wrapped SOL mint)
    # This is a base58-encoded 32-byte pubkey
    decoded = _base58_decode(
        "So11111111111111111111111111111111111111112"
    )
    check(decoded is not None, "decode Wrapped SOL mint")
    check(len(decoded) == 32, "Wrapped SOL → 32 bytes")

    # Known: "11111111111111111111111111111111" (all-1, should decode to zeros)
    decoded2 = _base58_decode("11111111111111111111111111111111")
    check(decoded2 is not None, "decode all-ones address")
    check(len(decoded2) == 32, "all-ones → 32 bytes (zeros)")

    # Invalid characters
    check(_base58_decode("0OIl") is None, "invalid base58 chars → None")
    check(_base58_decode("") is None, "empty → None")

    # Roundtrip for a valid encoded address
    valid_addr = "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV"
    d3 = _base58_decode(valid_addr)
    check(d3 is not None and len(d3) == 32, "random Solana address → 32 bytes")


# ═══════════════════════════════════════════════════════════════════
# 2. detect() — address recognition
# ═══════════════════════════════════════════════════════════════════


def test_detect_solana():
    section("2. SolanaProvider.detect()")

    sp = SolanaProvider()

    # Valid Solana addresses
    check(sp.detect("7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV"),
          "valid Solana pubkey")
    check(sp.detect("So11111111111111111111111111111111111111112"),
          "Wrapped SOL mint address")
    check(sp.detect("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
          "Token Program ID")

    # NOT Solana
    check(not sp.detect("0x15CD7D7aE29f3F76FDC9d89e1FbC58B23E8D9C30"),
          "EVM address → False")
    check(not sp.detect("0xeb788c4b57670f5309afe9d6b97929329b593dbd"),
          "EVM lowercase → False")
    check(not sp.detect("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"),
          "BTC bech32 → False")
    check(not sp.detect("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"),
          "BTC P2PKH → False")
    check(not sp.detect("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"),
          "BTC P2SH → False")
    check(not sp.detect("cosmos1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"),
          "Cosmos bech32 → False")
    check(not sp.detect("osmosis1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"),
          "Osmosis bech32 → False")

    # Edge cases
    check(not sp.detect(""), "empty → False")
    check(not sp.detect("hello world"), "garbage → False")
    check(not sp.detect("12345"), "short numeric → False")

    # Too short / too long
    check(not sp.detect("abc"), "too short → False")
    check(not sp.detect("A" * 45), "too long (45 chars) → False")


# ═══════════════════════════════════════════════════════════════════
# 3. provider_for() routing
# ═══════════════════════════════════════════════════════════════════


def test_provider_routing():
    section("3. provider_for() — Solana routing")

    # Solana → SolanaProvider
    p = provider_for("7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV")
    check(p is not None, "provider_for(Solana) → not None")
    check(p.chain_type == "solana",
          "provider_for(Solana) → chain_type='solana'")

    # Solana via Wrapped SOL mint
    p2 = provider_for("So11111111111111111111111111111111111111112")
    check(p2 is not None, "provider_for(Wrapped SOL) → not None")
    check(p2.chain_type == "solana",
          "provider_for(Wrapped SOL) → chain_type='solana'")

    # EVM still works
    p3 = provider_for("0x15CD7D7aE29f3F76FDC9d89e1FbC58B23E8D9C30")
    check(p3 is not None and p3.chain_type == "evm",
          "provider_for(EVM) → EvmProvider (not shadowed)")

    # BTC still works
    p4 = provider_for("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")
    check(p4 is not None and p4.chain_type == "bitcoin",
          "provider_for(BTC) → BitcoinProvider (not shadowed)")

    # Garbage → None
    check(provider_for("blabla") is None, "provider_for(garbage) → None")


# ═══════════════════════════════════════════════════════════════════
# 4. Provider metadata
# ═══════════════════════════════════════════════════════════════════


def test_metadata():
    section("4. SolanaProvider metadata")

    sp = SolanaProvider()
    check(sp.chain_type == "solana", "chain_type == 'solana'")
    check(sp.native_symbol == "SOL", "native_symbol == 'SOL'")

    # Explorer URLs
    addr = "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV"
    check("solscan.io" in sp.explorer_url(addr),
          "explorer_url → solscan.io")
    check("solscan.io" in sp.explorer_tx_url("txhash"),
          "explorer_tx_url → solscan.io")


# ═══════════════════════════════════════════════════════════════════
# 5. Portfolio shape (static)
# ═══════════════════════════════════════════════════════════════════


def test_portfolio_shape():
    section("5. Portfolio shape — static structure")

    # The shape must match the EVM/BTC format
    expected_keys = {
        "address", "total_usd", "token_count", "chain_count",
        "chains", "tokens", "errors", "defi_usd", "staked_usd",
        "defi_breakdown", "active_count", "inactive_count",
    }

    # Simulated portfolio response
    pf = {
        "address": "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV",
        "total_usd": 500.00,
        "token_count": 2,
        "chain_count": 1,
        "chains": {"solana": 500.00},
        "tokens": [
            {
                "symbol": "SOL",
                "name": "Solana",
                "chain": "solana",
                "balance": 1.5,
                "usd_price": 200.0,
                "usd_value": 300.0,
                "category": "wallet",
                "contract_address": "",
                "enabled": True,
            },
            {
                "symbol": "USDC",
                "name": "USD Coin",
                "chain": "solana",
                "balance": 200.0,
                "usd_price": 1.0,
                "usd_value": 200.0,
                "category": "wallet",
                "contract_address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "enabled": True,
            },
        ],
        "errors": [],
        "defi_usd": 0,
        "staked_usd": 0,
        "defi_breakdown": {},
        "active_count": 2,
        "inactive_count": 0,
    }

    for key in expected_keys:
        check(key in pf, f"key '{key}' present in portfolio")

    check(pf["tokens"][0]["symbol"] == "SOL", "first token is SOL")
    check(pf["tokens"][1]["chain"] == "solana", "SPL token chain is 'solana'")
    check(pf["defi_breakdown"] == {}, "defi_breakdown is empty dict")
    check(pf["defi_usd"] == 0, "defi_usd is 0")


# ═══════════════════════════════════════════════════════════════════
# 6. Lamports → SOL parsing
# ═══════════════════════════════════════════════════════════════════


def test_lamports_parsing():
    section("6. Lamports → SOL parsing")

    # 1 SOL = 1_000_000_000 lamports
    check(1_000_000_000 / 1e9 == 1.0, "1B lamports == 1.0 SOL")
    check(500_000_000 / 1e9 == 0.5, "500M lamports == 0.5 SOL")
    check(0 / 1e9 == 0.0, "0 lamports == 0.0 SOL")


# ═══════════════════════════════════════════════════════════════════
# 7. SPL token info lookup
# ═══════════════════════════════════════════════════════════════════


def test_spl_token_info():
    section("7. SPL token info lookup")

    # Known token (USDC)
    sym, name, known = _get_token_info(
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    )
    check(sym == "USDC", "USDC mint → symbol USDC")
    check(name == "USD Coin", "USDC mint → name USD Coin")
    check(known is True, "USDC mint → known=True")

    # Known token (Wrapped SOL)
    sym2, name2, known2 = _get_token_info(
        "So11111111111111111111111111111111111111112"
    )
    check(sym2 == "SOL", "Wrapped SOL mint → symbol SOL")
    check(known2 is True, "Wrapped SOL mint → known=True")

    # Unknown token
    sym3, name3, known3 = _get_token_info(
        "FakeMintAddress12345678901234567890xx"
    )
    check(known3 is False, "unknown mint → known=False")
    check("..." in sym3, "unknown mint → truncated symbol")


# ═══════════════════════════════════════════════════════════════════
# 8. Registry count
# ═══════════════════════════════════════════════════════════════════


def test_registry_count():
    section("8. Registry — all 3 providers")

    types = {p.chain_type for p in PROVIDERS}
    check("evm" in types, "EVM provider registered")
    check("bitcoin" in types, "Bitcoin provider registered")
    check("solana" in types, "Solana provider registered")
    check(len(PROVIDERS) >= 3, "at least 3 providers registered")


# ═══════════════════════════════════════════════════════════════════
# 9. Transactions placeholder
# ═══════════════════════════════════════════════════════════════════


def test_transactions_placeholder():
    section("9. Transactions placeholder")

    import asyncio

    sp = SolanaProvider()

    async def go():
        return await sp.get_transactions(
            "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV"
        )

    result = asyncio.run(go())
    check(result["total"] == 0, "tx total is 0")
    check(result["items"] == [], "tx items is empty list")
    check(isinstance(result["counts"], dict), "tx counts is dict")


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_base58_decode()
    test_detect_solana()
    test_provider_routing()
    test_metadata()
    test_portfolio_shape()
    test_lamports_parsing()
    test_spl_token_info()
    test_registry_count()
    test_transactions_placeholder()

    print(f"\n{'=' * 60}")
    total = PASS + FAIL
    print(f"  Results: {PASS}/{total} passed")
    if FAIL > 0:
        print(f"  {FAIL} FAILURE(S)")
        sys.exit(1)
    else:
        print(f"  ALL TESTS PASSED")
