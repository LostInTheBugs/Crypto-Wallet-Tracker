"""
Portfolio service — portfolio computation and snapshot formatting.

Fixes applied:
  1. Native coin balance fetched in parallel (ETH/POL/xDAI/CELO)
  2. DefiLlama price fallback for tokens without Blockscout exchange_rate
  3. Spam filter (_is_spam) applied at fetch time, not just in history rebuild
  4. Unpriced tokens retained with price_unknown=True flag
  5. usd0 mapping added in price_service.SYMBOL_TO_CG
"""
import asyncio
import logging

import httpx

from services.price_service import SYMBOL_TO_CG

logger = logging.getLogger("crypto.portfolio")

# ═══════════════════════════════════════════════════════════════════
# Chain configuration
# ═══════════════════════════════════════════════════════════════════

CHAINS = {
    "ethereum":   "eth.blockscout.com",
    "base":       "base.blockscout.com",
    "optimism":   "explorer.optimism.io",
    "arbitrum":   "arbitrum.blockscout.com",
    "polygon":    "polygon.blockscout.com",
    "gnosis":     "gnosis.blockscout.com",
    "zksync":     "zksync.blockscout.com",
    "celo":       "celo.blockscout.com",
    "scroll":     "scroll.blockscout.com",
    "soneium":    "soneium.blockscout.com",
    "ink":        "explorer.inkonchain.com",
    "mode":       "explorer.mode.network",
    "unichain":   "unichain.blockscout.com",
    "lisk":       "blockscout.lisk.com",
    "linea":      "api-explorer.linea.build",
    "etherlink":  "explorer.etherlink.com",
    "metis":      "andromeda-explorer.metis.io",
    "manta":      "pacific-explorer.manta.network",
    "bob":        "explorer.gobob.xyz",
    "zora":       "explorer.zora.energy",
    "worldchain": "worldchain-mainnet.explorer.alchemy.com",
}

# Chain → DefiLlama slug (for current price lookups)
CHAIN_TO_LLAMA = {
    "ethereum":   "ethereum",
    "base":       "base",
    "optimism":   "optimism",
    "arbitrum":   "arbitrum",
    "polygon":    "polygon",
    "gnosis":     "xdai",       # Gnosis = xdai on DefiLlama
    "zksync":     "era",        # zkSync = era on DefiLlama
    "celo":       "celo",
    "scroll":     "scroll",
    "soneium":    "soneium",
    "ink":        "ink",
    "mode":       "mode",
    "unichain":   "unichain",
    "lisk":       "lisk",
    "linea":      "linea",
    "etherlink":  "etherlink",
    "metis":      "metis",
    "manta":      "manta",
    "bob":        "bob",
    "zora":       "zora",
    "worldchain": "wc",         # worldchain = wc on DefiLlama
}

# Native coin metadata per chain
NATIVE_COIN = {
    "ethereum":   {"name": "Ethereum", "symbol": "ETH"},
    "base":       {"name": "Ethereum", "symbol": "ETH"},
    "optimism":   {"name": "Ethereum", "symbol": "ETH"},
    "arbitrum":   {"name": "Ethereum", "symbol": "ETH"},
    "zksync":     {"name": "Ethereum", "symbol": "ETH"},
    "scroll":     {"name": "Ethereum", "symbol": "ETH"},
    "soneium":    {"name": "Ethereum", "symbol": "ETH"},
    "ink":        {"name": "Ethereum", "symbol": "ETH"},
    "mode":       {"name": "Ethereum", "symbol": "ETH"},
    "unichain":   {"name": "Ethereum", "symbol": "ETH"},
    "lisk":       {"name": "Ethereum", "symbol": "ETH"},
    "linea":      {"name": "Ethereum", "symbol": "ETH"},
    "polygon":    {"name": "Polygon",  "symbol": "POL"},
    "gnosis":     {"name": "xDai",     "symbol": "xDAI"},
    "celo":       {"name": "Celo",     "symbol": "CELO"},
    "etherlink":  {"name": "Tezos",    "symbol": "XTZ"},
    "metis":      {"name": "Metis",    "symbol": "METIS"},
    "manta":      {"name": "Ethereum", "symbol": "ETH"},
    "bob":        {"name": "Ethereum", "symbol": "ETH"},
    "zora":       {"name": "Ethereum", "symbol": "ETH"},
    "worldchain": {"name": "Ethereum", "symbol": "ETH"},
}

# ═══════════════════════════════════════════════════════════════════
# Spam detection (shared with pnl_service — keep in sync)
# ═══════════════════════════════════════════════════════════════════

SPAM_PATTERNS = [
    "visit ", "claim ", "reward", "airdrop", "http", "t.me", ".cfd", ".cc",
    ".lat", ".lol", ".top", ".xyz", ".win", ".vip", ".club", "random",
    "you are eligible", "you received", "you won", "coupon", "giveaway",
    "visit website", "mint airdrop", "gift", "voucher", "bonus", "! ", "? ",
    "$ claim", "www.", "@", "token", "web3", "web4", "nft", "u5dc", "usdtclaim",
    "official website", "verify", "us_pool", "us_circle", "tronvanity",
]


def _is_spam(sym: str) -> bool:
    """Check if a token symbol matches known spam patterns."""
    sym_lower = sym.lower()
    for p in SPAM_PATTERNS:
        if p in sym_lower:
            return True
    return False


# ═══════════════════════════════════════════════════════════════════
# Token category detection (staked vs wallet)
# ═══════════════════════════════════════════════════════════════════

# Symbols/types that are known staked/receipt tokens
_STAKED_PREFIXES = ("a", "moo", "s*")        # aUSDT, mooBIFI, S*ETH
_STAKED_SUFFIXES = ("-gauge", "-lp")          # veloV2-gauge, UNI-V2-LP
_STAKED_EXACT = {
    "wsteth", "reth", "wrseth", "ezeth", "weeth", "rseth",
    "cbeth", "sfrxeth", "susde", "uni-v2",
}
# Tokens starting with "a" followed by a known symbol (aave aTokens)
_KNOWN_BASES = {"usdt", "usdc", "dai", "eth", "weth", "wbtc", "op", "arb", "matic", "pol", "link", "uni", "aave", "crv", "snx"}


def _token_category(symbol: str) -> str:
    """Classify a token as 'staked' or 'wallet' based on symbol heuristics."""
    sym = symbol.lower().strip()
    if not sym:
        return "wallet"

    # Exact matches
    if sym in _STAKED_EXACT:
        return "staked"

    # Suffix matches
    for sfx in _STAKED_SUFFIXES:
        if sym.endswith(sfx):
            return "staked"

    # Prefix: "a" + known symbol → Aave aToken
    if sym.startswith("a") and len(sym) > 1:
        base = sym[1:]
        if base in _KNOWN_BASES:
            return "staked"

    # Prefix: "moo" → Beefy
    if sym.startswith("moo"):
        return "staked"

    # Prefix: "s*" → Stargate
    if sym.startswith("s*"):
        return "staked"

    return "wallet"


# ═══════════════════════════════════════════════════════════════════
# Chain-level fetching (tokens + native coin, parallel)
# ═══════════════════════════════════════════════════════════════════

async def _fetch_native_coin(client, chain: str, host: str, address: str) -> dict | None:
    """Fetch native coin balance + USD price from Blockscout address endpoint.
    Returns a pseudo-token dict or None on failure.
    """
    try:
        r = await client.get(
            f"https://{host}/api/v2/addresses/{address}",
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        coin_balance = data.get("coin_balance")
        exchange_rate = data.get("exchange_rate")

        if not coin_balance or coin_balance == "0":
            return None

        bal = int(coin_balance)
        usd_price = float(exchange_rate or 0)
        meta = NATIVE_COIN.get(chain, {"name": "Native", "symbol": "?"})

        return {
            "name": meta["name"],
            "symbol": meta["symbol"],
            "decimals": 18,
            "balance_raw": str(bal),
            "usd_price": usd_price,
            "icon": "",
            "type": "native",
            "contract_address": None,  # native coins have no contract
        }
    except Exception:
        return None


async def fetch_chain(client, chain, host, address):
    """Fetch ERC-20/721/1155 tokens AND native coin balance in parallel."""
    try:
        # Parallel: tokens + native coin
        tokens_task = client.get(
            f"https://{host}/api/v2/addresses/{address}/tokens",
            timeout=15,
        )
        native_task = _fetch_native_coin(client, chain, host, address)

        # Await both
        r, native = await asyncio.gather(tokens_task, native_task)

        # --- Process token list ---
        if r.status_code != 200:
            tokens = []
            error = f"HTTP {r.status_code}"
        else:
            data = r.json()
            tokens = []
            for item in data.get("items", []):
                if item is None:
                    continue
                t = item.get("token") or {}
                raw = item.get("value")

                symbol = t.get("symbol", "?")
                name = t.get("name", "Unknown")

                # ── Spam filter ──
                if _is_spam(symbol):
                    continue

                contract_addr = t.get("address", "")

                tokens.append({
                    "name": name,
                    "symbol": symbol,
                    "decimals": int(t.get("decimals") or 18),
                    "balance_raw": str(raw) if raw else "0",
                    "usd_price": float(t.get("exchange_rate") or 0),
                    "icon": t.get("icon_url", ""),
                    "type": t.get("type", "ERC-20"),
                    "contract_address": contract_addr,
                })
            error = None

        # --- Prepend native coin if present ---
        if native:
            # Copy native fields into the token dict format
            tokens.insert(0, {
                "name": native["name"],
                "symbol": native["symbol"],
                "decimals": native["decimals"],
                "balance_raw": native["balance_raw"],
                "usd_price": native["usd_price"],
                "icon": native["icon"],
                "type": native["type"],
                "contract_address": native["contract_address"],
                "category": "wallet",
            })

        return {"chain": chain, "tokens": tokens, "error": error}

    except Exception as e:
        return {"chain": chain, "tokens": [], "error": str(e)[:100]}


# ═══════════════════════════════════════════════════════════════════
# DefiLlama batch price lookup (fallback for unpriced tokens)
# ═══════════════════════════════════════════════════════════════════

async def _fetch_defillama_current_prices(queries: list[tuple[str, str, str]]) -> dict[str, float]:
    """Batch-fetch current prices from DefiLlama.

    Args:
        queries: list of (chain_slug, contract_address, symbol) tuples.
                 chain_slug is the DefiLlama chain name (e.g. 'xdai' for Gnosis).

    Returns:
        {contract_address: price_usd} for tokens that DefiLlama knows about.
    """
    if not queries:
        return {}

    prices = {}
    llama_slug = CHAIN_TO_LLAMA

    # Group by chain for batch queries
    by_chain: dict[str, list[tuple[str, str]]] = {}
    for chain_slug, contract_addr, symbol in queries:
        slug = llama_slug.get(chain_slug, chain_slug)
        by_chain.setdefault(slug, []).append((contract_addr, symbol))

    for chain_slug, tokens_in_chain in by_chain.items():
        if not tokens_in_chain:
            continue

        # Build comma-separated address list (max ~50 per call)
        for i in range(0, len(tokens_in_chain), 50):
            batch = tokens_in_chain[i:i + 50]
            addr_list = ",".join(addr for addr, _ in batch)
            url = f"https://coins.llama.fi/prices/current/{chain_slug}:{addr_list}"

            try:
                async with httpx.AsyncClient(timeout=15) as c:
                    resp = await c.get(url)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                coins = data.get("coins", {})
                for key, coin_data in coins.items():
                    price = coin_data.get("price", 0)
                    if price > 0:
                        # Key format: "chain:address"
                        addr = key.split(":", 1)[-1] if ":" in key else key
                        prices[addr] = float(price)
            except Exception:
                continue

            # Rate limit between batches
            if i + 50 < len(tokens_in_chain):
                await asyncio.sleep(0.5)

    return prices


# ═══════════════════════════════════════════════════════════════════
# Portfolio computation
# ═══════════════════════════════════════════════════════════════════

async def _compute_portfolio(address: str) -> dict:
    logger.info(f"[TRACE] _compute_portfolio ENTER address={address[:12]}...")

    # 1. Fetch all chains in parallel
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(
            *[fetch_chain(client, c, h, address) for c, h in CHAINS.items()]
        )

    # Log raw chain results
    chain_counts = {r["chain"]: len(r.get("tokens", [])) for r in results}
    chain_errors = {r["chain"]: r.get("error") for r in results if r.get("error")}
    logger.info(f"[TRACE] chains={chain_counts} errors={chain_errors}")

    # 2. Build item list from all chains
    items = []
    total = 0.0
    unpriced_tokens: list[tuple[str, str, str]] = []  # (chain, contract_addr, symbol)

    for r in results:
        for t in r["tokens"]:
            try:
                bal = int(t["balance_raw"]) / (10 ** t["decimals"])
            except Exception:
                bal = 0

            usd_price = t.get("usd_price", 0)
            usd = bal * usd_price
            total += usd

            sym = t.get("symbol", "?")

            items.append({
                "chain": r["chain"],
                "name": t.get("name", "Unknown"),
                "symbol": sym,
                "balance": round(bal, 6),
                "usd_value": round(usd, 2),
                "usd_price": usd_price,
                "icon": t.get("icon", ""),
                "type": t.get("type", "ERC-20"),
                "contract_address": t.get("contract_address", ""),
                "price_unknown": False,
                "category": _token_category(sym) if not _is_spam(sym) else "wallet",
            })

            # Collect tokens without price but with balance (for DefiLlama fallback)
            if usd_price <= 0 and bal > 0 and not _is_spam(sym):
                contract_addr = t.get("contract_address", "")
                if contract_addr:
                    unpriced_tokens.append((r["chain"], contract_addr, sym))

    logger.info(
        f"[TRACE] _compute_portfolio AFTER build: raw_items={len(items)} "
        f"total_usd={total:.2f} unpriced={len(unpriced_tokens)}"
    )

    # 3. DefiLlama fallback for tokens without Blockscout price
    if unpriced_tokens:
        llama_prices = await _fetch_defillama_current_prices(unpriced_tokens)
        if llama_prices:
            enriched = 0
            for item in items:
                if item["usd_price"] > 0:
                    continue
                addr = item.get("contract_address", "")
                if addr and addr in llama_prices:
                    new_price = llama_prices[addr]
                    item["usd_price"] = new_price
                    new_usd = item["balance"] * new_price
                    delta = new_usd - item["usd_value"]
                    item["usd_value"] = round(new_usd, 2)
                    item["price_unknown"] = False
                    total += delta
                    enriched += 1
            logger.info(
                f"[TRACE] DefiLlama enriched {enriched}/{len(unpriced_tokens)} unpriced tokens"
            )

    # 4. Mark remaining unpriced tokens
    still_unpriced = 0
    for item in items:
        if item["usd_price"] <= 0 and item["balance"] > 0:
            item["price_unknown"] = True
            still_unpriced += 1
    if still_unpriced:
        logger.info(
            f"[TRACE] {still_unpriced} tokens still unpriced after DefiLlama fallback"
        )

    # 5. Filter: ONLY drop zero-balance tokens (and known spam, already filtered)
    raw_count = len(items)
    tokens_no_price = [p for p in items if p.get("usd_price", 0) <= 0]
    tokens_low_value = [p for p in items if 0 < p.get("usd_value", 0) < 0.01]
    zero_bal = [p for p in items if p.get("balance", 0) <= 0]

    items = [p for p in items if p.get("balance", 0) > 0]

    logger.info(
        f"[portfolio] {address[:10]}: raw={raw_count} kept={len(items)} "
        f"no_price={len(tokens_no_price)} low_val={len(tokens_low_value)} zero_bal={len(zero_bal)}"
    )
    if tokens_no_price:
        symbols = [p.get("symbol", "?") for p in tokens_no_price[:5]]
        logger.debug(f"[portfolio] {address[:10]}: no_price tokens={symbols}")
    if tokens_low_value:
        symbols = [p.get("symbol", "?") for p in tokens_low_value[:5]]
        logger.debug(f"[portfolio] {address[:10]}: low_value tokens={symbols}")

    # 6. Sort by USD value descending
    items.sort(key=lambda x: x["usd_value"], reverse=True)

    # 7. Chain totals for the pie chart
    chain_totals = {}
    for p in items:
        chain_totals[p["chain"]] = chain_totals.get(p["chain"], 0) + p["usd_value"]

    # 8. Compute staked aggregate
    staked_usd = sum(
        p["usd_value"] for p in items if p.get("category") == "staked"
    )

    # 9. Build response
    result = {
        "address": address,
        "total_usd": round(total, 2),
        "staked_usd": round(staked_usd, 2),
        "token_count": len(items),
        "chain_count": len([r for r in results if r["tokens"]]),
        "chains": {
            c: round(v, 2)
            for c, v in sorted(chain_totals.items(), key=lambda x: x[1], reverse=True)
        },
        "tokens": items[:200],
        "errors": [
            {"chain": r["chain"], "error": r["error"]}
            for r in results if r["error"]
        ],
        "cached": False,
    }

    logger.info(
        f"[TRACE] _compute_portfolio EXIT address={address[:12]}... "
        f"tokens={len(items)} total_usd={round(total,2)}"
    )

    return result


# ═══════════════════════════════════════════════════════════════════
# Snapshot formatting (v2 + legacy)
# ═══════════════════════════════════════════════════════════════════

def format_snapshots_v2(rows: list) -> dict:
    """Convert snapshot rows to standardized v2 format: {labels, values, meta}.

    Guarantees:
      - labels.length == values.length
      - No nulls, no NaN
      - meta always present
    """
    import math
    if not rows:
        return {"labels": [], "values": [], "meta": {"points": 0, "min": 0, "max": 0}}

    labels = []
    values = []
    for r in rows:
        lbl = r.get("date", "")
        val = r.get("total_usd", 0.0)
        if not math.isfinite(val):
            val = 0.0
        labels.append(str(lbl))
        values.append(round(float(val), 2))

    return {
        "labels": labels,
        "values": values,
        "meta": {
            "points": len(rows),
            "min": round(min(values), 2) if values else 0,
            "max": round(max(values), 2) if values else 0,
        },
    }


def format_snapshots_legacy(rows: list) -> list:
    """Convert snapshot rows to legacy format: array of {total_usd, quantity, token_count, date, cost_basis}.

    Hardened: guarantees no NaN, no nulls, no undefined in output.
    """
    import math
    result = []
    for r in rows:
        val = r.get("total_usd", 0.0)
        if not math.isfinite(val):
            val = 0.0
        cost = r.get("cost_basis", 0.0)
        if not math.isfinite(cost):
            cost = 0.0
        result.append({
            "total_usd": round(float(val), 2),
            "quantity": 0,
            "token_count": 0,
            "date": r.get("date", ""),
            "cost_basis": round(float(cost), 2),
        })
    return result
