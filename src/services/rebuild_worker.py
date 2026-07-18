"""Standalone history rebuild worker.

Executed as a SUBPROCESS (python services/rebuild_worker.py <user_id>) after a
user changes token preferences (enable/disable/manual add/remove), so the full
historical reconstruction (DefiLlama HTTP calls + daily_history rewrite) runs
in a clean process — same reliability rationale as enrich_worker.py — and
never blocks the HTTP response of the toggle endpoint.

Rebuilds daily_history for EVERY wallet of the user; _rebuild_history reads
user_token_prefs and merges disabled tids into its excluded set, so the
resulting snapshots / PNL series reflect only ENABLED tokens (retroactive).
Prints the total number of rebuilt days.
"""
import sys, os, asyncio, sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.pnl_service import _rebuild_history
from services.portfolio_service import _compute_portfolio

DB_PATH = os.environ.get("DB_PATH", "/data/wallets.db")


async def _run(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT address FROM wallets WHERE user_id=?", (user_id,)).fetchall()
    conn.close()
    total_days = 0
    for (address,) in rows:
        try:
            result = await _rebuild_history(user_id, address, _compute_portfolio)
            total_days += result.get("days", 0)
        except Exception as e:
            print(f"rebuild failed for {address[:10]}: {e}", file=sys.stderr)
    return total_days


if __name__ == "__main__":
    uid = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    print(asyncio.run(_run(uid)))
