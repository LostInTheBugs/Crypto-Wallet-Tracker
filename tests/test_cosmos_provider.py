"""
Tests for CosmosProvider — bech32 detection, provider_for routing,
portfolio shape, LCD JSON parsing, and EVM/BTC/Solana non-regression.

Run:  python3 tests/test_cosmos_provider.py
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.providers.base import provider_for, PROVIDERS
from services.providers.cosmos import (
    CosmosProvider,
    _identify_hrp,
    _hrp_info,
    _denom_to_symbol,
    _COSMOS_RE,
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
# 1. bech32 detection — CosmosProvider.detect()
# ═══════════════════════════════════════════════════════════════════


def test_detect_cosmos():
    section("1. CosmosProvider.detect()")

    cp = CosmosProvider()

    # Valid Cosmos addresses (public addresses from mainnet)
    check(
        cp.detect("cosmos1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"),
        "cosmos1... → True",
    )
    check(
        cp.detect(
            "cosmos1m3h30w0quvmj55jdguy8y7guyx5j5xkv9rxgtz"
        ),
        "cosmos1... (another) → True",
    )
    check(
        cp.detect("osmo1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"),
        "osmo1... → True",
    )

    # Other recognized HRPs
    check(
        cp.detect(
            "celestia1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"
        ),
        "celestia1... → True",
    )
    check(
        cp.detect("juno1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"),
        "juno1... → True",
    )
    check(
        cp.detect(
            "stars1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"
        ),
        "stars1... → True",
    )
    check(
        cp.detect(
            "akash1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"
        ),
        "akash1... → True",
    )
    check(
        cp.detect("inj1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"),
        "inj1... → True",
    )
    check(
        cp.detect(
            "kujira1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"
        ),
        "kujira1... → True",
    )
    check(
        cp.detect(
            "stride1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"
        ),
        "stride1... → True",
    )

    # NOT Cosmos — EVM
    check(
        not cp.detect(
            "0x15CD7D7aE29f3F76FDC9d89e1FbC58B23E8D9C30"
        ),
        "EVM address → False",
    )
    check(
        not cp.detect(
            "0xeb788c4b57670f5309afe9d6b97929329b593dbd"
        ),
        "EVM lowercase → False",
    )

    # NOT Cosmos — BTC
    check(
        not cp.detect(
            "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
        ),
        "BTC bech32 bc1... → False",
    )
    check(
        not cp.detect("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"),
        "BTC P2PKH → False",
    )
    check(
        not cp.detect("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"),
        "BTC P2SH → False",
    )

    # NOT Cosmos — Solana
    check(
        not cp.detect(
            "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV"
        ),
        "Solana base58 → False",
    )

    # Edge cases
    check(not cp.detect(""), "empty → False")
    check(not cp.detect("hello world"), "garbage → False")
    check(
        not cp.detect("cosmos1short"),
        "cosmos1 too short → False",
    )
    check(
        not cp.detect("unknown1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"),
        "unknown HRP → False",
    )


# ═══════════════════════════════════════════════════════════════════
# 2. HRP identification
# ═══════════════════════════════════════════════════════════════════


def test_identify_hrp():
    section("2. _identify_hrp()")

    check(
        _identify_hrp(
            "cosmos1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"
        )
        == "cosmos",
        "cosmos1... → 'cosmos'",
    )
    check(
        _identify_hrp("osmo1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd")
        == "osmo",
        "osmo1... → 'osmo'",
    )
    check(
        _identify_hrp(
            "celestia1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"
        )
        == "celestia",
        "celestia1... → 'celestia'",
    )
    check(
        _identify_hrp("0x15CD7D7aE29f3F76FDC9d89e1FbC58B23E8D9C30")
        is None,
        "EVM → None",
    )
    check(
        _identify_hrp(
            "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
        )
        is None,
        "BTC bech32 → None",
    )
    check(
        _identify_hrp("") is None,
        "empty → None",
    )


# ═══════════════════════════════════════════════════════════════════
# 3. HRP info lookup
# ═══════════════════════════════════════════════════════════════════


def test_hrp_info():
    section("3. _hrp_info()")

    info = _hrp_info("cosmos")
    check(info is not None, "cosmos → not None")
    check(info["symbol"] == "ATOM", "cosmos symbol = ATOM")
    check(info["coingecko_id"] == "cosmos", "cosmos CG ID = cosmos")
    check("cosmos-api.polkachu.com" in info["lcd"], "cosmos LCD is polkachu")

    info2 = _hrp_info("osmo")
    check(info2 is not None, "osmo → not None")
    check(info2["symbol"] == "OSMO", "osmo symbol = OSMO")
    check(info2["coingecko_id"] == "osmosis", "osmo CG ID = osmosis")

    check(_hrp_info("bitcoin") is None, "bitcoin HRP → None")
    check(_hrp_info("") is None, "empty HRP → None")


# ═══════════════════════════════════════════════════════════════════
# 4. Denom parsing
# ═══════════════════════════════════════════════════════════════════


def test_denom_to_symbol():
    section("4. _denom_to_symbol()")

    sym, exp = _denom_to_symbol("uatom")
    check(sym == "ATOM" and exp == 6, "uatom → ATOM, exponent 6")

    sym, exp = _denom_to_symbol("uosmo")
    check(sym == "OSMO" and exp == 6, "uosmo → OSMO, exponent 6")

    sym, exp = _denom_to_symbol("uinj")
    check(sym == "INJ" and exp == 18, "uinj → INJ, exponent 18")

    sym, exp = _denom_to_symbol("uunknown")
    check(sym == "UNKNOWN" and exp == 6, "uunknown → UNKNOWN, exponent 6")

    sym, exp = _denom_to_symbol("customdenom")
    check(
        sym == "CUSTOMDENOM" and exp == 0,
        "customdenom → CUSTOMDENOM, exponent 0",
    )


# ═══════════════════════════════════════════════════════════════════
# 5. provider_for() routing
# ═══════════════════════════════════════════════════════════════════


def test_provider_routing():
    section("5. provider_for() — Cosmos routing")

    # Cosmos → CosmosProvider
    p = provider_for(
        "cosmos1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"
    )
    check(p is not None, "provider_for(cosmos1...) → not None")
    check(
        p.chain_type == "cosmos",
        "provider_for(cosmos1...) → chain_type='cosmos'",
    )

    # Osmosis → CosmosProvider
    p2 = provider_for("osmo1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd")
    check(p2 is not None, "provider_for(osmo1...) → not None")
    check(
        p2.chain_type == "cosmos",
        "provider_for(osmo1...) → chain_type='cosmos'",
    )

    # EVM still works
    p3 = provider_for(
        "0x15CD7D7aE29f3F76FDC9d89e1FbC58B23E8D9C30"
    )
    check(
        p3 is not None and p3.chain_type == "evm",
        "provider_for(EVM) → EvmProvider (not shadowed)",
    )

    # BTC still works
    p4 = provider_for(
        "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
    )
    check(
        p4 is not None and p4.chain_type == "bitcoin",
        "provider_for(BTC) → BitcoinProvider (not shadowed)",
    )

    # Solana still works
    p5 = provider_for(
        "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV"
    )
    check(
        p5 is not None and p5.chain_type == "solana",
        "provider_for(Solana) → SolanaProvider (not shadowed)",
    )

    # Garbage → None
    check(
        provider_for("blabla") is None,
        "provider_for(garbage) → None",
    )


# ═══════════════════════════════════════════════════════════════════
# 6. Provider metadata
# ═══════════════════════════════════════════════════════════════════


def test_metadata():
    section("6. CosmosProvider metadata")

    cp = CosmosProvider()
    check(cp.chain_type == "cosmos", "chain_type == 'cosmos'")
    check(cp.native_symbol == "ATOM", "native_symbol == 'ATOM'")

    # Explorer URLs
    addr = "cosmos1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"
    eu = cp.explorer_url(addr)
    check(eu is not None, "explorer_url(cosmos1...) → not None")
    check(
        "mintscan.io/cosmos/address/" in eu,
        "explorer_url → mintscan.io/cosmos/address/",
    )

    # Tx URL
    txu = cp.explorer_tx_url("ABC123")
    check(txu is not None, "explorer_tx_url → not None")
    check("mintscan.io/cosmos/tx/" in txu, "explorer_tx_url → mintscan")

    # Osmosis explorer
    addr2 = "osmo1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"
    eu2 = cp.explorer_url(addr2)
    check(
        eu2 is not None
        and "mintscan.io/osmosis/address/" in eu2,
        "explorer_url(osmo) → mintscan.io/osmosis/address/",
    )

    # Unknown address → None
    check(
        cp.explorer_url("badaddress") is None,
        "explorer_url(invalid) → None",
    )


# ═══════════════════════════════════════════════════════════════════
# 7. Portfolio shape (static)
# ═══════════════════════════════════════════════════════════════════


def test_portfolio_shape():
    section("7. CosmosProvider portfolio shape (static)")

    cp = CosmosProvider()
    # We test the shape returned for an unsupported HRP (no LCD call)
    result = asyncio_get(cp.get_portfolio("coin1abcdef"))
    check(
        result.get("supported") is False,
        "unknown address → supported=False",
    )

    # Test the key fields ANY Cosmos portfolio should have
    check("total_usd" in result or "supported" in result,
          "response has total_usd or supported field")


# ═══════════════════════════════════════════════════════════════════
# 8. LCD JSON parsing (static examples)
# ═══════════════════════════════════════════════════════════════════


def test_lcd_balance_parsing():
    section("8. LCD balance parsing (static JSON)")

    # Simulate a /cosmos/bank/v1beta1/balances response
    balances_json = {
        "balances": [
            {"denom": "uatom", "amount": "12345678"},
            {"denom": "uosmo", "amount": "5000000"},
        ],
        "pagination": {"next_key": None, "total": "2"},
    }

    balances = balances_json.get("balances", [])
    check(len(balances) == 2, "2 balance entries")
    check(balances[0]["denom"] == "uatom", "first denom = uatom")
    check(balances[0]["amount"] == "12345678", "first amount = 12345678")

    # Convert to token units
    sym, exp = _denom_to_symbol("uatom")
    amount = float("12345678") / (10**exp)
    check(abs(amount - 12.345678) < 0.000001, "uatom 12345678 → 12.345678 ATOM")


def test_lcd_delegations_parsing():
    section("9. LCD delegations parsing (static JSON)")

    delegations_json = {
        "delegation_responses": [
            {
                "delegation": {
                    "delegator_address": "cosmos1...",
                    "validator_address": "cosmosvaloper1...",
                    "shares": "5000000.000000000000000000",
                },
                "balance": {"denom": "uatom", "amount": "5000000"},
            },
            {
                "delegation": {
                    "delegator_address": "cosmos1...",
                    "validator_address": "cosmosvaloper2...",
                    "shares": "3000000.000000000000000000",
                },
                "balance": {"denom": "uatom", "amount": "3000000"},
            },
        ]
    }

    del_list = delegations_json.get("delegation_responses", [])
    check(len(del_list) == 2, "2 delegation entries")
    total_delegated = sum(
        float(d.get("balance", {}).get("amount", "0")) for d in del_list
    )
    check(total_delegated == 8000000, "total delegated = 8000000 uatom")
    sym, exp = _denom_to_symbol("uatom")
    delegated = total_delegated / (10**exp)
    check(abs(delegated - 8.0) < 0.000001, "delegated 8 ATOM")


def test_lcd_rewards_parsing():
    section("10. LCD rewards parsing (static JSON)")

    rewards_json = {
        "rewards": [
            {
                "validator_address": "cosmosvaloper1...",
                "reward": [
                    {"denom": "uatom", "amount": "123456.000000000000000000"}
                ],
            }
        ],
        "total": [{"denom": "uatom", "amount": "123456.000000000000000000"}],
    }

    total_list = rewards_json.get("total", [])
    check(len(total_list) == 1, "1 reward denom in total")
    check(total_list[0]["denom"] == "uatom", "reward denom = uatom")

    # Truncate decimal part
    amt_str = total_list[0]["amount"].split(".")[0]
    check(amt_str == "123456", "truncated amount = 123456")

    sym, exp = _denom_to_symbol("uatom")
    reward = float(amt_str) / (10**exp)
    check(abs(reward - 0.123456) < 0.000001, "reward 0.123456 ATOM")


# ═══════════════════════════════════════════════════════════════════
# 9. Registry
# ═══════════════════════════════════════════════════════════════════


def test_registry():
    section("11. Registry")

    cosmos_providers = [
        p for p in PROVIDERS if p.chain_type == "cosmos"
    ]
    check(len(cosmos_providers) >= 1, "CosmosProvider in PROVIDERS")

    # All 4 chain types in registry
    types = {p.chain_type for p in PROVIDERS}
    check("evm" in types, "evm in registry")
    check("bitcoin" in types, "bitcoin in registry")
    check("solana" in types, "solana in registry")
    check("cosmos" in types, "cosmos in registry")


# ═══════════════════════════════════════════════════════════════════
# 10. Transactions placeholder
# ═══════════════════════════════════════════════════════════════════


def test_transactions_placeholder():
    section("12. Transactions placeholder")

    cp = CosmosProvider()
    result = asyncio_get(
        cp.get_transactions(
            "cosmos1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd"
        )
    )
    check(result["total"] == 0, "total = 0")
    check(result["items"] == [], "items = []")
    check("counts" in result, "has counts")


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def asyncio_get(coro):
    """Run a coroutine synchronously."""
    import asyncio

    return asyncio.get_event_loop().run_until_complete(coro)


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_detect_cosmos()
    test_identify_hrp()
    test_hrp_info()
    test_denom_to_symbol()
    test_provider_routing()
    test_metadata()
    test_portfolio_shape()
    test_lcd_balance_parsing()
    test_lcd_delegations_parsing()
    test_lcd_rewards_parsing()
    test_registry()
    test_transactions_placeholder()

    total = PASS + FAIL
    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {PASS}/{total} passed")
    if FAIL > 0:
        print(f"  {FAIL} FAILURES")
        sys.exit(1)
    else:
        print(f"  All good!")
        sys.exit(0)
