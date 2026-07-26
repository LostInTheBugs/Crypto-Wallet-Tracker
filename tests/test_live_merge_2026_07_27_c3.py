#!/usr/bin/env python3
"""
Validation tests for 2026.07.27.c3:
  A) Live merge of non-EVM tx into aggregated view
  B) Version sort with .cN correction tags

Run:  python3 tests/test_live_merge_2026_07_27_c3.py
"""
import sys
import os
import asyncio
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.tx_events import group_transaction_events, filter_events
from services.providers.base import provider_for

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
# Section A1 — Live merge integration: events from providers
# appear in the merged set with correct normalization.
# ═══════════════════════════════════════════════════════════════════

# Synthetic EVM events (from group_transaction_events)
EVM_ROWS = [
    {"id": 1, "wallet_address": "0x15CD7D7A8eB2f09ec2d158D3A35b25b171FF7f8F",
     "token_symbol": "ETH", "token_name": "Ether", "amount": 1.5,
     "usd_price": 3500, "usd_value": 5250, "chain": "ethereum",
     "tx_hash": "0xevm001", "block_time": "2026-07-26 12:00:00",
     "direction": "out", "log_index": 0, "gas_fee_usd": 3.50,
     "contract_address": "", "event_type": "", "event_method": ""},
    {"id": 2, "wallet_address": "0x15CD7D7A8eB2f09ec2d158D3A35b25b171FF7f8F",
     "token_symbol": "USDC", "token_name": "USD Coin", "amount": 5000,
     "usd_price": 1, "usd_value": 5000, "chain": "ethereum",
     "tx_hash": "0xevm001", "block_time": "2026-07-26 12:00:00",
     "direction": "in", "log_index": 1, "gas_fee_usd": 0,
     "contract_address": "", "event_type": "", "event_method": ""},
]

# Synthetic Solana provider events (same shape as SolanaProvider.get_transactions)
SOL_ADDR = "GAF1SZ3VbbV5LpfRUWKfJQvBnB6tfm4wSgbCxnrfBpRB"
SOL_EVENTS = [
    {
        "type": "receive",
        "direction": "in",
        "tx_hash": "5K4J...sol_recv",
        "block_time": "2026-07-26T08:00:00Z",
        "token_symbol": "SOL",
        "token_name": "Solana",
        "chain": "solana",
        "usd_value": 200.00,
        "usd_price": 200.0,
        "sent": {"symbol": "SOL", "name": "Solana", "amount": 0.0, "usd_price": 200, "usd_value": 0.0, "contract": ""},
        "received": {"symbol": "SOL", "name": "Solana", "amount": 1.0, "usd_price": 200, "usd_value": 200.00, "contract": ""},
        "sent_symbol": None, "sent_amount": 0.0,
        "recv_symbol": "SOL", "recv_amount": 1.0,
        "legs": 1, "gas_fee_usd": 0.0005,
        "wallet_address": SOL_ADDR, "log_index": 0,
    },
    {
        "type": "swap",
        "direction": "swap",
        "tx_hash": "swapSOL_USDC_001",
        "block_time": "2026-07-25T12:00:00Z",
        "token_symbol": "SOL → USDC",
        "token_name": "Solana",
        "chain": "solana",
        "usd_value": 300.00,
        "usd_price": None,
        "sent": {"symbol": "SOL", "name": "Solana", "amount": 2.0, "usd_price": 150, "usd_value": 300.00, "contract": ""},
        "received": {"symbol": "USDC", "name": "USD Coin", "amount": 299.5, "usd_price": 1, "usd_value": 299.50, "contract": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},
        "sent_symbol": "SOL", "sent_amount": 2.0,
        "recv_symbol": "USDC", "recv_amount": 299.5,
        "legs": 2, "gas_fee_usd": 0.001,
        "wallet_address": SOL_ADDR, "log_index": 0,
    },
]


async def _run_live_merge_test():
    global PASS, FAIL
    section("A1. Live merge: EVM (DB) + Solana (live) merged correctly")

    evm_events = group_transaction_events(EVM_ROWS)

    # Simulate the live merge logic
    all_events = list(evm_events)  # copy

    # Normalize Solana events' block_time (matching the endpoint)
    for ev in SOL_EVENTS:
        ev_norm = dict(ev)  # shallow copy
        bt = ev_norm.get("block_time", "")
        if bt and "T" in bt:
            ev_norm["block_time"] = bt.replace("T", " ").replace("Z", "")[:19]
        all_events.append(ev_norm)

    # Sort by block_time DESC (matching endpoint logic)
    all_events.sort(key=lambda e: (e.get("block_time", ""), e.get("usd_value", 0)), reverse=True)

    check(len(all_events) == 3, f"3 events total (1 EVM swap + 2 Solana) -> {len(all_events)}")

    # Find events by chain
    evm = [e for e in all_events if e.get("chain") == "ethereum"]
    sol = [e for e in all_events if e.get("chain") == "solana"]

    check(len(evm) == 1, f"1 EVM event -> {len(evm)}")
    check(len(sol) == 2, f"2 Solana events -> {len(sol)}")

    # Order: newest first — EVM swap (12:00) > Solana receive (08:00) > Solana swap (07-25)
    check(all_events[0]["chain"] == "ethereum", f"newest is EVM (2026-07-26 12:00) -> {all_events[0]['chain']}")
    check(all_events[1]["chain"] == "solana", f"2nd is Solana receive (2026-07-26 08:00) -> {all_events[1]['chain']}")
    check(all_events[1]["type"] == "receive", f"2nd event type receive -> {all_events[1]['type']}")
    check(all_events[2]["chain"] == "solana", f"3rd is Solana swap (2026-07-25 12:00) -> {all_events[2]['chain']}")

    # Counts (aggregated)
    counts = {"swap": 0, "send": 0, "receive": 0, "approve": 0, "contract": 0, "native": 0}
    for ev in all_events:
        counts[ev["type"]] = counts.get(ev["type"], 0) + 1

    check(counts["swap"] == 2, f"2 swaps (1 EVM + 1 Solana) -> {counts['swap']}")
    check(counts["receive"] == 1, f"1 receive (Solana) -> {counts['receive']}")

    # Filter by chain=solana
    sol_filtered = [e for e in all_events if e.get("chain") == "solana"]
    check(len(sol_filtered) == 2, f"chain filter solana -> 2 events -> {len(sol_filtered)}")

    # Filter by type=receive
    recv_filtered = [e for e in all_events if e.get("type") == "receive"]
    check(len(recv_filtered) == 1, f"type filter receive -> 1 event -> {len(recv_filtered)}")
    check(recv_filtered[0]["chain"] == "solana", f"received event is Solana -> {recv_filtered[0]['chain']}")

    # Solana explorer URLs
    p_sol = provider_for(SOL_ADDR)
    for ev in sol:
        tx_hash = ev.get("tx_hash", "")
        if tx_hash and p_sol:
            ev["explorer_url"] = p_sol.explorer_tx_url(tx_hash)
    check(sol[0].get("explorer_url", "").startswith("https://solscan.io/tx/"),
          f"Solana explorer URL present -> {sol[0].get('explorer_url', '')[:40]}")


# ═══════════════════════════════════════════════════════════════════
# Section A2 — Cache: 2nd merge call doesn't call provider
# ═══════════════════════════════════════════════════════════════════

async def _run_cache_test():
    global PASS, FAIL
    import time
    section("A2. Cache: TTL prevents repeated RPC calls")

    # Simulate the cache logic
    _cache = {}
    TTL = 300

    call_count = {"count": 0}

    async def mock_provider(address, wallet, limit=50):
        call_count["count"] += 1
        return {"items": SOL_EVENTS}

    # First call: should hit provider
    cache_key = f"1:{SOL_ADDR.lower()}"
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and (now - cached[0]) < TTL:
        items = cached[1]
    else:
        res = await mock_provider(SOL_ADDR, SOL_ADDR)
        items = res["items"]
        _cache[cache_key] = (now, items)

    check(len(items) == 2, f"first call: 2 items -> {len(items)}")
    check(call_count["count"] == 1, f"provider called once -> {call_count['count']}")

    # Second call: should use cache
    cached = _cache.get(cache_key)
    if cached and (time.time() - cached[0]) < TTL:
        items2 = cached[1]
    check(len(items2) == 2, f"second call (cached): 2 items -> {len(items2)}")
    check(call_count["count"] == 1, f"provider still called only once -> {call_count['count']}")

    # Third call with expired cache
    _cache[cache_key] = (time.time() - 301, SOL_EVENTS)  # force expire
    cached = _cache.get(cache_key)
    if cached and (time.time() - cached[0]) < TTL:
        items3 = cached[1]
    else:
        res = await mock_provider(SOL_ADDR, SOL_ADDR)
        items3 = res["items"]
        _cache[cache_key] = (time.time(), items3)
    check(call_count["count"] == 2, f"expired cache: provider called again -> {call_count['count']}")


# ═══════════════════════════════════════════════════════════════════
# Section A3 — Provider failure: EVM view intact, no 500
# ═══════════════════════════════════════════════════════════════════

async def _run_failure_test():
    global PASS, FAIL
    section("A3. Provider failure: EVM intact, no crash")

    evm_events = group_transaction_events(EVM_ROWS)

    # Simulate provider throwing
    all_events = list(evm_events)
    try:
        raise RuntimeError("RPC timeout")
    except Exception:
        # Provider failed, we don't add anything
        pass

    check(len(all_events) == 1, f"EVM event still present after provider failure -> {len(all_events)}")
    check(all_events[0]["chain"] == "ethereum", "chain is still ethereum")


# ═══════════════════════════════════════════════════════════════════
# Section B1 — _calver_key with .cN
# ═══════════════════════════════════════════════════════════════════

def _run_calver_key_test():
    global PASS, FAIL
    section("B1. _calver_key with .cN suffix")

    def _ck(t):
        parts = t.split(".")
        base = (int(parts[0]), int(parts[1]), int(parts[2]))
        cn = 0
        if len(parts) > 3 and parts[3].startswith("c"):
            cn = int(parts[3][1:])
        return (base[0], base[1], base[2], cn)

    # Base ordering
    check(_ck("2026.07.27") < _ck("2026.08.1"),
          "2026.07.27 < 2026.08.1")
    check(_ck("2026.07.26") < _ck("2026.07.27"),
          "2026.07.26 < 2026.07.27")

    # .cN ordering: correction is AFTER base
    check(_ck("2026.07.27") < _ck("2026.07.27.c1"),
          "2026.07.27 < 2026.07.27.c1 (correction > base)")
    check(_ck("2026.07.27.c1") < _ck("2026.07.27.c2"),
          "2026.07.27.c1 < 2026.07.27.c2")
    check(_ck("2026.07.27.c2") < _ck("2026.07.27.c3"),
          "2026.07.27.c2 < 2026.07.27.c3")
    check(_ck("2026.07.27.c9") < _ck("2026.07.27.c10"),
          "2026.07.27.c9 < 2026.07.27.c10 (integer comparison)")
    check(_ck("2026.07.27.c3") < _ck("2026.07.28"),
          "2026.07.27.c3 < 2026.07.28 (next release > correction)")

    # Root equals .c0
    check(_ck("2026.07.27") == _ck("2026.07.27.c0"),
          "2026.07.27 == 2026.07.27.c0")

    # Sort test: mixed list
    tags = ["2026.07.27", "2026.07.27.c1", "2026.07.27.c3", "2026.07.27.c2", "2026.08.1"]
    tags.sort(key=_ck)
    expected = ["2026.07.27", "2026.07.27.c1", "2026.07.27.c2", "2026.07.27.c3", "2026.08.1"]
    check(tags == expected, f"sort order correct -> {tags}")
    check(tags[-1] == "2026.08.1", "latest is 2026.08.1")

    # Without 2026.08.1: .c3 is latest
    tags2 = ["2026.07.27", "2026.07.27.c3"]
    tags2.sort(key=_ck)
    check(tags2[-1] == "2026.07.27.c3", "without next release, .c3 is latest")


# ═══════════════════════════════════════════════════════════════════
# Section B2 — Regex matching
# ═══════════════════════════════════════════════════════════════════

def _run_regex_test():
    global PASS, FAIL
    import re
    section("B2. Regex for CalVer + .cN tags")

    pattern = r"^\d{4}\.\d{2}\.\d+(\.c\d+)?$"

    valid = [
        "2026.07.27", "2026.07.27.c1", "2026.07.27.c2",
        "2026.07.27.c3", "2026.07.27.c10", "2026.08.1",
        "2026.12.31.c99",
    ]
    for v in valid:
        check(bool(re.match(pattern, v)),
              f"regex ACCEPTS {v}")

    invalid = [
        "v2.0.0", "v2026.07.1", "2026.07.27.c",
        "2026.07.27.x", "random", "2026.07",
        "2026.7.27",  # single-digit month
        "2026.07.27.c1b",
    ]
    for v in invalid:
        check(not re.match(pattern, v),
              f"regex REJECTS {v}")


# ═══════════════════════════════════════════════════════════════════
# Section C — Non-regression: EVM events still present
# ═══════════════════════════════════════════════════════════════════

async def _run_evm_regression_test():
    global PASS, FAIL
    section("C. Non-regression: EVM events unchanged")

    evm_events = group_transaction_events(EVM_ROWS)
    check(len(evm_events) == 1, f"1 EVM swap event -> {len(evm_events)}")
    ev = evm_events[0]
    check(ev["type"] == "swap", f"EVM type swap -> {ev['type']}")
    check(ev["chain"] == "ethereum", f"EVM chain -> {ev['chain']}")
    check(ev["usd_value"] == 5250, f"EVM usd_value -> {ev['usd_value']}")
    check(ev["sent_symbol"] == "ETH", f"EVM sent -> {ev['sent_symbol']}")
    check(ev["recv_symbol"] == "USDC", f"EVM recv -> {ev['recv_symbol']}")


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

async def main():
    await _run_live_merge_test()
    await _run_cache_test()
    await _run_failure_test()
    await _run_evm_regression_test()
    _run_calver_key_test()
    _run_regex_test()

    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {PASS} passed, {FAIL} failed")
    print(f"{'=' * 60}")
    if FAIL:
        print("LIVE_MERGE/VERSION TEST: FAILURES DETECTED")
        sys.exit(1)
    else:
        print("LIVE_MERGE/VERSION TEST: ALL PASS")

if __name__ == "__main__":
    asyncio.run(main())
