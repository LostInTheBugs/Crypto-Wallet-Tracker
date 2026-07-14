"""
Crypto Wallet Tracker — EVM portfolio aggregator.
Multi-chain via Blockscout API, multi-wallet, user accounts.
"""

from fastapi import FastAPI, Query, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager
import httpx, asyncio, jwt, bcrypt, aiosqlite, os, datetime

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
        try: await db.execute("ALTER TABLE snapshots ADD COLUMN token_quantity REAL DEFAULT 0")
        except: pass
        # Migrate old snapshots table — add columns if missing
        try:
            await db.execute("ALTER TABLE snapshots ADD COLUMN token_symbol TEXT DEFAULT NULL")
        except:
            pass
        try:
            await db.execute("ALTER TABLE snapshots ADD COLUMN chain TEXT DEFAULT NULL")
        except:
            pass
        try:
            await db.execute("ALTER TABLE snapshots ADD COLUMN wallet_label TEXT DEFAULT NULL")
        except:
            pass
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
    # Auto-backfill in background
    asyncio.create_task(_backfill_wallet(user["id"], address))
    asyncio.create_task(_fetch_transactions_for_wallet(user["id"], address))
    return {"ok": True}


@app.delete("/api/wallets/{wallet_id}")
async def del_wallet(wallet_id: int, user=Depends(get_current_user), db=Depends(get_db)):
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

_portfolio_cache = {}  # {address: {"data": ..., "ts": timestamp}}


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


@app.get("/api/portfolio")
async def portfolio(address: str = Query(...), force: bool = Query(False), user=Depends(get_current_user)):
    if not address.startswith("0x"):
        raise HTTPException(400, "Adresse invalide")

    now = _time.time()
    entry = _portfolio_cache.get(address)
    if not force and entry and (now - entry["ts"]) < 3600:  # 1 hour cache
        data = dict(entry["data"])
        data["cached"] = True
        return data

    data = await _compute_portfolio(address)
    _portfolio_cache[address] = {"data": data, "ts": now}

    # Save snapshot (max one per 10 min per user)
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


@app.get("/api/snapshots")
async def get_snapshots(token: str = Query(None), user=Depends(get_current_user), db=Depends(get_db)):
    if token:
        cur = await db.execute(
            "SELECT total_usd, token_quantity, token_count, created_at FROM snapshots WHERE user_id=? AND token_symbol=? ORDER BY created_at ASC",
            (user["id"], token.upper()))
    else:
        cur = await db.execute(
            "SELECT total_usd, token_quantity, token_count, created_at FROM snapshots WHERE user_id=? AND token_symbol IS NULL ORDER BY created_at ASC",
            (user["id"],))
    rows = await cur.fetchall()
    return [{"total_usd": r["total_usd"], "quantity": r["token_quantity"] or 0, "token_count": r["token_count"], "date": r["created_at"]} for r in rows]


@app.get("/api/snapshots/tokens")
async def get_snapshot_tokens(user=Depends(get_current_user), db=Depends(get_db)):
    """List distinct tokens that have snapshot data."""
    cur = await db.execute(
        "SELECT DISTINCT token_symbol FROM snapshots WHERE user_id=? AND token_symbol IS NOT NULL ORDER BY token_symbol",
        (user["id"],))
    rows = await cur.fetchall()
    return [r["token_symbol"] for r in rows]


@app.post("/api/snapshots/backfill")
async def backfill_snapshots(user=Depends(get_current_user), db=Depends(get_db)):
    """Manual trigger for backfill (also runs automatically on wallet add)."""
    cur = await db.execute("SELECT address FROM wallets WHERE user_id=?", (user["id"],))
    wallet_rows = await cur.fetchall()
    if not wallet_rows:
        raise HTTPException(400, "Aucun wallet")
    result = await _backfill_wallet(user["id"], wallet_rows[0]["address"])
    return result


# ── Transactions & Real History ─────────────────────────────────

@app.post("/api/transactions/fetch")
async def fetch_transactions(user=Depends(get_current_user), db=Depends(get_db)):
    """Fetch all token transfers for all user wallets from Blockscout."""
    cur = await db.execute("SELECT address, label FROM wallets WHERE user_id=?", (user["id"],))
    wallets_list = await cur.fetchall()
    if not wallets_list:
        raise HTTPException(400, "Aucun wallet")

    total_tx = 0
    for w in wallets_list:
        total_tx += await _fetch_transactions_for_wallet(user["id"], w["address"])
    await db.commit()
    return {"ok": True, "transactions_fetched": total_tx}


async def _fetch_transactions_for_wallet(user_id: int, address: str) -> int:
    """Fetch token transfers for one wallet, return count of new transactions."""
    total_tx = 0
    for chain, host in CHAINS.items():
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as bc:
                url = f"https://{host}/api/v2/addresses/{address}/token-transfers"
                params = {"type": "ERC-20,ERC-721,ERC-1155"}
                resp = await bc.get(url, params=params)
                if resp.status_code != 200: continue
                data = resp.json()
                async with aiosqlite.connect(DB_PATH) as db:
                    for item in data.get("items", []):
                        token = item.get("token") or {}
                        tx_hash = item.get("tx_hash", "")
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
                        await db.execute(
                            "INSERT INTO transactions (user_id, wallet_address, token_symbol, token_name, amount, chain, tx_hash, block_time) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (user_id, address, symbol, name, amount, chain, tx_hash, ts[:19] if ts else ""))
                        total_tx += 1
                    await db.commit()
        except Exception:
            continue
    return total_tx


@app.post("/api/transactions/enrich")
async def enrich_transactions(user=Depends(get_current_user), db=Depends(get_db)):
    """Add CoinGecko historical prices to stored transactions."""
    cur = await db.execute("SELECT id, token_symbol, block_time FROM transactions WHERE user_id=? AND usd_price=0 ORDER BY block_time", (user["id"],))
    rows = await cur.fetchall()
    if not rows:
        return {"ok": True, "enriched": 0}

    count = 0
    async with httpx.AsyncClient(timeout=30) as cg:
        for r in rows:
            cg_id = SYMBOL_TO_CG.get(r["token_symbol"].lower())
            if not cg_id: continue
            try:
                ts = _time.mktime(_time.strptime(r["block_time"][:10], "%Y-%m-%d"))
                url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/history"
                params = {"date": r["block_time"][:10].replace("-", "-"), "localization": "false"}
                resp = await cg.get(url, params=params)
                if resp.status_code == 200:
                    price = resp.json().get("market_data", {}).get("current_price", {}).get("usd", 0)
                    if price:
                        cur2 = await db.execute("SELECT amount FROM transactions WHERE id=?", (r["id"],))
                        tx = await cur2.fetchone()
                        usd_val = tx["amount"] * price if tx else 0
                        await db.execute("UPDATE transactions SET usd_price=?, usd_value=? WHERE id=?", (price, round(usd_val, 2), r["id"]))
                        count += 1
            except Exception:
                continue
    await db.commit()
    return {"ok": True, "enriched": count}


async def _backfill_wallet(user_id: int, address: str) -> dict:
    """Generate historical snapshots using CoinGecko prices since wallet creation."""
    
    # Find wallet creation date via Blockscout (oldest tx + 1 day margin)
    created_at = _time.time() - 90 * 86400  # default 90 days
    try:
        async with httpx.AsyncClient(timeout=10) as bc:
            r = await bc.get(f"https://eth.blockscout.com/api/v2/addresses/{address}/transactions?filter=from|to&limit=1&sort=asc")
            if r.status_code == 200:
                items = r.json().get("items", [])
                if items:
                    ts_str = items[0].get("timestamp", "")
                    if ts_str:
                        created_at = _time.mktime(_time.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")) - 86400
    except Exception:
        pass

    # Get current tokens
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(*[fetch_chain(client, c, h, address) for c, h in CHAINS.items()])

    tokens_cg = []
    for r in results:
        for t in r["tokens"]:
            try: bal = int(t["balance_raw"]) / (10 ** t["decimals"])
            except: bal = 0
            usd = bal * t["usd_price"]
            if usd < 1: continue
            cg_id = SYMBOL_TO_CG.get(t["symbol"].lower())
            if cg_id:
                tokens_cg.append({"id": cg_id, "balance": bal, "symbol": t["symbol"]})

    if not tokens_cg:
        return {"ok": False, "msg": "Aucun token mappé CoinGecko"}

    now = _time.time()

    weekly_prices = {}
    async with httpx.AsyncClient(timeout=45) as cg_client:
        for tok in tokens_cg:
            try:
                url = f"https://api.coingecko.com/api/v3/coins/{tok['id']}/market_chart/range"
                params = {"vs_currency": "usd", "from": int(created_at), "to": int(now)}
                resp = await cg_client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    weekly_prices[tok["id"]] = data.get("prices", [])
            except Exception:
                continue

    # Generate weekly snapshots from creation to now — both total and per-token
    new_snapshots = 0
    week_ms = 7 * 86400 * 1000
    start_ms = created_at * 1000
    end_ms = now * 1000
    
    async with aiosqlite.connect(DB_PATH) as db:
        for ts in range(int(start_ms), int(end_ms), int(week_ms)):
            total = 0.0
            date_str = datetime.datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")
            
            for tok in tokens_cg:
                prices = weekly_prices.get(tok["id"], [])
                price = 0
                for p in prices:
                    if p[0] <= ts:
                        price = p[1]
                tok_usd = tok["balance"] * price
                total += tok_usd
                
                # Per-token snapshot
                cur2 = await db.execute(
                    "SELECT id FROM snapshots WHERE user_id=? AND created_at=? AND token_symbol=?",
                    (user_id, date_str, tok["symbol"]))
                if not await cur2.fetchone() and tok_usd > 0.01:
                    await db.execute(
                        "INSERT INTO snapshots (user_id, total_usd, token_count, token_symbol, chain) VALUES (?, ?, ?, ?, ?)",
                        (user_id, round(tok_usd, 2), 1, tok["symbol"], "ethereum"))
                    new_snapshots += 1
            
            # Total snapshot
            if total > 0:
                cur3 = await db.execute(
                    "SELECT id FROM snapshots WHERE user_id=? AND created_at=? AND token_symbol IS NULL",
                    (user_id, date_str))
                if not await cur3.fetchone():
                    await db.execute(
                        "INSERT INTO snapshots (user_id, total_usd, token_count) VALUES (?, ?, ?)",
                        (user_id, round(total, 2), len(tokens_cg)))
                    new_snapshots += 1
        
        await db.commit()
    return {"ok": True, "snapshots_added": new_snapshots, "from_date": datetime.datetime.utcfromtimestamp(created_at).strftime("%Y-%m-%d")}


# CoinGecko symbol → id mapping (common tokens)
SYMBOL_TO_CG = {
    "eth": "ethereum", "weth": "ethereum", "matic": "matic-network", "pol": "polygon-ecosystem-token",
    "usdt": "tether", "usdc": "usd-coin", "dai": "dai", "wbtc": "wrapped-bitcoin", "btc": "bitcoin",
    "link": "chainlink", "uni": "uniswap", "aave": "aave", "crv": "curve-dao-token",
    "snx": "synthetix-network-token", "mkr": "maker", "comp": "compound-governance-token",
    "grt": "the-graph", "sand": "the-sandbox", "mana": "decentraland", "enj": "enjincoin",
    "bat": "basic-attention-token", "zrx": "0x", "1inch": "1inch", "ldo": "lido-dao",
    "op": "optimism", "arb": "arbitrum", "ape": "apecoin", "shib": "shiba-inu",
    "pepe": "pepe", "floki": "floki", "fet": "fetch-ai", "rndr": "render-token",
    "imx": "immutable-x", "axs": "axie-infinity", "sand": "the-sandbox",
    "gmx": "gmx", "dydx": "dydx", "stg": "stargate-finance", "woo": "woo-network",
    "ens": "ethereum-name-service", "lrc": "loopring", "blur": "blur",
    "strk": "starknet", "ena": "ethena", "eigen": "eigenlayer", "jup": "jupiter-exchange-solana",
    "bonk": "bonk", "wif": "dogwifcoin", "pyth": "pyth-network",
}


# ── Currency rates ──────────────────────────────────────────────

import time as _time

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
    return {"eur": 0.91}  # fallback ~rate


# ── Frontend ─────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse("public/index.html")


app.mount("/static", StaticFiles(directory="public"), name="static")
