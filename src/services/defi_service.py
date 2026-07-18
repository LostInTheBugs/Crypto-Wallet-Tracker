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
