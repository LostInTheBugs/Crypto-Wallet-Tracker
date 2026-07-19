"""Tests des fonctions pures (logique métier sans I/O).

Exécutable sans dépendance : `python tests/test_core.py`
(ou via pytest si installé : `pytest tests/`).
"""
import sys
import os
import math

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from services.portfolio_service import _is_spam, _token_category, format_snapshots_v2  # noqa: E402
from services.pnl_service import compute_pnl_from_rows, format_pnl_v2  # noqa: E402
from services.price_service import _interpolate_price  # noqa: E402
from services.token_prefs import token_tid, classify_token  # noqa: E402
from services.defi_service import classify_token_type as _defi_classify_token_type  # noqa: E402


def test_is_spam():
    assert _is_spam("visit claim.io to reward")
    assert _is_spam("airdrop")
    assert _is_spam("$ claim on: [ site.lol ]")
    assert not _is_spam("USDC")
    assert not _is_spam("ETH")
    assert not _is_spam(None)          # tolère None
    assert not _is_spam(123)           # tolère non-str


def test_token_category():
    assert _token_category("wsteth") == "staked"
    assert _token_category("aUSDC") == "lending"      # aToken Aave → lending
    assert _token_category("USDC") == "wallet"
    assert _token_category("ETH") == "wallet"
    assert _token_category(None) == "wallet"


def test_interpolate_price():
    prices = {1000: 10.0, 2000: 20.0, 3000: 30.0}
    assert _interpolate_price(prices, 2500) == 20.0   # dernier <= ts
    assert _interpolate_price(prices, 2000) == 20.0   # exact
    assert _interpolate_price(prices, 500) == 10.0    # avant le 1er -> plus ancien
    assert _interpolate_price(prices, 5000) == 30.0   # après le dernier -> dernier
    assert _interpolate_price({}, 1000) == 0.0        # vide


def test_compute_pnl():
    rows = [
        {"date": "2024-01-01", "value_usd": 100.0, "cost_basis_usd": 80.0, "net_flows_usd": 0.0},
        {"date": "2024-01-02", "value_usd": 120.0, "cost_basis_usd": 80.0, "net_flows_usd": 0.0},
    ]
    res = compute_pnl_from_rows(rows)
    assert res[0]["pnl"] == 20.0
    assert res[1]["pnl"] == 40.0
    assert res[1]["pnl_day"] == 20.0                  # 120 - 100 - 0
    assert res[0]["pnl_pct"] == 25.0                  # 20/80*100
    # division par zéro protégée
    z = compute_pnl_from_rows([{"date": "x", "value_usd": 10.0, "cost_basis_usd": 0.0, "net_flows_usd": 0.0}])
    assert z[0]["pnl_pct"] == 0.0
    # NaN neutralisé
    n = compute_pnl_from_rows([{"date": "x", "value_usd": float("nan"), "cost_basis_usd": 0.0, "net_flows_usd": 0.0}])
    assert n[0]["value"] == 0.0 and math.isfinite(n[0]["pnl"])


def test_format_snapshots_v2():
    out = format_snapshots_v2([{"date": "2024-01-01", "total_usd": 10.0},
                               {"date": "2024-01-02", "total_usd": 12.5}])
    assert out["labels"] == ["2024-01-01", "2024-01-02"]
    assert out["values"] == [10.0, 12.5]
    assert out["meta"]["points"] == 2
    empty = format_snapshots_v2([])
    assert empty["meta"]["points"] == 0 and empty["values"] == []


def test_format_pnl_v2():
    res = compute_pnl_from_rows([{"date": "2024-01-01", "value_usd": 100.0, "cost_basis_usd": 80.0, "net_flows_usd": 0.0}])
    out = format_pnl_v2(res)
    assert out["labels"] == ["2024-01-01"]
    assert out["values"] == [20.0]
    assert len(out["labels"]) == len(out["values"])   # invariant


# ── token_tid (2026.07.17) ────────────────────────────────────

def test_token_tid_with_contract():
    assert token_tid("USDC", "ethereum", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48") == "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def test_token_tid_fallback():
    assert token_tid("ETH", "ethereum", None) == "ethereum:eth"
    assert token_tid("MATIC", "polygon", "") == "polygon:matic"


def test_token_tid_native():
    assert token_tid("ETH", "", "") == ":eth"


# ── classify_token (2026.07.17) ───────────────────────────────

def test_classify_spam():
    en, reason = classify_token(100.0, 1.0, 100.0, None, is_spam=True)
    assert en == 0 and reason == "spam"


def test_classify_zero_value():
    en, reason = classify_token(0.0, 1.0, 100.0, None)
    assert en == 0 and reason == "zero_value"


def test_classify_no_price():
    en, reason = classify_token(0.0, 0.0, 100.0, None)
    assert en == 0 and reason == "zero_value"


def test_classify_low_confidence():
    en, reason = classify_token(100.0, 2.0, 50.0, 0.5)
    assert en == 0 and reason == "low_confidence"


def test_classify_memecoin():
    en, reason = classify_token(600.0, 0.00005, 20000000.0, 0.9)
    assert en == 0 and reason == "memecoin_pattern"


def test_classify_normal():
    en, reason = classify_token(1000.0, 50.0, 20.0, 0.95)
    assert en == 1 and reason == ""


def test_classify_barely_above_memecoin():
    # Price just above threshold → NOT memecoin
    en, reason = classify_token(600.0, 0.00011, 20000000.0, 0.9)
    assert en == 1 and reason == ""


def test_classify_non_finite_inputs():
    en, _ = classify_token(None, None, "abc", None)
    assert en == 1  # garbage input → keep enabled (conservative)


# ── DeFi token type classification (2026.07.17) ──────────────

def test_defi_classify_borrow():
    assert _defi_classify_token_type("borrowed") == "borrowed"
    assert _defi_classify_token_type("debt") == "borrowed"
    assert _defi_classify_token_type("DebtToken") == "borrowed"


def test_defi_classify_rewards():
    assert _defi_classify_token_type("reward") == "rewards"
    assert _defi_classify_token_type("rewards") == "rewards"
    assert _defi_classify_token_type("unclaimed") == "rewards"
    assert _defi_classify_token_type("claimable") == "rewards"


def test_defi_classify_supplied_default():
    assert _defi_classify_token_type("supplied") == "supplied"
    assert _defi_classify_token_type("staked") == "supplied"
    assert _defi_classify_token_type("defi-token") == "supplied"
    assert _defi_classify_token_type("unknown") == "supplied"
    assert _defi_classify_token_type("") == "supplied"
    assert _defi_classify_token_type(None) == "supplied"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in tests:
        fn()
        print("PASS", fn.__name__)
        passed += 1
    print(f"\n{passed}/{len(tests)} tests OK")
