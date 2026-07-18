#!/usr/bin/env python3
"""Tests unitaires du service analytics (2026.07.3) — stdlib only.

Run: python3 tests/test_analytics_service.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.analytics_service import (  # noqa: E402
    filter_active_tokens, build_allocation, compute_change_periods,
    compute_performers, pick_closest, pct_from_price_points,
)

FAILED = 0


def check(cond, label):
    global FAILED
    if cond:
        print(f"OK   {label}")
    else:
        print(f"FAIL {label}")
        FAILED += 1


# ── Jeu de données ────────────────────────────────────────────────
TOKENS = [
    {"symbol": "ETH", "chain": "ethereum", "category": "wallet",
     "usd_value": 600.0, "usd_price": 3000.0, "enabled": True},
    {"symbol": "ETH", "chain": "base", "category": "wallet",
     "usd_value": 150.0, "usd_price": 3000.0, "enabled": True},
    {"symbol": "USDC", "chain": "optimism", "category": "wallet",
     "usd_value": 200.0, "usd_price": 1.0, "enabled": True},
    {"symbol": "aEthUSDC", "chain": "ethereum", "category": "lending",
     "usd_value": 30.0, "usd_price": 1.0, "enabled": True},
    {"symbol": "wstETH", "chain": "arbitrum", "category": "staked",
     "usd_value": 20.0, "usd_price": 3500.0, "enabled": True},
    # inactif → exclu partout
    {"symbol": "SCAMX", "chain": "polygon", "category": "wallet",
     "usd_value": 999.0, "usd_price": 5.0, "enabled": False},
    # poussière → exclue des performers mais comptée dans l'allocation
    {"symbol": "DUST", "chain": "gnosis", "category": "wallet",
     "usd_value": 0.5, "usd_price": 0.001, "enabled": True},
    # valeur nulle → ignorée partout (allocation v<=0, performers)
    {"symbol": "ZERO", "chain": "celo", "category": "wallet",
     "usd_value": 0.0, "usd_price": 0.0, "enabled": True},
    # entrée non-dict → ignorée sans crash
    None,
]

# ── filter_active_tokens ─────────────────────────────────────────
active = filter_active_tokens(TOKENS)
check(len(active) == 7, "filter_active: 7 tokens actifs (SCAMX exclu, None exclu)")
check(all((tk.get("symbol") or "") != "SCAMX" for tk in active),
      "filter_active: le token désactivé est exclu")
check(filter_active_tokens(None) == [], "filter_active: None → []")

# ── build_allocation ─────────────────────────────────────────────
alloc = build_allocation(active)
total = alloc["total_usd"]
check(abs(total - 1000.5) < 0.01, f"allocation: total actif = 1000.5 (obtenu {total})")

chains = {c["key"]: c for c in alloc["by_chain"]}
check(abs(chains["ethereum"]["usd_value"] - 630.0) < 0.01,
      "allocation chaîne: ethereum = 600 + 30 (aEthUSDC)")
check(alloc["by_chain"][0]["key"] == "ethereum", "allocation chaîne: triée desc (ethereum #1)")
sum_chain_pct = sum(c["pct"] for c in alloc["by_chain"])
check(99.5 <= sum_chain_pct <= 100.5, f"allocation chaîne: somme pct ≈ 100 ({sum_chain_pct})")

cats = {c["key"]: c for c in alloc["by_category"]}
check(abs(cats["wallet"]["usd_value"] - 950.5) < 0.01, "allocation catégorie: wallet = 950.5")
check(abs(cats["lending"]["usd_value"] - 30.0) < 0.01, "allocation catégorie: lending = 30")
check(abs(cats["staked"]["usd_value"] - 20.0) < 0.01, "allocation catégorie: staked = 20")

assets = {a["symbol"]: a for a in alloc["by_asset"]}
check(abs(assets["ETH"]["usd_value"] - 750.0) < 0.01,
      "allocation actif: ETH agrégé multi-chaînes = 750")
check(abs(assets["ETH"]["pct"] - 74.96) < 0.1, f"allocation actif: ETH ≈ 75% ({assets['ETH']['pct']})")
check("ZERO" not in assets, "allocation actif: valeur 0 exclue")

# top 12 + OTHERS
many = [{"symbol": f"T{i:02d}", "chain": "ethereum", "category": "wallet",
         "usd_value": 100.0 - i, "usd_price": 1.0, "enabled": True} for i in range(15)]
alloc15 = build_allocation(many)
check(len(alloc15["by_asset"]) == 13, "allocation actif: 15 tokens → top 12 + OTHERS")
check(alloc15["by_asset"][-1]["symbol"] == "OTHERS", "allocation actif: dernier = OTHERS")
others_v = alloc15["by_asset"][-1]["usd_value"]
check(abs(others_v - (88 + 87 + 86)) < 0.01, f"allocation actif: OTHERS = somme de la queue ({others_v})")

check(build_allocation([]) == {"total_usd": 0.0, "by_chain": [], "by_category": [], "by_asset": []},
      "allocation: liste vide → structure vide sans crash")

# ── pick_closest ─────────────────────────────────────────────────
rows = [("2026-07-01", 100.0), ("2026-07-10", 110.0), ("2026-07-18", 120.0)]
check(pick_closest(rows, "2026-07-12", 2) == 110.0, "pick_closest: date la plus proche dans la tolérance")
check(pick_closest(rows, "2026-06-20", 5) is None, "pick_closest: hors tolérance → None")
check(pick_closest([], "2026-07-12", 2) is None, "pick_closest: liste vide → None")
check(pick_closest(rows, "garbage", 2) is None, "pick_closest: target invalide → None")
check(pick_closest([("bad-date", 5.0), ("2026-07-11", 42.0)], "2026-07-12", 2) == 42.0,
      "pick_closest: ligne à date invalide ignorée")
check(pick_closest([("2026-07-12", float("nan"))], "2026-07-12", 1) is None,
      "pick_closest: NaN → None")

# ── compute_change_periods ───────────────────────────────────────
hist = [("2026-06-19", 800.0), ("2026-07-12", 900.0), ("2026-07-18", 980.0)]
chg = compute_change_periods(hist, 1000.0, today="2026-07-19")
check(chg["24h"] == {"abs_usd": 20.0, "pct": 2.04}, f"change 24h: +20 / +2.04% ({chg['24h']})")
check(chg["7d"] == {"abs_usd": 100.0, "pct": 11.11}, f"change 7d: +100 / +11.11% ({chg['7d']})")
check(chg["30d"] == {"abs_usd": 200.0, "pct": 25.0}, f"change 30d: +200 / +25% ({chg['30d']})")

chg2 = compute_change_periods([("2026-07-18", 1100.0)], 1000.0, today="2026-07-19")
check(chg2["24h"] == {"abs_usd": -100.0, "pct": -9.09}, "change: variation négative correcte")
check(chg2["7d"] is None and chg2["30d"] is None,
      "change: historique insuffisant → None (7d, 30d)")

chg3 = compute_change_periods([], 1000.0, today="2026-07-19")
check(chg3 == {"24h": None, "7d": None, "30d": None}, "change: aucun historique → 3× None")
chg4 = compute_change_periods(hist, None, today="2026-07-19")
check(chg4 == {"24h": None, "7d": None, "30d": None}, "change: valeur courante None → 3× None")
chg5 = compute_change_periods([("2026-07-12", 0.0), ("2026-07-18", 980.0)], 1000.0, today="2026-07-19")
check(chg5["7d"] is None, "change: valeur passée 0 → None (pas de division par zéro)")

# ── compute_performers ───────────────────────────────────────────
past = {"eth": 2500.0, "usdc": 1.0, "wsteth": 3900.0}
perf = compute_performers(active, past)
best_syms = [p["symbol"] for p in perf["best"]]
worst_syms = [p["symbol"] for p in perf["worst"]]
check(best_syms and best_syms[0] == "ETH", f"performers: ETH meilleur (+20%) ({best_syms})")
eth_entry = perf["best"][0]
check(abs(eth_entry["pct"] - 20.0) < 0.01, f"performers: ETH pct = +20 ({eth_entry['pct']})")
check(abs(eth_entry["usd_value"] - 750.0) < 0.01, "performers: ETH agrégé multi-chaînes (750)")
check(worst_syms == ["WSTETH"], f"performers: wstETH pire (-10.26%) ({worst_syms})")
check(abs(perf["worst"][0]["pct"] + 10.26) < 0.01, "performers: wstETH pct = -10.26")
check("USDC" not in best_syms + worst_syms, "performers: stable à 0% ni gagnant ni perdant")
check("DUST" not in best_syms + worst_syms, "performers: poussière (<1$) ignorée")
check("AETHUSDC" not in best_syms + worst_syms, "performers: token sans prix passé ignoré")

perf2 = compute_performers(active, {})
check(perf2 == {"best": [], "worst": []}, "performers: aucun prix passé → listes vides")
perf3 = compute_performers([], past)
check(perf3 == {"best": [], "worst": []}, "performers: aucun token → listes vides")

# top 5 cap
manyp = [{"symbol": f"P{i}", "chain": "ethereum", "category": "wallet",
          "usd_value": 50.0, "usd_price": 100.0 + i * 10, "enabled": True} for i in range(8)]
pastp = {f"p{i}": 100.0 for i in range(8)}
perf4 = compute_performers(manyp, pastp)
check(len(perf4["best"]) == 5, "performers: top 5 gagnants max")
check(perf4["best"][0]["symbol"] == "P7", "performers: tri desc sur pct")

# ── pct_from_price_points ────────────────────────────────────────
pts = [{"timestamp": 1, "price": 100.0}, {"timestamp": 2, "price": 105.0},
       {"timestamp": 3, "price": 110.0}]
check(pct_from_price_points(pts) == 10.0, "benchmark: 100 → 110 = +10%")
check(pct_from_price_points([]) is None, "benchmark: série vide → None")
check(pct_from_price_points([{"price": 100.0}]) is None, "benchmark: 1 point → None")
check(pct_from_price_points([{"price": 0.0}, {"price": 5.0}]) is None,
      "benchmark: premier prix 0 → None")
check(pct_from_price_points([{"price": None}, {"price": 5.0}]) is None,
      "benchmark: prix None → None")

# ── Résultat ─────────────────────────────────────────────────────
print()
if FAILED:
    print(f"❌ {FAILED} test(s) en échec")
    sys.exit(1)
print("✅ Tous les tests analytics passent")
