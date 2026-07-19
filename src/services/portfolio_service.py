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
    "hyperevm":  "www.hyperscan.com",
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
    "hyperevm":  "hyperliquid",
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
    "hyperevm":  {"name": "Hyperliquid", "symbol": "HYPE"},
}

# Wrapped native token addresses for DefiLlama fallback pricing
# Used when Blockscout doesn't return a price for the native coin
NATIVE_WRAPPED = {
    "hyperevm": "0x5555555555555555555555555555555555555555",  # WHYPE
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


def _is_spam(sym) -> bool:
    """Check if a token symbol matches known spam patterns. Accepts None."""
    if not sym or not isinstance(sym, str):
        return False
    sym_lower = sym.lower()
    for p in SPAM_PATTERNS:
        if p in sym_lower:
            return True
    return False


# ═══════════════════════════════════════════════════════════════════
# Token category detection — fine-grained DeFi classification
# ═══════════════════════════════════════════════════════════════════
#
# Categories (stable labels — keep documented):
#   "lending"   : Aave aTokens (a+base), Compound cTokens (c+base),
#                 variableDebt/stableDebt tokens
#   "lp"        : Liquidity pool receipts — DEX LP, gauge tokens,
#                 Curve, Velodrome/Aerodrome
#   "staked"    : Liquid staking tokens (LST/LRT) — receipt tokens
#                 for staked ETH or other base assets
#   "vault"     : Auto-compounding yield vaults — Beefy (moo*),
#                 Yearn (yv*), Stargate (s*...), ERC-4626
#   "synthetic" : Protocol-issued stablecoins / synthetic assets
#                 (GHO, crvUSD, feUSD, hyUSD, sDAI, sUSDe, etc.)
#   "wallet"    : Everything else (regular ERC-20 tokens, native coins)
#
# Design rules:
#  • Conservative — when in doubt, return "wallet".
#  • No false positives: heuristics must not classify a memecoin as DeFi.
#  • Case-insensitive, None-tolerant (returns "wallet").
#  • First-match priority: order matters — staked > lending > vault > lp > synthetic.

# ── STAKED: Liquid staking tokens (LST/LRT) ──────────────────
# Receipt tokens representing staked ETH (or other base assets).
_LST_EXACT = frozenset({
    "wsteth", "reth", "wrseth", "ezeth", "weeth", "rseth",
    "cbeth", "sfrxeth", "steth", "ankreth", "lseth",
    "sweth", "oseth", "rsteth", "msweth", "wbeth",
    "wsupereth", "reth2", "rsweth",
})

# ── LENDING: Aave aTokens, Compound cTokens, debt tokens ─────
# Generic base symbols recognized as lending collateral when prefixed
# by 'a' (Aave) or 'c' (Compound).
_KNOWN_BASES = frozenset({
    "usdt", "usdc", "dai", "eth", "weth", "wbtc", "op", "arb",
    "matic", "pol", "link", "uni", "aave", "crv", "snx", "wsteth",
    "reth", "cbeth", "wmatic", "maticx", "cake", "bal", "ldo",
    "sushi", "1inch", "ens", "mkr", "gno",
})

# ── LP: DEX liquidity pool tokens ────────────────────────────
_LP_EXACT = frozenset({"uni-v2", "slp", "cake-lp", "spooky-lp", "joe-lp"})
_LP_SUFFIXES = ("-lp", "-gauge")

# ── VAULT: Yield vaults (Beefy, Yearn, Stargate, ERC-4626) ───
_VAULT_EXACT = frozenset({"yvusdc", "yvdai", "yvusdt", "yveth", "yvweth"})

# ── SYNTHETIC: Protocol stablecoins / synthetic assets ────────
_SYNTHETIC_EXACT = frozenset({
    "feusd", "hyusd", "susde", "sdai", "gho", "crvusd",
    "usde", "fdusd",
})

# ── Known DeFi categories (used for defi_usd aggregate) ──────
_DEFI_CATEGORIES = frozenset({"lending", "lp", "staked", "vault", "synthetic"})


def _is_defi_category(cat: str) -> bool:
    """Return True if the category string is a DeFi category (not 'wallet')."""
    return cat in _DEFI_CATEGORIES


def _token_category(symbol) -> str:
    """Classify a token into a fine-grained DeFi category.

    Returns one of: "lending", "lp", "staked", "vault", "synthetic", "wallet".
    Conservative: when in doubt, returns "wallet".
    Accepts None.
    """
    if not symbol or not isinstance(symbol, str):
        return "wallet"
    sym = symbol.lower().strip()
    if not sym:
        return "wallet"

    # ── 1. STAKED: LST/LRT exact matches ──────────────────────
    if sym in _LST_EXACT:
        return "staked"

    # ── 2. LENDING: aTokens (Aave) ────────────────────────────
    #   a + known_base → Aave aToken  (e.g. aUSDT, aETH, aWBTC)
    if sym.startswith("a") and len(sym) > 1:
        base = sym[1:]
        if base in _KNOWN_BASES:
            return "lending"

    # ── 3. LENDING: cTokens (Compound) ────────────────────────
    #   c + known_base → Compound cToken (e.g. cUSDT, cETH)
    if sym.startswith("c") and len(sym) > 1:
        base = sym[1:]
        if base in _KNOWN_BASES:
            return "lending"

    # ── 4. LENDING: debt tokens (Aave variable/stable debt) ──
    for debt_prefix in ("variabledebt", "stabledebt", "vardebt"):
        if sym.startswith(debt_prefix) and len(sym) > len(debt_prefix):
            return "lending"

    # ── 5. VAULT: Beefy (moo…) ─────────────────────────────────
    #   Guard: require original symbol (case-sensitive) to start with "moo"
    #   followed by at least one uppercase character → avoids matching "moon".
    if sym.startswith("moo") and len(sym) >= 4:
        if len(sym) >= 4 and sym[3].isupper():
            return "vault"  # e.g. mooBIFI, mooVeloV2
        if sym.startswith("moobifi") or sym.startswith("moovelo"):
            return "vault"

    # ── 6. VAULT: Yearn (yv…) ──────────────────────────────────
    if sym.startswith("yv") and len(sym) > 3:
        return "vault"

    # ── 7. VAULT: Stargate (s*…) ────────────────────────────────
    if sym.startswith("s*"):
        return "vault"

    # ── 8. VAULT: ERC-4626 vaults ───────────────────────────────
    if sym.startswith("erc") or sym.startswith("v-"):
        return "vault"

    # ── 9. LP: exact matches ────────────────────────────────────
    if sym in _LP_EXACT:
        return "lp"

    # ── 10. LP: suffix matches (-lp, -gauge) ────────────────────
    for sfx in _LP_SUFFIXES:
        if sym.endswith(sfx):
            return "lp"

    # ── 11. LP: Curve tokens (end with "crv" or "-f") ───────────
    if sym.endswith("crv") and len(sym) > 3:
        return "lp"
    if sym.endswith("-f") and len(sym) > 2:
        return "lp"

    # ── 12. LP: Velodrome/Aerodrome (vamm-* / samm-*) ──────────
    if sym.startswith("vamm-") or sym.startswith("samm-"):
        return "lp"

    # ── 13. SYNTHETIC: exact matches ────────────────────────────
    if sym in _SYNTHETIC_EXACT:
        return "synthetic"

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
        # Native coin (single call) runs first; then paginate the token list
        # (Blockscout returns ~50/page — a single page missed tokens on large
        # wallets). MAX_TOKEN_PAGES caps the cost on spam-heavy wallets.
        native = await _fetch_native_coin(client, chain, host, address)

        tokens = []
        error = None
        MAX_TOKEN_PAGES = 10
        url = f"https://{host}/api/v2/addresses/{address}/tokens"
        params = {}
        page = 0
        while page < MAX_TOKEN_PAGES:
            try:
                r = await client.get(url, params=params, timeout=15)
            except Exception as e:
                if page == 0:
                    error = str(e)[:80]
                break
            if r.status_code != 200:
                if page == 0:
                    error = f"HTTP {r.status_code}"
                break
            data = r.json()
            for item in data.get("items", []):
                if item is None:
                    continue
                try:
                    t = item.get("token") or {}
                    raw = item.get("value")

                    symbol = t.get("symbol") or "?"
                    name = t.get("name") or "Unknown"

                    # ── Spam filter ──
                    if _is_spam(symbol):
                        continue

                    contract_addr = t.get("address") or t.get("address_hash") or ""

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
                except Exception:
                    # One bad token must NOT kill the whole chain
                    logger.debug(f"[fetch_chain] {chain}: skipping malformed token item")
            nxt = data.get("next_page_params")
            if not nxt:
                break
            params = nxt
            page += 1

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

async def _fetch_defillama_current_prices(queries: list[tuple[str, str, str]]) -> dict[str, dict]:
    """Batch-fetch current prices (WITH confidence) from DefiLlama.

    Args:
        queries: list of (chain_slug, contract_address, symbol) tuples.
                 chain_slug is the DefiLlama chain name (e.g. 'xdai' for Gnosis).

    Returns:
        {contract_address: {"price": float, "confidence": float|None}}
        for tokens that DefiLlama knows about. `confidence` (0..1) is
        DefiLlama's own price reliability score; None when absent.
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
            addr_list = ",".join(f"{chain_slug}:{addr.lower()}" for addr, _ in batch)
            # DefiLlama requires chain prefix on EVERY address
            url = f"https://coins.llama.fi/prices/current/{addr_list}"
            # Safety: if URL is too long, trim batch
            if len(url) > 4000:
                half = len(batch) // 2
                batch = batch[:half]
                addr_list = ",".join(f"{chain_slug}:{addr.lower()}" for addr, _ in batch)
                url = f"https://coins.llama.fi/prices/current/{addr_list}"

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
                        addr = key.split(":", 1)[-1].lower() if ":" in key else key.lower()
                        conf = coin_data.get("confidence")
                        try:
                            conf = float(conf) if conf is not None else None
                        except (TypeError, ValueError):
                            conf = None
                        prices[addr] = {"price": float(price), "confidence": conf}
            except Exception:
                continue

            # Rate limit between batches
            if i + 50 < len(tokens_in_chain):
                await asyncio.sleep(0.5)

    return prices


# ═══════════════════════════════════════════════════════════════════
# CoinGecko current-price fallback (API key required)
# ═══════════════════════════════════════════════════════════════════

# Our chain names → CoinGecko asset_platform IDs
CHAIN_TO_CG_PLATFORM = {
    "ethereum":   "ethereum",
    "base":       "base",
    "optimism":   "optimism",
    "arbitrum":   "arbitrum-one",
    "polygon":    "polygon-pos",
    "gnosis":     "xdai",
    "zksync":     "zksync",
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
    "manta":      "manta-pacific",
    "bob":        "bob",
    "zora":       "zora",
    "worldchain": "world-chain",
    "hyperevm":  "hyperliquid",
}

# CoinGecko native coin IDs (for native coins without contract addresses)
NATIVE_COIN_CG_ID = {
    "ETH": "ethereum",
    "POL": "matic-network",
    "xDAI": "xdai",
    "CELO": "celo",
    "XTZ": "tezos",
    "METIS": "metis-token",
    "HYPE": "hyperliquid",
}


async def _fetch_coingecko_current_prices(
    queries: list[tuple[str, str, str]],
    api_key: str,
) -> dict[str, dict]:
    """Batch-fetch current prices from CoinGecko /simple/token_price.

    Args:
        queries: list of (chain, contract_address, symbol) tuples.
        api_key: CoinGecko demo API key.

    Returns:
        {contract_address_lower: {"price": float, "source": "coingecko"}}
        for tokens that CoinGecko priced successfully.
    """
    if not queries or not api_key:
        return {}

    prices = {}

    # Group by CG platform
    by_platform: dict[str, list[tuple[str, str]]] = {}
    for chain, contract_addr, symbol in queries:
        platform = CHAIN_TO_CG_PLATFORM.get(chain)
        if not platform:
            continue
        if contract_addr:
            addr = contract_addr.lower()
            by_platform.setdefault(platform, []).append((addr, symbol))

    if not by_platform:
        return {}

    async with httpx.AsyncClient(timeout=20) as c:
        for platform, tokens in by_platform.items():
            # CoinGecko /simple/token_price: max ~30 addresses per call
            for i in range(0, len(tokens), 30):
                batch = tokens[i:i + 30]
                addr_list = ",".join(addr for addr, _ in batch)
                url = (
                    f"https://api.coingecko.com/api/v3/simple/token_price/"
                    f"{platform}?contract_addresses={addr_list}&vs_currencies=usd"
                )
                try:
                    resp = await c.get(url, headers={"x-cg-demo-api-key": api_key})
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    for addr, coin_data in data.items():
                        price = coin_data.get("usd", 0)
                        if price > 0:
                            prices[addr.lower()] = {
                                "price": float(price),
                                "source": "coingecko",
                            }
                except Exception:
                    continue

                # Rate limit between batches
                if i + 30 < len(tokens):
                    await asyncio.sleep(1.5)

    return prices


async def _fetch_coingecko_native_prices(
    symbols: list[str],
    api_key: str,
) -> dict[str, dict]:
    """Fetch current prices for native coins via CoinGecko /simple/price.

    Returns {symbol: {"price": float, "source": "coingecko"}}
    """
    if not symbols or not api_key:
        return {}

    cg_ids = []
    sym_to_id = {}
    for sym in symbols:
        cg_id = NATIVE_COIN_CG_ID.get(sym)
        if cg_id:
            cg_ids.append(cg_id)
            sym_to_id[cg_id] = sym

    if not cg_ids:
        return {}

    prices = {}
    ids_str = ",".join(cg_ids)
    url = (
        f"https://api.coingecko.com/api/v3/simple/price?"
        f"ids={ids_str}&vs_currencies=usd"
    )
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.get(url, headers={"x-cg-demo-api-key": api_key})
            if resp.status_code != 200:
                return {}
            data = resp.json()
            for cg_id, coin_data in data.items():
                price = coin_data.get("usd", 0)
                if price > 0:
                    sym = sym_to_id.get(cg_id)
                    if sym:
                        prices[sym] = {"price": float(price), "source": "coingecko"}
    except Exception:
        pass

    return prices


# ═══════════════════════════════════════════════════════════════════
# Portfolio computation
# ═══════════════════════════════════════════════════════════════════

async def _compute_portfolio(address: str, cg_api_key: str = "") -> dict:
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
                "price_confidence": None,  # set when the price comes from DefiLlama
                "price_source": "blockscout",  # default source
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

    # 2b. CoinGecko current-price enrichment (priority when API key is available)
    # Overrides both priced and unpriced tokens conservatively: only when CG
    # returns a price > 0. Native coins priced via /simple/price, token
    # contracts via /simple/token_price.
    if cg_api_key:
        cg_queries = []
        cg_native_syms = set()
        for item in items:
            if item.get("type") == "native":
                cg_native_syms.add(item["symbol"])
            elif item.get("contract_address"):
                cg_queries.append((item["chain"], item["contract_address"], item["symbol"]))

        cg_prices = {}

        # Native coins
        if cg_native_syms:
            np = await _fetch_coingecko_native_prices(list(cg_native_syms), cg_api_key)
            cg_prices.update(np)

        # Token contracts
        if cg_queries:
            tp = await _fetch_coingecko_current_prices(cg_queries, cg_api_key)
            # Merge: contract-address-based prices (key = address)
            for addr, entry in tp.items():
                cg_prices[addr] = entry

        if cg_prices:
            cg_enriched = 0
            cg_overrides = 0
            for item in items:
                if item.get("type") == "native":
                    sym = item["symbol"]
                    if sym in cg_prices:
                        entry = cg_prices[sym]
                        new_price = entry["price"]
                        if new_price > 0:
                            old_price = item["usd_price"]
                            item["usd_price"] = new_price
                            new_usd = item["balance"] * new_price
                            delta = new_usd - item["usd_value"]
                            item["usd_value"] = round(new_usd, 2)
                            item["price_unknown"] = False
                            item["price_source"] = entry.get("source", "coingecko")
                            total += delta
                            if old_price > 0:
                                cg_overrides += 1
                            else:
                                cg_enriched += 1
                else:
                    addr = item.get("contract_address", "").lower()
                    if addr and addr in cg_prices:
                        entry = cg_prices[addr]
                        new_price = entry["price"]
                        if new_price > 0:
                            old_price = item["usd_price"]
                            item["usd_price"] = new_price
                            new_usd = item["balance"] * new_price
                            delta = new_usd - item["usd_value"]
                            item["usd_value"] = round(new_usd, 2)
                            item["price_unknown"] = False
                            item["price_source"] = entry.get("source", "coingecko")
                            total += delta
                            if old_price > 0:
                                cg_overrides += 1
                            else:
                                cg_enriched += 1
            logger.info(
                f"[TRACE] CoinGecko enriched={cg_enriched} overrides={cg_overrides} items"
            )

    # Rebuild unpriced list after CoinGecko pass
    unpriced_tokens = []
    for item in items:
        if item["usd_price"] <= 0 and item["balance"] > 0 and not _is_spam(item.get("symbol", "")):
            addr = item.get("contract_address", "")
            if addr:
                unpriced_tokens.append((item["chain"], addr, item["symbol"]))
    if unpriced_tokens:
        llama_prices = await _fetch_defillama_current_prices(unpriced_tokens)
        if llama_prices:
            enriched = 0
            for item in items:
                if item["usd_price"] > 0:
                    continue
                addr = item.get("contract_address", "")
                if addr and addr.lower() in llama_prices:
                    entry = llama_prices[addr.lower()]
                    new_price = entry["price"]
                    item["usd_price"] = new_price
                    new_usd = item["balance"] * new_price
                    delta = new_usd - item["usd_value"]
                    item["usd_value"] = round(new_usd, 2)
                    item["price_unknown"] = False
                    item["price_confidence"] = entry.get("confidence")
                    item["price_source"] = "defillama"
                    total += delta
                    enriched += 1
            logger.info(
                f"[TRACE] DefiLlama enriched {enriched}/{len(unpriced_tokens)} unpriced tokens"
            )

    # 3b. Native coin wrapped fallback — when Blockscout gives no price for a native coin,
    # price it via its wrapped token on DefiLlama (e.g. HYPE ← WHYPE)
    if NATIVE_WRAPPED:
        unpriced_natives = []
        for item in items:
            if item["usd_price"] <= 0 and item.get("type") == "native" and item["chain"] in NATIVE_WRAPPED:
                unpriced_natives.append(item)

        if unpriced_natives:
            # Build queries: (chain, wrapped_address, symbol)
            native_queries = [
                (item["chain"], NATIVE_WRAPPED[item["chain"]], item["symbol"])
                for item in unpriced_natives
            ]
            try:
                native_prices = await _fetch_defillama_current_prices(native_queries)
                if native_prices:
                    enriched_native = 0
                    for item in unpriced_natives:
                        wrapped_addr = NATIVE_WRAPPED.get(item["chain"], "").lower()
                        if wrapped_addr and wrapped_addr in native_prices:
                            entry = native_prices[wrapped_addr]
                            new_price = entry["price"]
                            if new_price > 0:
                                item["usd_price"] = new_price
                                new_usd = item["balance"] * new_price
                                delta = new_usd - item["usd_value"]
                                item["usd_value"] = round(new_usd, 2)
                                item["price_unknown"] = False
                                item["price_confidence"] = entry.get("confidence")
                                item["price_source"] = "defillama"
                                total += delta
                                enriched_native += 1
                    logger.info(
                        f"[TRACE] Native wrapped enrich: {enriched_native}/{len(unpriced_natives)}"
                    )
            except Exception:
                logger.warning("[TRACE] Native wrapped price lookup failed")

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

    # 8. Compute DeFi aggregates (all DeFi categories, fine-grained)
    defi_usd = 0.0
    defi_breakdown = {}  # per-category subtotals
    for p in items:
        cat = p.get("category", "wallet")
        if _is_defi_category(cat):
            defi_usd += p["usd_value"]
            defi_breakdown[cat] = defi_breakdown.get(cat, 0) + p["usd_value"]

    # Round breakdown values to 2 decimals
    defi_breakdown = {k: round(v, 2) for k, v in defi_breakdown.items()}

    # 9. Build response
    result = {
        "address": address,
        "total_usd": round(total, 2),
        "defi_usd": round(defi_usd, 2),
        "staked_usd": round(defi_usd, 2),  # backward compat — total DeFi
        "defi_breakdown": defi_breakdown,
        "token_count": len(items),
        "chain_count": len([r for r in results if r["tokens"]]),
        "chains": {
            c: round(v, 2)
            for c, v in sorted(chain_totals.items(), key=lambda x: x[1], reverse=True)
        },
        # Wide safety cap only — the per-user layer (_apply_user_token_prefs)
        # classifies EVERY token (zero_value/spam auto-disable needs to see
        # them all) and applies its own tighter response caps (v2.12.2).
        # Old [:200] cap silently hid worthless tokens from classification.
        "tokens": items[:1000],
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
