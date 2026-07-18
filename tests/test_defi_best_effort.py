#!/usr/bin/env python3
"""Test v2.12.9 — builder best-effort de positions DeFi (fallback GRATUIT sans Moralis).

Pur stdlib : n'importe QUE src/services/defi_service.py. Alimente
build_best_effort_positions avec une liste de tokens type _compute_portfolio
(un aToken, un variableDebt, un stETH, un LP, un vault Beefy, un spam à
ignorer, un token désactivé, un token sans valeur) et vérifie :
supplied/borrowed/staking, net (dette en NÉGATIF), rewards vides,
apy/health_factor/pnl = null, liens explorer, summary cohérent.

Usage : python3 tests/test_defi_best_effort.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from services.defi_service import (  # noqa: E402
    build_best_effort_positions, classify_best_effort_token,
    summarize_defi_positions, BEST_EFFORT_SOURCE,
)

FAILURES = 0


def check(cond, label):
    global FAILURES
    if cond:
        print("OK  " + label)
    else:
        print("FAIL " + label)
        FAILURES += 1


def fake_is_spam(sym):
    if not sym or not isinstance(sym, str):
        return False
    s = sym.lower()
    return "visit" in s or "claim" in s or "http" in s


EXPLORERS = {"ethereum": "eth.blockscout.com", "base": "base.blockscout.com",
             "optimism": "explorer.optimism.io"}

# ── Jeu de tokens type _compute_portfolio (symbol/balance/usd_value/…) ──
TOKENS = [
    # Aave v3 supplied (aToken avec infix chaîne)
    {"symbol": "aEthUSDC", "balance": 1500.0, "usd_value": 1500.0, "usd_price": 1.0,
     "contract_address": "0xAAVE1", "chain": "ethereum", "category": "lending", "enabled": True},
    # Aave DETTE (variableDebt) — doit compter en NÉGATIF
    {"symbol": "variableDebtEthUSDC", "balance": 400.0, "usd_value": 400.0, "usd_price": 1.0,
     "contract_address": "0xDEBT1", "chain": "ethereum", "enabled": True},
    # Lido staking
    {"symbol": "wstETH", "balance": 1.2, "usd_value": 3600.0, "usd_price": 3000.0,
     "contract_address": "0xWSTETH", "chain": "ethereum", "enabled": True},
    # Compound supplied
    {"symbol": "cUSDC", "balance": 200.0, "usd_value": 200.0, "usd_price": 1.0,
     "contract_address": "0xCUSDC", "chain": "base", "enabled": True},
    # LP token
    {"symbol": "UNI-V2", "balance": 3.0, "usd_value": 90.0, "usd_price": 30.0,
     "contract_address": "0xLP1", "chain": "optimism", "enabled": True},
    # Vault Beefy (majuscule après moo — pitfall 121)
    {"symbol": "mooVeloUSDC", "balance": 10.0, "usd_value": 55.0, "usd_price": 5.5,
     "contract_address": "0xMOO1", "chain": "optimism", "enabled": True},
    # SPAM — à ignorer même si le symbole ressemble à un aToken
    {"symbol": "aUSDC visit http://evil.xyz claim", "balance": 999999.0, "usd_value": 999999.0,
     "usd_price": 1.0, "contract_address": "0xSPAM", "chain": "ethereum", "enabled": True},
    # Token désactivé par l'utilisateur — à ignorer
    {"symbol": "aUSDT", "balance": 50.0, "usd_value": 50.0, "usd_price": 1.0,
     "contract_address": "0xOFF", "chain": "ethereum", "enabled": False},
    # Valeur nulle — à ignorer
    {"symbol": "aWETH", "balance": 0.5, "usd_value": 0.0, "usd_price": 0.0,
     "contract_address": "0xZERO", "chain": "ethereum", "enabled": True},
    # Token wallet normal — PAS une position DeFi
    {"symbol": "USDC", "balance": 100.0, "usd_value": 100.0, "usd_price": 1.0,
     "contract_address": "0xUSDC", "chain": "ethereum", "enabled": True},
]

positions = build_best_effort_positions(TOKENS, explorer_hosts=EXPLORERS, is_spam=fake_is_spam)

# ── Classification unitaire ─────────────────────────────────────────
c = classify_best_effort_token("aEthUSDC")
check(c and c["bucket"] == "supplied" and c["type"] == "lending" and c["protocol"] == "Aave",
      "classify: aEthUSDC → supplied/lending/Aave (infix v3)")
c = classify_best_effort_token("aUSDC")
check(c and c["bucket"] == "supplied" and c["protocol"] == "Aave", "classify: aUSDC → Aave supplied")
c = classify_best_effort_token("variableDebtEthUSDC")
check(c and c["bucket"] == "borrowed" and c["type"] == "lending" and c["protocol"] == "Aave",
      "classify: variableDebtEthUSDC → borrowed/lending/Aave")
c = classify_best_effort_token("stableDebtPolWMATIC")
check(c and c["bucket"] == "borrowed", "classify: stableDebtPolWMATIC → borrowed")
c = classify_best_effort_token("stETH")
check(c and c["type"] == "staking" and c["protocol"] == "Lido", "classify: stETH → staking/Lido")
c = classify_best_effort_token("rETH")
check(c and c["protocol"] == "Rocket Pool", "classify: rETH → Rocket Pool")
c = classify_best_effort_token("cbETH")
check(c and c["protocol"] == "Coinbase", "classify: cbETH → Coinbase")
c = classify_best_effort_token("cUSDCv3")
check(c and c["protocol"] == "Compound", "classify: cUSDCv3 → Compound")
c = classify_best_effort_token("mooBIFI")
check(c and c["type"] == "vault" and c["protocol"] == "Beefy", "classify: mooBIFI → vault/Beefy")
c = classify_best_effort_token("yvUSDC")
check(c and c["protocol"] == "Yearn", "classify: yvUSDC → vault/Yearn")
c = classify_best_effort_token("S*USDC")
check(c and c["protocol"] == "Stargate", "classify: S*USDC → vault/Stargate")
c = classify_best_effort_token("vAMM-WETH/USDC")
check(c and c["type"] == "liquidity", "classify: vAMM-… → liquidity")
c = classify_best_effort_token("3CRV")
check(c and c["type"] == "liquidity", "classify: 3CRV → liquidity")
# Conservateur : PAS de faux positifs
check(classify_best_effort_token("USDC") is None, "classify: USDC → None (wallet)")
check(classify_best_effort_token("AAVE") is None, "classify: AAVE (le token) → None")
check(classify_best_effort_token("ARB") is None, "classify: ARB → None")
check(classify_best_effort_token("MOON") is None, "classify: MOON → None (pas Beefy)")
check(classify_best_effort_token("CRV") is None, "classify: CRV seul → None")
check(classify_best_effort_token("CAKE") is None, "classify: CAKE → None")
check(classify_best_effort_token(None) is None, "classify: None → None")
check(classify_best_effort_token("") is None, "classify: '' → None")
check(classify_best_effort_token(123) is None, "classify: non-str → None")

# ── Structure des positions ─────────────────────────────────────────
check(isinstance(positions, list) and len(positions) == 5,
      f"builder: 5 positions (Aave, Lido, Compound, LP, Beefy) — obtenu {len(positions)}")

by_proto = {}
for p in positions:
    by_proto[(p["protocol"], p["chain"])] = p

aave = by_proto.get(("Aave", "ethereum"))
check(aave is not None, "builder: position Aave/ethereum présente")
if aave:
    check(aave["type"] == "lending", "Aave: type lending")
    check(len(aave["supplied"]) == 1 and aave["supplied"][0]["symbol"] == "aEthUSDC",
          "Aave: supplied contient aEthUSDC")
    check(len(aave["borrowed"]) == 1 and aave["borrowed"][0]["symbol"] == "variableDebtEthUSDC",
          "Aave: borrowed contient variableDebtEthUSDC")
    check(aave["supplied_usd"] == 1500.0, "Aave: supplied_usd = 1500")
    check(aave["borrowed_usd"] == 400.0, "Aave: borrowed_usd = 400")
    check(aave["net_usd"] == 1100.0, "Aave: net = supplied − borrowed = 1100 (dette en NÉGATIF)")
    check(aave["rewards"] == [] and aave["rewards_usd"] == 0.0, "Aave: rewards vides (jamais inventés)")
    check(aave["pnl"] is None and aave["health_factor"] is None and aave["apy"] is None,
          "Aave: pnl/health_factor/apy = null (indisponibles gratuitement)")
    check(aave["link"] == "https://eth.blockscout.com/address/0xAAVE1",
          "Aave: lien explorer Blockscout du 1er contrat")
    check(aave["source"] == BEST_EFFORT_SOURCE, "Aave: source = best-effort")

lido = by_proto.get(("Lido", "ethereum"))
check(lido is not None and lido["type"] == "staking" and lido["supplied_usd"] == 3600.0,
      "builder: position Lido staking 3600$")
check(lido and lido["net_usd"] == 3600.0, "Lido: net = supplied (pas de dette)")

comp = by_proto.get(("Compound", "base"))
check(comp is not None and comp["type"] == "lending" and comp["net_usd"] == 200.0,
      "builder: position Compound/base 200$")

lp = by_proto.get(("DEX / LP", "optimism"))
check(lp is not None and lp["type"] == "liquidity" and lp["net_usd"] == 90.0,
      "builder: position DEX / LP optimism 90$")
check(lp and lp["protocol_id"] == "dex-lp", "LP: protocol_id slug propre (dex-lp)")

beefy = by_proto.get(("Beefy", "optimism"))
check(beefy is not None and beefy["type"] == "vault" and beefy["net_usd"] == 55.0,
      "builder: position Beefy vault 55$")

# Exclusions
all_syms = []
for p in positions:
    for grp in (p["supplied"], p["borrowed"], p["rewards"]):
        for x in grp:
            all_syms.append(x["symbol"])
check(not any("SPAM" in s or "visit" in s.lower() for s in all_syms), "spam ignoré")
check("aUSDT" not in all_syms, "token désactivé (enabled=False) ignoré")
check("aWETH" not in all_syms, "token à valeur nulle ignoré")
check("USDC" not in all_syms, "token wallet normal non classé en DeFi")

# Tri par net décroissant
nets = [p["net_usd"] for p in positions]
check(nets == sorted(nets, reverse=True), "positions triées par net_usd décroissant")

# ── Summary ─────────────────────────────────────────────────────────
summary = summarize_defi_positions(positions)
check(summary["total_supplied_usd"] == 1500.0 + 3600.0 + 200.0 + 90.0 + 55.0,
      "summary: total_supplied_usd = 5445")
check(summary["total_borrowed_usd"] == 400.0, "summary: total_borrowed_usd = 400")
check(summary["total_rewards_usd"] == 0.0, "summary: total_rewards_usd = 0")
check(summary["net_usd"] == 5045.0, "summary: net_usd = 5045 (emprunt déduit)")
check(summary["positions_count"] == 5, "summary: positions_count = 5")

# ── Robustesse ──────────────────────────────────────────────────────
check(build_best_effort_positions([]) == [], "builder: liste vide → []")
check(build_best_effort_positions(None) == [], "builder: None → []")
check(build_best_effort_positions([None, 42, "x", {}]) == [], "builder: entrées garbage → []")
weird = build_best_effort_positions(
    [{"symbol": "stETH", "balance": "abc", "usd_value": float("nan"), "chain": None}])
check(weird == [], "builder: NaN/garbage numérique → ignoré (pas de crash)")
solo_debt = build_best_effort_positions(
    [{"symbol": "variableDebtEthWETH", "balance": 1.0, "usd_value": 2500.0,
      "contract_address": "0xD", "chain": "ethereum"}])
check(len(solo_debt) == 1 and solo_debt[0]["net_usd"] == -2500.0,
      "builder: dette seule → net NÉGATIF (-2500)")
no_host = build_best_effort_positions(
    [{"symbol": "stETH", "balance": 1.0, "usd_value": 100.0,
      "contract_address": "0xS", "chain": "chaininconnue"}])
check(len(no_host) == 1 and no_host[0]["link"] is None,
      "builder: chaîne sans explorer connu → link null (pas de crash)")

print()
if FAILURES:
    print(f"ECHEC: {FAILURES} test(s) en échec")
    sys.exit(1)
print("Tous les tests best-effort passent.")
