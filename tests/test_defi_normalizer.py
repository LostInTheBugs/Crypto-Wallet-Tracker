#!/usr/bin/env python3
"""Test v2.12.8 — normaliseur de positions DeFi Moralis.

Pur stdlib : n'importe QUE src/services/defi_service.py (aucune dépendance
FastAPI/httpx). Alimente le normaliseur avec le JSON d'exemple type Moralis
(Aave V3 lending + Lido staking) et vérifie le mapping supplied/borrowed/
rewards, les sommes, le net, health factor, APY et les liens.

Usage : python3 tests/test_defi_normalizer.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from services.defi_service import (  # noqa: E402
    normalize_defi_positions, normalize_defi_position,
    summarize_defi_positions, classify_token_type, CHAIN_EXPLORERS,
)

FAILURES = 0


def check(cond, label):
    global FAILURES
    if cond:
        print("OK  " + label)
    else:
        print("FAIL " + label)
        FAILURES += 1


# ── JSON d'exemple (structure type Moralis /wallets/{addr}/defi/positions) ──
SAMPLE = [
    {"protocol_name": "Aave V3", "protocol_id": "aave-v3",
     "protocol_url": "https://app.aave.com",
     "protocol_logo": "https://cdn.moralis.io/defi/aave.png",
     "position": {
         "label": "Lending", "balance_usd": 1234.5,
         "total_unclaimed_usd_value": 5.0,
         "tokens": [
             {"token_type": "supplied", "symbol": "USDC", "name": "USD Coin",
              "balance_formatted": "2000.0", "usd_value": 2000.0, "contract_address": "0xusdc"},
             {"token_type": "borrowed", "symbol": "WETH",
              "balance_formatted": "0.3", "usd_value": 765.5, "contract_address": "0xweth"},
             {"token_type": "reward", "symbol": "AAVE",
              "balance_formatted": "0.1", "usd_value": 5.0, "contract_address": "0xaave"},
         ],
         "position_details": {"health_factor": 1.85, "apy": 3.1},
     }},
    {"protocol_name": "Lido", "protocol_id": "lido",
     "position": {
         "label": "Staking",
         "tokens": [{"token_type": "supplied", "symbol": "stETH",
                     "balance_formatted": "1.0", "usd_value": 2550.0}],
         "balance_usd": 2550.0,
     }},
]

print("=== Test normaliseur DeFi (Moralis) v2.12.8 ===")

positions = normalize_defi_positions(SAMPLE, chain="eth")
check(len(positions) == 2, "2 positions normalisées")

# ── Aave V3 ──────────────────────────────────────────────────────
aave = positions[0]
check(aave["protocol"] == "Aave V3", "Aave: protocol name")
check(aave["protocol_id"] == "aave-v3", "Aave: protocol_id")
check(aave["chain"] == "eth", "Aave: chain=eth")
check(aave["type"] == "lending", "Aave: type=lending")
check(len(aave["supplied"]) == 1 and aave["supplied"][0]["symbol"] == "USDC"
      and aave["supplied"][0]["amount"] == 2000.0, "Aave: 1 token fourni (2000 USDC)")
check(len(aave["borrowed"]) == 1 and aave["borrowed"][0]["symbol"] == "WETH"
      and aave["borrowed"][0]["amount"] == 0.3, "Aave: 1 token emprunté (0.3 WETH)")
check(len(aave["rewards"]) == 1 and aave["rewards"][0]["symbol"] == "AAVE",
      "Aave: 1 token reward (AAVE)")
check(aave["supplied_usd"] == 2000.0, "Aave: supplied_usd=2000")
check(aave["borrowed_usd"] == 765.5, "Aave: borrowed_usd=765.5")
check(aave["rewards_usd"] == 5.0, "Aave: rewards_usd=5")
check(aave["net_usd"] == round(2000.0 - 765.5 + 5.0, 2),
      "Aave: net_usd cohérent (supplied-borrowed+rewards=1239.5)")
check(aave["health_factor"] == 1.85, "Aave: health_factor=1.85")
check(aave["apy"] == 3.1, "Aave: apy=3.1")
check(aave["pnl"] is None, "Aave: pnl absent -> None")
check(aave["link"] == "https://app.aave.com", "Aave: link = protocol_url")
check(aave["protocol_logo"] == "https://cdn.moralis.io/defi/aave.png", "Aave: logo transmis")

# ── Lido ─────────────────────────────────────────────────────────
lido = positions[1]
check(lido["protocol"] == "Lido", "Lido: protocol name")
check(lido["type"] == "staking", "Lido: type=staking")
check(lido["supplied_usd"] == 2550.0, "Lido: supplied_usd=2550")
check(lido["borrowed_usd"] == 0.0 and lido["rewards_usd"] == 0.0,
      "Lido: borrowed=0, rewards=0")
check(lido["net_usd"] == 2550.0, "Lido: net_usd=2550")
check(lido["health_factor"] is None and lido["apy"] is None,
      "Lido: health_factor/apy absents -> None")
check(lido["link"] is None,
      "Lido: link=None (pas d'URL protocole ni d'adresse de contrat)")

# ── Summary global ───────────────────────────────────────────────
summary = summarize_defi_positions(positions)
check(summary["total_supplied_usd"] == 4550.0, "Summary: total_supplied=4550")
check(summary["total_borrowed_usd"] == 765.5, "Summary: total_borrowed=765.5")
check(summary["total_rewards_usd"] == 5.0, "Summary: total_rewards=5")
check(summary["net_usd"] == round(4550.0 - 765.5 + 5.0, 2), "Summary: net=3789.5")
check(summary["positions_count"] == 2, "Summary: positions_count=2")

# ── Classification token_type (mapping intelligent) ──────────────
check(classify_token_type("supplied") == "supplied", "classify: supplied")
check(classify_token_type("borrowed") == "borrowed", "classify: borrowed")
check(classify_token_type("variable_debt") == "borrowed", "classify: variable_debt -> borrowed")
check(classify_token_type("reward") == "rewards", "classify: reward")
check(classify_token_type("rewards") == "rewards", "classify: rewards")
check(classify_token_type("unclaimed") == "rewards", "classify: unclaimed -> rewards")
check(classify_token_type("defi-token") == "supplied", "classify: defi-token -> supplied (doute)")
check(classify_token_type(None) == "supplied", "classify: None -> supplied (doute)")
check(classify_token_type("") == "supplied", "classify: vide -> supplied (doute)")

# ── Cas défensifs ────────────────────────────────────────────────
check(normalize_defi_positions([]) == [], "liste vide -> []")
check(normalize_defi_positions(None) == [], "None -> []")
check(normalize_defi_positions("garbage") == [], "string -> []")
check(normalize_defi_positions({"result": SAMPLE}, chain="eth") and
      len(normalize_defi_positions({"result": SAMPLE}, chain="eth")) == 2,
      "wrapper dict {result: [...]} accepté")
check(normalize_defi_position(None) is None, "position non-dict -> None")

# Entrée minimale / champs manquants → ne plante pas
minimal = normalize_defi_position({"protocol_name": "X"}, chain="polygon")
check(minimal is not None and minimal["supplied_usd"] == 0
      and minimal["net_usd"] == 0 and minimal["link"] is None,
      "position sans tokens/label -> zéros, pas de crash")

# Valeurs pourries (None, str non numérique, NaN) → défensif
dirty = normalize_defi_position({
    "protocol_id": "weird",
    "position": {
        "label": None,
        "tokens": [
            {"token_type": "supplied", "symbol": None, "balance_formatted": "abc", "usd_value": None},
            "not-a-dict",
            {"token_type": "borrowed", "symbol": "DAI", "balance_formatted": None,
             "balance": "5000000000000000000", "decimals": "18", "usd_value": "5.0"},
        ],
        "position_details": {"health_factor": "not-a-number", "apy": None},
    }}, chain="eth")
assert dirty is not None
check(dirty["borrowed_usd"] == 5.0
      and dirty["borrowed"][0]["amount"] == 5.0,
      "tokens sales: fallback balance/decimals + usd_value str -> ok")
check(dirty["health_factor"] is None and dirty["apy"] is None,
      "position_details sales -> None, pas de crash")

# Rewards implicites via total_unclaimed_usd_value (pas de ligne reward)
implicit = normalize_defi_position({
    "protocol_name": "Compound",
    "position": {
        "label": "lending",
        "total_unclaimed_usd_value": 12.34,
        "tokens": [{"token_type": "supplied", "symbol": "USDT",
                    "balance_formatted": "100", "usd_value": 100.0}],
    }}, chain="eth")
assert implicit is not None
check(implicit["rewards_usd"] == 12.34 and implicit["rewards"] == [],
      "total_unclaimed sans ligne reward -> rewards_usd repris")

# Lien fallback explorer via l'adresse du contrat de position
expl = normalize_defi_position({
    "protocol_name": "NoUrl",
    "position": {
        "label": "liquidity",
        "address": "0xPOOL",
        "tokens": [{"token_type": "supplied", "symbol": "LP",
                    "balance_formatted": "1", "usd_value": 10.0}],
    }}, chain="polygon")
assert expl is not None
check(expl["link"] == CHAIN_EXPLORERS["polygon"] + "/address/0xPOOL",
      "link fallback = explorer + adresse position")

# Summary sur liste vide → zéros
empty_sum = summarize_defi_positions([])
check(empty_sum == {"total_supplied_usd": 0, "total_borrowed_usd": 0,
                    "total_rewards_usd": 0, "net_usd": 0, "positions_count": 0},
      "summary vide -> zéros")

print()
if FAILURES:
    print(f"ECHEC: {FAILURES} test(s) en échec")
    sys.exit(1)
print("Tous les tests du normaliseur DeFi passent.")
