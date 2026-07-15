"""
Crypto Wallet Tracker — EVM portfolio aggregator.
Multi-chain via Blockscout API, multi-wallet, user accounts.
"""
from collections import defaultdict
from fastapi import FastAPI, Query, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager
import httpx, asyncio, jwt, bcrypt, aiosqlite, os, datetime, time as _time, bisect

DB_PATH = os.environ.get("DB_PATH", "/data/wallets.db")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "change-me")
TOKEN_EXPIRY = 30  # days


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                address TEXT NOT NULL,
                label TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                total_usd REAL NOT NULL,
                token_count INTEGER DEFAULT 0,
                token_quantity REAL DEFAULT 0,
                token_symbol TEXT DEFAULT NULL,
                chain TEXT DEFAULT NULL,
                wallet_label TEXT DEFAULT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                wallet_address TEXT NOT NULL,
                token_symbol TEXT NOT NULL,
                token_name TEXT DEFAULT '',
                amount REAL NOT NULL,
                usd_value REAL DEFAULT 0,
                usd_price REAL DEFAULT 0,
                chain TEXT DEFAULT 'ethereum',
                tx_hash TEXT DEFAULT '',
                block_time TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        # New: daily_history table for idempotent daily snapshots
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                wallet_address TEXT NOT NULL,
                date TEXT NOT NULL,
                value_usd REAL NOT NULL DEFAULT 0,
                cost_basis_usd REAL NOT NULL DEFAULT 0,
                net_flows_usd REAL NOT NULL DEFAULT 0,
                token_symbol TEXT DEFAULT NULL,
                chain TEXT DEFAULT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        try: await db.execute("CREATE INDEX IF NOT EXISTS idx_dh_user_date ON daily_history(user_id, date)")
        except: pass
        try: await db.execute("CREATE INDEX IF NOT EXISTS idx_dh_wallet ON daily_history(wallet_address, date)")
        except: pass
        # Price cache for DefiLlama
        await db.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                token_symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                price_usd REAL NOT NULL,
                PRIMARY KEY(token_symbol, date)
            )
        """)
        # Migrations
        for col, typ in [("token_quantity", "REAL DEFAULT 0"), ("token_symbol", "TEXT DEFAULT NULL"),
                          ("chain", "TEXT DEFAULT NULL"), ("wallet_label", "TEXT DEFAULT NULL")]:
            try: await db.execute(f"ALTER TABLE snapshots ADD COLUMN {col} {typ}")
            except: pass
        try: await db.execute("ALTER TABLE transactions ADD COLUMN direction TEXT DEFAULT 'in'")
        except: pass
        # Purge old polluted snapshot data
        await db.execute("DELETE FROM snapshots")
        await db.commit()
    yield


app = FastAPI(lifespan=lifespan, title="Crypto Wallet Tracker")


# ── Database helper ──────────────────────────────────────────────

async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db


# ── Auth ─────────────────────────────────────────────────────────

async def get_current_user(request: Request, db=Depends(get_db)):
    token = request.cookies.get("token")
    if not token:
        raise HTTPException(401, "Non authentifié")
    try:
        payload = jwt.decode(token, SESSION_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Token invalide")
    cur = await db.execute("SELECT id, username FROM users WHERE id=?", (payload["sub"],))
    user = await cur.fetchone()
    if not user:
        raise HTTPException(401, "Utilisateur introuvable")
    return {"id": user["id"], "username": user["username"]}


@app.post("/api/auth/register")
async def register(request: Request, db=Depends(get_db)):
    data = await request.json()
    username = (data.get("username") or "").strip().lower()
    password = data.get("password", "")
    if len(username) < 3 or len(password) < 4:
        raise HTTPException(400, "Username >=3, password >=4")
    if await (await db.execute("SELECT id FROM users WHERE username=?", (username,))).fetchone():
        raise HTTPException(409, "Ce compte existe déjà")
    h = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
    await db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, h.decode()))
    await db.commit()
    return {"ok": True, "msg": "Compte créé"}


@app.post("/api/auth/login")
async def login(request: Request, db=Depends(get_db)):
    data = await request.json()
    username = (data.get("username") or "").strip().lower()
    password = data.get("password", "")
    cur = await db.execute("SELECT id, password_hash FROM users WHERE username=?", (username,))
    user = await cur.fetchone()
    if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        raise HTTPException(401, "Identifiants invalides")
    token = jwt.encode(
        {"sub": user["id"], "exp": datetime.datetime.utcnow() + datetime.timedelta(days=TOKEN_EXPIRY)},
        SESSION_SECRET, algorithm="HS256")
    resp = JSONResponse({"ok": True, "username": username})
    resp.set_cookie("token", token, max_age=TOKEN_EXPIRY * 86400, httponly=True, samesite="lax")
    return resp


@app.get("/api/auth/me")
async def me(user=Depends(get_current_user)):
    return {"username": user["username"]}


@app.post("/api/auth/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("token")
    return resp


@app.put("/api/auth/password")
async def change_password(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    data = await request.json()
    old = data.get("old_password", "")
    new = data.get("new_password", "")
    if len(new) < 4:
        raise HTTPException(400, "Nouveau mot de passe >= 4 caractères")
    cur = await db.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],))
    row = await cur.fetchone()
    if not row or not bcrypt.checkpw(old.encode(), row["password_hash"].encode()):
        raise HTTPException(401, "Ancien mot de passe incorrect")
    h = bcrypt.hashpw(new.encode(), bcrypt.gensalt())
    await db.execute("UPDATE users SET password_hash=? WHERE id=?", (h.decode(), user["id"]))
    await db.commit()
    return {"ok": True, "msg": "Mot de passe modifié"}


# ── Wallet CRUD ──────────────────────────────────────────────────

@app.get("/api/wallets")
async def list_wallets(user=Depends(get_current_user), db=Depends(get_db)):
    cur = await db.execute("SELECT id, address, label FROM wallets WHERE user_id=? ORDER BY created_at", (user["id"],))
    return [{"id": r["id"], "address": r["address"], "label": r["label"]} for r in await cur.fetchall()]


@app.post("/api/wallets")
async def add_wallet(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    data = await request.json()
    address = (data.get("address") or "").strip()
    label = (data.get("label") or "").strip()[:50]
    if not address.startswith("0x") or len(address) != 42:
        raise HTTPException(400, "Adresse EVM invalide")
    await db.execute("INSERT INTO wallets (user_id, address, label) VALUES (?, ?, ?)", (user["id"], address, label))
    await db.commit()
    asyncio.create_task(_fetch_then_rebuild(user["id"], address))
    return {"ok": True}


@app.delete("/api/wallets/{wallet_id}")
async def del_wallet(wallet_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    cur = await db.execute("SELECT address FROM wallets WHERE id=? AND user_id=?", (wallet_id, user["id"]))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Wallet introuvable")
    address = row["address"]
    await db.execute("DELETE FROM transactions WHERE wallet_address=? AND user_id=?", (address, user["id"]))
    await db.execute("DELETE FROM daily_history WHERE wallet_address=? AND user_id=?", (address, user["id"]))
    await db.execute("DELETE FROM snapshots WHERE user_id=?", (user["id"],))
    _portfolio_cache.pop(address, None)
    await db.execute("DELETE FROM wallets WHERE id=? AND user_id=?", (wallet_id, user["id"]))
    await db.commit()
    return {"ok": True}


@app.put("/api/wallets/{wallet_id}")
async def edit_wallet(wallet_id: int, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    data = await request.json()
    label = (data.get("label") or "").strip()[:50]
    await db.execute("UPDATE wallets SET label=? WHERE id=? AND user_id=?", (label, wallet_id, user["id"]))
    await db.commit()
    return {"ok": True}


# ── Portfolio engine ─────────────────────────────────────────────

CHAINS = {
    "ethereum": "eth.blockscout.com",
    "base":     "base.blockscout.com",
    "optimism": "explorer.optimism.io",
    "arbitrum": "arbitrum.blockscout.com",
    "polygon":  "polygon.blockscout.com",
    "gnosis":   "gnosis.blockscout.com",
    "zksync":   "zksync.blockscout.com",
    "celo":     "celo.blockscout.com",
    "scroll":   "scroll.blockscout.com",
}

_portfolio_cache = {}


async def fetch_chain(client, chain, host, address):
    try:
        r = await client.get(f"https://{host}/api/v2/addresses/{address}/tokens", timeout=15)
        if r.status_code != 200:
            return {"chain": chain, "tokens": [], "error": f"HTTP {r.status_code}"}
        data = r.json()
        tokens = []
        for item in data.get("items", []):
            if item is None: continue
            t = item.get("token") or {}
            raw = item.get("value")
            tokens.append({
                "name": t.get("name", "Unknown"),
                "symbol": t.get("symbol", "?"),
                "decimals": int(t.get("decimals") or 18),
                "balance_raw": str(raw) if raw else "0",
                "usd_price": float(t.get("exchange_rate") or 0),
                "icon": t.get("icon_url", ""),
                "type": t.get("type", "ERC-20"),
            })
        return {"chain": chain, "tokens": tokens, "error": None}
    except Exception as e:
        return {"chain": chain, "tokens": [], "error": str(e)[:100]}


async def _compute_portfolio(address: str) -> dict:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(*[fetch_chain(client, c, h, address) for c, h in CHAINS.items()])

    items, total = [], 0.0
    for r in results:
        for t in r["tokens"]:
            try: bal = int(t["balance_raw"]) / (10 ** t["decimals"])
            except: bal = 0
            usd = bal * t["usd_price"]; total += usd
            items.append({
                "chain": r["chain"], "name": t["name"], "symbol": t["symbol"],
                "balance": round(bal, 6), "usd_value": round(usd, 2),
                "usd_price": t["usd_price"], "icon": t["icon"], "type": t["type"],
            })

    items = [p for p in items if p["usd_value"] >= 0.01]
    items.sort(key=lambda x: x["usd_value"], reverse=True)

    chain_totals = {}
    for p in items: chain_totals[p["chain"]] = chain_totals.get(p["chain"], 0) + p["usd_value"]

    return {
        "address": address,
        "total_usd": round(total, 2),
        "token_count": len(items),
        "chain_count": len([r for r in results if r["tokens"]]),
        "chains": {c: round(v, 2) for c, v in sorted(chain_totals.items(), key=lambda x: x[1], reverse=True)},
        "tokens": items[:200],
        "errors": [{"chain": r["chain"], "error": r["error"]} for r in results if r["error"]],
        "cached": False,
    }


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
}


# ── Transactions fetch ───────────────────────────────────────────

_import_progress = {}
_last_tx_refresh = {}


async def _fetch_transactions_for_wallet(user_id: int, address: str) -> int:
    total_tx = 0
    for chain, host in CHAINS.items():
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as bc:
                url = f"https://{host}/api/v2/addresses/{address}/token-transfers"
                params: dict = {"type": "ERC-20,ERC-721,ERC-1155"}
                for page in range(5):
                    resp = await bc.get(url, params=params)
                    if resp.status_code != 200: break
                    data = resp.json()
                    items = data.get("items", [])
                    if not items: break
                    async with aiosqlite.connect(DB_PATH) as db:
                        for item in items:
                            token = item.get("token") or {}
                            tx_hash = item.get("transaction_hash") or item.get("tx_hash", "")
                            if tx_hash:
                                cur2 = await db.execute("SELECT id FROM transactions WHERE tx_hash=? AND user_id=?", (tx_hash, user_id))
                                if await cur2.fetchone(): continue
                            try:
                                amount = int(item.get("total", {}).get("value", "0") or "0") / (10 ** (int(token.get("decimals") or 18)))
                            except:
                                amount = 0
                            if amount == 0: continue
                            symbol = token.get("symbol", "?")
                            name = token.get("name", "Unknown")
                            ts = item.get("timestamp", "")
                            to_addr = (item.get("to") or {}).get("hash", "")
                            direction = "in" if to_addr.lower() == address.lower() else "out"
                            await db.execute(
                                "INSERT INTO transactions (user_id, wallet_address, token_symbol, token_name, amount, chain, tx_hash, block_time, direction) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (user_id, address, symbol, name, amount, chain, tx_hash, ts[:19].replace("T", " ") if ts else "", direction))
                            total_tx += 1
                        await db.commit()
                    nxt = data.get("next_page_params")
                    if not nxt: break
                    params = {**params, **nxt}
        except Exception:
            continue
    return total_tx


# ── Price enrichment: DefiLlama (primary) + CoinGecko (optional) ─

async def _fetch_prices_per_token(user_id: int, wallet_address: str) -> dict:
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

    # Separate mapped vs unmapped
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
            except:
                from_ts = now - 365 * 86400
        else:
            from_ts = now - 365 * 86400

        # Try loading from cache first
        for sym in list(mapped_syms.keys()):
            cached = await _load_prices_from_cache(sym)
            if cached:
                prices[sym] = cached
                del mapped_syms[sym]  # no need to fetch

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
            cg_key = os.environ.get("COINGECKO_API_KEY", "")
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
            except:
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
        except:
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


async def _fetch_defillama_batch(mapped_syms: dict, from_ts: int, to_ts: int) -> tuple:
    """Fetch prices from DefiLlama in batches. Returns (prices_dict, calls_ok, calls_failed)."""
    prices = {}
    ok = 0
    failed = 0
    syms_list = list(mapped_syms.items())  # [(sym, cg_id), ...]
    
    # Build batches of up to 10 tokens per call
    for i in range(0, len(syms_list), 5):
        batch = syms_list[i:i+5]
        ids = ",".join(f"coingecko:{cg_id}" for _, cg_id in batch)
        max_span = max(10, 500 // len(batch))  # DefiLlama limit: 500 points total
        
        # Split into windows respecting the 500-point limit
        all_points = {}  # {sym: {ts_sec: price}}
        window_start = from_ts
        while window_start < to_ts:
            window_span = min(max_span, (to_ts - window_start) // 86400 + 1)
            url = f"https://coins.llama.fi/chart/{ids}?start={window_start}&span={window_span}&period=1d"
            
            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(timeout=30) as c:
                        resp = await c.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        coins = data.get("coins", {})
                        for coin_id, coin_data in coins.items():
                            sym = None
                            for s, cg in batch:
                                if f"coingecko:{cg}" == coin_id:
                                    sym = s
                                    break
                            if sym:
                                if sym not in all_points:
                                    all_points[sym] = {}
                                for pt in coin_data.get("prices", []):
                                    # DefiLlama returns timestamps in seconds
                                    all_points[sym][pt["timestamp"] * 1000] = pt["price"]
                        ok += 1
                        break
                    elif resp.status_code == 429:
                        await asyncio.sleep(2 ** attempt * 2)
                    else:
                        await asyncio.sleep(2 ** attempt)
                except Exception:
                    await asyncio.sleep(2 ** attempt)
            else:
                failed += 1
            
            window_start += max_span * 86400
            if window_start < to_ts:
                await asyncio.sleep(0.5)  # rate limit
        
        # Save to cache
        for sym, pts in all_points.items():
            await _save_prices_to_cache(sym, pts)
            prices[sym] = pts
    
    return prices, ok, failed


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

async def _cg_rate_limit_wait():
    global _CG_LAST_CALL
    elapsed = _time.time() - _CG_LAST_CALL
    if elapsed < 3.0:
        await asyncio.sleep(3.0 - elapsed)
    _CG_LAST_CALL = _time.time()


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


# ── History rebuild ──────────────────────────────────────────────

async def _rebuild_history(user_id: int, wallet_address: str):
    """Idempotent daily history rebuild: reconstruct balances from transactions,
    value them with CoinGecko prices, compute per-token cost basis."""

    # 1. Fetch prices per token (ALL mapped tokens, returns price series)
    price_result = await _fetch_prices_per_token(user_id, wallet_address)
    unmapped = set(u.lower() for u in price_result.get("unmapped", []))
    degraded = set(d.lower() for d in price_result.get("degraded", []))
    price_series = price_result.get("prices", {})
    # Exclude both unmapped AND degraded tokens from value/cost computation
    excluded = unmapped | degraded  # {sym_lower: {ts_ms: price}}

    # 2. Preload all transactions + sort price keys once
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT token_symbol, amount, usd_price, direction, block_time, chain FROM transactions WHERE user_id=? AND wallet_address=? ORDER BY block_time ASC",
            (user_id, wallet_address))
        txs = await cur.fetchall()
    if not txs:
        return {"ok": True, "days": 0, "unmapped_tokens": list(unmapped)}

    # Pre-sort price keys for fast binary search
    sorted_prices = {}
    for sym, p_dict in price_series.items():
        if p_dict:
            sorted_prices[sym] = sorted(p_dict.items())  # [(ts_ms, price), ...]

    # Build fallback prices from transactions (last known usd_price per token)
    fallback_prices = {}
    for tx in txs:
        sym = tx["token_symbol"].lower()
        if tx["usd_price"] > 0:
            fallback_prices[sym] = tx["usd_price"]  # last wins

    def _price_at(sym_lower: str, ts_ms: int) -> float:
        """Get price for sym_lower at or before ts_ms. Fallback: fallback_prices, then 0."""
        sp = sorted_prices.get(sym_lower)
        if sp:
            idx = bisect.bisect_right([p[0] for p in sp], ts_ms) - 1
            if idx >= 0:
                return sp[idx][1]
            return sp[0][1]  # before first point
        return fallback_prices.get(sym_lower, 0.0)

    # 3. Determine date range
    first_date = txs[0]["block_time"][:10]
    last_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")

    # 4. Build daily balance deltas (in memory — no SQL in loop)
    # Per day: {date: {sym: {"delta": float}}}
    daily_deltas: dict = defaultdict(dict)
    for tx in txs:
        sym = tx["token_symbol"].lower()
        date = tx["block_time"][:10]
        amount = tx["amount"] or 0
        price = tx["usd_price"] or 0

        if sym in excluded or (not SYMBOL_TO_CG.get(sym) and price == 0):
            excluded.add(sym)
            continue

        if date not in daily_deltas:
            daily_deltas[date] = {}
        if sym not in daily_deltas[date]:
            daily_deltas[date][sym] = 0.0

        if tx["direction"] == "in":
            daily_deltas[date][sym] += amount
        else:
            daily_deltas[date][sym] -= amount

    # 5. Generate daily history (pure computation, no I/O)
    date_cursor = datetime.datetime.strptime(first_date, "%Y-%m-%d")
    end_date = datetime.datetime.strptime(last_date, "%Y-%m-%d")

    balances: dict = defaultdict(float)   # sym → cumulative balance
    costs: dict = defaultdict(float)       # sym → cumulative cost basis
    daily_rows = []

    while date_cursor <= end_date:
        date_str = date_cursor.strftime("%Y-%m-%d")
        day_deltas = daily_deltas.get(date_str, {})

        # Compute day timestamp (noon UTC) for price lookup
        day_ts_ms = int(date_cursor.timestamp()) * 1000

        # Apply balance deltas + update per-token cost
        for sym, delta in day_deltas.items():
            old_bal = balances[sym]
            new_bal = max(0.0, old_bal + delta)

            if delta > 0 and old_bal >= 0:
                # Incoming: add cost at day's price
                tx_price = _price_at(sym, day_ts_ms)
                costs[sym] += delta * tx_price
            elif delta < 0 and old_bal > 0:
                # Outgoing: remove at average cost
                avg_cost = costs[sym] / old_bal if old_bal > 0 else 0.0
                costs[sym] = max(0.0, costs[sym] - abs(delta) * avg_cost)

            balances[sym] = new_bal
            if new_bal == 0:
                costs[sym] = 0.0  # fully exited this token

        # Compute daily value: sum(balance × price_at_date)
        value = 0.0
        for sym, bal in balances.items():
            if bal <= 0 or sym in excluded:
                continue
            if not SYMBOL_TO_CG.get(sym):
                continue
            p = _price_at(sym, day_ts_ms)
            if p > 0:
                value += bal * p

        # Net flows: sum(delta × price_at_date)
        net_flows = 0.0
        for sym, delta in day_deltas.items():
            p = _price_at(sym, day_ts_ms)
            net_flows += delta * p

        cost_basis = sum(costs.values())

        daily_rows.append((user_id, wallet_address, date_str,
                          round(value, 2), round(max(0, cost_basis), 2),
                          round(net_flows, 2), None, None))
        date_cursor += datetime.timedelta(days=1)

    # 6. Write to daily_history (idempotent)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM daily_history WHERE user_id=? AND wallet_address=?", (user_id, wallet_address))
        for row in daily_rows:
            await db.execute(
                "INSERT INTO daily_history (user_id, wallet_address, date, value_usd, cost_basis_usd, net_flows_usd, token_symbol, chain) VALUES (?,?,?,?,?,?,?,?)",
                row)
        await db.commit()

    return {"ok": True, "days": len(daily_rows), "unmapped_tokens": sorted(unmapped),
            "degraded_tokens": sorted(degraded),
            "price_calls_ok": price_result.get("price_calls_ok", 0),
            "price_calls_failed": price_result.get("price_calls_failed", 0),
            "tokens_with_series": len(price_series)}


# ── Import pipeline ──────────────────────────────────────────────

async def _fetch_then_rebuild(user_id: int, address: str):
    try:
        _import_progress[user_id] = {"stage": "fetch", "done": 0, "total": len(CHAINS), "in_done": 0, "in_total": 0, "out_done": 0, "out_total": 0}
        count = await _fetch_transactions_for_wallet(user_id, address)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT direction, COUNT(*) as c FROM transactions WHERE user_id=? AND usd_price=0 GROUP BY direction", (user_id,))
            dirs = {r["direction"]: r["c"] for r in await cur.fetchall()}
        in_total = dirs.get("in", 0)
        out_total = dirs.get("out", 0)
        _import_progress[user_id] = {"stage": "enrich", "done": 0, "total": count, "in_done": in_total, "in_total": in_total, "out_done": out_total, "out_total": out_total}
        result = await _rebuild_history(user_id, address)
        _import_progress[user_id] = {"stage": "done", "done": count, "total": count, "unmapped": result.get("unmapped_tokens", [])}
    except Exception:
        _import_progress[user_id] = {"stage": "done", "done": 0, "total": 0, "error": True}


@app.get("/api/import/progress")
async def import_progress(user=Depends(get_current_user)):
    return _import_progress.get(user["id"], {"stage": "idle", "done": 0, "total": 0})


# ── Portfolio endpoint ───────────────────────────────────────────

@app.get("/api/portfolio")
async def portfolio(address: str = Query(...), force: bool = Query(False), user=Depends(get_current_user)):
    if not address.startswith("0x"):
        raise HTTPException(400, "Adresse invalide")

    now = _time.time()
    if now - _last_tx_refresh.get(user["id"], 0) > 86400:
        _last_tx_refresh[user["id"]] = now
        asyncio.create_task(_daily_tx_refresh(user["id"]))

    entry = _portfolio_cache.get(address)
    if not force and entry and (now - entry["ts"]) < 3600:
        data = dict(entry["data"])
        data["cached"] = True
        return data

    data = await _compute_portfolio(address)
    _portfolio_cache[address] = {"data": data, "ts": now}

    # Save intraday snapshot for dashboard mini-chart
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT created_at FROM snapshots WHERE user_id=? AND token_symbol IS NULL ORDER BY created_at DESC LIMIT 1",
                (user["id"],))
            last = await cur.fetchone()
            if not last or (_time.time() - _time.mktime(_time.strptime(last["created_at"], "%Y-%m-%d %H:%M:%S"))) > 600:
                await db.execute(
                    "INSERT INTO snapshots (user_id, total_usd, token_count) VALUES (?, ?, ?)",
                    (user["id"], data["total_usd"], data["token_count"]))
                await db.commit()
    except Exception:
        pass

    return data


async def _daily_tx_refresh(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT address FROM wallets WHERE user_id=?", (user_id,))
        wallets_list = await cur.fetchall()
    for w in wallets_list:
        count = await _fetch_transactions_for_wallet(user_id, w["address"])
        if count > 0:
            await _rebuild_history(user_id, w["address"])


# ── Snapshots / History API ──────────────────────────────────────

@app.get("/api/snapshots")
async def get_snapshots(token: str = Query(None), wallet: str = Query(None), chain: str = Query(None),
                        user=Depends(get_current_user), db=Depends(get_db)):
    """Returns daily history as snapshots for chart compatibility."""
    conditions = ["user_id=?", str(user["id"])]
    params = [user["id"]]

    if token:
        conditions.append("token_symbol=?")
        params.append(token.upper())
    else:
        conditions.append("token_symbol IS NULL")

    if wallet and wallet != "ALL":
        conditions.append("wallet_address=?")
        params.append(wallet)
    if chain:
        conditions.append("chain=?")
        params.append(chain)

    where = " AND ".join(conditions)
    cur = await db.execute(
        f"SELECT value_usd as total_usd, 0 as token_quantity, 0 as token_count, cost_basis_usd as cost_basis, date FROM daily_history WHERE {where} ORDER BY date ASC",
        tuple(params))
    rows = await cur.fetchall()

    result = []
    for r in rows:
        result.append({
            "total_usd": r["total_usd"],
            "quantity": 0,
            "token_count": 0,
            "date": r["date"],
            "cost_basis": r["cost_basis"],
        })
    return result


@app.get("/api/snapshots/tokens")
async def get_snapshot_tokens(user=Depends(get_current_user), db=Depends(get_db)):
    cur = await db.execute(
        "SELECT DISTINCT token_symbol FROM daily_history WHERE user_id=? AND token_symbol IS NOT NULL ORDER BY token_symbol",
        (user["id"],))
    return [r["token_symbol"] for r in await cur.fetchall()]


@app.post("/api/snapshots/backfill")
async def backfill_snapshots(user=Depends(get_current_user), db=Depends(get_db)):
    cur = await db.execute("SELECT address FROM wallets WHERE user_id=?", (user["id"],))
    wallet_rows = await cur.fetchall()
    if not wallet_rows:
        raise HTTPException(400, "Aucun wallet")
    results = []
    for w in wallet_rows:
        result = await _rebuild_history(user["id"], w["address"])
        results.append(result)
    # Aggregate
    total_days = sum(r.get("days", 0) for r in results)
    all_unmapped = []
    all_degraded = []
    total_ok = 0
    total_failed = 0
    total_series = 0
    for r in results:
        all_unmapped.extend(r.get("unmapped_tokens", []))
        all_degraded.extend(r.get("degraded_tokens", []))
        total_ok += r.get("price_calls_ok", 0)
        total_failed += r.get("price_calls_failed", 0)
        total_series += r.get("tokens_with_series", 0)
    return {"ok": True, "days": total_days,
            "unmapped_tokens": sorted(set(all_unmapped)),
            "degraded_tokens": sorted(set(all_degraded)),
            "price_calls_ok": total_ok, "price_calls_failed": total_failed,
            "tokens_with_series": total_series}


# ── PNL endpoint ─────────────────────────────────────────────────

@app.get("/api/pnl")
async def get_pnl(wallet: str = Query(None), token: str = Query(None), range: str = Query("all"),
                  user=Depends(get_current_user), db=Depends(get_db)):
    conditions = ["user_id=?", str(user["id"])]
    params = [user["id"]]

    if wallet and wallet != "ALL":
        conditions.append("wallet_address=?")
        params.append(wallet)
    if token:
        conditions.append("token_symbol=?")
        params.append(token.upper())
    else:
        conditions.append("token_symbol IS NULL")

    where = " AND ".join(conditions)
    cur = await db.execute(
        f"SELECT date, value_usd, cost_basis_usd, net_flows_usd FROM daily_history WHERE {where} ORDER BY date ASC",
        tuple(params))
    rows = await cur.fetchall()

    # Apply range filter
    if range != "all":
        try:
            days = int(range)
            cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
            rows = [r for r in rows if r["date"] >= cutoff]
        except ValueError:
            pass

    result = []
    prev_value = None
    for r in rows:
        pnl = r["value_usd"] - r["cost_basis_usd"]
        pnl_pct = round(pnl / r["cost_basis_usd"] * 100, 2) if r["cost_basis_usd"] > 0 else 0.0
        pnl_day = round(r["value_usd"] - (prev_value or r["value_usd"]) - r["net_flows_usd"], 2) if prev_value is not None else 0.0
        result.append({
            "date": r["date"],
            "value": r["value_usd"],
            "cost_basis": r["cost_basis_usd"],
            "pnl": round(pnl, 2),
            "pnl_pct": pnl_pct,
            "pnl_day": pnl_day,
        })
        prev_value = r["value_usd"]

    return result


# ── Enrichment endpoint (manual trigger) ─────────────────────────

@app.post("/api/transactions/enrich")
async def enrich_transactions(user=Depends(get_current_user), db=Depends(get_db)):
    cur = await db.execute("SELECT address FROM wallets WHERE user_id=?", (user["id"],))
    wallets_list = await cur.fetchall()
    if not wallets_list:
        return {"ok": True, "enriched": 0}
    total = 0
    for w in wallets_list:
        result = await _fetch_prices_per_token(user["id"], w["address"])
        total += result.get("enriched", 0)
    return {"ok": True, "enriched": total}


@app.post("/api/transactions/fetch")
async def fetch_transactions(user=Depends(get_current_user), db=Depends(get_db)):
    cur = await db.execute("SELECT address, label FROM wallets WHERE user_id=?", (user["id"],))
    wallets_list = await cur.fetchall()
    if not wallets_list:
        raise HTTPException(400, "Aucun wallet")
    total_tx = 0
    for w in wallets_list:
        total_tx += await _fetch_transactions_for_wallet(user["id"], w["address"])
    return {"ok": True, "transactions_fetched": total_tx}


# ── Currency rates ──────────────────────────────────────────────

_rate_cache = {"eur": None, "ts": 0}

@app.get("/api/rates")
async def get_rates():
    global _rate_cache
    now = _time.time()
    if _rate_cache["eur"] and (now - _rate_cache["ts"]) < 3600:
        return _rate_cache["eur"]
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get("https://api.frankfurter.app/latest?from=USD&to=EUR", timeout=8)
            if r.status_code == 200:
                data = r.json()
                _rate_cache = {"eur": {"eur": data["rates"]["EUR"]}, "ts": now}
                return _rate_cache["eur"]
    except Exception:
        pass
    return {"eur": 0.91}


# ── Version ─────────────────────────────────────────────────────

@app.get("/api/version/latest")
async def latest_version():
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://api.github.com/repos/LostInTheBugs/Crypto-Wallet-Tracker/tags?per_page=1",
                headers={"Accept": "application/vnd.github+json"})
            if r.status_code == 200:
                data = r.json()
                tag = (data[0]["name"] if data else "").lstrip("v")
                return {"tag": tag}
    except Exception:
        pass
    return {"tag": ""}


# ── Frontend ─────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse("public/index.html")


app.mount("/static", StaticFiles(directory="public"), name="static")
