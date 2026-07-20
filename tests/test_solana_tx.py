"""
Tests for Solana transaction parsing (_parse_solana_tx).

Tests send/receive/swap detection from jsonParsed getTransaction output,
defensive parsing (one failed tx doesn't block others), and non-regression
of existing providers.

Run:  python3 tests/test_solana_tx.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.providers.solana import (
    _parse_solana_tx,
    SolanaProvider,
)
from services.providers.base import provider_for, PROVIDERS

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
# Synthetic JSON fixtures (jsonParsed getTransaction)
# ═══════════════════════════════════════════════════════════════════

ADDR = "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV"
ADDR2 = "DRpbCBMxVnDK7maPMoPVJHh8QXkgzpo5NcN7svK44gQm"
SOL_PRICE = 200.0
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"


def _mk_tx(block_time, pre_balances, post_balances,
           pre_tb=None, post_tb=None, fee=5000,
           account_keys=None, signature="sig123"):
    """Build a jsonParsed getTransaction response dict."""
    if account_keys is None:
        account_keys = [{"pubkey": ADDR}, {"pubkey": ADDR2}]
    if pre_tb is None:
        pre_tb = []
    if post_tb is None:
        post_tb = []
    return {
        "blockTime": block_time,
        "meta": {
            "fee": fee,
            "preBalances": pre_balances,
            "postBalances": post_balances,
            "preTokenBalances": pre_tb,
            "postTokenBalances": post_tb,
        },
        "transaction": {
            "signatures": [signature],
            "message": {
                "accountKeys": account_keys,
            },
        },
    }


def _tb_entry(account_index, mint, ui_amount, decimals=6):
    """Build a token balance entry."""
    amount_str = str(int(ui_amount * 10**decimals))
    return {
        "accountIndex": account_index,
        "mint": mint,
        "uiTokenAmount": {
            "uiAmount": ui_amount,
            "decimals": decimals,
            "amount": amount_str,
            "uiAmountString": str(ui_amount),
        },
    }


# ═══════════════════════════════════════════════════════════════════
# 1. Parse incoming SOL transfer → "receive"
# ═══════════════════════════════════════════════════════════════════


def test_sol_receive():
    section("1. SOL receive — incoming native SOL")
    # Pre: 1 SOL, Post: 2 SOL (address at index 0, fee-payer)
    tx = _mk_tx(1720000000,
                pre_balances=[1_000_000_000, 5_000_000_000],
                post_balances=[2_000_000_000, 4_000_000_000],
                account_keys=[{"pubkey": ADDR}, {"pubkey": ADDR2}],
                signature="rx_sol_in")

    ev = _parse_solana_tx(ADDR, tx, SOL_PRICE, {})
    check(ev is not None, "receive tx → event not None")
    check(ev["type"] == "receive", "type == 'receive'")
    check(ev["direction"] == "in", "direction == 'in'")
    check(ev["tx_hash"] == "rx_sol_in", "tx_hash preserved")
    check(ev["block_time"].startswith("2024-07-03"), "block_time ISO format")
    check(ev["chain"] == "solana", "chain == 'solana'")
    check(ev["token_symbol"] == "SOL", "token_symbol == 'SOL'")
    check(ev["recv_symbol"] == "SOL", "recv_symbol == 'SOL'")
    check(ev["recv_amount"] > 0, "recv_amount > 0")
    # Received ~1 SOL = ~$200
    check(ev["usd_value"] >= 190, f"usd_value ≈ $200 (got {ev['usd_value']})")
    check(ev["usd_price"] == 200.0, "usd_price == 200")
    check(ev["gas_fee_usd"] >= 0, "gas_fee_usd present (fee-payer)")


# ═══════════════════════════════════════════════════════════════════
# 2. Parse outgoing SOL transfer → "send"
# ═══════════════════════════════════════════════════════════════════


def test_sol_send():
    section("2. SOL send — outgoing native SOL")
    # Pre: 2 SOL, Post: 0.5 SOL, fee=5000 lamports. Address is fee-payer (idx 0)
    tx = _mk_tx(1720086400,
                pre_balances=[2_000_000_000, 3_000_000_000],
                post_balances=[500_000_000, 4_500_000_000],
                fee=5000,
                account_keys=[{"pubkey": ADDR}, {"pubkey": ADDR2}],
                signature="tx_sol_out")

    ev = _parse_solana_tx(ADDR, tx, SOL_PRICE, {})
    check(ev is not None, "send tx → event not None")
    check(ev["type"] == "send", "type == 'send'")
    check(ev["direction"] == "out", "direction == 'out'")
    check(ev["sent_symbol"] == "SOL", "sent_symbol == 'SOL'")
    check(ev["sent_amount"] > 0, "sent_amount > 0")
    # Sent ~1.5 SOL = ~$300
    check(ev["usd_value"] >= 280, f"usd_value ≈ $300 (got {ev['usd_value']})")
    check(ev["gas_fee_usd"] > 0, "gas_fee_usd > 0 (fee-payer)")


# ═══════════════════════════════════════════════════════════════════
# 3. Parse SPL token receive → "receive"
# ═══════════════════════════════════════════════════════════════════


def test_spl_receive():
    section("3. SPL receive — incoming USDC")
    # Address at index 1 (not fee-payer). SOL unchanged.
    tx = _mk_tx(1720172800,
                pre_balances=[10_000_000_000, 5_000_000_000],
                post_balances=[10_000_000_000, 5_000_000_000],
                pre_tb=[],
                post_tb=[_tb_entry(1, USDC_MINT, 500.0)],
                fee=5000,
                account_keys=[{"pubkey": ADDR2}, {"pubkey": ADDR}],
                signature="rx_usdc")

    spl_prices = {USDC_MINT: 1.0}
    ev = _parse_solana_tx(ADDR, tx, SOL_PRICE, spl_prices)
    check(ev is not None, "SPL receive → event not None")
    check(ev["type"] == "receive", "type == 'receive'")
    check(ev["direction"] == "in", "direction == 'in'")
    check(ev["recv_symbol"] == "USDC", "recv_symbol == 'USDC'")
    check(ev["recv_amount"] == 500.0, "recv_amount == 500 USDC")
    check(ev["usd_value"] == 500.0, "usd_value == 500")
    check(ev["gas_fee_usd"] == 0.0, "gas_fee_usd == 0 (not fee-payer)")


# ═══════════════════════════════════════════════════════════════════
# 4. Parse SPL token send → "send"
# ═══════════════════════════════════════════════════════════════════


def test_spl_send():
    section("4. SPL send — outgoing USDC")
    tx = _mk_tx(1720259200,
                pre_balances=[10_000_000_000, 5_000_000_000],
                post_balances=[10_000_000_000, 5_000_000_000],
                pre_tb=[_tb_entry(1, USDC_MINT, 500.0)],
                post_tb=[],
                fee=5000,
                account_keys=[{"pubkey": ADDR2}, {"pubkey": ADDR}],
                signature="tx_usdc_out")

    spl_prices = {USDC_MINT: 1.0}
    ev = _parse_solana_tx(ADDR, tx, SOL_PRICE, spl_prices)
    check(ev is not None, "SPL send → event not None")
    check(ev["type"] == "send", "type == 'send'")
    check(ev["direction"] == "out", "direction == 'out'")
    check(ev["sent_symbol"] == "USDC", "sent_symbol == 'USDC'")
    check(ev["sent_amount"] == 500.0, "sent_amount == 500 USDC")
    check(ev["usd_value"] == 500.0, "usd_value == 500")


# ═══════════════════════════════════════════════════════════════════
# 5. Parse swap (token A out, token B in) → "swap"
# ═══════════════════════════════════════════════════════════════════


def test_swap_tokens():
    section("5. Swap — USDC out, USDT in")
    # Address is fee-payer (idx 0), pays 500 USDC, receives 500 USDT
    tx = _mk_tx(1720345600,
                pre_balances=[10_000_000_000, 5_000_000_000],
                post_balances=[9_995_000_000, 5_000_000_000],  # fee subtracted
                pre_tb=[_tb_entry(0, USDC_MINT, 1000.0)],
                post_tb=[_tb_entry(0, USDC_MINT, 500.0),
                         _tb_entry(0, USDT_MINT, 500.0)],
                fee=5000,
                account_keys=[{"pubkey": ADDR}, {"pubkey": ADDR2}],
                signature="swap_usdc_usdt")

    spl_prices = {USDC_MINT: 1.0, USDT_MINT: 1.0}
    ev = _parse_solana_tx(ADDR, tx, SOL_PRICE, spl_prices)
    check(ev is not None, "swap → event not None")
    check(ev["type"] == "swap", "type == 'swap'")
    check(ev["direction"] == "swap", "direction == 'swap'")
    check("→" in ev["token_symbol"], f"token_symbol contains → (got {ev['token_symbol']})")
    check(ev["sent_symbol"] is not None and ev["recv_symbol"] is not None,
          "both sent_symbol and recv_symbol set")
    check(ev["legs"] >= 2, f"legs >= 2 (got {ev['legs']})")
    # usd_value should be ~500 (max of out/in), not ~1000
    check(490 <= ev["usd_value"] <= 510, f"usd_value ≈ 500 (got {ev['usd_value']})")
    check(ev["usd_price"] is None, "usd_price is None for swaps")


# ═══════════════════════════════════════════════════════════════════
# 6. Swap SOL→SPL → "swap"
# ═══════════════════════════════════════════════════════════════════


def test_swap_sol_to_spl():
    section("6. Swap — SOL → USDC")
    # Fee-payer spends 1 SOL, receives 200 USDC
    tx = _mk_tx(1720432000,
                pre_balances=[5_000_000_000, 10_000_000_000],
                post_balances=[4_000_000_000, 11_000_000_000],
                pre_tb=[],
                post_tb=[_tb_entry(0, USDC_MINT, 200.0)],
                fee=5000,
                account_keys=[{"pubkey": ADDR}, {"pubkey": ADDR2}],
                signature="swap_sol_usdc")

    spl_prices = {USDC_MINT: 1.0}
    ev = _parse_solana_tx(ADDR, tx, SOL_PRICE, spl_prices)
    check(ev is not None, "SOL→USDC → event not None")
    check(ev["type"] == "swap", "type == 'swap'")
    # SOL leg is outgoing, USDC leg is incoming
    check(ev["sent_symbol"] is not None, "sent_symbol set (SOL)")
    check(ev["recv_symbol"] is not None, "recv_symbol set (USDC)")
    check(ev["legs"] >= 2, "legs >= 2")


# ═══════════════════════════════════════════════════════════════════
# 7. Defensive: tx where our address is NOT in accountKeys
# ═══════════════════════════════════════════════════════════════════


def test_not_our_tx():
    section("7. Defensive — not our transaction")
    tx = _mk_tx(1720518400,
                pre_balances=[1_000_000_000, 5_000_000_000],
                post_balances=[500_000_000, 5_500_000_000],
                account_keys=[{"pubkey": ADDR2}, {"pubkey": "So11111111111111111111111111111111111111112"}],
                signature="not_ours")

    ev = _parse_solana_tx(ADDR, tx, SOL_PRICE, {})
    check(ev is None, "not our tx → None")


# ═══════════════════════════════════════════════════════════════════
# 8. Defensive: empty meta
# ═══════════════════════════════════════════════════════════════════


def test_empty_meta():
    section("8. Defensive — empty meta")
    tx = {
        "blockTime": 1720604800,
        "transaction": {"signatures": ["sig_empty"]},
    }
    ev = _parse_solana_tx(ADDR, tx, SOL_PRICE, {})
    check(ev is None, "empty meta → None")


# ═══════════════════════════════════════════════════════════════════
# 9. Defensive: no meaningful transfers (SOL unchanged, no SPL)
# ═══════════════════════════════════════════════════════════════════


def test_no_transfers():
    section("9. Defensive — no transfers")
    # Fee-payer with no net transfer: post = pre - fee.
    # The fee adjustment cancels out, so sol_change ≈ 0 → no event.
    tx = _mk_tx(1720691200,
                pre_balances=[5_000_000_000, 10_000_000_000],
                post_balances=[4_995_000_000, 10_000_000_000],  # pre - fee
                fee=5_000_000,  # 0.005 SOL
                account_keys=[{"pubkey": ADDR}, {"pubkey": ADDR2}])

    ev = _parse_solana_tx(ADDR, tx, SOL_PRICE, {})
    check(ev is None, "fee-only tx with sol_change≈0 → None (no meaningful transfer)")


# ═══════════════════════════════════════════════════════════════════
# 10. Multiple SPL tokens out + in (complex swap)
# ═══════════════════════════════════════════════════════════════════


def test_complex_swap():
    section("10. Complex swap — multiple SPL tokens")
    BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    tx = _mk_tx(1720777600,
                pre_balances=[10_000_000_000, 5_000_000_000],
                post_balances=[9_995_000_000, 5_000_000_000],
                pre_tb=[_tb_entry(0, USDC_MINT, 1000.0),
                        _tb_entry(0, BONK_MINT, 500000.0)],
                post_tb=[_tb_entry(0, USDC_MINT, 900.0),
                         _tb_entry(0, BONK_MINT, 1500000.0)],
                fee=5000,
                account_keys=[{"pubkey": ADDR}, {"pubkey": ADDR2}],
                signature="complex_swap")

    spl_prices = {USDC_MINT: 1.0, BONK_MINT: 0.00002}
    ev = _parse_solana_tx(ADDR, tx, SOL_PRICE, spl_prices)
    check(ev is not None, "complex swap → event not None")
    check(ev["type"] == "swap", "type == 'swap'")
    check(ev["legs"] >= 2, f"legs >= 2 (got {ev['legs']})")
    # Main sent leg = BONK (1M * 0.00002 = $20) vs USDC (100 * 1 = $100)
    # Wait, BONK: +1M = $20, USDC: -100 = $100. Main sent = USDC ($100)
    check("→" in ev["token_symbol"], "token_symbol contains →")


# ═══════════════════════════════════════════════════════════════════
# 11. Explorer URLs
# ═══════════════════════════════════════════════════════════════════


def test_explorer_urls():
    section("11. Explorer URLs")
    sp = SolanaProvider()
    tx_url = sp.explorer_tx_url("abc123")
    check("solscan.io/tx/abc123" in tx_url, f"explorer_tx_url → solscan.io/tx/ (got {tx_url})")
    addr_url = sp.explorer_url(ADDR)
    check("solscan.io/account/" in addr_url, f"explorer_url → solscan.io/account/ (got {addr_url})")


# ═══════════════════════════════════════════════════════════════════
# 12. get_transactions returns correct shape even when RPC fails
# ═══════════════════════════════════════════════════════════════════


def test_get_transactions_shape():
    section("12. get_transactions shape")
    import asyncio

    sp = SolanaProvider()

    async def go():
        return await sp.get_transactions(
            "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV"
        )

    result = asyncio.run(go())
    check("total" in result, "result has 'total'")
    check("items" in result, "result has 'items'")
    check("counts" in result, "result has 'counts'")
    check(isinstance(result["items"], list), "items is list")
    check(isinstance(result["counts"], dict), "counts is dict")
    # May be 0 if RPC is unreachable, but must never crash
    check(result["total"] >= 0, f"total >= 0 (got {result['total']})")


# ═══════════════════════════════════════════════════════════════════
# 13. Non-regression: EVM/BTC/Cosmos providers unchanged
# ═══════════════════════════════════════════════════════════════════


def test_non_regression():
    section("13. Non-regression — EVM/BTC/Cosmos routing")
    # EVM
    p_evm = provider_for("0x15CD7D7aE29f3F76FDC9d89e1FbC58B23E8D9C30")
    check(p_evm is not None and p_evm.chain_type == "evm",
          "EVM address → EvmProvider")
    # BTC
    p_btc = provider_for("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")
    check(p_btc is not None and p_btc.chain_type == "bitcoin",
          "BTC address → BitcoinProvider")
    # Cosmos
    p_cosmos = provider_for("cosmos1hsk6jryyqjfhp5dhv55tc4hfer5d6ylts98eqd")
    check(p_cosmos is not None and p_cosmos.chain_type == "cosmos",
          "Cosmos address → CosmosProvider")
    # Solana
    p_sol = provider_for("7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV")
    check(p_sol is not None and p_sol.chain_type == "solana",
          "Solana address → SolanaProvider")


# ═══════════════════════════════════════════════════════════════════
# 14. provider metadata unchanged
# ═══════════════════════════════════════════════════════════════════


def test_provider_metadata():
    section("14. Provider metadata")
    sp = SolanaProvider()
    check(sp.chain_type == "solana", "chain_type == 'solana'")
    check(sp.native_symbol == "SOL", "native_symbol == 'SOL'")
    check(callable(sp.detect), "detect is callable")
    check(callable(sp.get_transactions), "get_transactions is callable")
    check(callable(sp.get_portfolio), "get_portfolio is callable")


# ═══════════════════════════════════════════════════════════════════
# 15. Registry count includes all 4 providers
# ═══════════════════════════════════════════════════════════════════


def test_registry():
    section("15. Registry — 4 providers")
    types = {p.chain_type for p in PROVIDERS}
    check("evm" in types, "EVM registered")
    check("bitcoin" in types, "BTC registered")
    check("solana" in types, "SOL registered")
    check("cosmos" in types, "COSMOS registered")
    check(len(PROVIDERS) >= 4, f"≥ 4 providers (got {len(PROVIDERS)})")


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_sol_receive()
    test_sol_send()
    test_spl_receive()
    test_spl_send()
    test_swap_tokens()
    test_swap_sol_to_spl()
    test_not_our_tx()
    test_empty_meta()
    test_no_transfers()
    test_complex_swap()
    test_explorer_urls()
    test_get_transactions_shape()
    test_non_regression()
    test_provider_metadata()
    test_registry()

    print(f"\n{'=' * 60}")
    total = PASS + FAIL
    print(f"  Results: {PASS}/{total} passed")
    if FAIL > 0:
        print(f"  {FAIL} FAILURE(S)")
        sys.exit(1)
    else:
        print(f"  ALL TESTS PASSED")
