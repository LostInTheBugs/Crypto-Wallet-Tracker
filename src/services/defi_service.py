"""DeFi positions normalizer for the Moralis DeFi API (v2.12.8).

Pure stdlib module (no FastAPI, no httpx) so it can be unit-tested without
the app running:  python3 tests/test_defi_normalizer.py

Input: raw entries from GET /wallets/{address}/defi/positions
(base https://deep-index.moralis.io/api/v2.2). Every field access is
defensive — Moralis' schema varies per protocol and some fields are
missing/null depending on the position type.

Output: stable per-position dicts consumed by the frontend DeFi page:
{
  protocol, protocol_id, protocol_url, protocol_logo, chain, type,
  supplied: [{symbol, amount, usd_value}], borrowed: [...], rewards: [...],
  supplied_usd, borrowed_usd, rewards_usd, net_usd,
  pnl, health_factor, apy, link
}
"""

import math

# Chains queried on Moralis (their DeFi endpoints take one ?chain= each).
# Kept to the majors actually covered by Moralis' DeFi protocol support —
# every extra chain costs API quota on the free tier.
MORALIS_DEFI_CHAINS = [
    "eth", "polygon", "bsc", "arbitrum", "optimism",
    "base", "avalanche", "gnosis", "linea",
]

# Explorer per Moralis chain slug — fallback link when the protocol has no
# dapp URL (the position contract address is linked instead).
CHAIN_EXPLORERS = {
    "eth":       "https://etherscan.io",
    "ethereum":  "https://etherscan.io",
    "polygon":   "https://polygonscan.com",
    "bsc":       "https://bscscan.com",
    "arbitrum":  "https://arbiscan.io",
    "optimism":  "https://optimistic.etherscan.io",
    "base":      "https://basescan.org",
    "avalanche": "https://snowtrace.io",
    "gnosis":    "https://gnosisscan.io",
    "linea":     "https://lineascan.build",
}


def _f(v, default=0.0):
    """Defensive float: None/str/garbage/NaN/inf → default."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _first_number(d, keys):
    """First finite numeric value found under `keys` in dict d, else None."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        if d.get(k) is None:
            continue
        x = _f(d.get(k), default=float("nan"))
        if math.isfinite(x):
            return x
    return None


def classify_token_type(token_type):
    """Map Moralis token_type to supplied|borrowed|rewards.

    Typical values seen in the wild: "supplied", "borrowed", "reward",
    "rewards", "defi-token", "staked", "debt", "unclaimed". Unknown/absent
    values default to "supplied" (mission rule: en cas de doute → supplied).
    """
    tt = str(token_type or "").strip().lower()
    if "borrow" in tt or "debt" in tt:
        return "borrowed"
    if "reward" in tt or "unclaimed" in tt or "claimable" in tt:
        return "rewards"
    return "supplied"


def _norm_token(tk):
    """One raw Moralis token → {symbol, amount, usd_value} (or None)."""
    if not isinstance(tk, dict):
        return None
    symbol = str(tk.get("symbol") or tk.get("name") or "?")[:32]
    amount = _f(tk.get("balance_formatted"), default=float("nan"))
    if not math.isfinite(amount):
        # Fallback: raw integer balance + decimals
        try:
            dec = int(tk.get("decimals") or 18)
        except (TypeError, ValueError):
            dec = 18
        raw = _f(tk.get("balance"), default=0.0)
        amount = raw / (10 ** dec) if raw else 0.0
    usd_value = _f(tk.get("usd_value"), default=0.0)
    return {
        "symbol": symbol,
        "amount": round(amount, 8),
        "usd_value": round(usd_value, 2),
        "contract_address": str(tk.get("contract_address") or "") or None,
    }


def normalize_defi_position(raw, chain=""):
    """One raw Moralis position entry → stable dict for the UI, or None."""
    if not isinstance(raw, dict):
        return None
    pos = raw.get("position")
    if not isinstance(pos, dict):
        pos = {}

    protocol_id = str(raw.get("protocol_id") or "").strip()
    protocol = str(raw.get("protocol_name") or raw.get("protocol") or protocol_id or "Protocole inconnu").strip()
    if not protocol_id:
        protocol_id = protocol.lower().replace(" ", "-")
    protocol_url = str(raw.get("protocol_url") or "").strip() or None
    protocol_logo = str(raw.get("protocol_logo") or "").strip() or None
    chain_slug = str(raw.get("chain") or chain or "").strip().lower()

    ptype = str(pos.get("label") or raw.get("label") or "defi").strip().lower() or "defi"

    supplied, borrowed, rewards = [], [], []
    raw_tokens = pos.get("tokens")
    if not isinstance(raw_tokens, list):
        raw_tokens = []
    for tk in raw_tokens:
        item = _norm_token(tk)
        if item is None:
            continue
        bucket = classify_token_type(tk.get("token_type") if isinstance(tk, dict) else None)
        if bucket == "borrowed":
            borrowed.append(item)
        elif bucket == "rewards":
            rewards.append(item)
        else:
            supplied.append(item)

    supplied_usd = round(sum(x["usd_value"] for x in supplied), 2)
    borrowed_usd = round(sum(x["usd_value"] for x in borrowed), 2)
    rewards_usd = round(sum(x["usd_value"] for x in rewards), 2)
    # If Moralis reports unclaimed value but no reward token line, surface it.
    if not rewards:
        unclaimed = _f(pos.get("total_unclaimed_usd_value"), default=0.0)
        if unclaimed > 0:
            rewards_usd = round(unclaimed, 2)
    net_usd = round(supplied_usd - borrowed_usd + rewards_usd, 2)

    details = pos.get("position_details")
    if not isinstance(details, dict):
        details = {}
    health_factor = _first_number(details, ["health_factor", "healthFactor"])
    apy = _first_number(details, ["apy", "net_apy", "apr", "base_apy"])
    pnl = _first_number(details, ["unrealized_profit_usd", "unrealized_pnl_usd", "pnl", "profit_usd"])
    if pnl is None:
        pnl = _first_number(pos, ["unrealized_profit_usd", "pnl"])

    # Link: dapp URL first, else explorer page of the position/pool contract.
    link = protocol_url
    if not link:
        explorer = CHAIN_EXPLORERS.get(chain_slug)
        pos_addr = str(pos.get("address") or "").strip()
        if not pos_addr:
            for grp in (supplied, borrowed, rewards):
                for x in grp:
                    if x.get("contract_address"):
                        pos_addr = x["contract_address"]
                        break
                if pos_addr:
                    break
        if explorer and pos_addr:
            link = explorer + "/address/" + pos_addr

    # Strip the internal helper key before returning tokens to the UI.
    for grp in (supplied, borrowed, rewards):
        for x in grp:
            x.pop("contract_address", None)

    return {
        "protocol": protocol,
        "protocol_id": protocol_id,
        "protocol_url": protocol_url,
        "protocol_logo": protocol_logo,
        "chain": chain_slug,
        "type": ptype,
        "supplied": supplied,
        "borrowed": borrowed,
        "rewards": rewards,
        "supplied_usd": supplied_usd,
        "borrowed_usd": borrowed_usd,
        "rewards_usd": rewards_usd,
        "net_usd": net_usd,
        "pnl": round(pnl, 2) if pnl is not None else None,
        "health_factor": round(health_factor, 4) if health_factor is not None else None,
        "apy": round(apy, 4) if apy is not None else None,
        "link": link,
    }


def normalize_defi_positions(raw_list, chain=""):
    """List of raw Moralis entries → list of normalized positions.

    Accepts either a plain list or a dict wrapping it under "result"
    (Moralis sometimes paginates that way). Garbage entries are skipped.
    """
    if isinstance(raw_list, dict):
        raw_list = raw_list.get("result") or raw_list.get("positions") or []
    if not isinstance(raw_list, list):
        return []
    out = []
    for raw in raw_list:
        try:
            norm = normalize_defi_position(raw, chain=chain)
        except Exception:
            norm = None
        if norm is not None:
            out.append(norm)
    return out


def summarize_defi_positions(positions):
    """Global summary across normalized positions (always zero-filled)."""
    total_supplied = sum(_f(p.get("supplied_usd")) for p in positions or [])
    total_borrowed = sum(_f(p.get("borrowed_usd")) for p in positions or [])
    total_rewards = sum(_f(p.get("rewards_usd")) for p in positions or [])
    return {
        "total_supplied_usd": round(total_supplied, 2),
        "total_borrowed_usd": round(total_borrowed, 2),
        "total_rewards_usd": round(total_rewards, 2),
        "net_usd": round(total_supplied - total_borrowed + total_rewards, 2),
        "positions_count": len(positions or []),
    }


# ═══════════════════════════════════════════════════════════════════
# Best-effort DeFi positions (v2.12.9) — FREE fallback without Moralis
# ═══════════════════════════════════════════════════════════════════
#
# Built from the wallet's on-chain ERC-20 balances (Blockscout, free):
# DeFi receipt tokens (aTokens, cTokens, LSTs, LP, vault shares) and Aave
# debt tokens ARE regular ERC-20 balances, so a conservative symbol-based
# classification reconstructs supplied/borrowed/staking positions.
#
# Hard limits of the free mode (by design — never invented):
#   rewards = [] / rewards_usd = 0, apy = None, health_factor = None,
#   pnl = None. Only Moralis provides those.
#
# Design rules (same spirit as portfolio_service._token_category):
#   • Conservative: when in doubt, a token is NOT a DeFi position.
#   • Pure stdlib — unit-tested by tests/test_defi_best_effort.py.

BEST_EFFORT_SOURCE = "best-effort"

# ── STAKING: LST/LRT receipt tokens → inferred protocol ─────────────
_BE_LST_PROTOCOLS = {
    "steth":     "Lido",
    "wsteth":    "Lido",
    "reth":      "Rocket Pool",
    "reth2":     "StakeWise",
    "oseth":     "StakeWise",
    "cbeth":     "Coinbase",
    "wbeth":     "Binance",
    "weeth":     "Ether.fi",
    "eeth":      "Ether.fi",
    "ezeth":     "Renzo",
    "rseth":     "Kelp DAO",
    "wrseth":    "Kelp DAO",
    "sfrxeth":   "Frax",
    "frxeth":    "Frax",
    "ankreth":   "Ankr",
    "sweth":     "Swell",
    "rsweth":    "Swell",
    "lseth":     "Liquid Collective",
    "rsteth":    "Autre DeFi",
    "msweth":    "Autre DeFi",
    "wsupereth": "Autre DeFi",
}

# ── Deposit receipts with a known issuer (type vault) ────────────────
_BE_RECEIPT_PROTOCOLS = {
    "sdai":  "Spark",
    "susds": "Sky",
    "susde": "Ethena",
}

# ── LENDING: base symbols recognized behind a/c prefixes ─────────────
_BE_KNOWN_BASES = frozenset({
    "usdt", "usdc", "usdc.e", "usdbc", "dai", "eth", "weth", "wbtc", "btc",
    "op", "arb", "matic", "pol", "link", "uni", "aave", "crv", "snx",
    "wsteth", "steth", "reth", "cbeth", "wmatic", "maticx", "cake", "bal",
    "ldo", "sushi", "1inch", "ens", "mkr", "gno", "eurs", "lusd", "gusd",
    "frax", "usde", "gho", "ink", "xdai", "wxdai", "avax", "wavax",
})

# Aave v3 aTokens carry a chain infix: aEthUSDC, aOptWETH, aArbUSDC,
# aBasUSDbC, aPolWMATIC, aGnowstETH… → "a" + infix + known base.
_BE_AAVE_V3_INFIXES = frozenset({
    "eth", "opt", "arb", "bas", "pol", "gno", "ava", "avax", "scr",
    "lin", "era", "zk", "met", "cel", "son", "ink", "bnb", "ftm", "wld",
})

# ── LP: DEX liquidity pool tokens ────────────────────────────────────
_BE_LP_EXACT = frozenset({"uni-v2", "slp", "cake-lp", "spooky-lp", "joe-lp"})
_BE_LP_SUFFIXES = ("-lp", "-gauge")
_BE_LP_PREFIXES = ("vamm-", "samm-")

_BE_DEBT_AAVE_PREFIXES = ("variabledebt", "stabledebt", "vardebt")


def _be_base_match(rest):
    """rest (after the a/c prefix) is a known base symbol? (exact)."""
    return rest in _BE_KNOWN_BASES


def classify_best_effort_token(symbol):
    """Conservative DeFi classification of ONE wallet token symbol.

    Returns None (not a DeFi position — the default) or a dict:
      {"bucket": "supplied"|"borrowed", "type": "lending"|"staking"|
       "liquidity"|"vault", "protocol": "<inferred protocol>"}
    """
    if not symbol or not isinstance(symbol, str):
        return None
    sym = symbol.lower().strip()
    if not sym:
        return None

    # 1. BORROWED — Aave debt tokens held as ERC-20 balances
    #    (variableDebtEthUSDC, stableDebtPolWMATIC, …)
    for pfx in _BE_DEBT_AAVE_PREFIXES:
        if sym.startswith(pfx) and len(sym) > len(pfx):
            return {"bucket": "borrowed", "type": "lending", "protocol": "Aave"}
    if sym.startswith("debt") and len(sym) > 4:
        return {"bucket": "borrowed", "type": "lending", "protocol": "Autre DeFi"}

    # 2. STAKING — LST/LRT exact matches
    if sym in _BE_LST_PROTOCOLS:
        return {"bucket": "supplied", "type": "staking", "protocol": _BE_LST_PROTOCOLS[sym]}

    # 3. VAULT — known deposit receipts (sDAI, sUSDe, …)
    if sym in _BE_RECEIPT_PROTOCOLS:
        return {"bucket": "supplied", "type": "vault", "protocol": _BE_RECEIPT_PROTOCOLS[sym]}

    # 4. LENDING supplied — Aave aTokens
    if sym.startswith("a") and len(sym) > 1:
        rest = sym[1:]
        if _be_base_match(rest):
            return {"bucket": "supplied", "type": "lending", "protocol": "Aave"}
        # Aave v3 chain infix: a + infix + base (aEthUSDC → a|eth|usdc)
        for base in _BE_KNOWN_BASES:
            if rest.endswith(base):
                infix = rest[: len(rest) - len(base)]
                if infix and infix in _BE_AAVE_V3_INFIXES:
                    return {"bucket": "supplied", "type": "lending", "protocol": "Aave"}

    # 5. LENDING supplied — Compound cTokens (cUSDC, cDAI, cUSDCv3)
    if sym.startswith("c") and len(sym) > 1:
        rest = sym[1:]
        if _be_base_match(rest):
            return {"bucket": "supplied", "type": "lending", "protocol": "Compound"}
        if rest.endswith("v3") and _be_base_match(rest[:-2]):
            return {"bucket": "supplied", "type": "lending", "protocol": "Compound"}

    # 6. VAULT — Beefy (moo + uppercase on the ORIGINAL symbol, pitfall 121)
    if sym.startswith("moo") and len(sym) > 4:
        orig = symbol.strip()
        if (len(orig) > 3 and orig[3].isupper()) or sym.startswith(("moobifi", "moovelo")):
            return {"bucket": "supplied", "type": "vault", "protocol": "Beefy"}

    # 7. VAULT — Yearn (yv…)
    if sym.startswith("yv") and len(sym) > 3:
        return {"bucket": "supplied", "type": "vault", "protocol": "Yearn"}

    # 8. VAULT — Stargate (literal S*USDC style symbols)
    if sym.startswith("s*") and len(sym) > 2:
        return {"bucket": "supplied", "type": "vault", "protocol": "Stargate"}

    # 9. LIQUIDITY — LP / gauge / Curve / Velodrome-Aerodrome
    if sym in _BE_LP_EXACT:
        return {"bucket": "supplied", "type": "liquidity", "protocol": "DEX / LP"}
    for sfx in _BE_LP_SUFFIXES:
        if sym.endswith(sfx) and len(sym) > len(sfx):
            return {"bucket": "supplied", "type": "liquidity", "protocol": "DEX / LP"}
    for pfx in _BE_LP_PREFIXES:
        if sym.startswith(pfx) and len(sym) > len(pfx):
            return {"bucket": "supplied", "type": "liquidity", "protocol": "DEX / LP"}
    if sym.endswith("crv") and len(sym) > 3:
        return {"bucket": "supplied", "type": "liquidity", "protocol": "DEX / LP"}

    return None


def _be_slug(name):
    """Protocol display name → stable protocol_id slug."""
    out = []
    prev_dash = False
    for ch in str(name or "").lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-") or "defi"


def build_best_effort_positions(tokens, explorer_hosts=None, is_spam=None):
    """Wallet tokens (from _compute_portfolio) → best-effort DeFi positions.

    tokens: list of dicts with symbol / balance / usd_value / usd_price /
            contract_address / chain (extra keys ignored). Tokens disabled by
            the user (enabled == False), spam (is_spam callable) and tokens
            with no positive USD value are skipped — conservative by design.
    explorer_hosts: optional {chain: blockscout_host} used to build the
            explorer link of the first contract of each position.
    Returns positions in the same shape as normalize_defi_position, with
    rewards empty and pnl/health_factor/apy = None (unavailable for free).
    """
    groups = {}   # (protocol, chain, type) -> {"supplied": [...], "borrowed": [...], "contracts": [...]}
    order = []    # deterministic insertion order

    for tk in tokens or []:
        if not isinstance(tk, dict):
            continue
        if tk.get("enabled") is False:      # user-disabled token
            continue
        symbol = tk.get("symbol")
        if callable(is_spam):
            try:
                if is_spam(symbol):
                    continue
            except Exception:
                pass
        usd_value = _f(tk.get("usd_value"))
        if usd_value <= 0:                  # ignore worthless/unpriced balances
            continue
        cls = classify_best_effort_token(symbol)
        if cls is None:
            continue

        chain = str(tk.get("chain") or "").strip().lower()
        key = (cls["protocol"], chain, cls["type"])
        if key not in groups:
            groups[key] = {"supplied": [], "borrowed": [], "contracts": []}
            order.append(key)
        grp = groups[key]
        grp[cls["bucket"]].append({
            "symbol": str(symbol)[:32],
            "amount": round(_f(tk.get("balance")), 8),
            "usd_value": round(usd_value, 2),
        })
        contract = str(tk.get("contract_address") or "").strip()
        if contract:
            grp["contracts"].append(contract)

    positions = []
    for key in order:
        protocol, chain, ptype = key
        grp = groups[key]
        supplied_usd = round(sum(x["usd_value"] for x in grp["supplied"]), 2)
        borrowed_usd = round(sum(x["usd_value"] for x in grp["borrowed"]), 2)

        link = None
        host = (explorer_hosts or {}).get(chain)
        if host and grp["contracts"]:
            link = "https://" + str(host).strip().strip("/") + "/address/" + grp["contracts"][0]

        positions.append({
            "protocol": protocol,
            "protocol_id": _be_slug(protocol),
            "protocol_url": None,
            "protocol_logo": None,
            "chain": chain,
            "type": ptype,
            "supplied": grp["supplied"],
            "borrowed": grp["borrowed"],
            "rewards": [],                      # unavailable in free mode
            "supplied_usd": supplied_usd,
            "borrowed_usd": borrowed_usd,
            "rewards_usd": 0.0,                 # never invented
            "net_usd": round(supplied_usd - borrowed_usd, 2),  # debt counts negative
            "pnl": None,
            "health_factor": None,
            "apy": None,
            "link": link,
            "source": BEST_EFFORT_SOURCE,
        })

    positions.sort(key=lambda p: p["net_usd"], reverse=True)
    return positions
