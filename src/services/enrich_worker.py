"""Standalone historical price enrichment worker.

Executed as a SUBPROCESS (python services/enrich_worker.py <user_id>) so the
DefiLlama HTTP calls run in a clean process. The exact same logic run inside
the long-lived uvicorn event loop fails intermittently (connections error out),
whereas a fresh process is 100% reliable. Prints the number of priced rows.
"""
import sys, os, asyncio, sqlite3, calendar, datetime
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.portfolio_service import CHAIN_TO_LLAMA

DB_PATH = os.environ.get("DB_PATH", "/data/wallets.db")
CONCURRENCY = 6
TIMEOUT = 12
RETRIES = 3


async def _run(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, chain, contract_address, block_time, amount FROM transactions "
        "WHERE user_id=? AND (usd_price IS NULL OR usd_price=0) "
        "AND contract_address!='' AND price_checked=0 LIMIT 2000",
        (user_id,)).fetchall()
    if not rows:
        conn.close()
        return 0
    sem = asyncio.Semaphore(CONCURRENCY)
    price_updates = []
    checked_only = []

    async def one(row):
        slug = CHAIN_TO_LLAMA.get(row["chain"])
        if not slug:
            checked_only.append(row["id"]); return
        try:
            dt = datetime.datetime.strptime(row["block_time"][:19], "%Y-%m-%d %H:%M:%S")
            ts = int(calendar.timegm(dt.timetuple()))
        except Exception:
            checked_only.append(row["id"]); return
        addr = (row["contract_address"] or "").lower()
        key = f"{slug}:{addr}"
        url = f"https://coins.llama.fi/prices/historical/{ts}/{key}"
        async with sem:
            for a in range(RETRIES):
                try:
                    async with httpx.AsyncClient(timeout=TIMEOUT) as cl:
                        r = await cl.get(url)
                    if r.status_code == 200:
                        p = r.json().get("coins", {}).get(key, {}).get("price", 0)
                        if p and p > 0:
                            price_updates.append((row["id"], p, row["amount"]))
                        else:
                            checked_only.append(row["id"])
                        return
                except Exception:
                    pass
                if a < RETRIES - 1:
                    await asyncio.sleep(1.5 ** a)

    await asyncio.gather(*(one(r) for r in rows))
    for rid, price, amount in price_updates:
        conn.execute(
            "UPDATE transactions SET usd_price=?, usd_value=ROUND(?*?,2), price_checked=1 WHERE id=?",
            (round(price, 6), amount, price, rid))
    for rid in checked_only:
        conn.execute("UPDATE transactions SET price_checked=1 WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    return len(price_updates)


if __name__ == "__main__":
    uid = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    print(asyncio.run(_run(uid)))
