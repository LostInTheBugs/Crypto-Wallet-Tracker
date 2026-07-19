"""
SQLite database module — global write lock to eliminate "database is locked".

WAL mode allows concurrent readers during a write, but SQLite permits
only ONE writer at a time.  Opening many aiosqlite connections and writing
in parallel (background workers + user requests) causes spurious
"database is locked" errors when a second writer tries to begin a
transaction before the first one has committed.

This module provides a global asyncio.Lock that serializes ALL in-process
writes (INSERT/UPDATE/DELETE/CREATE/ALTER/REPLACE + commit).  Reads do
NOT take the lock (WAL enables concurrent reads during a write).

Subprocess workers (rebuild_worker.py, enrich_worker.py) use sqlite3
(synchronous) and are covered by busy_timeout (defence in depth).
"""

import asyncio
import os

DB_PATH = os.environ.get("DB_PATH", "/data/wallets.db")

_db_write_lock = asyncio.Lock()


def write_locked() -> asyncio.Lock:
    """Return the global write lock so callers can use
       `async with write_locked(): ...`.

       Usage:
           from services.db import write_locked
           async with write_locked():
               await db.execute("INSERT ...")
               await db.commit()
    """
    return _db_write_lock
