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
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
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
    return {"ok": True}


@app.delete("/api/wallets/{wallet_id}")
async def del_wallet(wallet_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    await db.execute("DELETE FROM wallets WHERE id=? AND user_id=?", (wallet_id, user["id"]))
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
                "SELECT created_at FROM snapshots WHERE user_id=? ORDER BY created_at DESC LIMIT 1",
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
async def get_snapshots(user=Depends(get_current_user), db=Depends(get_db)):
    cur = await db.execute(
        "SELECT total_usd, token_count, created_at FROM snapshots WHERE user_id=? ORDER BY created_at ASC",
        (user["id"],))
    rows = await cur.fetchall()
    return [{"total_usd": r["total_usd"], "token_count": r["token_count"], "date": r["created_at"]} for r in rows]


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
