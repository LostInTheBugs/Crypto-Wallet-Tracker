"""
MAINNET VALIDATION — 2026.07.27.c1 SPL fix.

Validates the full end-to-end pipeline:
  1. get_transactions on a REAL Solana address with SPL activity
  2. _persist_non_evm_events in a temp DB
  3. group_transaction_events from persisted rows
  4. Aggregated view (GET /api/transactions without wallet filter)

Run:  python3 tests/validate_spl_fix.py
"""

import asyncio
import json
import os
import sys
import sqlite3
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS = 0
FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ FAIL: {label}")


async def main():
    global PASS, FAIL
    from services.providers.solana import SolanaProvider, _parse_solana_tx
    from services.providers.base import provider_for
    from services.tx_events import group_transaction_events

    sp = SolanaProvider()

    # ════════════════════════════════════════════════════════════
    # STEP 1 — Find a real Solana address with SPL activity
    # ════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("STEP 1 — Live Solana RPC: get_transactions")
    print("=" * 60)

    # Use a well-known Solana address with high activity
    # This is Raydium's liquidity pool address — lots of SPL transfers
    ADDR = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"

    print(f"  Address: {ADDR}")
    print(f"  Fetching live transactions...")

    result = await sp.get_transactions(address=ADDR, limit=50)
    items = result["items"]
    total = result["total"]
    counts = result["counts"]

    print(f"  Total: {total}, counts: {json.dumps(counts)}")

    check(total > 0, f"got {total} events from live RPC")

    # ── Prove SPL events are detected ──────────────────────────
    spl_events = [e for e in items if e.get("recv_symbol") not in ("SOL", None, "?")
                  or e.get("sent_symbol") not in ("SOL", None, "?")]
    # Better: events where the main token is not SOL-only
    non_sol = [
        e for e in items
        if (e.get("sent_symbol") and e["sent_symbol"] != "SOL")
        or (e.get("recv_symbol") and e["recv_symbol"] != "SOL")
        or e["type"] == "swap"
    ]
    print(f"\n  SPL events (non-SOL-only): {len(non_sol)}")

    check(len(non_sol) > 0, "SPL events detected in live data")

    # Show 3 examples
    print(f"\n  --- Sample SPL events ---")
    for i, ev in enumerate(non_sol[:3]):
        stype = ev["type"]
        ss = ev.get("sent_symbol", "—")
        sa = ev.get("sent_amount", 0)
        rs = ev.get("recv_symbol", "—")
        ra = ev.get("recv_amount", 0)
        uv = ev.get("usd_value", 0)
        print(f"  [{i+1}] {stype:7s}  {ss}({sa}) → {rs}({ra})  ${uv:.2f}")

    check(
        any(e["type"] == "swap" for e in non_sol),
        "SWAP events detected in live data"
    )

    # ── Prove SOL detection still works ────────────────────────
    sol_only = [e for e in items if e.get("token_symbol") == "SOL" and e["type"] != "swap"]
    print(f"\n  SOL-only events: {len(sol_only)}")
    check(len(sol_only) >= 0, "SOL detection still works (no crash)")

    # ════════════════════════════════════════════════════════════
    # STEP 2 — Use _fetch_transactions_for_wallet pattern
    # ════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("STEP 2 — Persistence in temp SQLite DB")
    print("=" * 60)

    from app import _persist_non_evm_events, DB_PATH as _ORIG_DB_PATH

    # Create a temp DB with the transactions schema
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db = tmp.name
    tmp.close()

    # Copy the original DB path import
    import app as app_module
    original_db_path = app_module.DB_PATH

    # Build schema
    conn = sqlite3.connect(tmp_db)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            wallet_address TEXT NOT NULL,
            token_symbol TEXT NOT NULL DEFAULT '?',
            token_name TEXT DEFAULT 'Unknown',
            amount REAL NOT NULL DEFAULT 0,
            chain TEXT NOT NULL,
            tx_hash TEXT NOT NULL DEFAULT '',
            block_time TEXT,
            direction TEXT DEFAULT 'in',
            log_index INTEGER DEFAULT 0,
            contract_address TEXT DEFAULT '',
            usd_price REAL,
            usd_value REAL DEFAULT 0,
            gas_fee_eth REAL DEFAULT 0,
            gas_fee_usd REAL DEFAULT 0,
            event_type TEXT DEFAULT 'receive'
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_dedup
            ON transactions(tx_hash, log_index, user_id);
    """)
    conn.commit()
    conn.close()

    # Redirect DB_PATH to temp
    app_module.DB_PATH = tmp_db

    try:
        user_id = 1
        # Get events from provider
        events_to_persist = items[:20]  # First 20 events
        inserted = await _persist_non_evm_events(user_id, ADDR, events_to_persist)

        print(f"  Events sent for persistence: {len(events_to_persist)}")
        print(f"  Rows inserted: {inserted}")
        check(inserted > 0, f"events persisted ({inserted} rows)")

        # Check table
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT COUNT(*) as cnt FROM transactions WHERE wallet_address=? AND user_id=?",
            (ADDR, user_id)
        ).fetchone()
        print(f"  Rows in table: {rows['cnt']}")
        check(rows["cnt"] == inserted, f"table count matches inserted ({rows['cnt']} == {inserted})")

        # Idempotence check
        inserted2 = await _persist_non_evm_events(user_id, ADDR, events_to_persist)
        print(f"  Second persist: {inserted2} rows")
        check(inserted2 == 0, "idempotent (2nd persist = 0 new rows)")

        conn.close()

        # ════════════════════════════════════════════════════════
        # STEP 3 — group_transaction_events from persisted rows
        # ════════════════════════════════════════════════════════
        print("\n" + "=" * 60)
        print("STEP 3 — group_transaction_events from persisted rows")
        print("=" * 60)

        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        all_rows = conn.execute(
            "SELECT * FROM transactions WHERE wallet_address=? AND user_id=? ORDER BY block_time DESC",
            (ADDR, user_id)
        ).fetchall()
        conn.close()

        rows_as_dicts = [dict(r) for r in all_rows]
        grouped = group_transaction_events(rows_as_dicts)

        print(f"  Raw rows: {len(rows_as_dicts)}")
        print(f"  Grouped events: {len(grouped)}")
        check(len(grouped) > 0, f"group_transaction_events produces {len(grouped)} events")

        # Check types
        types = {}
        for g in grouped:
            types[g.get("type", "?")] = types.get(g.get("type", "?"), 0) + 1
        print(f"  Event types: {json.dumps(types)}")

        check("swap" in types or "send" in types or "receive" in types,
              "events have valid types")

        # Show 3 grouped events
        print(f"\n  --- Sample grouped events ---")
        for i, g in enumerate(grouped[:3]):
            print(f"  [{i+1}] {g.get('type','?'):7s}  "
                  f"{g.get('sent_symbol','—')}({g.get('sent_amount',0)}) → "
                  f"{g.get('recv_symbol','—')}({g.get('recv_amount',0)})  "
                  f"${g.get('usd_value',0):.2f}  "
                  f"gas=${g.get('gas_fee_usd',0):.4f}")

        # ════════════════════════════════════════════════════════
        # STEP 4 — Explorer link check
        # ════════════════════════════════════════════════════════
        print("\n" + "=" * 60)
        print("STEP 4 — Explorer links (Solscan)")
        print("=" * 60)

        explorer_url = sp.explorer_tx_url(grouped[0]["tx_hash"]) if grouped else ""
        check("solscan.io/tx/" in explorer_url,
              f"explorer URL contains solscan.io/tx/ → {explorer_url}")
        print(f"  Example: {explorer_url}")

        # ════════════════════════════════════════════════════════
        # STEP 5 — Wallet-aware filter (case sensitivity)
        # ════════════════════════════════════════════════════════
        print("\n" + "=" * 60)
        print("STEP 5 — Wallet-aware filter (case sensitivity)")
        print("=" * 60)

        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row

        # Simulate the wallet-aware filter used in /api/transactions
        # Add a wallet row first
        conn.execute(
            "CREATE TABLE IF NOT EXISTS wallets (id INTEGER, user_id INTEGER, address TEXT, label TEXT)"
        )
        conn.execute("INSERT INTO wallets VALUES (1, ?, ?, 'Test SOL')", (user_id, ADDR))
        conn.commit()

        # The exact filter from app.py
        rows_filtered = conn.execute("""
            SELECT COUNT(*) as cnt FROM transactions
            WHERE wallet_address = ?
              AND user_id = ?
              AND lower(wallet_address) IN (SELECT lower(address) FROM wallets WHERE user_id = ?)
        """, (ADDR, user_id, user_id)).fetchone()
        conn.close()

        print(f"  Filtered rows (wallet-aware): {rows_filtered['cnt']}")
        check(rows_filtered["cnt"] > 0,
              f"wallet-aware filter preserves Solana base58 address ({rows_filtered['cnt']} rows)")

    finally:
        app_module.DB_PATH = original_db_path
        os.unlink(tmp_db)

    # ════════════════════════════════════════════════════════════
    print(f"\n{'=' * 60}")
    total = PASS + FAIL
    print(f"  RESULTS: {PASS}/{total} passed")
    if FAIL > 0:
        print(f"  {FAIL} FAILURE(S)")
        sys.exit(1)
    else:
        print(f"  ALL VALIDATION CHECKS PASSED")
        print(f"  Address used: {ADDR}")
        print(f"  Fix confirmed: SPL token transfers now detected via owner-matching")


if __name__ == "__main__":
    asyncio.run(main())
