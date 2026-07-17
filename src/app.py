"""
Crypto Wallet Tracker — EVM portfolio aggregator.
Multi-chain via Blockscout API, multi-wallet, user accounts.
"""
from collections import defaultdict
from fastapi import FastAPI, Query, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager
import httpx, asyncio, jwt, bcrypt, aiosqlite, os, datetime, time as _time, bisect, math, subprocess

# Service imports
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.price_service import (
    SYMBOL_TO_CG, _price_at, _fetch_prices_per_token,
    _load_prices_from_cache, _save_prices_to_cache,
    _interpolate_price, _cg_rate_limit_wait,
    _fetch_defillama_batch, _fetch_coingecko_batch,
)
from services.portfolio_service import (
    _compute_portfolio, CHAINS, NATIVE_COIN, fetch_chain,
    format_snapshots_v2, format_snapshots_legacy,
)
from services.pnl_service import (
    _rebuild_history, compute_pnl_from_rows, format_pnl_v2,
)

# ── Logging configuration ───────────────────────────────────────
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),  # stdout (captured by uvicorn)
    ]
)
# Set noisy libs to WARNING
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("aiosqlite").setLevel(logging.WARNING)

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
        try: await db.execute("ALTER TABLE transactions ADD COLUMN log_index INTEGER DEFAULT 0")
        except: pass
        try: await db.execute("ALTER TABLE transactions ADD COLUMN gas_fee_eth REAL DEFAULT 0")
        except: pass
        try: await db.execute("ALTER TABLE transactions ADD COLUMN gas_fee_usd REAL DEFAULT 0")
        except: pass
        try: await db.execute("CREATE INDEX IF NOT EXISTS idx_tx_dedup ON transactions(tx_hash, log_index, user_id)")
        except: pass
        # User API keys
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_api_keys (
                user_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                api_key TEXT NOT NULL,
                PRIMARY KEY(user_id, provider),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
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


_portfolio_cache = {}


# ── Transactions fetch ───────────────────────────────────────────

_import_progress = {}
_last_tx_refresh = {}


# ── Tx fetch constants ────────────────────────────────────────────
MAX_TX_PAGES = int(os.environ.get("MAX_TX_PAGES", "1000"))
TX_RETRIES = 3
TX_RETRY_BACKOFF = 1.5         # seconds base backoff (exponential)


async def _fetch_transactions_for_wallet(user_id: int, address: str) -> int:
    """Fetch token transfers with pagination. Dedup on (tx_hash, log_index, user_id).

    Continues until next_page_params is exhausted (or MAX_TX_PAGES safety cap).
    Retries transient HTTP errors (timeout, 5xx) with exponential backoff.
    A chain that fails does not interrupt the others.
    """
    total_tx = 0
    import logging
    _tx_log = logging.getLogger("crypto.tx_fetch")

    for chain, host in CHAINS.items():
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as bc:
                url = f"https://{host}/api/v2/addresses/{address}/token-transfers"
                params: dict = {"type": "ERC-20,ERC-721,ERC-1155"}
                page_count = 0
                while page_count < MAX_TX_PAGES:
                    # Retry loop for transient HTTP errors
                    last_err = None
                    resp = None
                    for attempt in range(1, TX_RETRIES + 1):
                        try:
                            resp = await bc.get(url, params=params)
                            if resp.status_code == 200:
                                break  # success — exit retry loop
                            last_err = f"HTTP {resp.status_code}"
                            if resp.status_code < 500:
                                # Client error (4xx): no point retrying
                                break
                        except Exception as e:
                            last_err = str(e)[:100]
                            resp = None
                        # Only retry on 5xx/timeout (transient)
                        if attempt < TX_RETRIES and last_err and (
                                "50" in last_err or
                                "timeout" in last_err.lower() or
                                "Connection" in last_err):
                            backoff = TX_RETRY_BACKOFF ** attempt
                            _tx_log.debug(
                                "Retry %d/%d for chain=%s page=%d (err=%s) after %.1fs",
                                attempt + 1, TX_RETRIES, chain, page_count + 1,
                                last_err, backoff)
                            await asyncio.sleep(backoff)
                        else:
                            break

                    if resp is None or (last_err and resp.status_code != 200):
                        _tx_log.warning(
                            "Giving up on chain=%s at page %d: %s",
                            chain, page_count + 1, last_err)
                        break

                    data = resp.json()
                    items = data.get("items", [])
                    if not items:
                        break

                    async with aiosqlite.connect(DB_PATH) as db:
                        for item in items:
                            token = item.get("token") or {}
                            tx_hash = item.get("transaction_hash") or item.get("tx_hash", "")
                            log_index = int(item.get("log_index") or 0)
                            # Dedup on (tx_hash, log_index, user_id)
                            if tx_hash:
                                cur2 = await db.execute(
                                    "SELECT id FROM transactions WHERE tx_hash=? AND log_index=? AND user_id=?",
                                    (tx_hash, log_index, user_id))
                                if await cur2.fetchone():
                                    continue
                            try:
                                amount = int(item.get("total", {}).get("value", "0") or "0") / (10 ** (int(token.get("decimals") or 18)))
                            except Exception:
                                amount = 0
                            if amount == 0:
                                continue
                            symbol = token.get("symbol", "?")
                            name = token.get("name", "Unknown")
                            ts = item.get("timestamp", "")
                            to_addr = (item.get("to") or {}).get("hash", "")
                            direction = "in" if to_addr.lower() == address.lower() else "out"
                            await db.execute(
                                "INSERT INTO transactions (user_id, wallet_address, token_symbol, token_name, amount, chain, tx_hash, block_time, direction, log_index) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (user_id, address, symbol, name, amount, chain, tx_hash, ts[:19].replace("T", " ") if ts else "", direction, log_index))
                            total_tx += 1
                        await db.commit()

                    nxt = data.get("next_page_params")
                    if not nxt:
                        break
                    params = {**params, **nxt}
                    page_count += 1

                if page_count >= MAX_TX_PAGES:
                    _tx_log.warning(
                        "Reached MAX_TX_PAGES=%d for chain=%s wallet=%s — history may be incomplete",
                        MAX_TX_PAGES, chain, address[:10])
        except Exception:
            continue

    return total_tx


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
        result = await _rebuild_history(user_id, address, _compute_portfolio)
        _import_progress[user_id] = {"stage": "done", "done": count, "total": count, "unmapped": result.get("unmapped_tokens", [])}
        # Fetch gas fees after rebuild (non-blocking, fire-and-forget)
        asyncio.create_task(_fetch_gas_for_user(user_id))
    except Exception:
        _import_progress[user_id] = {"stage": "done", "done": 0, "total": 0, "error": True}


@app.get("/api/import/progress")
async def import_progress(user=Depends(get_current_user)):
    return _import_progress.get(user["id"], {"stage": "idle", "done": 0, "total": 0})


@app.get("/api/transactions")
async def get_transactions(wallet: str = Query(None), chain: str = Query(None), token: str = Query(None),
                           direction: str = Query(None), limit: int = Query(100), offset: int = Query(0),
                           user=Depends(get_current_user), db=Depends(get_db)):
    conditions = ["user_id=?", str(user["id"])]
    params = [user["id"]]
    if wallet:
        conditions.append("wallet_address=?")
        params.append(wallet)
    if chain:
        conditions.append("chain=?")
        params.append(chain)
    if token:
        conditions.append("LOWER(token_symbol)=?")
        params.append(token.lower())
    if direction and direction in ("in", "out"):
        conditions.append("direction=?")
        params.append(direction)
    where = " AND ".join(conditions)
    # Count total
    total_cur = await db.execute(f"SELECT COUNT(*) FROM transactions WHERE {where}", tuple(params))
    total = (await total_cur.fetchone())[0]
    # Fetch page
    cur = await db.execute(
        f"SELECT id, wallet_address, token_symbol, token_name, amount, usd_price, usd_value, chain, tx_hash, block_time, direction, log_index, gas_fee_usd FROM transactions WHERE {where} ORDER BY block_time DESC LIMIT ? OFFSET ?",
        tuple(params + [limit, offset]))
    rows = await cur.fetchall()
    items = [{
        "id": r["id"], "wallet_address": r["wallet_address"], "token_symbol": r["token_symbol"],
        "token_name": r["token_name"], "amount": r["amount"], "usd_price": r["usd_price"],
        "usd_value": r["usd_value"], "chain": r["chain"], "tx_hash": r["tx_hash"],
        "block_time": r["block_time"], "direction": r["direction"], "log_index": r["log_index"],
        "gas_fee_usd": r["gas_fee_usd"],
        "wallet_label": _wallet_labels.get(r["wallet_address"], ""),
        "explorer_url": f"https://{CHAINS[r['chain']]}/tx/{r['tx_hash']}" if r["tx_hash"] and CHAINS.get(r["chain"]) else ""
    } for r in rows]
    return {"total": total, "items": items}


# ── Gas fee constants ─────────────────────────────────────────────
GAS_TIMEOUT = 8                # seconds per HTTP request
GAS_CONCURRENCY = 5            # max parallel requests per chain
GAS_CB_FAILURES = 5            # consecutive failures before circuit-breaker trips

import logging
_gas_log = logging.getLogger("crypto.gas")

# ── Native price cache ────────────────────────────────────────────
_native_price_cache: dict[str, tuple[float, float]] = {}  # chain → (price_usd, timestamp)
_NATIVE_PRICE_TTL = 3600  # 1 hour


async def _get_native_price(chain: str, host: str) -> float:
    """Get native coin USD price via Blockscout /api/v2/stats → coin_price.

    Cached in memory with 1h TTL. For ETH-gas chains, falls back to
    _get_eth_price_at if the Blockscout endpoint is unreachable.
    For non-ETH chains, returns 0 when no price is available (never
    substitutes the ETH price).
    """
    now = _time.time()
    cached = _native_price_cache.get(chain)
    if cached:
        price, ts = cached
        if now - ts < _NATIVE_PRICE_TTL:
            return price

    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as bc:
            r = await bc.get(f"https://{host}/api/v2/stats")
            if r.status_code == 200:
                data = r.json()
                coin_price = float(data.get("coin_price") or 0)
                if coin_price > 0:
                    _native_price_cache[chain] = (coin_price, now)
                    return coin_price
    except Exception:
        pass

    # Fallback: only ETH-gas chains may fall back to _get_eth_price_at
    native_symbol = NATIVE_COIN.get(chain, {}).get("symbol", "")
    if native_symbol == "ETH":
        try:
            return await _get_eth_price_at("")
        except Exception:
            pass
    return 0.0


async def _fetch_gas_for_user(user_id: int) -> int:
    """Fetch gas fees for a user's transactions that don't have them yet.

    Processes chains in parallel, with bounded concurrency per chain
    and a circuit breaker that abandons a chain after N consecutive failures.

    Returns count of updated distinct tx_hashes.
    Gas is imputed to exactly ONE row per tx_hash (the one with minimal log_index)
    to avoid overcounting when a single tx has multiple transfers.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT DISTINCT tx_hash, chain, wallet_address FROM transactions "
            "WHERE user_id=? AND tx_hash!='' AND gas_fee_eth=0 LIMIT 500",
            (user_id,))
        txns = await cur.fetchall()
    if not txns:
        return 0

    # by_chain: chain → list of (tx_hash, wallet_address) tuples
    by_chain: dict[str, list[tuple[str, str]]] = {}
    for t in txns:
        by_chain.setdefault(t["chain"], []).append((t["tx_hash"], t["wallet_address"]))

    # Shared counter for atomic updates across parallel chains
    updated_lock = asyncio.Lock()
    updated = 0

    async def _process_chain(chain: str, items: list[tuple[str, str]]) -> int:
        host = CHAINS.get(chain)
        if not host:
            return 0

        sem = asyncio.Semaphore(GAS_CONCURRENCY)
        chain_updated = 0
        consecutive_fails = 0

        async def _fetch_one(tx_hash: str) -> tuple[str, dict | None]:
            nonlocal consecutive_fails
            try:
                async with httpx.AsyncClient(timeout=GAS_TIMEOUT, follow_redirects=True) as bc:
                    r = await bc.get(f"https://{host}/api/v2/transactions/{tx_hash}")
                    if r.status_code == 200:
                        consecutive_fails = 0
                        return (tx_hash, r.json())
                    # Non-200: treat as failure
                    consecutive_fails += 1
                    if 400 <= r.status_code < 500:
                        # Client error (e.g. tx not found) — don't retry, don't count toward breaker
                        consecutive_fails = 0
                    return (tx_hash, None)
            except Exception:
                consecutive_fails += 1
                return (tx_hash, None)

        async def _fetch_with_sem(tx_hash: str, wallet_address: str):
            nonlocal chain_updated
            async with sem:
                # Circuit breaker check
                if consecutive_fails >= GAS_CB_FAILURES:
                    return  # abandon this chain
                tx_hash, data = await _fetch_one(tx_hash)
                if data is None:
                    return
                try:
                    gas_used = float(data.get("gas_used") or 0)
                    gas_price = float(data.get("gas_price") or 0)
                    eth_fee = (gas_used * gas_price) / 1e18
                    # Only count gas USD if wallet is the sender
                    from_hash = (data.get("from") or {}).get("hash", "")
                    paid = bool(from_hash) and from_hash.lower() == wallet_address.lower()
                    native_price = await _get_native_price(chain, host) if paid else 0.0
                    usd_fee = round(eth_fee * native_price, 2) if (paid and native_price > 0) else 0.0
                    # Always mark tx as processed (gas_fee_eth); USD=0 for receipts
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute(
                            "UPDATE transactions SET gas_fee_eth=?, gas_fee_usd=? "
                            "WHERE id=(SELECT id FROM transactions "
                            "WHERE tx_hash=? AND user_id=? AND wallet_address=? "
                            "ORDER BY log_index ASC LIMIT 1)",
                            (round(eth_fee, 8), usd_fee, tx_hash, user_id, wallet_address))
                        await db.commit()
                    if usd_fee > 0:
                        chain_updated += 1
                except Exception:
                    pass

        # Process all items for this chain in parallel (bounded by semaphore)
        tasks = [_fetch_with_sem(tx_hash, wallet_address) for tx_hash, wallet_address in items]
        await asyncio.gather(*tasks)

        if consecutive_fails >= GAS_CB_FAILURES:
            _gas_log.warning(
                "Circuit breaker tripped for chain=%s after %d consecutive failures "
                "(processed %d/%d items)",
                chain, consecutive_fails, chain_updated, len(items))

        return chain_updated

    # Process all chains in parallel
    results = await asyncio.gather(
        *(_process_chain(chain, items) for chain, items in by_chain.items()),
        return_exceptions=True)

    for r in results:
        if isinstance(r, int):
            updated += r
        elif isinstance(r, Exception):
            _gas_log.warning("Gas fetch chain task failed: %s", r)

    return updated


@app.post("/api/transactions/fetch-gas")
async def fetch_gas_fees(user=Depends(get_current_user)):
    """Public endpoint — triggers gas fee fetch for the authenticated user."""
    updated = await _fetch_gas_for_user(user["id"])
    return {"ok": True, "updated": updated}


@app.get("/api/transactions/gas-total")
async def gas_total(user=Depends(get_current_user), db=Depends(get_db),
                    wallet: str = Query(None)):
    if wallet:
        cur = await db.execute(
            "SELECT COALESCE(SUM(gas_fee_usd),0) as total "
            "FROM transactions WHERE user_id=? AND wallet_address=?",
            (user["id"], wallet))
    else:
        cur = await db.execute(
            "SELECT COALESCE(SUM(gas_fee_usd),0) as total "
            "FROM transactions WHERE user_id=?", (user["id"],))
    row = await cur.fetchone()
    return {"total_gas_usd": round(row["total"] if row else 0, 2)}


async def _get_eth_price_at(timestamp_str: str) -> float:
    """Get ETH price at a given timestamp. Returns USD price."""
    if not timestamp_str:
        return 2000.0  # fallback
    try:
        # Load from price_history cache
        date = timestamp_str[:10]
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT price_usd FROM price_history WHERE token_symbol='eth' AND date=? LIMIT 1", (date,))
            row = await cur.fetchone()
            if row and row["price_usd"] > 0:
                return row["price_usd"]
            # Try weth
            cur = await db.execute("SELECT price_usd FROM price_history WHERE token_symbol='weth' AND date=? LIMIT 1", (date,))
            row = await cur.fetchone()
            if row and row["price_usd"] > 0:
                return row["price_usd"]
    except:
        pass
    return 2000.0  # fallback


_wallet_labels = {}


# ── Portfolio endpoint ───────────────────────────────────────────

@app.get("/api/portfolio")
async def portfolio(address: str = Query(...), force: bool = Query(False), user=Depends(get_current_user)):
    import logging
    _log = logging.getLogger("crypto.portfolio")
    _log.info(f"[TRACE] /api/portfolio ENTER address={address[:12]}... force={force}")

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
    _log.info(
        f"[TRACE] /api/portfolio AFTER compute: tokens={len(data.get('tokens',[]))} "        f"total_usd={data.get('total_usd',0)}"
    )

    # Add per-token cost basis from daily_history
    total_cost = None
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT date, value_usd, cost_basis_usd FROM daily_history WHERE user_id=? AND wallet_address=? AND token_symbol IS NULL ORDER BY date DESC LIMIT 1",
                (user["id"], address))
            row = await cur.fetchone()
        if row:
            total_cost = round(row["cost_basis_usd"], 2)
    except Exception:
        pass
    
    if total_cost is not None:
        data["total_cost_basis"] = total_cost
        data["total_pnl"] = round(data["total_usd"] - total_cost, 2)
        
        # Per-token PNL enrichment (NON-BLOCKING: one bad token never breaks all)
        enriched = 0
        missing_history = 0
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            for t in data["tokens"]:
                try:
                    sym = (t.get("symbol") or "").lower()
                    if not sym:
                        continue
                    usd_val = t.get("usd_value", 0) or 0
                    # Try daily_history first — rescale reconstructed cost
                    # to on-chain value (avoids phantom PNL when
                    # reconstructed balance ≠ on-chain balance).
                    cur2 = await db.execute(
                        "SELECT cost_basis_usd, value_usd FROM daily_history "
                        "WHERE user_id=? AND wallet_address=? AND token_symbol=? "
                        "ORDER BY date DESC LIMIT 1",
                        (user["id"], address, sym))
                    token_row = await cur2.fetchone()
                    if token_row and token_row["cost_basis_usd"] > 0:
                        hist_value = token_row["value_usd"] or 0
                        if hist_value > 0:
                            ratio = token_row["cost_basis_usd"] / hist_value
                            if math.isfinite(ratio):
                                # Rescale: cost ≈ usd_val × ratio
                                # Stablecoin: ratio ≈ 1 ⇒ pnl ≈ 0
                                cost = round(usd_val * ratio, 2)
                                t["cost_basis"] = cost
                                t["pnl"] = round(usd_val - cost, 2)
                                enriched += 1
                                continue
                    missing_history += 1
                    # Fallback: compute avg cost per unit from transactions,
                    # then multiply by current balance (avoids PNL distortion
                    # when some TXs have usd_price=0, e.g. stablecoins).
                    cur3 = await db.execute(
                        "SELECT "
                        "SUM(CASE WHEN direction='in' THEN amount ELSE -amount END) as solde, "
                        "SUM(CASE WHEN direction='in' THEN amount*usd_price ELSE -amount*usd_price END) as cost "
                        "FROM transactions WHERE user_id=? AND wallet_address=? AND LOWER(token_symbol)=?",
                        (user["id"], address, sym))
                    cost_row = await cur3.fetchone()
                    solde_recon = (cost_row[0] or 0) if cost_row else 0
                    cost_recon = (cost_row[1] or 0) if cost_row else 0
                    if solde_recon > 0 and cost_recon > 0:
                        avg_cost = cost_recon / solde_recon
                        if math.isfinite(avg_cost):
                            bal = t.get("balance", 0) or 0
                            cost = avg_cost * bal
                            t["cost_basis"] = round(cost, 2)
                            t["pnl"] = round(usd_val - cost, 2)
                            continue
                    # Acquisition cost unknown (no priced transactions, no
                    # usable history): report null instead of a misleading
                    # pnl == usd_value ("bought for free").
                    t["cost_basis"] = None
                    t["pnl"] = None
                except Exception:
                    # Single token failure must not affect others
                    t.setdefault("cost_basis", None)
                    t.setdefault("pnl", None)

        import logging
        logger = logging.getLogger("crypto.portfolio")
        _log.info(
            f"[TRACE] /api/portfolio AFTER enrich: enriched={enriched} "            f"missing_history={missing_history} tokens={len(data.get('tokens',[]))}"
        )

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

    _log.info(
        f"[TRACE] /api/portfolio EXIT: tokens={len(data.get('tokens',[]))} "        f"total={data.get('total_usd',0)} cached={data.get('cached',False)}"
    )
    return data


async def _daily_tx_refresh(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT address FROM wallets WHERE user_id=?", (user_id,))
        wallets_list = await cur.fetchall()
    for w in wallets_list:
        count = await _fetch_transactions_for_wallet(user_id, w["address"])
        if count > 0:
            await _rebuild_history(user_id, w["address"], _compute_portfolio)
    # Fetch gas fees after daily refresh (non-blocking)
    asyncio.create_task(_fetch_gas_for_user(user_id))


# ── Snapshots / History API ──────────────────────────────────────

@app.get("/api/snapshots")
async def get_snapshots(token: str = Query(None), wallet: str = Query(None), chain: str = Query(None),
                        format: str = Query("v1"),
                        user=Depends(get_current_user), db=Depends(get_db)):
    """Returns daily history as snapshots for chart compatibility.

    Query params:
        format: 'v1' (default, legacy array of objects) or 'v2' ({labels, values, meta}).
    """
    conditions = ["user_id=?", str(user["id"])]
    params = [user["id"]]

    if token:
        conditions.append("LOWER(token_symbol)=?")
        params.append(token.lower())
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
        f"SELECT value_usd as total_usd, cost_basis_usd as cost_basis, date FROM daily_history WHERE {where} ORDER BY date ASC",
        tuple(params))
    rows = await cur.fetchall()
    # Convert sqlite3.Row to dict — .get() not supported on Row objects
    rows = [dict(r) for r in rows]

    if not rows:
        if format == "v2":
            return {"labels": [], "values": [], "meta": {"points": 0, "min": 0, "max": 0}}
        return []

    # Patch last value with current portfolio if this is the aggregate (no token filter)
    if not token and rows and wallet:
        try:
            pf = await _compute_portfolio(wallet)
            pf_mapped = 0.0
            for t in pf.get("tokens", []):
                sym = (t.get("symbol") or "").lower()
                if sym and (SYMBOL_TO_CG.get(sym) or sym in ("usdc", "usdt", "dai", "eth", "weth", "wbtc")):
                    pf_mapped += t.get("usd_value", 0) or 0
            if pf_mapped > 0:
                rows[-1] = dict(rows[-1])
                rows[-1]["total_usd"] = round(pf_mapped, 2)
        except Exception:
            pass

    if format == "v2":
        return format_snapshots_v2(rows)

    return format_snapshots_legacy(rows)


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
        result = await _rebuild_history(user["id"], w["address"], _compute_portfolio)
        results.append(result)
    # Aggregate
    total_days = sum(r.get("days", 0) for r in results)
    all_unmapped, all_degraded = [], []
    total_ok = total_failed = total_series = 0
    for r in results:
        all_unmapped.extend(r.get("unmapped_tokens", []))
        all_degraded.extend(r.get("degraded_tokens", []))
        total_ok += r.get("price_calls_ok", 0)
        total_failed += r.get("price_calls_failed", 0)
        total_series += r.get("tokens_with_series", 0)
    
    # Reconciliation: compare last history value with portfolio
    reconciliation = None
    try:
        cur2 = await db.execute(
            "SELECT value_usd FROM daily_history WHERE user_id=? AND token_symbol IS NULL ORDER BY date DESC LIMIT 1",
            (user["id"],))
        hist_row = await cur2.fetchone()
        hist_value = hist_row["value_usd"] if hist_row else 0
        # Portfolio value for mapped tokens only
        address = wallet_rows[0]["address"]
        data = await _compute_portfolio(address)
        port_value = 0.0
        excluded = set(u.lower() for u in all_unmapped) | set(d.lower() for d in all_degraded)
        for t in data.get("tokens", []):
            sym = (t.get("symbol") or "").lower()
            if sym and sym not in excluded and SYMBOL_TO_CG.get(sym):
                port_value += t.get("usd_value", 0) or 0
        if hist_value > 0:
            delta_pct = round((hist_value - port_value) / port_value * 100, 1)
            reconciliation = {"history_last_value": round(hist_value, 2),
                            "portfolio_mapped_value": round(port_value, 2),
                            "delta_pct": delta_pct}
    except Exception:
        pass
    
    resp = {"ok": True, "days": total_days,
            "unmapped_tokens": sorted(set(all_unmapped)),
            "degraded_tokens": sorted(set(all_degraded)),
            "price_calls_ok": total_ok, "price_calls_failed": total_failed,
            "tokens_with_series": total_series}
    if reconciliation:
        resp["reconciliation"] = reconciliation
    return resp


# ── PNL endpoint ─────────────────────────────────────────────────

@app.get("/api/pnl")
async def get_pnl(wallet: str = Query(None), token: str = Query(None), range: str = Query("all"),
                  format: str = Query("v1"),
                  user=Depends(get_current_user), db=Depends(get_db)):
    """PNL endpoint with NaN-safe computation.

    Query params:
        format: 'v1' (default, array of objects) or 'v2' ({labels, values, meta}).
    """
    conditions = ["user_id=?", str(user["id"])]
    params = [user["id"]]

    if wallet and wallet != "ALL":
        conditions.append("wallet_address=?")
        params.append(wallet)
    if token:
        conditions.append("LOWER(token_symbol)=?")
        params.append(token.lower())
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

    # Use pure computation from pnl_service (NaN-safe)
    result = compute_pnl_from_rows(rows)

    if format == "v2":
        return format_pnl_v2(result)

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


@app.post("/api/update")
async def update_application(user=Depends(get_current_user)):
    """Trigger git pull + docker rebuild from GitHub. Auth required."""
    import asyncio.subprocess
    try:
        # Run git pull
        proc = await asyncio.subprocess.create_subprocess_exec(
            "git", "pull", "origin", "main",
            cwd="/opt/crypto-wallet-tracker",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        git_ok = proc.returncode == 0

        if not git_ok:
            return {
                "ok": False,
                "msg": f"git pull failed: {stderr.decode()[:200]}",
            }

        # Run docker compose up -d --build (background — takes ~60s)
        proc2 = await asyncio.subprocess.create_subprocess_exec(
            "docker", "compose", "up", "-d", "--build",
            cwd="/opt/crypto-wallet-tracker",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Don't wait — docker compose will recreate the container
        # Return immediately; the new container will serve this response

        return {
            "ok": True,
            "msg": "Mise à jour lancée. L'application redémarre...",
            "git_output": stdout.decode()[:200],
        }
    except Exception as e:
        return {"ok": False, "msg": str(e)[:200]}


# ── API Keys (per user) ─────────────────────────────────────────

async def _get_user_cg_key(user_id: int) -> str:
    """Get CoinGecko API key for user, fallback to env var."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT api_key FROM user_api_keys WHERE user_id=? AND provider='coingecko'", (user_id,))
        row = await cur.fetchone()
    if row:
        return row["api_key"]
    return os.environ.get("COINGECKO_API_KEY", "")


@app.get("/api/settings/keys")
async def list_api_keys(user=Depends(get_current_user), db=Depends(get_db)):
    providers = ["coingecko", "alchemy"]
    result = []
    for p in providers:
        cur = await db.execute("SELECT api_key FROM user_api_keys WHERE user_id=? AND provider=?", (user["id"], p))
        row = await cur.fetchone()
        if row:
            masked = "..." + row["api_key"][-4:] if len(row["api_key"]) > 4 else "***"
            result.append({"provider": p, "configured": True, "masked": masked})
        else:
            result.append({"provider": p, "configured": False, "masked": None})
    return result


@app.put("/api/settings/keys/{provider}")
async def set_api_key(provider: str, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    data = await request.json()
    api_key = (data.get("api_key") or "").strip()
    if not api_key:
        raise HTTPException(400, "Clé API requise")
    
    # Validate
    valid, msg = await _validate_api_key(provider, api_key)
    if not valid:
        raise HTTPException(400, msg)
    
    await db.execute(
        "INSERT OR REPLACE INTO user_api_keys (user_id, provider, api_key) VALUES (?, ?, ?)",
        (user["id"], provider, api_key))
    await db.commit()
    return {"ok": True, "provider": provider, "configured": True}


@app.delete("/api/settings/keys/{provider}")
async def delete_api_key(provider: str, user=Depends(get_current_user), db=Depends(get_db)):
    await db.execute("DELETE FROM user_api_keys WHERE user_id=? AND provider=?", (user["id"], provider))
    await db.commit()
    return {"ok": True, "provider": provider, "configured": False}


async def _validate_api_key(provider: str, api_key: str) -> tuple:
    """Validate API key against provider. Returns (is_valid, message)."""
    if provider == "coingecko":
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get("https://api.coingecko.com/api/v3/ping",
                                headers={"x-cg-demo-api-key": api_key})
                if r.status_code == 200:
                    return True, "Clé CoinGecko valide"
                return False, f"CoinGecko: HTTP {r.status_code}"
        except Exception as e:
            return False, f"CoinGecko: {str(e)[:80]}"
    elif provider == "alchemy":
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"https://eth-mainnet.g.alchemy.com/v2/{api_key}",
                                json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})
                data = r.json()
                if "result" in data:
                    return True, "Clé Alchemy valide"
                return False, "Alchemy: réponse invalide"
        except Exception as e:
            return False, f"Alchemy: {str(e)[:80]}"
    return False, "Provider inconnu"

@app.get("/")
async def index():
    return FileResponse("public/index.html")


app.mount("/static", StaticFiles(directory="public"), name="static")
