"""Test CosmosProvider.get_transactions() with a real Osmosis address.

Uses the live Polkachu LCD REST endpoint.
"""
import asyncio
import sys
import os

# Path setup
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import logging
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s:%(name)s:%(message)s')

from services.providers.cosmos import CosmosProvider


async def main():
    provider = CosmosProvider()

    # ── Test 1: Detection ─────────────────────────────────────
    print("=" * 60)
    print("TEST 1: Detection")
    assert provider.detect("cosmos1q2fatzv5sgyylu3kut3xr7mc0ysjx3s9c3a0mm"), "Should detect cosmos1"
    assert provider.detect("osmo19ce3d285j37fvdm277qlvw4sth2j7cwapjk6sc"), "Should detect osmo1"
    assert not provider.detect("0x1234567890123456789012345678901234567890"), "Should reject EVM"
    assert not provider.detect("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"), "Should reject BTC"
    print("  PASSED")

    # ── Test 2: Real Osmosis address — outgoing txs ─────────────
    print("=" * 60)
    print("TEST 2: Real Osmosis address transactions")
    addr = "osmo19ce3d285j37fvdm277qlvw4sth2j7cwapjk6sc"
    result = await provider.get_transactions(address=addr, limit=10)

    assert "total" in result, f"Missing 'total' in {result.keys()}"
    assert "items" in result, f"Missing 'items'"
    assert "counts" in result, f"Missing 'counts'"
    assert isinstance(result["items"], list), f"items should be list"
    print(f"  Total events: {result['total']}")
    print(f"  Counts: {result['counts']}")

    items = result["items"]
    if len(items) == 0:
        print("  WARNING: 0 events returned (address may have no MsgSend txs)")
        print("  This is OK — best-effort mode")
    else:
        print(f"  First {min(5, len(items))} events:")
        for ev in items[:5]:
            print(f"    ---")
            print(f"    type={ev.get('type')} direction={ev.get('direction')}")
            print(f"    tx_hash={ev.get('tx_hash','?')[:20]}...")
            print(f"    block_time={ev.get('block_time')}")
            print(f"    token_symbol={ev.get('token_symbol')}")
            print(f"    amount={ev.get('amount')}")
            print(f"    sent_amount={ev.get('sent_amount')}")
            print(f"    recv_amount={ev.get('recv_amount')}")
            print(f"    usd_value={ev.get('usd_value')}")
            print(f"    chain={ev.get('chain')}")
            print(f"    wallet_address={ev.get('wallet_address','?')[:20]}...")
            print(f"    sent_symbol={ev.get('sent_symbol')}")
            print(f"    recv_symbol={ev.get('recv_symbol')}")
            print(f"    legs={ev.get('legs')}")
            print(f"    gas_fee_usd={ev.get('gas_fee_usd')}")
            print(f"    log_index={ev.get('log_index')}")
            # Check for sent/received dicts
            sent = ev.get('sent', {})
            recv = ev.get('received', {})
            print(f"    sent={ {k:sent.get(k) for k in ('symbol','amount','usd_price','usd_value','contract') if k in sent} }")
            print(f"    received={ {k:recv.get(k) for k in ('symbol','amount','usd_price','usd_value','contract') if k in recv} }")

        print(f"\n  ── EVENT SHAPE VALIDATION ──")
        required_keys = [
            "type", "direction", "tx_hash", "block_time", "token_symbol",
            "token_name", "chain", "amount", "usd_value", "usd_price",
            "sent", "sent_symbol", "sent_amount",
            "received", "recv_symbol", "recv_amount",
            "legs", "gas_fee_usd", "wallet_address", "log_index",
        ]
        for i, ev in enumerate(items[:5]):
            for key in required_keys:
                assert key in ev, f"Event {i} missing key: {key}"
        print("  All required keys present ✓")

        # ── Root amount check ──────────────────────────────────
        for ev in items[:5]:
            etype = ev.get("type")
            if etype == "send":
                assert ev["amount"] > 0, f"send should have amount > 0, got {ev['amount']}"
                assert ev["amount"] == ev["sent_amount"], f"send amount mismatch: {ev['amount']} != {ev['sent_amount']}"
                print(f"  ✓ send amount={ev['amount']} (sent_amount={ev['sent_amount']})")
            elif etype == "receive":
                assert ev["amount"] > 0, f"receive should have amount > 0, got {ev['amount']}"
                assert ev["amount"] == ev["recv_amount"], f"receive amount mismatch: {ev['amount']} != {ev['recv_amount']}"
                print(f"  ✓ receive amount={ev['amount']} (recv_amount={ev['recv_amount']})")
            elif etype == "swap":
                print(f"  ✓ swap amount={ev['amount']}")

        # ── Chain format ───────────────────────────────────────
        for ev in items[:3]:
            assert ev["chain"] == "cosmos:osmo", f"Expected cosmos:osmo, got {ev['chain']}"
        print("  Chain format correct ✓")

    # ── Test 3: Address with incoming txs ───────────────────────
    print("=" * 60)
    print("TEST 3: Real Osmosis address — incoming txs")
    addr_recv = "osmo129uhlqcsvmehxgzcsdxksnsyz94dvea907e575"
    result2 = await provider.get_transactions(address=addr_recv, limit=10)
    print(f"  Total events: {result2['total']}, counts: {result2['counts']}")
    receives = [e for e in result2.get("items", []) if e.get("type") == "receive"]
    print(f"  Receive events: {len(receives)}")
    if receives:
        for ev in receives[:3]:
            print(f"    amount={ev.get('amount')} recv_amount={ev.get('recv_amount')} from={ev.get('sent_symbol')}")
            assert ev["amount"] == ev["recv_amount"], f"receive amount mismatch"
        print("  Receive amount check ✓")

    # ── Test 4: Filters ────────────────────────────────────────
    print("=" * 60)
    print("TEST 4: Filters")
    if items:
        r_dir = await provider.get_transactions(address=addr, limit=10, direction="out")
        print(f"  direction=out: {r_dir['total']} events")
        for e in r_dir.get("items", []):
            assert e["direction"] == "out", f"Expected out, got {e['direction']}"

        r_type = await provider.get_transactions(address=addr, limit=10, event_type="send")
        print(f"  type=send: {r_type['total']} events")
        for e in r_type.get("items", []):
            t = e["type"]
            assert t == "send", f"Expected send, got {t}"

        # Chain filter: should return empty for non-matching chain
        r_wrong_chain = await provider.get_transactions(address=addr, limit=10, chain="ethereum")
        assert r_wrong_chain["total"] == 0, f"Expected 0 for ethereum chain filter, got {r_wrong_chain['total']}"
        print("  Chain filter ✓")

    # ── Test 5: Portfolio not broken ───────────────────────────
    print("=" * 60)
    print("TEST 5: get_portfolio still works")
    pf = await provider.get_portfolio(addr)
    assert "total_usd" in pf or pf.get("supported") is False, f"Portfolio broken: {list(pf.keys())}"
    print(f"  Portfolio OK: total_usd={pf.get('total_usd', 'N/A')}")

    print()
    print("=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
