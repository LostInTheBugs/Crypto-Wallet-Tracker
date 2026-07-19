"""
Crypto Wallet Tracker — EVM portfolio aggregator.
Multi-chain via Blockscout API, multi-wallet, user accounts.
"""
from collections import defaultdict
from fastapi import FastAPI, Query, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from contextlib import asynccontextmanager
import httpx, asyncio, jwt, bcrypt, aiosqlite, os, datetime, calendar, time as _time, bisect, math, subprocess, re, json

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
    _compute_portfolio, CHAINS, NATIVE_COIN, fetch_chain, _is_spam,
    format_snapshots_v2, format_snapshots_legacy,
    _token_category, _is_defi_category, CHAIN_TO_LLAMA,
    _fetch_defillama_current_prices,
)
from services.pnl_service import (
    _rebuild_history, compute_pnl_from_rows, format_pnl_v2,
)
from services.token_prefs import (
    token_tid, classify_token, load_user_prefs, get_disabled_tids,
    insert_default_prefs, reclassify_prefs,
)
from services.tx_events import group_transaction_events, filter_events
from services.defi_service import (
    MORALIS_DEFI_CHAINS, normalize_defi_positions, summarize_defi_positions,
    build_best_effort_positions,
)
from services.analytics_service import (
    filter_active_tokens, build_allocation, compute_change_periods,
    compute_performers, pick_closest, pct_from_price_points,
    TOLERANCE_DAYS as AN_TOLERANCE_DAYS,
)
from services.export_service import (
    rows_to_csv, aggregate_holdings, build_holdings_rows, build_pnl_rows,
    build_transaction_rows, build_summary_pdf,
    HOLDINGS_HEADERS, TRANSACTIONS_HEADERS, PNL_HEADERS,
)
from services.alerts_service import (
    run_evaluator, evaluate_alerts_for_user, send_alert_notification,
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
def _load_session_secret() -> str:
    """Use SESSION_SECRET if a strong value is provided; otherwise generate a
    random secret and persist it in the data volume. Never fall back to an
    empty or well-known value (that would make JWTs forgeable)."""
    import secrets, pathlib
    env = (os.environ.get("SESSION_SECRET") or "").strip()
    if env and env != "change-me" and len(env) >= 16:
        return env
    try:
        p = pathlib.Path(os.path.dirname(DB_PATH) or "/data") / ".session_secret"
        if p.exists():
            s = p.read_text().strip()
            if len(s) >= 16:
                return s
        os.makedirs(os.path.dirname(str(p)), exist_ok=True)
        s = secrets.token_hex(32)
        p.write_text(s)
        try: os.chmod(p, 0o600)
        except Exception: pass
        return s
    except Exception:
        # Last resort: ephemeral (sessions won't survive a restart, but stay secure)
        return secrets.token_hex(32)


SESSION_SECRET = _load_session_secret()
TOKEN_EXPIRY = 30  # days


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        # WAL: concurrent readers during a write, far fewer "database is locked"
        try:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=5000")
        except Exception:
            pass
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
        try: await db.execute("ALTER TABLE transactions ADD COLUMN contract_address TEXT DEFAULT ''")
        except: pass
        try: await db.execute("ALTER TABLE transactions ADD COLUMN price_checked INTEGER DEFAULT 0")
        except: pass
        try: await db.execute("ALTER TABLE transactions ADD COLUMN event_type TEXT DEFAULT 'transfer'")
        except: pass
        try: await db.execute("ALTER TABLE transactions ADD COLUMN event_method TEXT DEFAULT ''")
        except: pass
        try: await db.execute("ALTER TABLE transactions ADD COLUMN event_to TEXT DEFAULT ''")
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
        # Per-user token preferences (enable/disable + manual tokens) — v2.12.0
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_token_prefs (
                user_id INTEGER NOT NULL,
                tid TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'detected',
                chain TEXT DEFAULT '',
                contract_address TEXT DEFAULT '',
                symbol TEXT DEFAULT '',
                name TEXT DEFAULT '',
                reason TEXT DEFAULT '',
                default_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY(user_id, tid),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        try: await db.execute("CREATE INDEX IF NOT EXISTS idx_utp_user ON user_token_prefs(user_id)")
        except: pass
        # Tx tags table (2026.07.5) — user notes/annotations on transactions
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_tx_tags (
                user_id INTEGER NOT NULL,
                tx_hash TEXT NOT NULL,
                chain TEXT NOT NULL,
                category TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY(user_id, tx_hash, chain),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        # Alerts system (2026.07.6)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                params_json TEXT DEFAULT '{}',
                enabled INTEGER DEFAULT 1,
                cooldown_min INTEGER DEFAULT 60,
                last_triggered_at TEXT DEFAULT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                alert_id INTEGER DEFAULT 0,
                title TEXT NOT NULL,
                body TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                read INTEGER DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS notif_channels (
                user_id INTEGER NOT NULL,
                channel TEXT NOT NULL,
                config_json TEXT DEFAULT '{}',
                enabled INTEGER DEFAULT 0,
                PRIMARY KEY(user_id, channel),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS digest_prefs (
                user_id INTEGER NOT NULL PRIMARY KEY,
                frequency TEXT DEFAULT 'off',
                channel TEXT DEFAULT '',
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        # v2.12.5 — sweep idempotent des lignes orphelines au démarrage : purge
        # toute donnée dont le wallet n'existe plus dans wallets (comparaison
        # insensible à la casse — le worker de reconstruction a pu écrire des
        # adresses en casse checksum). Protège contre d'anciens cascades ratés.
        try:
            cur = await db.execute(
                "DELETE FROM daily_history WHERE NOT EXISTS ("
                "SELECT 1 FROM wallets w WHERE w.user_id=daily_history.user_id "
                "AND lower(w.address)=lower(daily_history.wallet_address))")
            dh_orphans = cur.rowcount
            cur = await db.execute(
                "DELETE FROM transactions WHERE NOT EXISTS ("
                "SELECT 1 FROM wallets w WHERE w.user_id=transactions.user_id "
                "AND lower(w.address)=lower(transactions.wallet_address))")
            tx_orphans = cur.rowcount
            cur = await db.execute(
                "DELETE FROM snapshots WHERE user_id NOT IN (SELECT DISTINCT user_id FROM wallets)")
            sn_orphans = cur.rowcount
            logging.getLogger("crypto.app").info(
                "[SWEEP] orphan rows removed: daily_history=%s transactions=%s snapshots=%s",
                dh_orphans, tx_orphans, sn_orphans)
        except Exception as e:
            logging.getLogger("crypto.app").warning("[SWEEP] orphan sweep failed: %s", e)
        await db.commit()
        # Start alert evaluator background task
        asyncio.create_task(run_evaluator())
    yield


app = FastAPI(lifespan=lifespan, title="Crypto Wallet Tracker")


# ── Database helper ──────────────────────────────────────────────

async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # busy_timeout is PER-CONNECTION in SQLite — without it, any write
        # colliding with a background rebuild commit fails instantly with
        # "database is locked" (v2.12.1 fix).
        try:
            await db.execute("PRAGMA busy_timeout=10000")
        except Exception:
            pass
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
    # v2.12.5 — cascade INSENSIBLE À LA CASSE. Le worker de reconstruction peut
    # écrire wallet_address avec une casse (checksum) différente de wallets.address ;
    # une comparaison exacte laissait des lignes orphelines dans transactions et
    # daily_history, qui restaient visibles dans Transactions/Statistiques.
    await db.execute("DELETE FROM transactions WHERE user_id=? AND lower(wallet_address)=lower(?)", (user["id"], address))
    await db.execute("DELETE FROM daily_history WHERE user_id=? AND lower(wallet_address)=lower(?)", (user["id"], address))
    await db.execute("DELETE FROM snapshots WHERE user_id=?", (user["id"],))
    # Purge du cache portfolio, insensible à la casse également.
    for _k in [k for k in list(_portfolio_cache) if isinstance(k, str) and k.lower() == address.lower()]:
        _portfolio_cache.pop(_k, None)
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


def _invalidate_portfolio_cache(addresses):
    """v2.12.6 — retire du cache portfolio les entrées des adresses données
    (comparaison insensible à la casse, même pattern que del_wallet).

    Appelé à la fin d'un rebuild d'historique : le portfolio mis en cache
    AVANT la fin du rebuild a été calculé avec daily_history vide (PNL par
    token = None) et serait sinon servi tel quel jusqu'à 1h. Purger l'entrée
    force le prochain /api/portfolio (même sans force=true) à recalculer
    avec le cost basis désormais disponible."""
    wanted = {a.lower() for a in addresses if isinstance(a, str)}
    if not wanted:
        return
    for _k in [k for k in list(_portfolio_cache) if isinstance(k, str) and k.lower() in wanted]:
        _portfolio_cache.pop(_k, None)


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
                            contract = (token.get("address") or token.get("address_hash") or "")
                            # Dedup on (tx_hash, log_index, user_id)
                            if tx_hash:
                                cur2 = await db.execute(
                                    "SELECT id, contract_address FROM transactions WHERE tx_hash=? AND log_index=? AND user_id=?",
                                    (tx_hash, log_index, user_id))
                                existing = await cur2.fetchone()
                                if existing:
                                    # Backfill contract_address if missing
                                    if contract and not (existing[1] or ""):
                                        await db.execute(
                                            "UPDATE transactions SET contract_address=? WHERE tx_hash=? AND log_index=? AND user_id=?",
                                            (contract, tx_hash, log_index, user_id))
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
                                "INSERT INTO transactions (user_id, wallet_address, token_symbol, token_name, amount, chain, tx_hash, block_time, direction, log_index, contract_address) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (user_id, address, symbol, name, amount, chain, tx_hash, ts[:19].replace("T", " ") if ts else "", direction, log_index, contract))
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


async def _fetch_full_address_transactions(user_id: int, address: str) -> int:
    """Fetch ALL address transactions (approve, contract, native) from Blockscout,
    skipping those already captured as token-transfers.
    Dedup on tx_hash — a tx that already exists in the DB is never duplicated.
    """
    import logging
    _tx2_log = logging.getLogger("crypto.tx_fetch2")
    total_new = 0

    for chain, host in CHAINS.items():
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as bc:
                url = f"https://{host}/api/v2/addresses/{address}/transactions"
                params = {}
                page_count = 0
                while page_count < MAX_TX_PAGES:
                    last_err = None
                    resp = None
                    for attempt in range(1, TX_RETRIES + 1):
                        try:
                            resp = await bc.get(url, params=params)
                            if resp.status_code == 200:
                                break
                            last_err = "HTTP %d" % resp.status_code
                            if resp.status_code < 500:
                                break
                        except Exception as e:
                            last_err = str(e)[:100]
                            resp = None
                        if attempt < TX_RETRIES and last_err and (
                                "50" in last_err or
                                "timeout" in last_err.lower() or
                                "Connection" in last_err):
                            backoff = TX_RETRY_BACKOFF ** attempt
                            await asyncio.sleep(backoff)
                        else:
                            break

                    if resp is None or (last_err and resp.status_code != 200):
                        _tx2_log.warning(
                            "Giving up full-tx on chain=%s at page %d: %s",
                            chain, page_count + 1, last_err)
                        break

                    data = resp.json()
                    items = data.get("items", [])
                    if not items:
                        break

                    async with aiosqlite.connect(DB_PATH) as db:
                        for item in items:
                            if not item:
                                continue
                            tx_hash = item.get("hash") or item.get("tx_hash", "")
                            if not tx_hash:
                                continue

                            # Skip if already in DB
                            cur2 = await db.execute(
                                "SELECT id, event_type FROM transactions WHERE tx_hash=? AND user_id=? LIMIT 1",
                                (tx_hash, user_id))
                            existing = await cur2.fetchone()
                            if existing:
                                method = (item.get("method") or "").strip().lower()
                                if method and not (existing[1] or "").strip():
                                    await db.execute(
                                        "UPDATE transactions SET event_method=? WHERE tx_hash=? AND user_id=? AND id=?",
                                        (method, tx_hash, user_id, existing[0]))
                                continue

                            # Classify
                            method = (item.get("method") or "").strip().lower()
                            ts = item.get("timestamp", "")
                            to_addr = (item.get("to") or {}).get("hash", "")
                            from_addr = (item.get("from") or {}).get("hash", "")
                            value_raw = item.get("value", "0")

                            typ = None
                            amount = 0.0
                            token_sym = ""
                            direction = ""
                            usd_price = 0.0

                            if method in ("approve", "increaseallowance"):
                                typ = "approve"
                                token_sym = "?"
                                try:
                                    tok_cur = await db.execute(
                                        "SELECT DISTINCT token_symbol FROM transactions "
                                        "WHERE contract_address=? AND user_id=? AND chain=? LIMIT 1",
                                        (to_addr.lower() if to_addr else "", user_id, chain))
                                    tok_row = await tok_cur.fetchone()
                                    if tok_row:
                                        token_sym = tok_row[0] or "?"
                                except Exception:
                                    pass
                            elif value_raw and value_raw != "0":
                                typ = "native"
                                try:
                                    amount = int(value_raw) / 1e18
                                except Exception:
                                    amount = 0.0
                                nc = NATIVE_COIN.get(chain, {"symbol": "?"})
                                token_sym = nc["symbol"] if nc else "?"
                                direction = "in" if to_addr.lower() == address.lower() else "out"
                                try:
                                    usd_price = await _get_native_price(chain, host)
                                except Exception:
                                    usd_price = 0.0
                            elif method:
                                typ = "contract"
                                token_sym = method[:20]
                                direction = ""

                            if not typ:
                                continue

                            block_time = ts[:19].replace("T", " ") if ts else ""
                            await db.execute(
                                "INSERT INTO transactions (user_id, wallet_address, token_symbol, token_name, "
                                "amount, usd_value, usd_price, chain, tx_hash, block_time, direction, "
                                "log_index, event_type, event_method, event_to) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (user_id, address, token_sym, typ, round(amount, 8),
                                 round(amount * usd_price, 2), usd_price,
                                 chain, tx_hash, block_time, direction, 0,
                                 typ, method, to_addr.lower() if to_addr else ""))
                            total_new += 1
                        await db.commit()

                    nxt = data.get("next_page_params")
                    if not nxt:
                        break
                    params = {**params, **nxt}
                    page_count += 1

                if page_count >= MAX_TX_PAGES:
                    _tx2_log.warning(
                        "Reached MAX_TX_PAGES=%d for chain=%s wallet=%s (full-tx)",
                        MAX_TX_PAGES, chain, address[:10])
        except Exception:
            continue

    return total_new


# ── Import pipeline ──────────────────────────────────────────────

async def _fetch_then_rebuild(user_id: int, address: str):
    try:
        _import_progress[user_id] = {"stage": "fetch", "done": 0, "total": len(CHAINS), "in_done": 0, "in_total": 0, "out_done": 0, "out_total": 0}
        count = await _fetch_transactions_for_wallet(user_id, address)
        count2 = await _fetch_full_address_transactions(user_id, address)
        await _enrich_historical_prices(user_id)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT direction, COUNT(*) as c FROM transactions WHERE user_id=? AND usd_price=0 GROUP BY direction", (user_id,))
            dirs = {r["direction"]: r["c"] for r in await cur.fetchall()}
        in_total = dirs.get("in", 0)
        out_total = dirs.get("out", 0)
        _import_progress[user_id] = {"stage": "enrich", "done": 0, "total": count, "in_done": in_total, "in_total": in_total, "out_done": out_total, "out_total": out_total}
        result = await _rebuild_history(user_id, address, _compute_portfolio)
        # v2.12.6 — daily_history vient d'être rempli : purge l'entrée de cache
        # de ce wallet pour que le PNL par token apparaisse au prochain
        # /api/portfolio sans attendre l'expiration du TTL (1h) ni un force=true.
        _invalidate_portfolio_cache([address])
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
                           direction: str = Query(None), event_type: str = Query(None, alias="type"),
                           limit: int = Query(100), offset: int = Query(0),
                           user=Depends(get_current_user), db=Depends(get_db)):
    # v2.12.4 — événements regroupés par (wallet, chain, tx_hash) : swap/send/receive.
    # Le REGROUPEMENT se fait AVANT la pagination (sinon les deux jambes d'un swap
    # peuvent tomber sur deux pages différentes). Filtres wallet/chain en SQL ;
    # token/direction/type appliqués APRÈS regroupement pour garder les jambes entières.
    conditions = ["user_id=?"]
    params = [user["id"]]
    # v2.12.5 — défense en profondeur : ne jamais exposer les lignes d'un wallet
    # absent de la table wallets (orphelins d'une suppression). Insensible à la casse.
    conditions.append("lower(wallet_address) IN (SELECT lower(address) FROM wallets WHERE user_id=?)")
    params.append(user["id"])
    if wallet:
        conditions.append("lower(wallet_address)=lower(?)")
        params.append(wallet)
    if chain:
        conditions.append("chain=?")
        params.append(chain)
    where = " AND ".join(conditions)
    cur = await db.execute(
        f"SELECT id, wallet_address, token_symbol, token_name, amount, usd_price, usd_value, chain, tx_hash, block_time, direction, log_index, gas_fee_usd, contract_address, event_type, event_method FROM transactions WHERE {where} ORDER BY block_time DESC",
        tuple(params))
    rows = await cur.fetchall()

    events = group_transaction_events(rows)
    events = filter_events(events, token=token, direction=direction)
    counts = {"swap": 0, "send": 0, "receive": 0, "approve": 0, "contract": 0, "native": 0}
    for ev in events:
        counts[ev["type"]] = counts.get(ev["type"], 0) + 1
    events = filter_events(events, event_type=event_type)

    total = len(events)
    page = events[offset:offset + limit]

    # 2026.07.5 — attach user tags to events
    tag_cur = await db.execute(
        "SELECT tx_hash, chain, category, note FROM user_tx_tags WHERE user_id=?",
        (user["id"],))
    tag_map = {}
    for tr in await tag_cur.fetchall():
        tag_map[f"{tr['tx_hash']}:{tr['chain']}"] = {"category": tr["category"] or "", "note": tr["note"] or ""}

    for ev in page:
        ev["wallet_label"] = _wallet_labels.get(ev["wallet_address"], "")
        ev["explorer_url"] = f"https://{CHAINS[ev['chain']]}/tx/{ev['tx_hash']}" if ev["tx_hash"] and CHAINS.get(ev["chain"]) else ""
        tag_key = f"{ev['tx_hash']}:{ev['chain']}"
        ev["tag"] = tag_map.get(tag_key) or {}
    return {"total": total, "items": page, "counts": counts}


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

        async def _fetch_one(bc, tx_hash: str) -> tuple[str, dict | None]:
            nonlocal consecutive_fails
            try:
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

        async def _fetch_with_sem(bc, tx_hash: str, wallet_address: str):
            nonlocal chain_updated
            async with sem:
                # Circuit breaker check
                if consecutive_fails >= GAS_CB_FAILURES:
                    return  # abandon this chain
                tx_hash, data = await _fetch_one(bc, tx_hash)
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

        # Process all items for this chain in parallel (bounded by semaphore),
        # sharing ONE HTTP client per chain to avoid per-request connection churn
        async with httpx.AsyncClient(timeout=GAS_TIMEOUT, follow_redirects=True) as bc:
            tasks = [_fetch_with_sem(bc, tx_hash, wallet_address) for tx_hash, wallet_address in items]
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
    # v2.12.5 — anti-orphelins : ne sommer que le gaz des wallets encore présents.
    exists_w = "lower(wallet_address) IN (SELECT lower(address) FROM wallets WHERE user_id=?)"
    if wallet:
        cur = await db.execute(
            "SELECT COALESCE(SUM(gas_fee_usd),0) as total "
            f"FROM transactions WHERE user_id=? AND {exists_w} AND lower(wallet_address)=lower(?)",
            (user["id"], user["id"], wallet))
    else:
        cur = await db.execute(
            "SELECT COALESCE(SUM(gas_fee_usd),0) as total "
            f"FROM transactions WHERE user_id=? AND {exists_w}", (user["id"], user["id"]))
    row = await cur.fetchone()
    return {"total_gas_usd": round(row["total"] if row else 0, 2)}


@app.get("/api/gas/analytics")
async def gas_analytics(address: str = Query(None), range: str = Query("30d"),
                        user=Depends(get_current_user), db=Depends(get_db)):
    """2026.07.5 — gas analytics: total spent, daily time series, by-chain breakdown.
    Defensive: never 500, empty structure on any failure."""
    import logging
    _ga_log = logging.getLogger("crypto.gas_analytics")

    empty = {"total_gas_usd": 0.0, "by_day": [], "by_chain": []}

    try:
        conditions = ["user_id=?"]
        params = [user["id"]]
        conditions.append(
            "lower(wallet_address) IN (SELECT lower(address) FROM wallets WHERE user_id=?)")
        params.append(user["id"])
        if address:
            conditions.append("lower(wallet_address)=lower(?)")
            params.append(address)

        # Range filter: default 30 days
        range_days = 30
        if range and range.rstrip("d").isdigit():
            range_days = int(range.rstrip("d"))

        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=range_days)).strftime("%Y-%m-%d")
        conditions.append("block_time >= ?")
        params.append(cutoff)

        where = " AND ".join(conditions)

        # Total gas
        cur = await db.execute(
            f"SELECT COALESCE(SUM(gas_fee_usd), 0) as total FROM transactions WHERE {where}",
            tuple(params))
        row = await cur.fetchone()
        total = round(row["total"] if row else 0, 2)

        # By day
        cur = await db.execute(
            f"SELECT SUBSTR(block_time, 1, 10) as d, COALESCE(SUM(gas_fee_usd), 0) as g "
            f"FROM transactions WHERE {where} "
            f"GROUP BY d ORDER BY d ASC",
            tuple(params))
        by_day = [{"date": r["d"], "gas_usd": round(r["g"], 2)} for r in await cur.fetchall()]

        # By chain
        cur = await db.execute(
            f"SELECT chain, COALESCE(SUM(gas_fee_usd), 0) as g "
            f"FROM transactions WHERE {where} "
            f"GROUP BY chain ORDER BY g DESC",
            tuple(params))
        by_chain = [{"chain": r["chain"], "gas_usd": round(r["g"], 2)} for r in await cur.fetchall()]

        return {"total_gas_usd": total, "by_day": by_day, "by_chain": by_chain}
    except Exception as e:
        _ga_log.error(f"gas/analytics failed: {e}")
        return empty


# ── Transaction tags (2026.07.5) ─────────────────────────────────

@app.post("/api/transactions/tag")
async def set_transaction_tag(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Upsert a tag/note on a transaction. Body: {tx_hash, chain, category?, note?}."""
    data = await request.json()
    tx_hash = (data.get("tx_hash") or "").strip()
    chain = (data.get("chain") or "").strip()
    category = (data.get("category") or "").strip().lower()[:50]
    note = (data.get("note") or "").strip()[:500]
    if not tx_hash or not chain:
        raise HTTPException(400, "tx_hash et chain requis")
    await db.execute(
        "INSERT OR REPLACE INTO user_tx_tags (user_id, tx_hash, chain, category, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (user["id"], tx_hash, chain, category, note))
    await db.commit()
    return {"ok": True, "tx_hash": tx_hash, "chain": chain, "category": category, "note": note}


@app.get("/api/transactions/tags")
async def get_transaction_tags(user=Depends(get_current_user), db=Depends(get_db)):
    """Get all tags for the current user. Returns {tx_hash:chain -> {category, note}}."""
    cur = await db.execute(
        "SELECT tx_hash, chain, category, note, created_at FROM user_tx_tags "
        "WHERE user_id=? ORDER BY created_at DESC",
        (user["id"],))
    items = {}
    for r in await cur.fetchall():
        key = f"{r['tx_hash']}:{r['chain']}"
        items[key] = {"category": r["category"] or "", "note": r["note"] or "", "created_at": r["created_at"] or ""}
    return {"tags": items}


# ── Historical price enrichment (DefiLlama) ──────────────────────

HIST_PRICE_CONCURRENCY = 6      # global (all chains share this), gentle on DefiLlama
HIST_PRICE_TIMEOUT = 12         # seconds per request
HIST_PRICE_RETRIES = 3          # retries on timeout / non-200 before giving up

async def _enrich_historical_prices(user_id: int) -> int:
    """Enrich transactions with DefiLlama historical prices, in a SUBPROCESS.

    The HTTP calls are 100% reliable in a fresh process but fail intermittently
    when run inside the long-lived uvicorn event loop, so we delegate to
    services/enrich_worker.py. Returns the number of rows newly priced.
    """
    import logging
    _elog = logging.getLogger("crypto.enrich")
    worker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "services", "enrich_worker.py")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, worker, str(user_id),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "DB_PATH": DB_PATH})
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=900)
        out = (stdout.decode() or "").strip().split()
        n = int(out[-1]) if out and out[-1].lstrip("-").isdigit() else 0
        if stderr:
            err = stderr.decode()[:200].strip()
            if err:
                _elog.warning(f"enrich worker stderr: {err}")
        _elog.info(f"Historical price enrichment (subprocess): {n} priced, user {user_id}")
        return n
    except Exception as e:
        _elog.warning(f"enrich subprocess failed: {e}")
        return 0


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


# ── Token preferences / management (v2.12.0) ────────────────────

_manual_token_cache: dict = {}   # (wallet_address_lower, tid) -> {"item": dict|None, "ts": float}
_MANUAL_TOKEN_TTL = 3600

_rebuild_state: dict = {}        # user_id -> {"running": bool, "rerun": bool}


async def _run_history_rebuild(user_id: int):
    """Rebuild daily_history for all the user's wallets in a SUBPROCESS
    (services/rebuild_worker.py) — the same reliable mechanism as the price
    enrichment worker. Runs in the background; never blocks an HTTP response.

    Debounced: if a rebuild is already running for this user, one rerun is
    queued so the LAST preference state always wins.
    """
    st = _rebuild_state.setdefault(user_id, {"running": False, "rerun": False})
    if st["running"]:
        st["rerun"] = True
        return
    st["running"] = True
    _rlog = logging.getLogger("crypto.rebuild")
    worker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "services", "rebuild_worker.py")
    try:
        while True:
            st["rerun"] = False
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, worker, str(user_id),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    env={**os.environ, "DB_PATH": DB_PATH})
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=1800)
                out = (stdout.decode() or "").strip()
                _rlog.info(f"history rebuild done user={user_id}: days={out[-20:] if out else '?'}")
                if stderr:
                    err = stderr.decode()[:200].strip()
                    if err:
                        _rlog.warning(f"rebuild worker stderr: {err}")
                # v2.12.6 — reconstruction terminée : purge le cache portfolio de
                # tous les wallets de l'utilisateur pour que le prochain
                # /api/portfolio (même sans force) recalcule avec le cost basis
                # désormais présent dans daily_history.
                try:
                    async with aiosqlite.connect(DB_PATH) as db:
                        db.row_factory = aiosqlite.Row
                        cur = await db.execute("SELECT address FROM wallets WHERE user_id=?", (user_id,))
                        addrs = [r["address"] for r in await cur.fetchall()]
                    _invalidate_portfolio_cache(addrs)
                except Exception as ce:
                    _rlog.warning(f"portfolio cache invalidation failed: {ce}")
            except Exception as e:
                _rlog.warning(f"rebuild subprocess failed: {e}")
            if not st["rerun"]:
                break
    finally:
        st["running"] = False


async def _fetch_manual_token_item(wallet_address: str, pref: dict):
    """Live balance + price for a manually added token on ONE wallet.

    Blockscout /token-balances (single call, no pagination) filtered on the
    contract; DefiLlama current-price fallback when Blockscout has no rate.
    Result cached 1h per (wallet, tid). Returns an item dict or None.
    """
    tid = (pref.get("tid") or "").lower()
    chain = (pref.get("chain") or "").lower()
    contract = (pref.get("contract_address") or "").lower()
    host = CHAINS.get(chain)
    if not host or not contract:
        return None

    key = (wallet_address.lower(), tid)
    now = _time.time()
    entry = _manual_token_cache.get(key)
    if entry and now - entry["ts"] < _MANUAL_TOKEN_TTL:
        return dict(entry["item"]) if entry["item"] else None

    balance = 0.0
    usd_price = 0.0
    symbol = pref.get("symbol") or "?"
    name = pref.get("name") or symbol
    icon = ""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=12) as client:
            r = await client.get(f"https://{host}/api/v2/addresses/{wallet_address}/token-balances")
            if r.status_code == 200:
                for b in (r.json() or []):
                    tk = (b or {}).get("token") or {}
                    addr = (tk.get("address") or tk.get("address_hash") or "").lower()
                    if addr != contract:
                        continue
                    try:
                        decimals = int(tk.get("decimals") or 18)
                        balance = int(b.get("value") or 0) / (10 ** decimals)
                    except Exception:
                        balance = 0.0
                    usd_price = float(tk.get("exchange_rate") or 0)
                    symbol = tk.get("symbol") or symbol
                    name = tk.get("name") or name
                    icon = tk.get("icon_url") or ""
                    break
    except Exception:
        pass

    price_confidence = None
    if usd_price <= 0:
        try:
            llama = await _fetch_defillama_current_prices([(chain, contract, symbol)])
            lp = llama.get(contract)
            if lp:
                usd_price = lp["price"]
                price_confidence = lp.get("confidence")
        except Exception:
            pass

    item = {
        "chain": chain,
        "name": name,
        "symbol": symbol,
        "balance": round(balance, 6),
        "usd_value": round(balance * usd_price, 2),
        "usd_price": usd_price,
        "icon": icon,
        "type": "ERC-20",
        "contract_address": contract,
        "price_unknown": usd_price <= 0,
        "price_confidence": price_confidence,
        "category": _token_category(symbol),
        "tid": tid,
        "enabled": True,
        "reason": pref.get("reason") or "manual",
        "source": "manual",
    }
    _manual_token_cache[key] = {"item": item, "ts": now}
    return dict(item)


async def _apply_user_token_prefs(user_id: int, data: dict, wallet_address: str) -> dict:
    """Attach per-user enable/disable prefs to portfolio items and recompute
    the aggregates over ENABLED tokens only.

    • Every item gets: tid, enabled, reason, source.
    • Newly seen tids are auto-classified (classify_token) and inserted with
      the computed default (INSERT OR IGNORE — an existing user choice is
      NEVER overwritten).
    • Active manually-added tokens are merged in with live balance/price.
    • total_usd / defi_usd / defi_breakdown / token_count / chains reflect
      enabled tokens only; ALL items stay in the response with their flags.
    • Operates on a COPY: the per-address _portfolio_cache is shared between
      users and must never be polluted with user-specific fields.

    Sets data["_new_auto_disabled"] (internal) when new dubious tokens were
    auto-disabled — the caller uses it to trigger a history rebuild.
    """
    out = dict(data)
    items = [dict(it) for it in data.get("tokens", [])]
    prefs = await load_user_prefs(user_id)

    new_rows = []
    reclass_rows = []
    new_auto_disabled = 0
    for it in items:
        tid = token_tid(it.get("symbol"), it.get("chain"), it.get("contract_address"))
        it["tid"] = tid
        pref = prefs.get(tid)
        spam_flag = bool(_is_spam(it.get("symbol")) or _is_spam(it.get("name")))
        if pref is None:
            de, reason = classify_token(
                it.get("usd_value", 0), it.get("usd_price", 0),
                it.get("balance", 0), it.get("price_confidence"),
                is_spam=spam_flag)
            new_rows.append((
                user_id, tid, de, "detected", it.get("chain") or "",
                it.get("contract_address") or "", it.get("symbol") or "",
                it.get("name") or "", reason, de))
            prefs[tid] = {"tid": tid, "enabled": de, "source": "detected", "reason": reason}
            if not de:
                new_auto_disabled += 1
            it["enabled"] = bool(de)
            it["reason"] = reason
            it["source"] = "detected"
        else:
            it["enabled"] = bool(pref.get("enabled", 1))
            it["reason"] = pref.get("reason") or ""
            it["source"] = pref.get("source") or "detected"
            # Retroactive reclassification (v2.12.2): rows inserted before the
            # zero_value/spam heuristics existed, still pristine (enabled, no
            # reason, never toggled by the user) are re-evaluated once. The
            # SQL side re-checks the pristine guard (updated_at = created_at).
            if (it["source"] == "detected" and it["enabled"] and not it["reason"]
                    and (pref.get("updated_at") is None
                         or pref.get("updated_at") == pref.get("created_at"))):
                de2, reason2 = classify_token(
                    it.get("usd_value", 0), it.get("usd_price", 0),
                    it.get("balance", 0), it.get("price_confidence"),
                    is_spam=spam_flag)
                if not de2:
                    reclass_rows.append((reason2, user_id, tid))
                    pref["enabled"] = 0
                    pref["reason"] = reason2
                    it["enabled"] = False
                    it["reason"] = reason2
                    new_auto_disabled += 1

    if new_rows:
        try:
            await insert_default_prefs(new_rows)
        except Exception as e:
            logging.getLogger("crypto.portfolio").warning(f"[prefs] insert failed: {e}")
    if reclass_rows:
        try:
            await reclassify_prefs(reclass_rows)
            logging.getLogger("crypto.portfolio").info(
                f"[prefs] user={user_id}: {len(reclass_rows)} pref(s) retroactively "
                "auto-disabled (zero_value/spam)")
        except Exception as e:
            logging.getLogger("crypto.portfolio").warning(f"[prefs] reclassify failed: {e}")

    # Merge active manual tokens that are not already detected on-chain
    present = {it["tid"] for it in items}
    for pref in prefs.values():
        if pref.get("source") != "manual" or not pref.get("enabled"):
            continue
        if (pref.get("tid") or "").lower() in present:
            continue
        try:
            m_item = await _fetch_manual_token_item(wallet_address, pref)
        except Exception:
            m_item = None
        if m_item and (m_item.get("balance") or 0) > 0:
            items.append(m_item)

    # Recompute aggregates over ENABLED tokens only
    active = [it for it in items if it.get("enabled", True)]
    inactive = [it for it in items if not it.get("enabled", True)]
    total = 0.0
    chain_totals: dict = {}
    defi_usd = 0.0
    defi_breakdown: dict = {}
    for it in active:
        v = float(it.get("usd_value") or 0)
        total += v
        ch = it.get("chain") or "?"
        chain_totals[ch] = chain_totals.get(ch, 0) + v
        cat = it.get("category", "wallet")
        if _is_defi_category(cat):
            defi_usd += v
            defi_breakdown[cat] = defi_breakdown.get(cat, 0) + v

    # Response layout: ALL actives first (value desc), then inactives (value
    # desc, capped to bound the payload — a spam-heavy wallet can hold
    # hundreds). Counts stay EXACT even when the list is capped.
    active.sort(key=lambda x: (x.get("usd_value") or 0), reverse=True)
    inactive.sort(key=lambda x: (x.get("usd_value") or 0), reverse=True)
    out["tokens"] = active[:250] + inactive[:400]
    out["total_usd"] = round(total, 2)
    out["defi_usd"] = round(defi_usd, 2)
    out["staked_usd"] = round(defi_usd, 2)  # backward compat
    out["defi_breakdown"] = {k: round(v, 2) for k, v in defi_breakdown.items()}
    out["token_count"] = len(active)
    out["active_count"] = len(active)
    out["inactive_count"] = len(inactive)
    out["chains"] = {
        c: round(v, 2)
        for c, v in sorted(chain_totals.items(), key=lambda x: x[1], reverse=True)
        if v > 0
    }
    out["chain_count"] = len(out["chains"])
    out["disabled_count"] = len(inactive)  # backward compat (v2.12.0)
    if out.get("total_cost_basis") is not None:
        out["total_pnl"] = round(out["total_usd"] - out["total_cost_basis"], 2)
    if new_auto_disabled:
        out["_new_auto_disabled"] = new_auto_disabled
    return out


@app.get("/api/tokens")
async def list_tokens(scope: str = Query("detected"), user=Depends(get_current_user), db=Depends(get_db)):
    """Tokens of the management page. scope=detected|manual."""
    if scope not in ("detected", "manual"):
        raise HTTPException(400, "scope doit être 'detected' ou 'manual'")
    cur = await db.execute(
        "SELECT * FROM user_token_prefs WHERE user_id=? AND source=? ORDER BY LOWER(symbol)",
        (user["id"], scope))
    prefs = [dict(r) for r in await cur.fetchall()]

    cur = await db.execute("SELECT address FROM wallets WHERE user_id=?", (user["id"],))
    wallet_rows = await cur.fetchall()

    # Live data per tid, aggregated across the user's wallets
    live: dict = {}
    if scope == "detected":
        for w in wallet_rows:
            entry = _portfolio_cache.get(w["address"])
            if not entry:
                continue
            for it in entry["data"].get("tokens", []):
                tid = token_tid(it.get("symbol"), it.get("chain"), it.get("contract_address"))
                agg = live.setdefault(tid, {"balance": 0.0, "usd_value": 0.0, "usd_price": 0.0})
                agg["balance"] += float(it.get("balance") or 0)
                agg["usd_value"] += float(it.get("usd_value") or 0)
                if not agg["usd_price"]:
                    agg["usd_price"] = float(it.get("usd_price") or 0)
    else:
        for p in prefs:
            agg = live.setdefault(p["tid"], {"balance": 0.0, "usd_value": 0.0, "usd_price": 0.0})
            for w in wallet_rows:
                try:
                    m = await _fetch_manual_token_item(w["address"], p)
                except Exception:
                    m = None
                if m:
                    agg["balance"] += float(m.get("balance") or 0)
                    agg["usd_value"] += float(m.get("usd_value") or 0)
                    if not agg["usd_price"]:
                        agg["usd_price"] = float(m.get("usd_price") or 0)

    tokens = []
    for p in prefs:
        lv = live.get(p["tid"], {})
        tokens.append({
            "tid": p["tid"],
            "chain": p.get("chain") or "",
            "symbol": p.get("symbol") or "?",
            "name": p.get("name") or "",
            "balance": round(float(lv.get("balance") or 0), 6),
            "usd_value": round(float(lv.get("usd_value") or 0), 2),
            "usd_price": float(lv.get("usd_price") or 0),
            "enabled": bool(p.get("enabled")),
            "reason": p.get("reason") or "",
            "source": p.get("source") or scope,
            "default_enabled": bool(p.get("default_enabled", 1)),
        })
    # Sort: biggest value first, then symbol
    tokens.sort(key=lambda x: (-x["usd_value"], x["symbol"].lower()))
    return {"scope": scope, "tokens": tokens}


@app.post("/api/tokens/toggle")
async def toggle_token(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Enable/disable a token. Triggers a retroactive history rebuild."""
    data = await request.json()
    tid = (data.get("tid") or "").strip().lower()
    enabled = 1 if data.get("enabled") else 0
    if not tid:
        raise HTTPException(400, "tid requis")
    cur = await db.execute(
        "SELECT tid FROM user_token_prefs WHERE user_id=? AND tid=?", (user["id"], tid))
    row = await cur.fetchone()
    if row:
        await db.execute(
            "UPDATE user_token_prefs SET enabled=?, updated_at=datetime('now') "
            "WHERE user_id=? AND tid=?",
            (enabled, user["id"], tid))
    else:
        await db.execute(
            "INSERT INTO user_token_prefs (user_id, tid, enabled, source, reason, default_enabled) "
            "VALUES (?,?,?,?,?,?)",
            (user["id"], tid, enabled, "detected", "", 1))
    await db.commit()
    asyncio.create_task(_run_history_rebuild(user["id"]))
    return {"ok": True, "tid": tid, "enabled": bool(enabled)}


@app.post("/api/tokens/bulk")
async def bulk_toggle_tokens(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Enable/disable ALL tokens of a scope at once."""
    data = await request.json()
    scope = (data.get("scope") or "detected").strip()
    enabled = 1 if data.get("enabled") else 0
    if scope not in ("detected", "manual"):
        raise HTTPException(400, "scope doit être 'detected' ou 'manual'")
    await db.execute(
        "UPDATE user_token_prefs SET enabled=?, updated_at=datetime('now') "
        "WHERE user_id=? AND source=?",
        (enabled, user["id"], scope))
    await db.commit()
    asyncio.create_task(_run_history_rebuild(user["id"]))
    return {"ok": True, "scope": scope, "enabled": bool(enabled)}


@app.post("/api/tokens/manual")
async def add_manual_token(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Manually add (and enable) a token by chain + contract address."""
    data = await request.json()
    chain = (data.get("chain") or "").strip().lower()
    contract = (data.get("contract_address") or "").strip().lower()
    symbol = (data.get("symbol") or "").strip()[:20]
    if chain not in CHAINS:
        raise HTTPException(400, "Chaîne inconnue")
    if not contract.startswith("0x") or len(contract) != 42:
        raise HTTPException(400, "Adresse de contrat invalide (0x + 40 caractères hex)")
    tid = contract

    # Token metadata via Blockscout (best effort)
    name = ""
    try:
        host = CHAINS[chain]
        async with httpx.AsyncClient(follow_redirects=True, timeout=12) as client:
            r = await client.get(f"https://{host}/api/v2/tokens/{contract}")
            if r.status_code == 200:
                tk = r.json() or {}
                symbol = tk.get("symbol") or symbol
                name = tk.get("name") or ""
    except Exception:
        pass
    if not symbol:
        symbol = "?"
    if not name:
        name = symbol

    cur = await db.execute(
        "SELECT tid FROM user_token_prefs WHERE user_id=? AND tid=?", (user["id"], tid))
    exists = await cur.fetchone()
    if exists:
        await db.execute(
            "UPDATE user_token_prefs SET enabled=1, source='manual', chain=?, "
            "contract_address=?, symbol=?, name=?, reason='manual', "
            "updated_at=datetime('now') WHERE user_id=? AND tid=?",
            (chain, contract, symbol, name, user["id"], tid))
    else:
        await db.execute(
            "INSERT INTO user_token_prefs (user_id, tid, enabled, source, chain, "
            "contract_address, symbol, name, reason, default_enabled) "
            "VALUES (?,?,1,'manual',?,?,?,?,'manual',1)",
            (user["id"], tid, chain, contract, symbol, name))
    await db.commit()

    # Best-effort live data across the user's wallets
    item = {"tid": tid, "chain": chain, "symbol": symbol, "name": name,
            "balance": 0.0, "usd_value": 0.0, "usd_price": 0.0,
            "enabled": True, "reason": "manual", "source": "manual"}
    try:
        cur = await db.execute(
            "SELECT address FROM wallets WHERE user_id=? ORDER BY created_at", (user["id"],))
        wrows = await cur.fetchall()
        pref = {"tid": tid, "chain": chain, "contract_address": contract,
                "symbol": symbol, "name": name, "reason": "manual"}
        bal = usd = price = 0.0
        for w in wrows:
            m = await _fetch_manual_token_item(w["address"], pref)
            if m:
                bal += float(m.get("balance") or 0)
                usd += float(m.get("usd_value") or 0)
                if not price:
                    price = float(m.get("usd_price") or 0)
        item.update({"balance": round(bal, 6), "usd_value": round(usd, 2), "usd_price": price})
    except Exception:
        pass

    asyncio.create_task(_run_history_rebuild(user["id"]))
    return {"ok": True, "token": item}


@app.delete("/api/tokens/manual")
async def del_manual_token(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Remove a manually added token preference."""
    data = await request.json()
    tid = (data.get("tid") or "").strip().lower()
    if not tid:
        raise HTTPException(400, "tid requis")
    await db.execute(
        "DELETE FROM user_token_prefs WHERE user_id=? AND tid=? AND source='manual'",
        (user["id"], tid))
    await db.commit()
    for k in list(_manual_token_cache.keys()):
        if k[1] == tid:
            _manual_token_cache.pop(k, None)
    asyncio.create_task(_run_history_rebuild(user["id"]))
    return {"ok": True, "tid": tid}


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
        # Apply per-user token prefs on a COPY (cache is shared between users)
        data = await _apply_user_token_prefs(user["id"], entry["data"], address)
        if data.pop("_new_auto_disabled", None):
            asyncio.create_task(_run_history_rebuild(user["id"]))
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
                    # Fallback: weighted-average cost method. Replay the
                    # token's transactions in chronological order: buys add
                    # amount*price to the cumulative cost, sells remove at
                    # the AVERAGE cost per unit — NOT at the sale price.
                    # (The old signed SUM subtracted sale PROCEEDS from the
                    # cost, yielding absurd averages, e.g. STETH at
                    # ~$296/unit instead of ~$1730.)
                    cur3 = await db.execute(
                        "SELECT direction, amount, usd_price FROM transactions "
                        "WHERE user_id=? AND wallet_address=? AND LOWER(token_symbol)=? "
                        "ORDER BY block_time ASC",
                        (user["id"], address, sym))
                    tx_rows = await cur3.fetchall()
                    qty = 0.0
                    cost = 0.0
                    any_price = False
                    for r in tx_rows:
                        amount = r["amount"] or 0
                        price = r["usd_price"] or 0
                        if not (math.isfinite(amount) and math.isfinite(price)):
                            continue
                        if price > 0:
                            any_price = True
                        if r["direction"] == "in":
                            qty += amount
                            cost += amount * price
                        elif r["direction"] == "out":
                            if qty > 0:
                                avg = cost / qty
                                removed = min(cost, avg * amount)
                                cost -= removed
                            qty -= amount
                            if qty < 0:
                                qty = 0.0
                            if qty == 0:
                                cost = 0.0
                    if any_price and qty > 0 and cost > 0 and math.isfinite(cost):
                        avg_cost = cost / qty if qty > 0 else 0.0
                        final_cost = avg_cost * (t.get("balance", 0) or 0)
                        if math.isfinite(final_cost):
                            t["cost_basis"] = round(final_cost, 2)
                            t["pnl"] = round(usd_val - final_cost, 2)
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

    # Apply per-user token prefs (enable/disable + manual tokens) on a COPY —
    # the address-keyed cache stays user-agnostic. Totals below (snapshot,
    # response) reflect ENABLED tokens only.
    data = await _apply_user_token_prefs(user["id"], data, address)
    if data.pop("_new_auto_disabled", None):
        # Newly auto-disabled dubious tokens → retroactive history rebuild
        asyncio.create_task(_run_history_rebuild(user["id"]))

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
        await _fetch_full_address_transactions(user_id, w["address"])
        await _enrich_historical_prices(user_id)
        if count > 0:
            await _rebuild_history(user_id, w["address"], _compute_portfolio)
    # Fetch gas fees after daily refresh (non-blocking)
    asyncio.create_task(_fetch_gas_for_user(user_id))


# ── NFTs ─────────────────────────────────────────────────────────

async def _fetch_nfts_chain(client, chain: str, host: str, address: str, max_pages: int = 2) -> list:
    """Fetch owned NFTs (ERC-721/1155/404) on one chain, spam-filtered."""
    out = []
    url = f"https://{host}/api/v2/addresses/{address}/nft"
    params = {"type": "ERC-721,ERC-1155,ERC-404"}
    page = 0
    while page < max_pages:
        try:
            r = await client.get(url, params=params, timeout=12)
        except Exception:
            break
        if r.status_code != 200:
            break
        data = r.json()
        for it in data.get("items", []):
            if not it:
                continue
            try:
                t = it.get("token") or {}
                coll = t.get("name") or "?"
                sym = t.get("symbol") or ""
                if _is_spam(coll) or _is_spam(sym):
                    continue
                md = it.get("metadata") if isinstance(it.get("metadata"), dict) else {}
                name = (md.get("name") if md else None) or f"{coll} #{it.get('id', '')}"[:60]
                img = it.get("image_url") or it.get("media_url") or ""
                if isinstance(img, str) and img.startswith("ipfs://"):
                    img = "https://ipfs.io/ipfs/" + img[7:]
                out.append({
                    "chain": chain,
                    "collection": coll,
                    "token_type": it.get("token_type") or t.get("type") or "",
                    "id": str(it.get("id") or ""),
                    "name": name,
                    "image": img,
                    "contract": (t.get("address") or t.get("address_hash") or ""),
                })
            except Exception:
                continue
        nxt = data.get("next_page_params")
        if not nxt:
            break
        params = {**params, **nxt}
        page += 1
    return out


@app.get("/api/nfts")
async def get_nfts(address: str = Query(...), user=Depends(get_current_user)):
    if not address.startswith("0x"):
        raise HTTPException(400, "Adresse invalide")
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(
            *[_fetch_nfts_chain(client, c, h, address) for c, h in CHAINS.items()],
            return_exceptions=True)
    nfts = []
    for r in results:
        if isinstance(r, list):
            nfts.extend(r)
    # Group count by collection for a quick summary
    return {"address": address, "count": len(nfts), "nfts": nfts[:600]}


# ── Snapshots / History API ──────────────────────────────────────

@app.get("/api/snapshots")
async def get_snapshots(token: str = Query(None), wallet: str = Query(None), chain: str = Query(None),
                        format: str = Query("v1"),
                        user=Depends(get_current_user), db=Depends(get_db)):
    """Returns daily history as snapshots for chart compatibility.

    Query params:
        format: 'v1' (default, legacy array of objects) or 'v2' ({labels, values, meta}).
    """
    conditions = ["user_id=?"]
    params = [user["id"]]
    # v2.12.5 — défense en profondeur : restreindre aux wallets encore présents
    # dans la table wallets (aucune donnée d'un wallet supprimé, cascade ou pas).
    conditions.append("lower(wallet_address) IN (SELECT lower(address) FROM wallets WHERE user_id=?)")
    params.append(user["id"])

    if token:
        conditions.append("LOWER(token_symbol)=?")
        params.append(token.lower())
    else:
        conditions.append("token_symbol IS NULL")

    if wallet and wallet != "ALL":
        conditions.append("lower(wallet_address)=lower(?)")
        params.append(wallet)
    if chain:
        conditions.append("chain=?")
        params.append(chain)

    where = " AND ".join(conditions)
    # SUM per date: daily_history has one aggregate row PER WALLET per date, so
    # across multiple wallets we must sum them (otherwise the chart plots each
    # wallet as a separate point on the same date → wild oscillation).
    cur = await db.execute(
        f"SELECT date, SUM(value_usd) as total_usd, SUM(cost_basis_usd) as cost_basis "
        f"FROM daily_history WHERE {where} GROUP BY date ORDER BY date ASC",
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
            # Use the portfolio total over ENABLED tokens only (tokens are
            # already spam-filtered in _compute_portfolio; user-disabled tids
            # are excluded here so the last point matches the filtered history).
            disabled = await get_disabled_tids(user["id"])
            pf_total = 0.0
            for tk in pf.get("tokens", []):
                tk_tid = token_tid(tk.get("symbol"), tk.get("chain"), tk.get("contract_address"))
                if tk_tid in disabled:
                    continue
                pf_total += tk.get("usd_value", 0) or 0
            if pf_total > 0:
                rows[-1] = dict(rows[-1])
                rows[-1]["total_usd"] = round(pf_total, 2)
        except Exception:
            pass

    if format == "v2":
        return format_snapshots_v2(rows)

    return format_snapshots_legacy(rows)


@app.get("/api/snapshots/tokens")
async def get_snapshot_tokens(user=Depends(get_current_user), db=Depends(get_db)):
    cur = await db.execute(
        "SELECT DISTINCT token_symbol FROM daily_history WHERE user_id=? AND token_symbol IS NOT NULL "
        "AND lower(wallet_address) IN (SELECT lower(address) FROM wallets WHERE user_id=?) "  # v2.12.5 anti-orphelins
        "ORDER BY token_symbol",
        (user["id"], user["id"]))
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
        # Portfolio value over the SAME set the history now covers (all priced,
        # spam-filtered tokens, user-disabled tids excluded), so delta_pct is a
        # meaningful indicator.
        address = wallet_rows[0]["address"]
        data = await _compute_portfolio(address)
        disabled = await get_disabled_tids(user["id"])
        port_value = 0.0
        for tk in data.get("tokens", []):
            tk_tid = token_tid(tk.get("symbol"), tk.get("chain"), tk.get("contract_address"))
            if tk_tid in disabled:
                continue
            port_value += tk.get("usd_value", 0) or 0
        if hist_value > 0 and port_value > 0:
            delta_pct = round((hist_value - port_value) / port_value * 100, 1)
            reconciliation = {"history_last_value": round(hist_value, 2),
                            "portfolio_value": round(port_value, 2),
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
    conditions = ["user_id=?"]
    params = [user["id"]]
    # v2.12.5 — défense en profondeur : ignorer les lignes daily_history dont le
    # wallet n'existe plus (orphelins), pour ne jamais fausser le PNL.
    conditions.append("lower(wallet_address) IN (SELECT lower(address) FROM wallets WHERE user_id=?)")
    params.append(user["id"])

    if wallet and wallet != "ALL":
        conditions.append("lower(wallet_address)=lower(?)")
        params.append(wallet)
    if token:
        conditions.append("LOWER(token_symbol)=?")
        params.append(token.lower())
    else:
        conditions.append("token_symbol IS NULL")

    where = " AND ".join(conditions)
    # SUM per date across wallets (one aggregate row per wallet per date), else
    # the PNL series gets several conflicting points per date → nonsense.
    cur = await db.execute(
        f"SELECT date, SUM(value_usd) as value_usd, SUM(cost_basis_usd) as cost_basis_usd, "
        f"SUM(net_flows_usd) as net_flows_usd FROM daily_history WHERE {where} "
        f"GROUP BY date ORDER BY date ASC",
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
        return {"ok": True, "enriched": 0, "historical": 0}
    # Historical enrichment FIRST so it gets a clean DefiLlama rate budget
    # (running _fetch_prices_per_token first would exhaust it and make the
    # historical calls fail).
    historical = await _enrich_historical_prices(user["id"])
    total = 0
    for w in wallets_list:
        result = await _fetch_prices_per_token(user["id"], w["address"])
        total += result.get("enriched", 0)
    return {"ok": True, "enriched": total, "historical": historical}


@app.post("/api/transactions/fetch")
async def fetch_transactions(user=Depends(get_current_user), db=Depends(get_db)):
    cur = await db.execute("SELECT address, label FROM wallets WHERE user_id=?", (user["id"],))
    wallets_list = await cur.fetchall()
    if not wallets_list:
        raise HTTPException(400, "Aucun wallet")
    total_tx = 0
    for w in wallets_list:
        total_tx += await _fetch_transactions_for_wallet(user["id"], w["address"])
        total_tx += await _fetch_full_address_transactions(user["id"], w["address"])
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
                "https://api.github.com/repos/LostInTheBugs/Crypto-Wallet-Tracker/tags?per_page=100",
                headers={"Accept": "application/vnd.github+json"})
            if r.status_code == 200:
                data = r.json()
                calver_tags = []
                semver_tags = []
                for item in data:
                    name = item["name"]
                    if re.match(r"^\d{4}\.\d{2}\.\d+$", name):
                        calver_tags.append(name)
                    elif re.match(r"^v?\d+\.\d+\.\d+$", name):
                        semver_tags.append(name.lstrip("v"))
                # CalVer tags are always more recent than semver
                if calver_tags:
                    def _calver_key(t):
                        p = t.split(".")
                        return (int(p[0]), int(p[1]), int(p[2]))
                    calver_tags.sort(key=_calver_key, reverse=True)
                    return {"tag": calver_tags[0]}
                elif semver_tags:
                    from packaging.version import Version
                    semver_tags.sort(key=lambda t: Version(t), reverse=True)
                    return {"tag": semver_tags[0]}
    except Exception:
        pass
    return {"tag": ""}


@app.post("/api/update")
async def update_application(user=Depends(get_current_user)):
    """Trigger git pull + docker rebuild from GitHub. Disabled by default:
    set ALLOW_UPDATE=1 to enable (it can run arbitrary upstream code)."""
    if (os.environ.get("ALLOW_UPDATE") or "").strip().lower() not in ("1", "true", "yes"):
        raise HTTPException(403, "Mise à jour désactivée sur ce serveur (ALLOW_UPDATE non défini)")
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


# ── DeFi positions (Moralis) — v2.12.8 / fallback gratuit v2.12.9 ──

_defi_cache: dict = {}          # (user_id, address_lower) -> {"data": dict, "ts": float}
_DEFI_CACHE_TTL = 600           # seconds — protects Moralis free-tier quotas


async def _get_user_moralis_key(user_id: int) -> str:
    """Get Moralis API key for user, fallback to env var (same pattern as CoinGecko)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT api_key FROM user_api_keys WHERE user_id=? AND provider='moralis'", (user_id,))
        row = await cur.fetchone()
    if row:
        return row["api_key"]
    return os.environ.get("MORALIS_API_KEY", "")


def _invalidate_defi_cache(user_id: int):
    """Drop cached DeFi responses for one user (key added/changed/removed)."""
    for _k in [k for k in list(_defi_cache) if isinstance(k, tuple) and k and k[0] == user_id]:
        _defi_cache.pop(_k, None)


async def _fetch_moralis_defi_positions(api_key: str, address: str):
    """Query Moralis DeFi positions for `address` on every supported chain.

    One GET per chain (the endpoint takes a single ?chain=), all in parallel.
    Fully defensive: any per-chain failure (HTTP != 200, timeout, bad JSON)
    is recorded as a readable error string and never raises.
    Returns (positions, errors).
    """
    _log = logging.getLogger("crypto.defi")
    base = "https://deep-index.moralis.io/api/v2.2"
    headers = {"X-API-Key": api_key, "Accept": "application/json"}

    async def one_chain(client, chain):
        try:
            r = await client.get(
                f"{base}/wallets/{address}/defi/positions",
                params={"chain": chain}, headers=headers)
            if r.status_code == 200:
                try:
                    payload = r.json()
                except Exception:
                    return [], f"{chain}: réponse JSON invalide"
                return normalize_defi_positions(payload, chain=chain), None
            if r.status_code == 401:
                return [], "Moralis: clé API invalide ou expirée (401)"
            if r.status_code == 429:
                return [], f"{chain}: quota Moralis dépassé (429)"
            # 400 = chaîne non supportée pour cette route → silencieux
            if r.status_code == 400:
                _log.debug(f"[DEFI] chain={chain} not supported (400)")
                return [], None
            return [], f"{chain}: HTTP {r.status_code}"
        except Exception as e:
            return [], f"{chain}: {str(e)[:80]}"

    positions, errors = [], []
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            results = await asyncio.gather(
                *[one_chain(client, c) for c in MORALIS_DEFI_CHAINS],
                return_exceptions=True)
    except Exception as e:
        return [], [f"Moralis: {str(e)[:120]}"]

    for res in results:
        if isinstance(res, BaseException):
            errors.append(str(res)[:80])
            continue
        chain_positions, err = res
        positions.extend(chain_positions or [])
        if err:
            errors.append(err)

    # Dedupe error strings (an invalid key repeats once per chain)
    seen = set()
    uniq_errors = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            uniq_errors.append(e)
    _log.info(f"[DEFI] address={address[:12]}... positions={len(positions)} errors={len(uniq_errors)}")
    return positions, uniq_errors


async def _get_portfolio_for_defi(user_id: int, address: str) -> dict:
    """Portfolio tokens for the best-effort DeFi fallback (v2.12.9).

    Reuses _portfolio_cache (same keying and 1h TTL as /api/portfolio) so the
    DeFi page never triggers a duplicate 22-chain Blockscout scan, then applies
    the per-user token prefs so user-disabled tokens are excluded.
    """
    now = _time.time()
    entry = _portfolio_cache.get(address)
    if entry and (now - entry["ts"]) < 3600:
        data = entry["data"]
    else:
        data = await _compute_portfolio(address)
        _portfolio_cache[address] = {"data": data, "ts": now}
    data = await _apply_user_token_prefs(user_id, data, address)
    if data.pop("_new_auto_disabled", None):
        asyncio.create_task(_run_history_rebuild(user_id))
    return data


@app.get("/api/defi/positions")
async def defi_positions(address: str = Query(...), force: bool = Query(False), user=Depends(get_current_user)):
    """DeFi positions (lending/borrowing/staking/LP) of a wallet.

    • With a Moralis key: rich positions via Moralis (rewards, APY, health
      factor) — source:"moralis". Unchanged behaviour.
    • Without a key (v2.12.9): FREE best-effort positions built from the
      on-chain Blockscout balances (aTokens, debt tokens, LSTs, LP, vaults) —
      source:"best-effort", rewards/APY/health factor unavailable (0/null).
    Never returns a 5xx: any failure degrades to an empty positions list.
    """
    if not address.startswith("0x"):
        raise HTTPException(400, "Adresse invalide")

    api_key = await _get_user_moralis_key(user["id"])
    cache_key = (user["id"], address.lower())
    now = _time.time()
    entry = _defi_cache.get(cache_key)
    if not force and entry and (now - entry["ts"]) < _DEFI_CACHE_TTL:
        return {**entry["data"], "cached": True}

    _log = logging.getLogger("crypto.defi")

    if not api_key:
        # ── Free best-effort fallback from on-chain balances ──────
        positions, be_error = [], None
        try:
            pf = await _get_portfolio_for_defi(user["id"], address)
            positions = build_best_effort_positions(
                pf.get("tokens") or [],
                explorer_hosts=CHAINS,
                is_spam=_is_spam,
            )
        except Exception as e:
            _log.warning(f"[DEFI] best-effort failed for {address[:12]}...: {e}")
            be_error = f"best-effort: {str(e)[:120]}"
        summary = summarize_defi_positions(positions)
        summary["source"] = "best-effort"
        data = {
            "configured": False,
            "source": "best-effort",
            "address": address,
            "positions": positions,
            "summary": summary,
        }
        if be_error:
            data["error"] = be_error
        _log.info(f"[DEFI] best-effort address={address[:12]}... positions={len(positions)}")
        _defi_cache[cache_key] = {"data": data, "ts": now}
        return data

    positions, errors = await _fetch_moralis_defi_positions(api_key, address)
    summary = summarize_defi_positions(positions)
    summary["source"] = "moralis"
    data = {
        "configured": True,
        "source": "moralis",
        "address": address,
        "positions": positions,
        "summary": summary,
    }
    if errors:
        data["error"] = " ; ".join(errors[:3])[:300]
    _defi_cache[cache_key] = {"data": data, "ts": now}
    return data


# ── Analytics (2026.07.3) ───────────────────────────────────────

_analytics_cache: dict = {}   # (user_id, address_lower, range) -> {"data","ts"}
_ANALYTICS_CACHE_TTL = 300    # 5 min — cache serveur court par (user, address, range)
_benchmark_cache: dict = {}   # days -> {"data","ts"} (indépendant de l'utilisateur)

ANALYTICS_RANGES = {"24h": 1, "7d": 7, "30d": 30}


async def _fetch_benchmark_pcts(days: int):
    """Variation % de BTC et ETH sur `days` jours via DefiLlama chart.

    Best-effort : timeout court, toute erreur → None (le bloc benchmark est
    alors omis proprement de la réponse). Cache 300 s partagé entre users.
    """
    now = _time.time()
    entry = _benchmark_cache.get(days)
    if entry and (now - entry["ts"]) < _ANALYTICS_CACHE_TTL:
        return entry["data"]
    data = None
    try:
        start = int(now) - days * 86400 - 43200
        url = (f"https://coins.llama.fi/chart/coingecko:bitcoin,coingecko:ethereum"
               f"?start={start}&span={days + 1}&period=1d")
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url)
        if r.status_code == 200:
            coins = (r.json() or {}).get("coins") or {}
            btc = pct_from_price_points((coins.get("coingecko:bitcoin") or {}).get("prices") or [])
            eth = pct_from_price_points((coins.get("coingecko:ethereum") or {}).get("prices") or [])
            if btc is not None or eth is not None:
                data = {"btc_pct": btc, "eth_pct": eth}
    except Exception as e:
        logging.getLogger("crypto.analytics").info(f"[ANALYTICS] benchmark indisponible: {e}")
    _benchmark_cache[days] = {"data": data, "ts": now}
    return data


@app.get("/api/analytics")
async def analytics(address: str = Query(...), range: str = Query("7d"),
                    user=Depends(get_current_user)):
    """Analytics du portefeuille — répartition & performance (2026.07.3).

    • address: adresse 0x… ou "ALL" (agrège tous les wallets de l'utilisateur,
      même convention que /api/snapshots?wallet=ALL).
    • range: 24h | 7d | 30d (défaut 7d) — utilisé pour performers + benchmark;
      le bloc change renvoie TOUJOURS les 3 périodes.
    • allocation par chaîne / catégorie / actif (top 12 + OTHERS) sur les
      tokens ACTIFS uniquement (enabled != false).
    • performers: variation de PRIX (price_history → neutralise les
      apports/retraits) ; spam et poussière ignorés.
    • Défensif: données manquantes → null / listes vides, jamais de 500.
    """
    _log = logging.getLogger("crypto.analytics")
    rng = range if range in ANALYTICS_RANGES else "7d"
    rng_days = ANALYTICS_RANGES[rng]

    if address != "ALL" and not address.startswith("0x"):
        raise HTTPException(400, "Adresse invalide")

    cache_key = (user["id"], address.lower(), rng)
    now = _time.time()
    entry = _analytics_cache.get(cache_key)
    if entry and (now - entry["ts"]) < _ANALYTICS_CACHE_TTL:
        return {**entry["data"], "cached": True}

    empty = {
        "address": address, "range": rng, "total_usd": 0.0,
        "allocation": {"by_chain": [], "by_category": [], "by_asset": []},
        "change": {"24h": None, "7d": None, "30d": None},
        "performers": {"best": [], "worst": []},
        "cached": False,
    }

    try:
        # ── 1. Adresses concernées ────────────────────────────────
        if address == "ALL":
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT address FROM wallets WHERE user_id=?", (user["id"],))
                addrs = [r["address"] for r in await cur.fetchall()]
        else:
            addrs = [address]
        if not addrs:
            return empty

        # ── 2. Portfolios (réutilise _portfolio_cache + prefs user) ─
        results = await asyncio.gather(
            *[_get_portfolio_for_defi(user["id"], a) for a in addrs],
            return_exceptions=True)
        tokens = []
        current_total = 0.0
        pf_errors = 0
        for res in results:
            if isinstance(res, Exception) or not isinstance(res, dict):
                pf_errors += 1
                continue
            tokens.extend(filter_active_tokens(res.get("tokens")))
            current_total += float(res.get("total_usd") or 0)

        allocation = build_allocation(tokens)

        # ── 3. Variations 24h/7j/30j (agrégat daily_history) ──────
        conditions = ["user_id=?", "token_symbol IS NULL",
                      "lower(wallet_address) IN (SELECT lower(address) FROM wallets WHERE user_id=?)"]
        params = [user["id"], user["id"]]
        if address != "ALL":
            conditions.append("lower(wallet_address)=lower(?)")
            params.append(address)
        agg_rows = []
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    f"SELECT date, SUM(value_usd) AS v FROM daily_history "
                    f"WHERE {' AND '.join(conditions)} GROUP BY date ORDER BY date ASC",
                    tuple(params))
                agg_rows = [(r["date"], r["v"] or 0) for r in await cur.fetchall()]
        except Exception as e:
            _log.warning(f"[ANALYTICS] daily_history agrégat illisible: {e}")
        change = compute_change_periods(agg_rows, current_total)

        # ── 4. Performers (prix passés via cache price_history) ───
        tol = AN_TOLERANCE_DAYS[rng]
        target_date = (datetime.datetime.utcnow()
                       - datetime.timedelta(days=rng_days)).strftime("%Y-%m-%d")
        d_from = (datetime.datetime.utcnow()
                  - datetime.timedelta(days=rng_days + tol)).strftime("%Y-%m-%d")
        d_to = (datetime.datetime.utcnow()
                - datetime.timedelta(days=max(0, rng_days - tol))).strftime("%Y-%m-%d")
        past_prices = {}
        syms = sorted({(tk.get("symbol") or "").lower()
                       for tk in tokens if tk.get("symbol")})
        if syms:
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    db.row_factory = aiosqlite.Row
                    qmarks = ",".join("?" for _ in syms)
                    cur = await db.execute(
                        f"SELECT LOWER(token_symbol) AS s, date, price_usd FROM price_history "
                        f"WHERE LOWER(token_symbol) IN ({qmarks}) AND date BETWEEN ? AND ?",
                        tuple(syms) + (d_from, d_to))
                    series: dict = {}
                    for r in await cur.fetchall():
                        series.setdefault(r["s"], []).append((r["date"], r["price_usd"] or 0))
                for s, rows_s in series.items():
                    p = pick_closest(rows_s, target_date, tol)
                    if p is not None and p > 0:
                        past_prices[s] = p
            except Exception as e:
                _log.warning(f"[ANALYTICS] price_history illisible: {e}")
        performers = compute_performers(tokens, past_prices)

        data = {
            "address": address, "range": rng,
            "total_usd": round(current_total, 2),
            "allocation": {
                "by_chain": allocation["by_chain"],
                "by_category": allocation["by_category"],
                "by_asset": allocation["by_asset"],
            },
            "change": change,
            "performers": performers,
        }
        if pf_errors:
            data["partial"] = True

        # ── 5. Benchmark BTC/ETH (best-effort, omis si indisponible) ─
        bench = await _fetch_benchmark_pcts(rng_days)
        if bench is not None:
            chg_rng = change.get(rng)
            data["benchmark"] = {
                "portfolio_pct": chg_rng.get("pct") if isinstance(chg_rng, dict) else None,
                "btc_pct": bench.get("btc_pct"),
                "eth_pct": bench.get("eth_pct"),
            }

        _analytics_cache[cache_key] = {"data": data, "ts": now}
        _log.info(
            f"[ANALYTICS] user={user['id']} addr={address[:12]} range={rng} "
            f"total={data['total_usd']} chains={len(allocation['by_chain'])} "
            f"assets={len(allocation['by_asset'])} best={len(performers['best'])} "
            f"worst={len(performers['worst'])}")
        return data
    except Exception as e:
        # Jamais de 500 : structure vide + log de l'erreur inattendue
        import traceback
        _log.error(f"[ANALYTICS] échec inattendu: {e}\n{traceback.format_exc()}")
        return empty


# ── Export de données — CSV + PDF (2026.07.4) ───────────────────

EXPORT_TX_TYPE_LABELS = {"swap": "Swap", "send": "Envoyé", "receive": "Reçu"}


def _export_check_address(address: str):
    if address != "ALL" and not address.startswith("0x"):
        raise HTTPException(400, "Adresse invalide")


async def _export_addresses(user_id: int, address: str) -> list:
    """ALL → tous les wallets de l'utilisateur ; sinon [address]."""
    if address == "ALL":
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT address FROM wallets WHERE user_id=?", (user_id,))
            return [r["address"] for r in await cur.fetchall()]
    return [address]


async def _export_portfolio_data(user, address: str) -> dict:
    """Tokens ACTIFS enrichis (cost_basis/pnl) + agrégats, pour les exports.

    Réutilise le endpoint /api/portfolio (cache 1h, enrichissement PNL
    per-token, prefs utilisateur) : mêmes chiffres que le dashboard. Un wallet
    en échec est ignoré (export partiel plutôt que 500)."""
    addrs = await _export_addresses(user["id"], address)
    results = await asyncio.gather(
        *[portfolio(address=a, force=False, user=user) for a in addrs],
        return_exceptions=True)
    tokens = []
    total = 0.0
    pnl_sum, pnl_seen = 0.0, False
    cost_sum, cost_seen = 0.0, False
    wallets_ok = 0
    for res in results:
        if isinstance(res, Exception) or not isinstance(res, dict):
            logging.getLogger("crypto.export").warning(f"[EXPORT] wallet ignoré: {res}")
            continue
        wallets_ok += 1
        tokens.extend(filter_active_tokens(res.get("tokens")))
        total += float(res.get("total_usd") or 0)
        tp = res.get("total_pnl")
        if isinstance(tp, (int, float)) and math.isfinite(tp):
            pnl_sum += tp
            pnl_seen = True
        tc = res.get("total_cost_basis")
        if isinstance(tc, (int, float)) and math.isfinite(tc):
            cost_sum += tc
            cost_seen = True
    return {
        "tokens": tokens,
        "total_usd": round(total, 2),
        "total_pnl": round(pnl_sum, 2) if pnl_seen else None,
        "total_cost": round(cost_sum, 2) if cost_seen else None,
        "wallets_ok": wallets_ok,
        "wallets_requested": len(addrs),
    }


def _csv_download(text: str, base: str) -> Response:
    fname = f"{base}_{datetime.datetime.utcnow().strftime('%Y-%m-%d')}.csv"
    return Response(
        content=text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"',
                 "Cache-Control": "no-store"},
    )


@app.get("/api/export/holdings.csv")
async def export_holdings_csv(address: str = Query("ALL"), user=Depends(get_current_user)):
    """Export CSV des holdings (une ligne par token actif, agrégé par
    symbole+chaîne). Données manquantes → cellules vides, jamais de 500."""
    _export_check_address(address)
    rows = []
    try:
        pf = await _export_portfolio_data(user, address)
        rows = build_holdings_rows(aggregate_holdings(pf["tokens"]))
    except Exception as e:
        logging.getLogger("crypto.export").error(f"[EXPORT] holdings.csv: {e}")
    return _csv_download(rows_to_csv(HOLDINGS_HEADERS, rows), "holdings")


@app.get("/api/export/transactions.csv")
async def export_transactions_csv(address: str = Query("ALL"),
                                  user=Depends(get_current_user), db=Depends(get_db)):
    """Export CSV des transactions (événements swap/send/receive v2.12.4,
    regroupés par tx). Wallets existants uniquement (défense v2.12.5)."""
    _export_check_address(address)
    rows = []
    try:
        conditions = ["user_id=?",
                      "lower(wallet_address) IN (SELECT lower(address) FROM wallets WHERE user_id=?)"]
        params = [user["id"], user["id"]]
        if address != "ALL":
            conditions.append("lower(wallet_address)=lower(?)")
            params.append(address)
        cur = await db.execute(
            f"SELECT id, wallet_address, token_symbol, token_name, amount, usd_price, usd_value, "
            f"chain, tx_hash, block_time, direction, log_index, gas_fee_usd, contract_address "
            f"FROM transactions WHERE {' AND '.join(conditions)} ORDER BY block_time DESC",
            tuple(params))
        db_rows = await cur.fetchall()
        events = group_transaction_events(db_rows)
        rows = build_transaction_rows(events, EXPORT_TX_TYPE_LABELS)
    except Exception as e:
        logging.getLogger("crypto.export").error(f"[EXPORT] transactions.csv: {e}")
    return _csv_download(rows_to_csv(TRANSACTIONS_HEADERS, rows), "transactions")


@app.get("/api/export/pnl.csv")
async def export_pnl_csv(address: str = Query("ALL"), user=Depends(get_current_user)):
    """Rapport PnL/fiscal best-effort : coût moyen unitaire, coût total,
    valeur actuelle et PnL latent par token (même logique que le PNL par
    token du dashboard). Coût inconnu → cellules vides (pitfall v2.11.10)."""
    _export_check_address(address)
    rows = []
    try:
        pf = await _export_portfolio_data(user, address)
        rows = build_pnl_rows(aggregate_holdings(pf["tokens"]))
    except Exception as e:
        logging.getLogger("crypto.export").error(f"[EXPORT] pnl.csv: {e}")
    return _csv_download(rows_to_csv(PNL_HEADERS, rows), "pnl_report")


@app.get("/api/export/summary.pdf")
async def export_summary_pdf(address: str = Query("ALL"), user=Depends(get_current_user)):
    """PDF de synthèse : valeur totale, PnL, répartition par chaîne et par
    catégorie (même logique que /api/analytics), top holdings, date de
    génération. Générateur PDF interne sans dépendance — jamais de 500."""
    _export_check_address(address)
    summary: dict = {
        "generated_at": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "scope_label": address,
    }
    try:
        pf = await _export_portfolio_data(user, address)
        agg = aggregate_holdings(pf["tokens"])
        alloc = build_allocation(pf["tokens"])   # réutilise la logique /api/analytics
        total = pf["total_usd"]
        pnl_total = pf["total_pnl"]
        cost_total = pf["total_cost"]
        if pnl_total is None:
            known = [a for a in agg if a.get("pnl") is not None]
            if known:
                pnl_total = round(sum(a["pnl"] for a in known), 2)
                cost_total = round(sum(a["cost_basis"] for a in known), 2)
        top = []
        for a in agg[:15]:
            top.append({
                "symbol": a.get("symbol"), "chain": a.get("chain"),
                "balance": a.get("balance"), "usd_value": a.get("usd_value"),
                "pct": (a.get("usd_value") or 0) / total * 100 if total > 0 else 0,
                "pnl": a.get("pnl"),
            })
        if address == "ALL":
            scope = f"Tous les wallets ({pf['wallets_ok']}/{pf['wallets_requested']})" \
                if pf["wallets_ok"] != pf["wallets_requested"] \
                else f"Tous les wallets ({pf['wallets_requested']})"
        else:
            scope = address
        summary.update({
            "scope_label": scope,
            "wallet_count": pf["wallets_requested"],
            "total_usd": total,
            "pnl_usd": pnl_total,
            "cost_usd": cost_total,
            "token_count": len(agg),
            "chain_count": len(alloc.get("by_chain") or []),
            "by_chain": alloc.get("by_chain"),
            "by_category": alloc.get("by_category"),
            "top_holdings": top,
        })
    except Exception as e:
        logging.getLogger("crypto.export").error(f"[EXPORT] summary.pdf: {e}")
    try:
        pdf_bytes = build_summary_pdf(summary)
    except Exception as e:
        logging.getLogger("crypto.export").error(f"[EXPORT] génération PDF: {e}")
        pdf_bytes = build_summary_pdf({"generated_at": summary.get("generated_at"),
                                       "scope_label": address})
    fname = f"portfolio_summary_{datetime.datetime.utcnow().strftime('%Y-%m-%d')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"',
                 "Cache-Control": "no-store"},
    )


# ── External API Keys catalogue ─────────────────────────────

API_KEY_CATALOGUE = [
    {"id": "coingecko",    "name": "CoinGecko",     "category": "Pricing",      "description": "Prix des tokens (multi-chaînes)",              "get_key_url": "https://www.coingecko.com/en/developers/dashboard"},
    {"id": "opensea",      "name": "OpenSea",        "category": "NFT",          "description": "Prix planchers & métadonnées NFT",             "get_key_url": "https://docs.opensea.io/reference/api-keys"},
    {"id": "etherscan",    "name": "Etherscan",      "category": "Explorer",     "description": "Données on-chain / transactions",              "get_key_url": "https://etherscan.io/myapikey"},
    {"id": "defillama",    "name": "DefiLlama",      "category": "Pricing/DeFi", "description": "Prix & données DeFi (Pro)",                    "get_key_url": "https://defillama.com/pro-api"},
    {"id": "alchemy",      "name": "Alchemy",        "category": "RPC/Data",     "description": "Accès RPC / données multi-chaînes",           "get_key_url": "https://dashboard.alchemy.com/"},
    {"id": "moralis",      "name": "Moralis",        "category": "Data/NFT",     "description": "Données tokens & NFT",                        "get_key_url": "https://admin.moralis.io/"},
    {"id": "coinmarketcap","name": "CoinMarketCap",  "category": "Pricing",      "description": "Prix des tokens (alternative)",               "get_key_url": "https://pro.coinmarketcap.com/account"},
]

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
    """Return the full catalogue of providers with per-user configuration status."""
    # Fetch all stored keys for this user in one query
    cur = await db.execute(
        "SELECT provider, api_key FROM user_api_keys WHERE user_id=?",
        (user["id"],))
    rows = await cur.fetchall()
    stored = {row["provider"]: row["api_key"] for row in rows}
    
    result = []
    for prov in API_KEY_CATALOGUE:
        entry = dict(prov)
        stored_key = stored.get(prov["id"])
        if stored_key:
            masked = "..." + stored_key[-4:] if len(stored_key) > 4 else "***"
            entry["configured"] = True
            entry["masked"] = masked
        else:
            entry["configured"] = False
            entry["masked"] = None
        result.append(entry)
    return result


@app.put("/api/settings/keys/{provider}")
async def set_api_key(provider: str, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    data = await request.json()
    api_key = (data.get("api_key") or "").strip()
    if not api_key:
        raise HTTPException(400, "Clé API requise")
    
    # Validate (best-effort — only reject if validator actually fails)
    valid, msg = await _validate_api_key(provider, api_key)
    if not valid:
        raise HTTPException(400, msg)
    
    await db.execute(
        "INSERT OR REPLACE INTO user_api_keys (user_id, provider, api_key) VALUES (?, ?, ?)",
        (user["id"], provider, api_key))
    await db.commit()
    if provider == "moralis":
        _invalidate_defi_cache(user["id"])
    return {"ok": True, "provider": provider, "configured": True, "msg": "Clé enregistrée"}


@app.delete("/api/settings/keys/{provider}")
async def delete_api_key(provider: str, user=Depends(get_current_user), db=Depends(get_db)):
    await db.execute("DELETE FROM user_api_keys WHERE user_id=? AND provider=?", (user["id"], provider))
    await db.commit()
    if provider == "moralis":
        _invalidate_defi_cache(user["id"])
    return {"ok": True, "provider": provider, "configured": False}


async def _validate_api_key(provider: str, api_key: str) -> tuple:
    """Validate API key against provider. Returns (is_valid, message).
    
    Best-effort validation: only providers with a real validator can fail;
    unknown providers pass through without blocking.
    """
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
    # Unknown providers / no validator: store without blocking
    return True, "Clé enregistrée (validation best-effort)"


# ══════════════════════════════════════════════════════════════════
# Alerts + Notifications + Digest (2026.07.6)
# ══════════════════════════════════════════════════════════════════

def _mask_config(channel: str, config: dict) -> dict:
    """Mask secrets in channel config before returning to client."""
    masked = dict(config)
    if channel == "telegram":
        if "bot_token" in masked and masked["bot_token"]:
            masked["bot_token"] = "..." + masked["bot_token"][-4:] if len(masked["bot_token"]) > 4 else "..."
    elif channel == "email":
        if "password" in masked and masked["password"]:
            masked["password"] = "..." + masked["password"][-2:] if len(masked["password"]) > 2 else "..."
    if channel == "webhook":
        if "url" in masked:
            masked["url"] = masked["url"][:60] + "..." if len(masked.get("url", "")) > 63 else masked.get("url", "")
    return masked


# ── Alerts CRUD ──────────────────────────────────────────────────

@app.get("/api/alerts")
async def list_alerts(user=Depends(get_current_user), db=Depends(get_db)):
    cur = await db.execute(
        "SELECT id, type, params_json, enabled, cooldown_min, last_triggered_at, created_at "
        "FROM alerts WHERE user_id=? ORDER BY created_at DESC",
        (user["id"],))
    rows = await cur.fetchall()
    result = []
    for r in rows:
        try:
            params = json.loads(r["params_json"] or "{}")
        except Exception:
            params = {}
        result.append({
            "id": r["id"], "type": r["type"], "params": params,
            "enabled": bool(r["enabled"]), "cooldown_min": r["cooldown_min"],
            "last_triggered_at": r["last_triggered_at"], "created_at": r["created_at"],
        })
    return result


@app.post("/api/alerts")
async def create_alert(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    data = await request.json()
    alert_type = (data.get("type") or "").strip().lower()
    if alert_type not in ("price", "portfolio", "move"):
        raise HTTPException(400, "Type invalide (price, portfolio, move)")
    params = data.get("params") or data.get("params_json") or {}
    if isinstance(params, str):
        try: params = json.loads(params)
        except Exception: raise HTTPException(400, "params_json invalide")
    enabled = 1 if data.get("enabled", True) else 0
    cooldown = int(data.get("cooldown_min") or 60)
    await db.execute(
        "INSERT INTO alerts (user_id, type, params_json, enabled, cooldown_min) VALUES (?, ?, ?, ?, ?)",
        (user["id"], alert_type, json.dumps(params), enabled, max(1, cooldown)))
    await db.commit()
    return {"ok": True, "id": db.last_insert_rowid if hasattr(db, 'last_insert_rowid') else None}


@app.put("/api/alerts/{alert_id}")
async def update_alert(alert_id: int, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    cur = await db.execute("SELECT id FROM alerts WHERE id=? AND user_id=?", (alert_id, user["id"]))
    if not await cur.fetchone():
        raise HTTPException(404, "Alerte introuvable")
    data = await request.json()
    updates = []
    params_list = []
    if "enabled" in data:
        updates.append("enabled=?")
        params_list.append(1 if data["enabled"] else 0)
    if "cooldown_min" in data:
        updates.append("cooldown_min=?")
        params_list.append(max(1, int(data["cooldown_min"])))
    if "params" in data or "params_json" in data:
        params = data.get("params") or data.get("params_json") or {}
        updates.append("params_json=?")
        params_list.append(json.dumps(params))
    if not updates:
        raise HTTPException(400, "Aucun champ à modifier")
    params_list.extend([alert_id, user["id"]])
    await db.execute(
        f"UPDATE alerts SET {', '.join(updates)} WHERE id=? AND user_id=?",
        tuple(params_list))
    await db.commit()
    return {"ok": True}


@app.delete("/api/alerts/{alert_id}")
async def delete_alert(alert_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    await db.execute("DELETE FROM alerts WHERE id=? AND user_id=?", (alert_id, user["id"]))
    await db.commit()
    return {"ok": True}


# ── Notifications ────────────────────────────────────────────────

@app.get("/api/notifications")
async def list_notifications(limit: int = Query(50), unread_only: bool = Query(False),
                             user=Depends(get_current_user), db=Depends(get_db)):
    where = "user_id=?"
    params: list = [user["id"]]
    if unread_only:
        where += " AND read=0"
    cur = await db.execute(
        f"SELECT id, alert_id, title, body, created_at, read FROM notifications "
        f"WHERE {where} ORDER BY created_at DESC LIMIT ?",
        tuple(params + [limit]))
    rows = await cur.fetchall()
    return [{
        "id": r["id"], "alert_id": r["alert_id"], "title": r["title"],
        "body": r["body"], "created_at": r["created_at"], "read": bool(r["read"]),
    } for r in rows]


@app.post("/api/notifications/read")
async def mark_notifications_read(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    data = await request.json()
    ids = data.get("ids") or data.get("all")
    if ids == "all":
        await db.execute("UPDATE notifications SET read=1 WHERE user_id=?", (user["id"],))
    elif isinstance(ids, list) and ids:
        placeholders = ",".join(["?"] * len(ids))
        await db.execute(
            f"UPDATE notifications SET read=1 WHERE user_id=? AND id IN ({placeholders})",
            [user["id"]] + ids)
    await db.commit()
    return {"ok": True}


# ── Notification channels config ─────────────────────────────────

@app.get("/api/settings/notif-channels")
async def get_notif_channels(user=Depends(get_current_user), db=Depends(get_db)):
    cur = await db.execute(
        "SELECT channel, config_json, enabled FROM notif_channels WHERE user_id=?",
        (user["id"],))
    rows = await cur.fetchall()
    channels = {}
    for r in rows:
        try:
            config = json.loads(r["config_json"] or "{}")
        except Exception:
            config = {}
        channels[r["channel"]] = {
            "config": _mask_config(r["channel"], config),
            "enabled": bool(r["enabled"]),
        }
    return channels


@app.put("/api/settings/notif-channels")
async def put_notif_channels(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    data = await request.json()
    channel = (data.get("channel") or "").strip().lower()
    if channel not in ("webhook", "telegram", "email"):
        raise HTTPException(400, "Canal invalide (webhook, telegram, email)")
    config = data.get("config") or {}
    enabled = 1 if data.get("enabled", False) else 0
    await db.execute(
        "INSERT OR REPLACE INTO notif_channels (user_id, channel, config_json, enabled) VALUES (?, ?, ?, ?)",
        (user["id"], channel, json.dumps(config), enabled))
    await db.commit()
    return {"ok": True}


# ── Digest prefs ─────────────────────────────────────────────────

@app.get("/api/settings/digest")
async def get_digest_prefs(user=Depends(get_current_user), db=Depends(get_db)):
    cur = await db.execute(
        "SELECT frequency, channel FROM digest_prefs WHERE user_id=?", (user["id"],))
    row = await cur.fetchone()
    if row:
        return {"frequency": row["frequency"], "channel": row["channel"]}
    return {"frequency": "off", "channel": ""}


@app.put("/api/settings/digest")
async def put_digest_prefs(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    data = await request.json()
    frequency = (data.get("frequency") or "off").strip().lower()
    if frequency not in ("off", "daily", "weekly"):
        raise HTTPException(400, "Fréquence invalide (off, daily, weekly)")
    channel = (data.get("channel") or "").strip().lower()
    await db.execute(
        "INSERT OR REPLACE INTO digest_prefs (user_id, frequency, channel) VALUES (?, ?, ?)",
        (user["id"], frequency, channel))
    await db.commit()
    return {"ok": True}


# ── Test channel ─────────────────────────────────────────────────

@app.post("/api/alerts/test-channel")
async def test_channel(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    data = await request.json()
    channel = (data.get("channel") or "").strip().lower()
    if channel not in ("webhook", "telegram", "email"):
        raise HTTPException(400, "Canal invalide (webhook, telegram, email)")

    cur = await db.execute(
        "SELECT channel, config_json FROM notif_channels WHERE user_id=? AND channel=? AND enabled=1",
        (user["id"], channel))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(400, "Canal non configuré ou désactivé")

    try:
        config = json.loads(row["config_json"] or "{}")
    except Exception:
        config = {}

    title = "🧪 Test de notification — Crypto Wallet Tracker"
    body = "Ceci est un message de test. Si tu le reçois, ton canal est correctement configuré !"

    from services.alerts_service import _send_webhook, _send_telegram, _send_email
    ok = False
    if channel == "webhook":
        url = config.get("url", "")
        if not url:
            raise HTTPException(400, "URL webhook manquante")
        ok = await _send_webhook(url, title, body)
    elif channel == "telegram":
        bot_token = config.get("bot_token", "")
        chat_id = config.get("chat_id", "")
        if not bot_token or not chat_id:
            raise HTTPException(400, "bot_token ou chat_id manquant")
        ok = await _send_telegram(bot_token, chat_id, title, body)
    elif channel == "email":
        ok = await _send_email(
            config.get("smtp_host", ""),
            int(config.get("smtp_port") or 587),
            config.get("user", ""),
            config.get("password", ""),
            config.get("from", ""),
            config.get("to", ""),
            title, body
        )

    if not ok:
        raise HTTPException(500, "Échec de l'envoi — vérifie ta configuration")
    return {"ok": True, "msg": f"Message de test envoyé via {channel}"}


# ── Unread count (for badge) ─────────────────────────────────────

@app.get("/api/notifications/count")
async def unread_count(user=Depends(get_current_user), db=Depends(get_db)):
    cur = await db.execute(
        "SELECT COUNT(*) as c FROM notifications WHERE user_id=? AND read=0",
        (user["id"],))
    row = await cur.fetchone()
    return {"count": row["c"] if row else 0}


@app.get("/")
async def index(request: Request):
    import hashlib, os
    path = "public/index.html"
    with open(path, "rb") as f:
        body = f.read()
    etag = hashlib.md5(body).hexdigest()
    # If-None-Match : retour 304 si le navigateur a la version courante
    raw = (request.headers.get("if-none-match") or "").strip()
    header_tag = raw.strip('"').strip()
    if header_tag == etag:
        return Response(status_code=304, headers={"Cache-Control": "no-cache, must-revalidate", "ETag": f'"{etag}"'})
    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, must-revalidate",
            "ETag": f'"{etag}"',
            "Accept-Ranges": "bytes",
        }
    )


app.mount("/static", StaticFiles(directory="public"), name="static")
