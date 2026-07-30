"""
Tests for tax_service.py — weighted-average cost basis and PnL.

Run: cd /opt/hermes-work/repo && .venv/bin/python tests/test_tax_service.py
"""
import asyncio
import os
import sys
import tempfile

# Add source directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import aiosqlite


# ── Test helpers ────────────────────────────────────────────────

def _fresh_db():
    """Create a fresh temp DB and set DB_PATH env var."""
    db_path = os.path.join(tempfile.gettempdir(), f"test_tax_{os.getpid()}.db")
    # Remove if exists
    try:
        os.unlink(db_path)
    except FileNotFoundError:
        pass
    os.environ["DB_PATH"] = db_path
    return db_path


async def _setup_db(db_path: str, user_id=1, wallet_addr="0xTest1"):
    """Create minimal schema with one user and one wallet."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY)")
        await db.execute(f"INSERT OR IGNORE INTO users(id) VALUES({user_id})")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS wallets(
                id INTEGER PRIMARY KEY, user_id INTEGER, address TEXT, label TEXT, chain_type TEXT DEFAULT 'evm')
        """)
        await db.execute(
            "INSERT OR IGNORE INTO wallets(id,user_id,address,label) VALUES(1,?,?,?)",
            (user_id, wallet_addr, "Test Wallet"))
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions(
                id INTEGER PRIMARY KEY, user_id INTEGER, wallet_address TEXT,
                token_symbol TEXT, token_name TEXT, amount REAL, usd_price REAL,
                usd_value REAL, chain TEXT, tx_hash TEXT, block_time TEXT,
                direction TEXT DEFAULT 'in', log_index INTEGER DEFAULT 0,
                gas_fee_usd REAL DEFAULT 0, contract_address TEXT DEFAULT '',
                event_type TEXT DEFAULT 'transfer', event_method TEXT DEFAULT '',
                event_to TEXT DEFAULT '')
        """)
        await db.commit()


async def _insert_tx(db_path: str, user_id, wallet, sym, chain, amount, price,
                     direction, block_time):
    value = round(amount * price, 2)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO transactions(user_id,wallet_address,token_symbol,amount,"
            "usd_price,usd_value,chain,tx_hash,block_time,direction) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (user_id, wallet, sym, amount, price, value, chain,
             f"0x{sym}_{block_time}_{direction}", block_time, direction))
        await db.commit()


async def _portfolio_fn_factory(assets):
    """Return an async function that mimics _compute_portfolio."""
    async def _fn(address):
        tokens = []
        total = 0.0
        for a in assets:
            tokens.append({
                "symbol": a["symbol"],
                "chain": a.get("chain", "ethereum"),
                "balance": a.get("balance", 0),
                "usd_price": a.get("price", 0),
                "usd_value": a.get("value", 0),
            })
            total += a.get("value", 0)
        return {"tokens": tokens, "total_usd": total, "token_count": len(tokens)}
    return _fn


# ── Test cases ──────────────────────────────────────────────────

def test_weighted_average_cost():
    """Simulate: buy 2 ETH @ $3000, buy 1 ETH @ $3500, sell 1.5 ETH @ $4000."""
    db_path = _fresh_db()

    async def _run():
        from services.tax_service import compute_tax
        await _setup_db(db_path)
        await _insert_tx(db_path, 1, "0xTest1", "ETH", "ethereum", 2.0, 3000.0, "in", "2025-01-01 10:00:00")
        await _insert_tx(db_path, 1, "0xTest1", "ETH", "ethereum", 1.0, 3500.0, "in", "2025-02-01 10:00:00")
        await _insert_tx(db_path, 1, "0xTest1", "ETH", "ethereum", 1.5, 4000.0, "out", "2025-03-01 10:00:00")

        pf = await _portfolio_fn_factory([
            {"symbol": "ETH", "chain": "ethereum", "balance": 1.5, "price": 4000.0, "value": 6000.0}
        ])

        result = await compute_tax(1, wallet_address="0xTest1", portfolio_fn=pf)
        assets = result["assets"]
        totals = result["totals"]

        assert len(assets) == 1, f"Expected 1 asset, got {len(assets)}"
        eth = assets[0]
        assert eth["symbol"] == "ETH"
        assert eth["status"] == "ok"
        assert abs(eth["cost_basis"] - 4750.0) < 1.0, f"cost_basis={eth['cost_basis']}"
        assert abs(eth["current_value"] - 6000.0) < 1.0
        assert abs(eth["unrealized_pnl"] - 1250.0) < 2.0
        assert abs(eth["realized_pnl"] - 1250.0) < 2.0
        assert abs(totals["total_cost_basis"] - 4750.0) < 1.0
        assert abs(totals["total_current_value"] - 6000.0) < 1.0

    asyncio.run(_run())
    print("  PASS test_weighted_average_cost")


def test_incomplete_asset_no_transactions():
    """Asset with current value but no transactions → incomplete."""
    db_path = _fresh_db()

    async def _run():
        from services.tax_service import compute_tax
        await _setup_db(db_path)
        pf = await _portfolio_fn_factory([
            {"symbol": "SOL", "chain": "solana", "balance": 10.0, "price": 150.0, "value": 1500.0}
        ])
        result = await compute_tax(1, wallet_address="0xTest1", portfolio_fn=pf)
        assets = result["assets"]
        assert len(assets) >= 1, f"Expected >=1 assets, got {len(assets)}"
        sol = [a for a in assets if a["symbol"] == "SOL"]
        assert len(sol) == 1
        assert sol[0]["status"] == "incomplete", f"Expected incomplete, got {sol[0]['status']}"

    asyncio.run(_run())
    print("  PASS test_incomplete_asset_no_transactions")


def test_user_isolation():
    """User 2 should not see User 1's transactions."""
    db_path = _fresh_db()

    async def _run():
        from services.tax_service import compute_tax
        await _setup_db(db_path, user_id=1, wallet_addr="0xTest1")
        # Add user 2
        async with aiosqlite.connect(db_path) as db:
            await db.execute("INSERT OR IGNORE INTO users(id) VALUES(2)")
            await db.execute("INSERT OR IGNORE INTO wallets(id,user_id,address,label) VALUES(2,2,'0xUser2','User2 Wallet')")
            await db.commit()
        await _insert_tx(db_path, 1, "0xTest1", "ETH", "ethereum", 5.0, 2000.0, "in", "2024-01-01 10:00:00")
        pf = await _portfolio_fn_factory([
            {"symbol": "ETH", "chain": "ethereum", "balance": 5.0, "price": 2000.0, "value": 10000.0}
        ])
        result = await compute_tax(2, wallet_address="0xUser2", portfolio_fn=pf)
        assets = result["assets"]
        eth = [a for a in assets if a["symbol"] == "ETH"]
        # User2 should have no ETH transactions → incomplete
        assert len(eth) == 0 or eth[0]["status"] == "incomplete"

    asyncio.run(_run())
    print("  PASS test_user_isolation")


def test_multiple_assets():
    """ETH + USDC: one with cost, one stablecoin."""
    db_path = _fresh_db()

    async def _run():
        from services.tax_service import compute_tax
        await _setup_db(db_path)
        await _insert_tx(db_path, 1, "0xTest1", "ETH", "ethereum", 1.0, 2500.0, "in", "2024-06-01 10:00:00")
        await _insert_tx(db_path, 1, "0xTest1", "USDC", "ethereum", 5000.0, 1.0, "in", "2024-06-02 10:00:00")
        pf = await _portfolio_fn_factory([
            {"symbol": "ETH", "chain": "ethereum", "balance": 1.0, "price": 3000.0, "value": 3000.0},
            {"symbol": "USDC", "chain": "ethereum", "balance": 5000.0, "price": 1.0, "value": 5000.0},
        ])
        result = await compute_tax(1, wallet_address="0xTest1", portfolio_fn=pf)
        assets = result["assets"]
        totals = result["totals"]
        assert len(assets) >= 2
        eth = [a for a in assets if a["symbol"] == "ETH"][0]
        usdc = [a for a in assets if a["symbol"] == "USDC"][0]
        assert eth["cost_basis"] == 2500.0
        assert eth["unrealized_pnl"] == 500.0
        assert usdc["cost_basis"] == 5000.0
        assert abs(totals["total_cost_basis"] - 7500.0) < 1.0

    asyncio.run(_run())
    print("  PASS test_multiple_assets")


def test_all_wallets():
    """Address=ALL should aggregate across wallets."""
    db_path = _fresh_db()

    async def _run():
        from services.tax_service import compute_tax
        await _setup_db(db_path)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("INSERT OR IGNORE INTO wallets(id,user_id,address,label) VALUES(3,1,'0xWallet2','Second')")
            await db.commit()
        await _insert_tx(db_path, 1, "0xTest1", "ETH", "ethereum", 2.0, 2000.0, "in", "2024-01-01 10:00:00")
        await _insert_tx(db_path, 1, "0xWallet2", "ETH", "ethereum", 1.0, 2500.0, "in", "2024-02-01 10:00:00")
        pf = await _portfolio_fn_factory([
            {"symbol": "ETH", "chain": "ethereum", "balance": 3.0, "price": 3000.0, "value": 9000.0}
        ])
        result = await compute_tax(1, wallet_address="ALL", portfolio_fn=pf)
        assets = result["assets"]
        assert len(assets) >= 1
        eth = [a for a in assets if a["symbol"] == "ETH"][0]
        assert 6400.0 <= eth["cost_basis"] <= 6600.0, f"cost_basis={eth['cost_basis']}"
        print("  PASS test_all_wallets")

    asyncio.run(_run())


if __name__ == "__main__":
    print("=== Tax Service Tests ===\n")
    test_weighted_average_cost()
    test_incomplete_asset_no_transactions()
    test_user_isolation()
    test_multiple_assets()
    test_all_wallets()
    print("\n✅ All tax service tests passed.")
