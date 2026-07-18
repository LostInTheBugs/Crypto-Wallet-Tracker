#!/usr/bin/env python3
"""Smoke-test v2.12.4 — regroupement des transferts en événements swap/send/receive.

Pur stdlib : n'importe QUE src/services/tx_events.py (aucune dépendance FastAPI).
Usage : python3 tests/test_swap_grouping.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from services.tx_events import group_transaction_events, filter_events  # noqa: E402

FAILURES = 0


def check(cond, label):
    global FAILURES
    if cond:
        print("OK  " + label)
    else:
        print("FAIL " + label)
        FAILURES += 1


W1 = "0xAAAA111122223333444455556666777788889999"
W2 = "0xBBBB111122223333444455556666777788889999"


def row(**kw):
    base = {
        "id": kw.get("id", 0), "wallet_address": W1, "token_symbol": "ETH",
        "token_name": "Ether", "amount": 1.0, "usd_price": 3700.0, "usd_value": 3700.0,
        "chain": "ethereum", "tx_hash": "0xh1", "block_time": "2026-07-15 10:00:00",
        "direction": "in", "log_index": 0, "gas_fee_usd": 0.0, "contract_address": "0xc0",
    }
    base.update(kw)
    return base


rows = [
    # txSWAP : 1 out (ETH) + 1 in (USDC) même hash/wallet/chain → SWAP.
    # Gaz stocké sur UNE seule jambe (2.5) ; block_time in > out (le max doit gagner).
    row(id=1, tx_hash="0xswap", direction="out", token_symbol="ETH", token_name="Ether",
        amount=1.2, usd_price=3700, usd_value=4440, gas_fee_usd=2.5,
        block_time="2026-07-15 10:00:00", log_index=3),
    row(id=2, tx_hash="0xswap", direction="in", token_symbol="USDC", token_name="USD Coin",
        amount=4435, usd_price=1, usd_value=4435, gas_fee_usd=0,
        block_time="2026-07-15 10:00:05", log_index=7, contract_address="0xusdc"),
    # txSEND : uniquement out → SEND.
    row(id=3, tx_hash="0xsend", direction="out", token_symbol="AERO", token_name="Aerodrome",
        amount=50, usd_price=0.9, usd_value=45, gas_fee_usd=0.1,
        block_time="2026-07-16 08:00:00"),
    # txRECV : uniquement in → RECEIVE.
    row(id=4, tx_hash="0xrecv", direction="in", token_symbol="WBTC", token_name="Wrapped BTC",
        amount=0.01, usd_price=95000, usd_value=950, block_time="2026-07-17 09:00:00"),
    # txMULTI : swap multi-jambes 2 out + 1 in ; jambe principale out = WETH (usd_value max).
    row(id=5, tx_hash="0xmulti", direction="out", token_symbol="WETH", token_name="Wrapped Ether",
        amount=0.27, usd_price=3700, usd_value=1000, block_time="2026-07-14 12:00:00"),
    row(id=6, tx_hash="0xmulti", direction="out", token_symbol="AERO", token_name="Aerodrome",
        amount=55, usd_price=0.9, usd_value=50, block_time="2026-07-14 12:00:00"),
    row(id=7, tx_hash="0xmulti", direction="in", token_symbol="USDC", token_name="USD Coin",
        amount=1040, usd_price=1, usd_value=1040, gas_fee_usd=1.1,
        block_time="2026-07-14 12:00:00"),
    # Même hash, DEUX wallets du même user : W1 out + W2 in → PAS un swap
    # (le swap est défini POUR UN wallet) → 1 send (W1) + 1 receive (W2).
    row(id=8, tx_hash="0xself", direction="out", wallet_address=W1, token_symbol="DAI",
        token_name="Dai", amount=100, usd_price=1, usd_value=100,
        block_time="2026-07-13 07:00:00"),
    row(id=9, tx_hash="0xself", direction="in", wallet_address=W2, token_symbol="DAI",
        token_name="Dai", amount=100, usd_price=1, usd_value=100,
        block_time="2026-07-13 07:00:00"),
    # Deux lignes SANS hash → jamais regroupées entre elles (2 événements distincts).
    row(id=10, tx_hash="", direction="in", token_symbol="ZZZ", token_name="Zzz",
        amount=7, usd_price=0, usd_value=0, block_time=""),
    row(id=11, tx_hash="", direction="out", token_symbol="YYY", token_name="Yyy",
        amount=3, usd_price=0, usd_value=0, block_time=""),
]

events = group_transaction_events(rows)
by_hash = {}
for e in events:
    by_hash.setdefault(e["tx_hash"], []).append(e)

check(len(events) == 8, "8 événements pour 11 lignes (11 -> 8: 2 swaps fusionnés, 2 sans hash séparés) -> %d" % len(events))

# ── Classement swap/send/receive ─────────────────────────────────
sw = by_hash["0xswap"][0]
check(sw["type"] == "swap" and sw["direction"] == "swap", "0xswap: type=swap")
check(sw["sent_symbol"] == "ETH" and sw["sent_amount"] == 1.2, "0xswap: jambe sortante principale ETH 1.2")
check(sw["recv_symbol"] == "USDC" and sw["recv_amount"] == 4435, "0xswap: jambe entrante principale USDC 4435")
check(sw["usd_value"] == 4440, "0xswap: usd_value = max(out,in) = 4440 (pas de double comptage)")
check(sw["gas_fee_usd"] == 2.5, "0xswap: gaz compté UNE fois = 2.5")
check(sw["block_time"] == "2026-07-15 10:00:05", "0xswap: block_time = jambe la plus récente")
check(len(sw["sent"]) == 1 and len(sw["received"]) == 1 and sw["legs"] == 2, "0xswap: jambes exposées (1 out, 1 in)")
check(sw["received"][0]["contract"] == "0xusdc", "0xswap: contract présent dans la jambe")
check(sw["usd_price"] is None, "0xswap: usd_price=None (pas de prix unique pour un swap)")

se = by_hash["0xsend"][0]
check(se["type"] == "send" and se["direction"] == "out", "0xsend: type=send (uniquement out)")
check(se["token_symbol"] == "AERO" and se["amount"] == 50 and se["usd_value"] == 45, "0xsend: résumé jambe principale")

re_ = by_hash["0xrecv"][0]
check(re_["type"] == "receive" and re_["direction"] == "in", "0xrecv: type=receive (uniquement in)")
check(re_["recv_symbol"] == "WBTC" and re_["sent_symbol"] is None, "0xrecv: recv_symbol=WBTC, sent_symbol=None")

mu = by_hash["0xmulti"][0]
check(mu["type"] == "swap" and mu["legs"] == 3, "0xmulti: swap multi-jambes (3 jambes)")
check(mu["sent_symbol"] == "WETH", "0xmulti: jambe sortante principale = WETH (plus grosse usd_value)")
check(mu["usd_value"] == 1050, "0xmulti: usd_value = max(1050, 1040) = 1050")
check(mu["token_symbol"] == "WETH → USDC", "0xmulti: token_symbol résumé 'WETH → USDC'")

selfs = by_hash["0xself"]
check(len(selfs) == 2 and {e["type"] for e in selfs} == {"send", "receive"},
      "0xself: 2 wallets même hash -> send + receive, PAS de swap (déf. par wallet)")

nohash = [e for e in events if e["tx_hash"] == ""]
check(len(nohash) == 2, "lignes sans hash: jamais fusionnées (2 événements)")

# ── Tri (block_time DESC, vides en fin) ──────────────────────────
times = [e["block_time"] for e in events]
non_empty = [x for x in times if x]
check(non_empty == sorted(non_empty, reverse=True), "tri: block_time DESC")
check(times[-1] == "" and times[-2] == "", "tri: block_time vides en fin de liste")

# ── Filtres ──────────────────────────────────────────────────────
check([e["tx_hash"] for e in filter_events(events, event_type="swap")] == ["0xswap", "0xmulti"],
      "filtre type=swap -> exactement les 2 swaps")
check(len(filter_events(events, event_type="send")) == 3, "filtre type=send -> 3 (0xsend + 0xself/W1 + YYY sans hash)")
check(len(filter_events(events, event_type="receive")) == 3, "filtre type=receive -> 3 (0xrecv + 0xself/W2 + ZZZ sans hash)")
check(len(filter_events(events, direction="in")) == 5, "rétro-compat direction=in -> 3 receives + 2 swaps = 5")
check(len(filter_events(events, direction="out")) == 5, "rétro-compat direction=out -> 3 sends + 2 swaps = 5")
tok = filter_events(events, token="usdc")
check([e["tx_hash"] for e in tok] == ["0xswap", "0xmulti"], "filtre token=usdc matche les JAMBES des swaps")

# ── Pagination sur les ÉVÉNEMENTS (jambes d'un swap jamais séparées) ──
page0, page1 = events[0:3], events[3:6]
check(len(page0) == 3 and all("sent" in e and "received" in e for e in page0),
      "pagination: slice d'événements complets (jambes toujours ensemble)")
check(not [e for e in page0 + page1 if e["type"] == "swap" and (not e["sent"] or not e["received"])],
      "pagination: aucun swap amputé d'une jambe")

print()
if FAILURES:
    print("SMOKE-TEST BACKEND: %d FAILURE(S)" % FAILURES)
    sys.exit(1)
print("SMOKE-TEST BACKEND: ALL PASS")
