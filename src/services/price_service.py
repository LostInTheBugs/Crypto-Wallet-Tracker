"""Price service — CoinGecko/DefiLlama price fetching, caching, and interpolation."""
import asyncio
import datetime
import os
import time as _time
import bisect

import aiosqlite
import httpx

DB_PATH = os.environ.get("DB_PATH", "/data/wallets.db")

# ── CoinGecko mapping ────────────────────────────────────────────

SYMBOL_TO_CG = {
    "eth": "ethereum", "weth": "ethereum", "matic": "matic-network", "pol": "polygon-ecosystem-token",
    "usdt": "tether", "usdc": "usd-coin", "dai": "dai", "wbtc": "wrapped-bitcoin", "btc": "bitcoin",
    "link": "chainlink", "uni": "uniswap", "aave": "aave", "crv": "curve-dao-token",
    "snx": "synthetix-network-token", "mkr": "maker", "comp": "compound-governance-token",
    "grt": "the-graph", "sand": "the-sandbox", "mana": "decentraland", "enj": "enjincoin",
    "bat": "basic-attention-token", "zrx": "0x", "1inch": "1inch", "ldo": "lido-dao",
    "op": "optimism", "arb": "arbitrum", "ape": "apecoin", "shib": "shiba-inu",
    "pepe": "pepe", "floki": "floki", "fet": "fetch-ai", "rndr": "render-token",
    "imx": "immutable-x", "axs": "axie-infinity",
    "gmx": "gmx", "dydx": "dydx", "stg": "stargate-finance", "woo": "woo-network",
    "ens": "ethereum-name-service", "lrc": "loopring", "blur": "blur",
    "strk": "starknet", "ena": "ethena", "eigen": "eigenlayer", "jup": "jupiter-exchange-solana",
    "bonk": "bonk", "wif": "dogwifcoin", "pyth": "pyth-network",
    "celo": "celo", "cusd": "celo-dollar", "creal": "celo-real",
    "zro": "layerzero", "joe": "joe", "magic": "treasure",
    "edu": "open-campus", "ube": "ubeswap", "gmx_dao": "gmx",
    "usdc.e": "usd-coin", "usdt0": "tether", "orbeth": "ethereum",
    "doge": "dogecoin", "wld": "worldcoin-wld",
    # New mappings for common DeFi tokens
    "wsteth": "wrapped-steth", "reth": "rocket-pool-eth", "morpho": "morpho",
    "sena": "sena", "thales": "thales", "nexo": "nexo", "adai": "aave-dai",
    "usde": "ethena-usde", "wormhole": "wormhole", "frax": "frax",
    "rseth": "kelp-dao-restaked-eth", "ezeth": "renzo-restaked-eth",
    "weeth": "wrapped-eeth", "susdc": "usd-coin", "ausdc": "usd-coin",
    "ceur": "celo-euro", "ceth": "celo", "pendle": "pendle",
    "mav": "maverick-protocol", "fluid": "fluid", "spectra": "spectra",
    "seam": "seamless-protocol", "logx": "logx", "hyper": "hypercycle",
    # Stables
    "eura": "monerium-eur-money", "usdt.e": "tether",
    "usd0": "usual-usd",
}

# ── Rate limiting ───────────────────────────────────────────────

_CG_LAST_CALL: float = 0.0
_CG_BACKOFF_UNTIL: float = 0.0


async def _cg_rate_limit_wait():
    """Enforce minimum 3-second gap between CoinGecko API calls."""
    global _CG_LAST_CALL
    elapsed = _time.time() - _CG_LAST_CALL
    if elapsed < 3.0:
        await asyncio.sleep(3.0 - elapsed)
    _CG_LAST_CALL = _time.time()


# ── Price interpolation ─────────────────────────────────────────

def _interpolate_price(prices: dict, ts_ms: int) -> float:
    """Find closest price before or at ts_ms."""
    keys = sorted(prices.keys())
    if not keys:
        return 0.0
    best = None
    for k in keys:
        if k <= ts_ms:
            best = k
        else:
            break
    if best is None:
        best = keys[0]  # earliest available
    return prices.get(best, 0.0)


def _price_at(sym_lower: str, ts_ms: int,
              sorted_prices: dict, fallback_prices: dict,
              current_prices: dict) -> float:
    """Get price for sym_lower at or before ts_ms.

    Fallback chain (priority order):
      1. DefiLlama/CoinGecko historical price series (sorted_prices)
      2. Last known transaction USD price (fallback_prices)
      3. Current portfolio live price (current_prices)
      4. 0.0 — no price data available

    Timestamp normalization: if ts_ms appears to be in seconds (< 10000000000,
    i.e. before year 2286 in ms), multiply by 1000 to convert to milliseconds.
    """
    # Normalize timestamp to milliseconds
    if ts_ms < 10000000000:
        ts_ms = ts_ms * 1000

    sp = sorted_prices.get(sym_lower)
    if sp:
        timestamps = [p[0] for p in sp]
        idx = bisect.bisect_right(timestamps, ts_ms) - 1
        if idx >= 0:
            return sp[idx][1]
        # Before first point — return earliest available
        return sp[0][1]

    if sym_lower in fallback_prices:
        return fallback_prices[sym_lower]

    return current_prices.get(sym_lower, 0.0)


# ── Price cache I/O ─────────────────────────────────────────────

async def _load_prices_from_cache(sym_lower: str) -> dict:
    """Load cached daily prices from price_history. Returns {ts_ms: price} or {}."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT date, price_usd FROM price_history WHERE token_symbol=? ORDER BY date ASC",
            (sym_lower,))
        rows = await cur.fetchall()
    if not rows:
        return {}
    result = {}
    for r in rows:
        try:
            ts = int(_time.mktime(_time.strptime(r["date"], "%Y-%m-%d"))) * 1000
            result[ts] = r["price_usd"]
        except Exception:
            pass
    return result


async def _save_prices_to_cache(sym_lower: str, prices: dict):
    """Save daily prices to price_history (idempotent: INSERT OR REPLACE)."""
    if not prices:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        for ts_ms, price in prices.items():
            date_str = datetime.datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
            await db.execute(
                "INSERT OR REPLACE INTO price_history (token_symbol, date, price_usd) VALUES (?, ?, ?)",
                (sym_lower, date_str, price))
        await db.commit()


# ── DefiLlama batch fetch ───────────────────────────────────────

async def _fetch_defillama_batch(mapped_syms: dict, from_ts: int, to_ts: int) -> tuple:
    """Fetch prices from DefiLlama. Fetches tokens individually for reliability.
    Returns (prices_dict, calls_ok, calls_failed)."""
    prices = {}
    ok = 0
    failed = 0
    syms_list = list(mapped_syms.items())  # [(sym, cg_id), ...]
    window_days = 200  # Fetch up to 200 days per call (safe margin below 500-point limit)

    for sym_lower, cg_id in syms_list:
        token_ok = False
        token_points = {}  # {ts_ms: price}

        # Split into time windows
        window_start = from_ts
        while window_start < to_ts:
            window_end = min(to_ts, window_start + window_days * 86400)
            span_days = max(1, (window_end - window_start) // 86400)
            url = f"https://coins.llama.fi/chart/coingecko:{cg_id}?start={window_start}&span={span_days}&period=1d"

            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(timeout=30) as c:
                        resp = await c.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        coin_data = data.get("coins", {}).get(f"coingecko:{cg_id}", {})
                        for pt in coin_data.get("prices", []):
                            # DefiLlama returns timestamps in seconds → convert to ms
                            token_points[pt["timestamp"] * 1000] = pt["price"]
                        ok += 1
                        token_ok = True
                        break
                    elif resp.status_code == 429:
                        await asyncio.sleep(2 ** attempt * 3)  # Longer backoff for rate limits
                    else:
                        await asyncio.sleep(2 ** attempt)
                except Exception:
                    await asyncio.sleep(2 ** attempt)
            else:
                failed += 1
                token_ok = False
                break  # Stop trying more windows for this token

            window_start = window_end
            if window_start < to_ts:
                await asyncio.sleep(1.0)  # Rate limit between windows

        if token_ok and token_points:
            await _save_prices_to_cache(sym_lower, token_points)
            prices[sym_lower] = token_points

        # Rate limit between tokens
        await asyncio.sleep(2.0)

    return prices, ok, failed


# ── CoinGecko batch fetch (fallback) ────────────────────────────

async def _fetch_coingecko_batch(mapped_syms: dict, from_ts: int, to_ts: int, api_key: str) -> tuple:
    """Fallback: CoinGecko if API key is available."""
    prices = {}
    ok = 0
    failed = 0
    for sym_lower, cg_id in mapped_syms.items():
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=45) as cg:
                    url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart/range"
                    resp = await cg.get(url, params={"vs_currency": "usd", "from": from_ts, "to": to_ts},
                                        headers={"x-cg-demo-api-key": api_key})
                    if resp.status_code == 200:
                        data = resp.json()
                        prices[sym_lower] = {p[0]: p[1] for p in data.get("prices", [])}
                        await _save_prices_to_cache(sym_lower, prices[sym_lower])
                        ok += 1
                        break
                    elif resp.status_code == 429:
                        await asyncio.sleep(2 ** attempt * 5)
                    else:
                        await asyncio.sleep(2 ** attempt)
            except Exception:
                await asyncio.sleep(2 ** attempt)
        else:
            prices[sym_lower] = {}
            failed += 1
        await asyncio.sleep(1.0)  # rate limit
    return prices, ok, failed


# ── Price enrichment (main orchestrator) ────────────────────────

async def _fetch_prices_per_token(user_id: int, wallet_address: str, _get_user_cg_key=None) -> dict:
    """Fetch historical daily prices using DefiLlama API (free, no key).
    Caches results in price_history table. Enriches transactions."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT DISTINCT LOWER(token_symbol) as sym FROM transactions WHERE user_id=? AND wallet_address=?",
            (user_id, wallet_address))
        symbols = [r["sym"] for r in await cur.fetchall()]

    prices = {}           # {sym_lower: {ts_ms: price_usd}}
    unmapped = []         # tokens not in SYMBOL_TO_CG
    degraded = []         # mapped tokens where API failed
    api_calls_ok = 0
    api_calls_failed = 0

    # Separate mapped vs unmapped, prioritize by value
    mapped_syms = {}  # sym_lower → cg_id
    for s in symbols:
        cg_id = SYMBOL_TO_CG.get(s)
        if cg_id:
            mapped_syms[s] = cg_id
        else:
            unmapped.append(s.upper())

    if mapped_syms:
        # Determine date range from earliest tx
        now = int(_time.time())
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT MIN(block_time) as earliest FROM transactions WHERE user_id=? AND wallet_address=?",
                (user_id, wallet_address))
            row = await cur.fetchone()
        if row and row["earliest"]:
            try:
                from_ts = int(_time.mktime(_time.strptime(row["earliest"][:19], "%Y-%m-%d %H:%M:%S"))) - 86400
            except Exception:
                from_ts = now - 365 * 86400
        else:
            from_ts = now - 365 * 86400

        # Try loading from cache first
        for sym in list(mapped_syms.keys()):
            cached = await _load_prices_from_cache(sym)
            if cached:
                prices[sym] = cached
                del mapped_syms[sym]  # no need to fetch

        # Sort remaining tokens by total USD value (descending) to prioritize important ones
        if mapped_syms:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    f"SELECT LOWER(token_symbol) as sym, SUM(usd_value) as total_val FROM transactions WHERE user_id=? AND usd_value>0 GROUP BY sym ORDER BY total_val DESC",
                    (user_id,))
                val_rows = await cur.fetchall()
            sym_values = {r["sym"]: r["total_val"] for r in val_rows}
            # Sort: highest value first
            sorted_mapped = sorted(mapped_syms.items(), key=lambda x: sym_values.get(x[0], 0), reverse=True)
            mapped_syms = dict(sorted_mapped)

        # Fetch remaining tokens from DefiLlama in batches
        if mapped_syms:
            fetched_prices, ok, failed = await _fetch_defillama_batch(mapped_syms, from_ts, now)
            api_calls_ok += ok
            api_calls_failed += failed
            for sym, p_dict in fetched_prices.items():
                prices[sym] = p_dict
                # Mark as degraded if API failed for this token
                if not p_dict:
                    degraded.append(sym.upper())

            # Try CoinGecko as fallback if API key is set
            if _get_user_cg_key:
                cg_key = await _get_user_cg_key(user_id)
                still_missing = {s: c for s, c in mapped_syms.items() if not prices.get(s)}
                if cg_key and still_missing:
                    cg_prices, cg_ok, cg_failed = await _fetch_coingecko_batch(still_missing, from_ts, now, cg_key)
                    api_calls_ok += cg_ok
                    api_calls_failed += cg_failed
                    for sym, p_dict in cg_prices.items():
                        if p_dict:
                            prices[sym] = p_dict
                        else:
                            degraded.append(sym.upper())

        # Any mapped token still without prices → degraded
        for sym in mapped_syms:
            if not prices.get(sym):
                degraded.append(sym.upper())

    # Enrich transactions
    enriched = 0
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, LOWER(token_symbol) as sym, amount, block_time FROM transactions WHERE user_id=? AND wallet_address=? AND usd_price=0",
            (user_id, wallet_address))
        rows = await cur.fetchall()
        for r in rows:
            sym_prices = prices.get(r["sym"], {})
            if not sym_prices:
                continue
            try:
                ts_ms = int(_time.mktime(_time.strptime(r["block_time"][:19], "%Y-%m-%d %H:%M:%S"))) * 1000
            except Exception:
                continue
            price = _interpolate_price(sym_prices, ts_ms)
            if price > 0:
                usd_val = r["amount"] * price
                await db.execute("UPDATE transactions SET usd_price=?, usd_value=? WHERE id=?", (price, round(usd_val, 2), r["id"]))
                enriched += 1
        await db.commit()

    return {
        "enriched": enriched, "unmapped": unmapped, "prices": prices,
        "degraded": degraded,
        "price_calls_ok": api_calls_ok, "price_calls_failed": api_calls_failed,
    }
