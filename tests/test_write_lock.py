"""
Concurrency test for the global SQLite write lock.

Spawns N coroutines that each perform INSERT+COMMIT concurrently.
Before the write lock, this would raise "database is locked" under
contention. After the fix, all writes succeed with 0 lock errors.

Run: python3 tests/test_write_lock.py
"""
import asyncio
import aiosqlite
import os
import sys
import tempfile

# Ensure the services package is importable
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from services.db import write_locked


DB_PATH: str = ""


async def setup_db():
    """Create a test DB with a users table."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
        """)
        await db.commit()


async def writer(worker_id: int, n_writes: int) -> tuple[int, int]:
    """Perform N writes under the global write lock. Returns (ok_count, error_count)."""
    ok = 0
    errors = 0
    for i in range(n_writes):
        try:
            async with write_locked():
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                        (f"user_{worker_id}_{i}", f"hash_{worker_id}_{i}"),
                    )
                    await db.commit()
            ok += 1
        except Exception as e:
            err_str = str(e)
            if "database is locked" in err_str:
                print(f"FAIL: worker {worker_id} hit 'database is locked' on write {i}")
            errors += 1
    return (ok, errors)


async def main():
    N_WORKERS = 20
    N_WRITES_PER_WORKER = 25  # 500 total writes

    print(f"Spawning {N_WORKERS} workers, {N_WRITES_PER_WORKER} writes each "
          f"(total {N_WORKERS * N_WRITES_PER_WORKER}) ...")

    tasks = [writer(i, N_WRITES_PER_WORKER) for i in range(N_WORKERS)]
    results = await asyncio.gather(*tasks)

    total_ok = sum(r[0] for r in results)
    total_errors = sum(r[1] for r in results)

    print(f"Results: {total_ok} OK, {total_errors} errors "
          f"(DB locked errors: {sum(1 for r in results if r[1] > 0)} workers)")

    # Verify all rows are in the DB
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        row = await cur.fetchone()
        count = row[0] if row else 0
    print(f"DB row count: {count} (expected {total_ok})")

    if total_errors == 0 and count == total_ok:
        print("\nPASS: No 'database is locked' errors, all writes committed successfully.")
        return 0
    else:
        print(f"\nFAIL: {total_errors} write errors, row count mismatch ({count} vs {total_ok})")
        return 1


if __name__ == "__main__":
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        DB_PATH = f.name
        os.environ["DB_PATH"] = DB_PATH
    try:
        asyncio.run(setup_db())
        exit_code = asyncio.run(main())
    finally:
        try:
            os.unlink(DB_PATH)
        except Exception:
            pass
    sys.exit(exit_code)
