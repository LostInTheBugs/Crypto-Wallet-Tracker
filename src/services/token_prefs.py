"""
Token preferences service — shared token identity (tid) + auto-disable heuristics.

Single source of truth for:
  • token_tid(): the token identity used by BOTH the live portfolio filtering
    (app.py) and the historical rebuild (pnl_service.py). A token's identity is
    its CONTRACT ADDRESS (lowercase); fallback "chain:symbol" for native coins.
  • classify_token(): conservative auto-disable heuristic for dubious tokens
    (illiquid memecoins / low-confidence prices). When in doubt → keep enabled.
  • user_token_prefs DB helpers (load prefs, disabled tid set).

No imports from other services (keeps the dependency graph acyclic).
"""
import os

import aiosqlite
from services.db import write_locked

DB_PATH = os.environ.get("DB_PATH", "/data/wallets.db")


async def _connect():
    """Open a connection with busy_timeout set (per-connection pragma) so
    writes survive a concurrent history-rebuild commit instead of raising
    'database is locked'."""
    db = await aiosqlite.connect(DB_PATH)
    try:
        await db.execute("PRAGMA busy_timeout=10000")
    except Exception:
        pass
    return db

# ═══════════════════════════════════════════════════════════════════
# Auto-disable heuristic thresholds (tune here)
# ═══════════════════════════════════════════════════════════════════

# 1) low_confidence: DefiLlama returns a per-token "confidence" (0..1).
#    Below this threshold the price is considered unreliable.
#    Only applies when the price actually CAME from DefiLlama — tokens priced
#    by Blockscout have confidence=None and are never flagged low_confidence.
DEFILLAMA_MIN_CONFIDENCE = 0.8

# 2) memecoin_pattern: huge balance × microscopic unit price × big USD value.
#    All three must hold simultaneously (conservative).
MEMECOIN_MIN_VALUE = 500.0        # USD value threshold (>=)
MEMECOIN_MAX_UNIT_PRICE = 1e-4    # unit price threshold (<=)
MEMECOIN_MIN_BALANCE = 1e7        # balance threshold (>=)

# Valid `reason` values stored in user_token_prefs
REASONS = ("low_confidence", "memecoin_pattern", "zero_value", "spam", "manual", "")


def token_tid(symbol, chain, contract) -> str:
    """Canonical token identity — MUST stay identical to the historical
    rebuild's notion of identity (pnl_service uses this exact function).

    contract address (lowercase) when available, else "chain:symbol".
    """
    c = (contract or "").strip().lower()
    return c if c else f"{(chain or '').lower()}:{(symbol or '').lower()}"


def classify_token(usd_value, usd_price, balance, confidence, is_spam=False) -> tuple[int, str]:
    """Compute the default enabled state for a newly detected token.

    Returns (default_enabled, reason):
      (0, "spam")              — spam-pattern token (is_spam passed by the caller,
                                 which owns the _is_spam heuristic — no cyclic import)
      (0, "zero_value")        — worthless holding: usd_value <= 0 OR unknown price
      (0, "low_confidence")    — DefiLlama price confidence below threshold
      (0, "memecoin_pattern")  — big value from a dust-priced token in huge supply
      (1, "")                  — normal token, enabled by default

    Conservative by design: missing/None inputs never disable a token
    (except zero_value/spam, which are explicit worthlessness signals).
    """
    try:
        usd_value = float(usd_value or 0)
        usd_price = float(usd_price or 0)
        balance = float(balance or 0)
    except (TypeError, ValueError):
        return 1, ""

    # 0) Spam pattern — decided by the caller (portfolio_service._is_spam)
    if is_spam:
        return 0, "spam"

    # 0bis) Zero value — no price known OR the position is worth nothing.
    #       These rows only pollute the token list; re-enable is one click away.
    if usd_value <= 0 or usd_price <= 0:
        return 0, "zero_value"

    # 1) DefiLlama confidence — only when a confidence value exists
    if confidence is not None:
        try:
            conf = float(confidence)
            if 0 < conf < DEFILLAMA_MIN_CONFIDENCE:
                return 0, "low_confidence"
        except (TypeError, ValueError):
            pass

    # 2) Illiquid memecoin pattern — all three conditions required
    if (usd_value >= MEMECOIN_MIN_VALUE
            and 0 < usd_price <= MEMECOIN_MAX_UNIT_PRICE
            and balance >= MEMECOIN_MIN_BALANCE):
        return 0, "memecoin_pattern"

    return 1, ""


# ═══════════════════════════════════════════════════════════════════
# DB helpers
# ═══════════════════════════════════════════════════════════════════

async def load_user_prefs(user_id: int) -> dict:
    """Return {tid: pref_row_dict} for a user. Empty dict on missing table."""
    try:
        db = await _connect()
        try:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM user_token_prefs WHERE user_id=?", (user_id,))
            return {r["tid"]: dict(r) for r in await cur.fetchall()}
        finally:
            await db.close()
    except Exception:
        return {}


async def get_disabled_tids(user_id: int) -> set:
    """Set of tids the user has disabled (or that were auto-disabled)."""
    try:
        db = await _connect()
        try:
            cur = await db.execute(
                "SELECT tid FROM user_token_prefs WHERE user_id=? AND enabled=0",
                (user_id,))
            return {r[0] for r in await cur.fetchall()}
        finally:
            await db.close()
    except Exception:
        return set()


async def insert_default_prefs(rows: list) -> None:
    """Bulk-insert prefs for newly seen tids. NEVER overwrites an existing
    row (INSERT OR IGNORE) — the user's explicit choice is preserved.

    rows: list of (user_id, tid, enabled, source, chain, contract_address,
                   symbol, name, reason, default_enabled)
    """
    if not rows:
        return
    async with write_locked():
        db = await _connect()
        try:
            await db.executemany(
                "INSERT OR IGNORE INTO user_token_prefs "
                "(user_id, tid, enabled, source, chain, contract_address, symbol, "
                "name, reason, default_enabled) VALUES (?,?,?,?,?,?,?,?,?,?)",
                rows)
            await db.commit()
        finally:
            await db.close()


async def reclassify_prefs(rows: list) -> None:
    """Retroactively auto-disable prefs that the NEW heuristics (zero_value /
    spam — v2.12.2) flag as dubious. Only rows still in their pristine
    auto-managed state are touched: enabled=1, no reason, and NEVER updated
    since insertion (updated_at = created_at — any user toggle bumps
    updated_at). An explicit user choice is therefore never overwritten,
    even under a concurrent toggle.

    rows: list of (reason, user_id, tid)
    """
    if not rows:
        return
    async with write_locked():
        db = await _connect()
        try:
            await db.executemany(
                "UPDATE user_token_prefs SET enabled=0, reason=?, default_enabled=0 "
                "WHERE user_id=? AND tid=? AND enabled=1 AND reason='' "
                "AND (updated_at IS NULL OR updated_at = created_at)",
                rows)
            await db.commit()
        finally:
            await db.close()
