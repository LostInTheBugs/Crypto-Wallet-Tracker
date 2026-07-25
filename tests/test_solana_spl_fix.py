"""
Test that _parse_solana_tx detects SPL transfers via owner-matching.

This is the fix for 2026.07.27.c1: before, _tb_lookup filtered by
accountIndex (which is the SPL token ATA, NOT the wallet owner) so
pure-SPL transactions were always dropped. Now it filters by `owner`
(the wallet pubkey), which is what Solana's pre/postTokenBalances
actually contain.

Run:  python3 tests/test_solana_spl_fix.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.providers.solana import _parse_solana_tx

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


ADDR = "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV"
ADDR2 = "DRpbCBMxVnDK7maPMoPVJHh8QXkgzpo5NcN7svK44gQm"
SOL_PRICE = 200.0
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def _tb_entry(mint, ui_amount, decimals=6, owner=ADDR):
    """Build a token balance entry matching the real Solana JSON shape."""
    amount_str = str(int(ui_amount * 10**decimals))
    return {
        "accountIndex": 99,  # deliberately DIFFERENT from our accountKeys index
        "mint": mint,
        "owner": owner,
        "uiTokenAmount": {
            "uiAmount": ui_amount,
            "decimals": decimals,
            "amount": amount_str,
            "uiAmountString": str(ui_amount),
        },
    }


def _mk_tx(block_time, pre_balances, post_balances,
           pre_tb=None, post_tb=None, fee=5000,
           account_keys=None, signature="sig123",
           our_idx=0):
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


# ═══════════════════════════════════════════════════════════════════
# 1. Core fix: SPL receive detected via owner (not accountIndex)
# ═══════════════════════════════════════════════════════════════════


def test_spl_receive_via_owner():
    section("1. SPL receive via owner (accountIndex != our index)")
    # ADDR is fee-payer at index 0, but the token balance entry
    # has accountIndex=99 (DIFFERENT from our accountKeys index)
    # and owner=ADDR.  Before the fix, this was dropped.
    # After the fix, the USDC receive is detected.
    tx = _mk_tx(1720172800,
                pre_balances=[10_000_000_000, 5_000_000_000],
                post_balances=[10_000_000_000, 5_000_000_000],
                pre_tb=[],
                post_tb=[_tb_entry(USDC_MINT, 500.0)],
                account_keys=[{"pubkey": ADDR}, {"pubkey": ADDR2}],
                signature="rx_usdc_owner")

    spl_prices = {USDC_MINT: 1.0}
    ev = _parse_solana_tx(ADDR, tx, SOL_PRICE, spl_prices)
    check(ev is not None, "SPL receive via owner → event not None (was None before fix)")
    check(ev["type"] == "receive", "type == 'receive'")
    check(ev["recv_symbol"] == "USDC", "recv_symbol == 'USDC'")
    check(ev["recv_amount"] == 500.0, "recv_amount == 500 USDC")
    check(ev["usd_value"] == 500.0, "usd_value == 500")


# ═══════════════════════════════════════════════════════════════════
# 2. Core fix: SPL send via owner (not accountIndex)
# ═══════════════════════════════════════════════════════════════════


def test_spl_send_via_owner():
    section("2. SPL send via owner (accountIndex != our index)")
    tx = _mk_tx(1720259200,
                pre_balances=[10_000_000_000, 5_000_000_000],
                post_balances=[10_000_000_000, 5_000_000_000],
                pre_tb=[_tb_entry(USDC_MINT, 500.0)],
                post_tb=[],
                fee=0,  # no fee to avoid phantom SOL leg
                account_keys=[{"pubkey": ADDR2}, {"pubkey": ADDR}],  # ADDR at idx 1 (not fee-payer)
                signature="tx_usdc_owner")

    spl_prices = {USDC_MINT: 1.0}
    ev = _parse_solana_tx(ADDR, tx, SOL_PRICE, spl_prices)
    check(ev is not None, "SPL send via owner → event not None")
    check(ev["type"] == "send", "type == 'send'")
    check(ev["sent_symbol"] == "USDC", "sent_symbol == 'USDC'")
    check(ev["sent_amount"] == 500.0, "sent_amount == 500 USDC")
    check(ev["usd_value"] == 500.0, "usd_value == 500")


# ═══════════════════════════════════════════════════════════════════
# 3. Different owner → NOT captured (defensive)
# ═══════════════════════════════════════════════════════════════════


def test_different_owner_not_captured():
    section("3. Different owner → NOT captured")
    # Token balance entry with owner=ADDR2, not our tracked ADDR
    tx = _mk_tx(1720345600,
                pre_balances=[10_000_000_000, 5_000_000_000],
                post_balances=[10_000_000_000, 5_000_000_000],
                pre_tb=[],
                post_tb=[_tb_entry(USDC_MINT, 500.0, owner=ADDR2)],
                fee=0,  # no fee
                account_keys=[{"pubkey": ADDR2}, {"pubkey": ADDR}],  # ADDR at idx 1
                signature="rx_not_ours")

    spl_prices = {USDC_MINT: 1.0}
    ev = _parse_solana_tx(ADDR, tx, SOL_PRICE, spl_prices)
    # No SOL transfer either (balances unchanged), no SPL for us → None
    check(ev is None, "different owner SPL tx → None (correct, not ours)")


# ═══════════════════════════════════════════════════════════════════
# 4. SOL detection unchanged (regression guard)
# ═══════════════════════════════════════════════════════════════════


def test_sol_receive_unchanged():
    section("4. SOL receive detection UNCHANGED (non-regression)")
    tx = _mk_tx(1720000000,
                pre_balances=[1_000_000_000, 5_000_000_000],
                post_balances=[2_000_000_000, 4_000_000_000],
                account_keys=[{"pubkey": ADDR}, {"pubkey": ADDR2}],
                signature="sol_in_unchanged")

    ev = _parse_solana_tx(ADDR, tx, SOL_PRICE, {})
    check(ev is not None, "SOL receive → event not None")
    check(ev["type"] == "receive", "type == 'receive'")
    check(ev["token_symbol"] == "SOL", "token_symbol == 'SOL'")
    check(ev["recv_amount"] > 0, "recv_amount > 0")
    check(ev["usd_value"] >= 190, f"usd_value ≈ $200 (got {ev['usd_value']})")


# ═══════════════════════════════════════════════════════════════════
# 5. Account created (no pre, has post) → correct delta
# ═══════════════════════════════════════════════════════════════════


def test_token_account_created():
    section("5. Token account created (pre absent, post present)")
    # ATA opened + funded in same tx: pre has no entry, post has 100 USDC
    tx = _mk_tx(1720432000,
                pre_balances=[10_000_000_000, 5_000_000_000],
                post_balances=[10_000_000_000, 5_000_000_000],
                pre_tb=[],
                post_tb=[_tb_entry(USDC_MINT, 100.0)],
                fee=0,  # no fee to avoid phantom SOL leg
                account_keys=[{"pubkey": ADDR2}, {"pubkey": ADDR}],  # ADDR at idx 1
                signature="ata_created")

    spl_prices = {USDC_MINT: 1.0}
    ev = _parse_solana_tx(ADDR, tx, SOL_PRICE, spl_prices)
    check(ev is not None, "ATA created → event not None")
    check(ev["type"] == "receive", "type == 'receive' (deposit via ATA creation)")
    check(ev["recv_symbol"] == "USDC", "recv_symbol == 'USDC'")
    check(ev["recv_amount"] == 100.0, "recv_amount == 100")
    check(ev["usd_value"] == 100.0, "usd_value == 100")
    # SOL fee-only without meaningful transfer → ignored for SOL leg
    # but the deposit counts → receive event
    check(ev["sent_amount"] == 0.0, "no send leg (SOL fee-only ignored)")


# ═══════════════════════════════════════════════════════════════════
# 6. Account closed (has pre, no post) → correct delta
# ═══════════════════════════════════════════════════════════════════


def test_token_account_closed():
    section("6. Token account closed (pre present, post absent)")
    tx = _mk_tx(1720518400,
                pre_balances=[10_000_000_000, 5_000_000_000],
                post_balances=[10_000_000_000, 5_000_000_000],
                pre_tb=[_tb_entry(USDC_MINT, 100.0)],
                post_tb=[],
                fee=0,  # no fee to avoid phantom SOL leg
                account_keys=[{"pubkey": ADDR2}, {"pubkey": ADDR}],  # ADDR at idx 1
                signature="ata_closed")

    spl_prices = {USDC_MINT: 1.0}
    ev = _parse_solana_tx(ADDR, tx, SOL_PRICE, spl_prices)
    check(ev is not None, "ATA closed → event not None")
    check(ev["type"] == "send", "type == 'send' (withdrawal via ATA close)")
    check(ev["sent_symbol"] == "USDC", "sent_symbol == 'USDC'")
    check(ev["sent_amount"] == 100.0, "sent_amount == 100")
    check(ev["usd_value"] == 100.0, "usd_value == 100")


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_spl_receive_via_owner()
    test_spl_send_via_owner()
    test_different_owner_not_captured()
    test_sol_receive_unchanged()
    test_token_account_created()
    test_token_account_closed()

    print(f"\n{'=' * 60}")
    total = PASS + FAIL
    print(f"  Results: {PASS}/{total} passed")
    if FAIL > 0:
        print(f"  {FAIL} FAILURE(S)")
        sys.exit(1)
    else:
        print(f"  ALL TESTS PASSED")
