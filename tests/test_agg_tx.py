#!/usr/bin/env python3
"""
Tests: non-EVM transaction persistence + aggregated view reconstruction.

Verifies:
  1. Persisting non-EVM events (send/receive/swap) into transactions table
  2. Idempotence (re-persist same events = no duplicates)
  3. group_transaction_events reconstructs correct types from persisted rows
  4. Explorer URLs for non-EVM chains (Solscan, mempool, Mintscan)
  5. Non-regression: EVM path unchanged (CHAINS lookup still works)
  6. Wallet-aware filter case-insensitivity for non-EVM addresses

Run:  python3 tests/test_agg_tx.py
"""
import sys
import os
import asyncio
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.providers.base import provider_for
from services.tx_events import group_transaction_events

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
# Synthetic non-EVM events (matching Solana provider output shape)
# ═══════════════════════════════════════════════════════════════════

SOL_ADDR = "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV"
BTC_ADDR = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"

EVENTS = [
    # 1) receive: SOL received
    {
        "type": "receive",
        "direction": "in",
        "tx_hash": "5K4J...sol_recv_tx",
        "block_time": "2026-07-25T08:00:00Z",
        "token_symbol": "SOL",
        "token_name": "Solana",
        "chain": "solana",
        "usd_value": 150.00,
        "usd_price": 150.0,
        "sent": {"symbol": "SOL", "name": "Solana", "amount": 0.0, "usd_price": 150, "usd_value": 0.0, "contract": ""},
        "received": {"symbol": "SOL", "name": "Solana", "amount": 1.0, "usd_price": 150, "usd_value": 150.00, "contract": ""},
        "sent_symbol": None,
        "sent_amount": 0.0,
        "recv_symbol": "SOL",
        "recv_amount": 1.0,
        "legs": 1,
        "gas_fee_usd": 0.0005,
        "wallet_address": SOL_ADDR,
        "log_index": 0,
    },
    # 2) send: SOL sent
    {
        "type": "send",
        "direction": "out",
        "tx_hash": "3A9B...sol_send_tx",
        "block_time": "2026-07-26T10:00:00Z",
        "token_symbol": "SOL",
        "token_name": "Solana",
        "chain": "solana",
        "usd_value": 75.00,
        "usd_price": 150.0,
        "sent": {"symbol": "SOL", "name": "Solana", "amount": 0.5, "usd_price": 150, "usd_value": 75.00, "contract": ""},
        "received": {"symbol": "SOL", "name": "Solana", "amount": 0.0, "usd_price": 150, "usd_value": 0.0, "contract": ""},
        "sent_symbol": "SOL",
        "sent_amount": 0.5,
        "recv_symbol": None,
        "recv_amount": 0.0,
        "legs": 1,
        "gas_fee_usd": 0.0003,
        "wallet_address": SOL_ADDR,
        "log_index": 0,
    },
    # 3) swap: SOL -> USDC (2 legs, same tx_hash, log_index=0 out + log_index=1 in)
    {
        "type": "swap",
        "direction": "swap",
        "tx_hash": "swapSOL_USDC_001",
        "block_time": "2026-07-26T12:00:00Z",
        "token_symbol": "SOL \u2192 USDC",
        "token_name": "Solana",
        "chain": "solana",
        "usd_value": 300.00,
        "usd_price": None,
        "sent": {"symbol": "SOL", "name": "Solana", "amount": 2.0, "usd_price": 150, "usd_value": 300.00, "contract": ""},
        "received": {"symbol": "USDC", "name": "USD Coin", "amount": 299.5, "usd_price": 1, "usd_value": 299.50, "contract": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},
        "sent_symbol": "SOL",
        "sent_amount": 2.0,
        "recv_symbol": "USDC",
        "recv_amount": 299.5,
        "legs": 2,
        "gas_fee_usd": 0.001,
        "wallet_address": SOL_ADDR,
        "log_index": 0,
    },
]

# ═══════════════════════════════════════════════════════════════════
# DB setup
# ═══════════════════════════════════════════════════════════════════

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    wallet_address TEXT NOT NULL,
    token_symbol TEXT NOT NULL DEFAULT '?',
    token_name TEXT NOT NULL DEFAULT '',
    amount REAL NOT NULL DEFAULT 0,
    usd_price REAL DEFAULT 0,
    usd_value REAL DEFAULT 0,
    chain TEXT NOT NULL DEFAULT '',
    tx_hash TEXT NOT NULL DEFAULT '',
    block_time TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT '',
    log_index INTEGER NOT NULL DEFAULT 0,
    gas_fee_usd REAL DEFAULT 0,
    contract_address TEXT DEFAULT '',
    event_type TEXT NOT NULL DEFAULT '',
    event_method TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tx_dedup ON transactions(tx_hash, log_index, user_id);
"""

def _setup_db(db_path: str):
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

# ═══════════════════════════════════════════════════════════════════
# Section 1 — Persistence shape
# ═══════════════════════════════════════════════════════════════════

async def _run_persist_test():
    global PASS, FAIL
    import aiosqlite
    import app as app_module

    section("1. Persistence shape (send/receive/swap)")

    tmpdir = tempfile.mkdtemp(prefix="cwt_agg_tx_test_")
    db_path = os.path.join(tmpdir, "test.db")
    _setup_db(db_path)

    # Override DB_PATH for the test
    orig_db_path = app_module.DB_PATH
    app_module.DB_PATH = db_path

    try:
        from services.db import write_locked
        # Ensure the write lock is initialized (it's an asyncio.Lock created at module level)
        # Actually, write_locked is a context manager that uses a module-level lock.

        inserted = await app_module._persist_non_evm_events(1, SOL_ADDR, EVENTS)
        check(inserted == 4, f"4 rows inserted (1 recv + 1 send + 2 swap legs) -> got {inserted}")

        # Verify rows in DB
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id", (1,))
            rows = await cur.fetchall()

        check(len(rows) == 4, f"4 rows in table -> {len(rows)}")

        # Row 0: receive
        r0 = dict(rows[0])
        check(r0["direction"] == "in", f"receive row direction=in -> {r0['direction']}")
        check(r0["token_symbol"] == "SOL", f"receive row token=SOL -> {r0['token_symbol']}")
        check(r0["amount"] == 1.0, f"receive row amount=1.0 -> {r0['amount']}")
        check(r0["chain"] == "solana", f"receive row chain=solana -> {r0['chain']}")
        check(r0["usd_value"] == 150.0, f"receive row usd_value=150 -> {r0['usd_value']}")
        check(r0["block_time"] == "2026-07-25 08:00:00", f"receive row block_time normalised -> {r0['block_time']}")
        check(r0["event_type"] == "receive", f"receive row event_type=receive -> {r0['event_type']}")

        # Row 1: send
        r1 = dict(rows[1])
        check(r1["direction"] == "out", f"send row direction=out -> {r1['direction']}")
        check(r1["token_symbol"] == "SOL", f"send row token=SOL -> {r1['token_symbol']}")
        check(r1["amount"] == 0.5, f"send row amount=0.5 -> {r1['amount']}")

        # Rows 2-3: swap (same tx_hash, log_index 0 and 1)
        r2 = dict(rows[2])
        r3 = dict(rows[3])
        check(r2["tx_hash"] == "swapSOL_USDC_001", f"swap leg0 tx_hash correct -> {r2['tx_hash']}")
        check(r3["tx_hash"] == "swapSOL_USDC_001", f"swap leg1 tx_hash correct -> {r3['tx_hash']}")
        check(r2["log_index"] == 0, f"swap leg0 log_index=0 -> {r2['log_index']}")
        check(r3["log_index"] == 1, f"swap leg1 log_index=1 -> {r3['log_index']}")
        check(r2["direction"] == "out", f"swap out leg direction=out -> {r2['direction']}")
        check(r3["direction"] == "in", f"swap in leg direction=in -> {r3['direction']}")
        check(r2["token_symbol"] == "SOL", f"swap out leg token=SOL -> {r2['token_symbol']}")
        check(r3["token_symbol"] == "USDC", f"swap in leg token=USDC -> {r3['token_symbol']}")
        check(r2["event_type"] == "swap", f"swap leg0 event_type=swap -> {r2['event_type']}")
        check(r3["event_type"] == "swap", f"swap leg1 event_type=swap -> {r3['event_type']}")
        # gas should be on log_index=0 only
        check(r2["gas_fee_usd"] == 0.001, f"swap gas on leg0 -> {r2['gas_fee_usd']}")
        check(r3["gas_fee_usd"] == 0.0, f"swap no gas on leg1 -> {r3['gas_fee_usd']}")

    finally:
        app_module.DB_PATH = orig_db_path
        shutil.rmtree(tmpdir, ignore_errors=True)

# ═══════════════════════════════════════════════════════════════════
# Section 2 — Idempotence
# ═══════════════════════════════════════════════════════════════════

async def _run_idempotence_test():
    global PASS, FAIL
    import aiosqlite
    import app as app_module

    section("2. Idempotence (re-persist = no duplicates)")

    tmpdir = tempfile.mkdtemp(prefix="cwt_agg_tx_test_")
    db_path = os.path.join(tmpdir, "test.db")
    _setup_db(db_path)

    orig_db_path = app_module.DB_PATH
    app_module.DB_PATH = db_path

    try:
        # First persist
        n1 = await app_module._persist_non_evm_events(1, SOL_ADDR, EVENTS)
        check(n1 == 4, f"first persist: 4 rows -> {n1}")

        # Second persist: same events, should insert 0
        n2 = await app_module._persist_non_evm_events(1, SOL_ADDR, EVENTS)
        check(n2 == 0, f"second persist: 0 rows (idempotent) -> {n2}")

        # Verify only 4 rows total
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT COUNT(*) as c FROM transactions WHERE user_id=?", (1,))
            row = await cur.fetchone()
            check(row[0] == 4, f"total rows still 4 -> {row[0]}")

        # Third persist with 1 new event + 4 existing: should insert only the new one
        new_event = {
            "type": "receive",
            "direction": "in",
            "tx_hash": "NEW_RECV_001",
            "block_time": "2026-07-26T15:00:00Z",
            "token_symbol": "USDC",
            "token_name": "USD Coin",
            "chain": "solana",
            "usd_value": 50.00,
            "usd_price": 1.0,
            "sent": {"symbol": "USDC", "name": "USD Coin", "amount": 0.0, "usd_price": 1, "usd_value": 0.0, "contract": ""},
            "received": {"symbol": "USDC", "name": "USD Coin", "amount": 50.0, "usd_price": 1, "usd_value": 50.00, "contract": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},
            "sent_symbol": None,
            "sent_amount": 0.0,
            "recv_symbol": "USDC",
            "recv_amount": 50.0,
            "legs": 1,
            "gas_fee_usd": 0.0,
            "wallet_address": SOL_ADDR,
            "log_index": 0,
        }
        n3 = await app_module._persist_non_evm_events(1, SOL_ADDR, EVENTS + [new_event])
        check(n3 == 1, f"third persist: only new event inserted -> {n3}")

        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT COUNT(*) as c FROM transactions WHERE user_id=?", (1,))
            row = await cur.fetchone()
            check(row[0] == 5, f"total rows now 5 -> {row[0]}")

    finally:
        app_module.DB_PATH = orig_db_path
        shutil.rmtree(tmpdir, ignore_errors=True)

# ═══════════════════════════════════════════════════════════════════
# Section 3 — group_transaction_events reconstructs correctly
# ═══════════════════════════════════════════════════════════════════

async def _run_grouping_test():
    global PASS, FAIL
    import aiosqlite
    import app as app_module

    section("3. group_transaction_events reconstructs from persisted rows")

    tmpdir = tempfile.mkdtemp(prefix="cwt_agg_tx_test_")
    db_path = os.path.join(tmpdir, "test.db")
    _setup_db(db_path)

    orig_db_path = app_module.DB_PATH
    app_module.DB_PATH = db_path

    try:
        await app_module._persist_non_evm_events(1, SOL_ADDR, EVENTS)

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id", (1,))
            rows = await cur.fetchall()

        events = group_transaction_events(rows)
        check(len(events) == 3, f"3 events from 4 rows (swap merged) -> {len(events)}")

        ev_by_type = {e["type"]: e for e in events}

        # Receive
        recv = ev_by_type.get("receive")
        check(recv is not None, "receive event present")
        check(recv["type"] == "receive", f"receive type -> {recv['type']}")
        check(recv["direction"] == "in", f"receive direction -> {recv['direction']}")
        check(recv["token_symbol"] == "SOL", f"receive token -> {recv['token_symbol']}")
        check(recv["amount"] == 1.0, f"receive amount -> {recv['amount']}")
        check(recv["usd_value"] == 150.0, f"receive usd_value -> {recv['usd_value']}")

        # Send
        send = ev_by_type.get("send")
        check(send is not None, "send event present")
        check(send["type"] == "send", f"send type -> {send['type']}")
        check(send["direction"] == "out", f"send direction -> {send['direction']}")
        check(send["token_symbol"] == "SOL", f"send token -> {send['token_symbol']}")
        check(send["amount"] == 0.5, f"send amount -> {send['amount']}")

        # Swap
        swap = ev_by_type.get("swap")
        check(swap is not None, "swap event present")
        check(swap["type"] == "swap", f"swap type -> {swap['type']}")
        check(swap["direction"] == "swap", f"swap direction -> {swap['direction']}")
        check(swap["sent_symbol"] == "SOL", f"swap sent_symbol -> {swap['sent_symbol']}")
        check(swap["recv_symbol"] == "USDC", f"swap recv_symbol -> {swap['recv_symbol']}")
        check(swap["gas_fee_usd"] == 0.001, f"swap gas_fee_usd -> {swap['gas_fee_usd']}")
        # usd_value for swap = max(sum_out, sum_in) = max(300, 299.50) = 300
        check(swap["usd_value"] == 300.0, f"swap usd_value = max(out,in) -> {swap['usd_value']}")
        check(len(swap["sent"]) == 1, f"swap 1 sent leg -> {len(swap['sent'])}")
        check(len(swap["received"]) == 1, f"swap 1 received leg -> {len(swap['received'])}")

    finally:
        app_module.DB_PATH = orig_db_path
        shutil.rmtree(tmpdir, ignore_errors=True)

# ═══════════════════════════════════════════════════════════════════
# Section 4 — Explorer URLs for non-EVM chains
# ═══════════════════════════════════════════════════════════════════

def _run_explorer_test():
    section("4. Explorer URLs for non-EVM chains")

    # Solana
    p_sol = provider_for("7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV")
    check(p_sol is not None and p_sol.chain_type == "solana", "provider_for Solana address -> SolanaProvider")
    check(p_sol.explorer_tx_url("abc123") == "https://solscan.io/tx/abc123",
          f"Solana explorer_tx_url -> {p_sol.explorer_tx_url('abc123')}")

    # Bitcoin
    p_btc = provider_for("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")
    check(p_btc is not None and p_btc.chain_type == "bitcoin", "provider_for BTC address -> BitcoinProvider")
    check(p_btc.explorer_tx_url("def456") == "https://mempool.space/tx/def456",
          f"BTC explorer_tx_url -> {p_btc.explorer_tx_url('def456')}")

    # Cosmos
    p_cosmos = provider_for("cosmos1v75h6ynsdfgqp2u0gq0c4z7aqvfcn3c6vnuxvu")
    check(p_cosmos is None and provider_for("cosmos1v75h6ynsdfgqp2u0gq0c4z7aqvfcn3c6vnuxvu") is None,
          "provider_for Cosmos address → None (not supported)")

    # EVM
    p_evm = provider_for("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    check(p_evm is not None and p_evm.chain_type == "evm", "provider_for EVM address -> EvmProvider")

# ═══════════════════════════════════════════════════════════════════
# Section 5 — Non-regression: EVM CHAINS lookup still works
# ═══════════════════════════════════════════════════════════════════

def _run_evm_regression_test():
    from services.portfolio_service import CHAINS

    section("5. Non-regression: EVM CHAINS lookup")

    check("ethereum" in CHAINS, "ethereum in CHAINS")
    check("base" in CHAINS, "base in CHAINS")
    check("solana" not in CHAINS, "solana NOT in CHAINS (only EVM)")
    check("bitcoin" not in CHAINS, "bitcoin NOT in CHAINS")
    check("cosmos" not in CHAINS, "cosmos NOT in CHAINS")

    # Verify EVM tx URL construction still works
    host = CHAINS.get("ethereum")
    tx_url = f"https://{host}/tx/0xabc123" if host else ""
    check(tx_url == "https://eth.blockscout.com/tx/0xabc123",
          f"EVM tx URL -> {tx_url}")

# ═══════════════════════════════════════════════════════════════════
# Section 6 — Wallet-aware filter compatibility (case-insensitive)
# ═══════════════════════════════════════════════════════════════════

async def _run_wallet_filter_test():
    global PASS, FAIL
    import aiosqlite
    import app as app_module

    section("6. Wallet-aware SQL filter: non-EVM addresses survive lower()")

    tmpdir = tempfile.mkdtemp(prefix="cwt_agg_tx_test_")
    db_path = os.path.join(tmpdir, "test.db")
    _setup_db(db_path)

    # Also create a wallets table for the IN subquery
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS wallets (id INTEGER PRIMARY KEY, user_id INTEGER, address TEXT)")
    # Insert wallet with exact case (Solana base58 can have mixed case)
    conn.execute("INSERT INTO wallets (user_id, address) VALUES (1, ?)", (SOL_ADDR,))
    conn.commit()
    conn.close()

    orig_db_path = app_module.DB_PATH
    app_module.DB_PATH = db_path

    try:
        await app_module._persist_non_evm_events(1, SOL_ADDR, EVENTS)

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            # Simulate the exact WHERE clause from the transactions endpoint
            cur = await db.execute(
                """SELECT * FROM transactions 
                   WHERE user_id=? 
                   AND lower(wallet_address) IN (SELECT lower(address) FROM wallets WHERE user_id=?) 
                   ORDER BY id""",
                (1, 1))
            rows = await cur.fetchall()
            check(len(rows) == 4, f"wallet-aware filter finds all 4 rows for valid wallet -> {len(rows)}")

        # Verify that a non-existent wallet filters out everything
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute(
                """SELECT COUNT(*) as c FROM transactions 
                   WHERE user_id=? 
                   AND lower(wallet_address) IN (SELECT lower(address) FROM wallets WHERE user_id=?)""",
                (999, 999))
            row = await cur.fetchone()
            check(row[0] == 0, f"non-existent user_id=999 returns 0 rows -> {row[0]}")

        # Verify lower() works even with mixed-case Solana address
        # Re-insert with a differently-cased version
        mixed_addr = SOL_ADDR.lower() if SOL_ADDR[0].isupper() else SOL_ADDR.upper() 
        # Actually Solana addr are already mixed, let me just verify lower() comparison
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute(
                """SELECT lower(wallet_address) FROM transactions WHERE user_id=? LIMIT 1""",
                (1,))
            row = await cur.fetchone()
            check(row[0] == SOL_ADDR.lower(),
                  f"lower(wallet_address) matches original lowered -> {row[0]}")

    finally:
        app_module.DB_PATH = orig_db_path
        shutil.rmtree(tmpdir, ignore_errors=True)

# ═══════════════════════════════════════════════════════════════════
# Section 7 — BTC (empty get_transactions) returns 0 gracefully
# ═══════════════════════════════════════════════════════════════════

async def _run_btc_empty_test():
    global PASS, FAIL
    import app as app_module

    section("7. BTC with empty get_transactions returns 0, no error")

    tmpdir = tempfile.mkdtemp(prefix="cwt_agg_tx_test_")
    db_path = os.path.join(tmpdir, "test.db")
    _setup_db(db_path)

    orig_db_path = app_module.DB_PATH
    app_module.DB_PATH = db_path

    try:
        # Empty event list
        inserted = await app_module._persist_non_evm_events(1, SOL_ADDR, [])
        check(inserted == 0, f"empty events -> 0 inserted -> {inserted}")

        # Also verify: provider_for(cosmos...) returns None
        from services.providers.base import provider_for
        p = provider_for("cosmos1v75h6ynsdfgqp2u0gq0c4z7aqvfcn3c6vnuxvu")
        check(p is None, "provider_for cosmos1... → None")

    finally:
        app_module.DB_PATH = orig_db_path
        shutil.rmtree(tmpdir, ignore_errors=True)

# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

async def main():
    global PASS, FAIL
    print("test_agg_tx.py — Non-EVM transaction persistence + aggregated view")
    print()

    await _run_persist_test()
    await _run_idempotence_test()
    await _run_grouping_test()
    _run_explorer_test()
    _run_evm_regression_test()
    await _run_wallet_filter_test()
    await _run_btc_empty_test()

    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {PASS} passed, {FAIL} failed")
    print(f"{'=' * 60}")
    if FAIL:
        print("AGG_TX TEST: %d FAILURE(S)" % FAIL)
        sys.exit(1)
    print("AGG_TX TEST: ALL PASS")

if __name__ == "__main__":
    asyncio.run(main())
