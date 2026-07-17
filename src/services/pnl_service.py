"""
PNL service — history rebuild and PNL computation.

Unified timeline approach:
  1. Build daily timeline from first tx → today
  2. Normalize balances per token (forward-fill)
  3. Normalize prices per token (forward-fill from DefiLlama → fallback chain)
  4. Compute daily value = sum(balance × price) — always numeric
  5. Write to daily_history (idempotent)
  6. Debug logging for diagnostics
"""
import calendar
import datetime
import logging
import math
from collections import defaultdict
from typing import Dict, List, Tuple, Optional, Set

import aiosqlite

from services.price_service import (
    DB_PATH, SYMBOL_TO_CG, _price_at, _fetch_prices_per_token,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('crypto.pnl')

# Spam token patterns to filter out
SPAM_PATTERNS = [
    "visit ", "claim ", "reward", "airdrop", "http", "t.me", ".cfd", ".cc",
    ".lat", ".lol", ".top", ".xyz", ".win", ".vip", ".club", "random",
    "you are eligible", "you received", "you won", "coupon", "giveaway",
    "visit website", "mint airdrop", "gift", "voucher", "bonus", "! ", "? ",
    "$ claim", "www.", "@", "token", "web3", "web4", "nft", "u5dc", "usdtclaim",
    "official website", "verify", "us_pool", "us_circle", "tronvanity",
]


def _is_spam(sym: str) -> bool:
    sym_lower = sym.lower()
    for p in SPAM_PATTERNS:
        if p in sym_lower:
            return True
    return False


def build_timeline(first_date_str: str, last_date_str: str) -> List[str]:
    """Build a unified daily timeline: ['2024-01-01', '2024-01-02', ...].

    Every date from first_date_str to last_date_str inclusive, with no gaps.
    """
    timeline = []
    cursor = datetime.datetime.strptime(first_date_str, "%Y-%m-%d")
    end = datetime.datetime.strptime(last_date_str, "%Y-%m-%d")
    while cursor <= end:
        timeline.append(cursor.strftime("%Y-%m-%d"))
        cursor += datetime.timedelta(days=1)
    return timeline


def normalize_prices_for_timeline(
    timeline: List[str],
    sym_lower: str,
    sorted_prices: Dict[str, List[Tuple[int, float]]],
    fallback_prices: Dict[str, float],
    current_prices: Dict[str, float],
) -> List[float]:
    """Return a list of prices aligned with the timeline, one per day.

    Forward-fill from best available source:
      1. DefiLlama/CoinGecko historical series (sorted_prices)
      2. Last known transaction USD price (fallback_prices)
      3. Current portfolio live price (current_prices)
      4. 0.0 — no price data available

    Always returns exactly len(timeline) items. Never None, never NaN.
    """
    result = []
    last_known_price = 0.0

    # Get price series for this token (sorted by timestamp)
    sp = sorted_prices.get(sym_lower, [])

    # Static fallback: single-value price sources
    static_price = fallback_prices.get(sym_lower, 0.0) or current_prices.get(sym_lower, 0.0)

    # Pre-compute: for each day, find the best price from the series
    # We'll do a two-pointer pass through the timeline and price series
    series_idx = 0
    series_len = len(sp)

    for day_str in timeline:
        # Compute noon UTC timestamp in milliseconds for this day
        # Use UTC noon timestamp for consistent alignment with DefiLlama UTC data
        day_dt = datetime.datetime.strptime(day_str, "%Y-%m-%d")
        day_ts = calendar.timegm(day_dt.timetuple()) + 43200  # noon UTC
        day_ts_ms = day_ts * 1000

        # Advance through price series to find the last point <= day_ts_ms
        while series_idx < series_len and sp[series_idx][0] <= day_ts_ms:
            last_known_price = sp[series_idx][1]
            series_idx += 1

        # If we have a price from series, use it
        if last_known_price > 0:
            result.append(float(last_known_price))
        elif static_price > 0:
            result.append(float(static_price))
        else:
            result.append(0.0)

    # Backward-extrapolate: if we have ANY price data and the first entries
    # are 0, fill them with the first known price. This prevents a disruptive
    # 0->real jump in charts when DefiLlama data starts after the first tx.
    if sp:
        first_known = sp[0][1] if sp[0][1] > 0 else (static_price if static_price > 0 else None)
        if first_known:
            for idx in range(len(result)):
                if result[idx] > 0:
                    break
                result[idx] = float(first_known)

    return result


async def _rebuild_history(
    user_id: int,
    wallet_address: str,
    compute_portfolio=None,
) -> dict:
    """Idempotent daily history rebuild with unified timeline.

    Guarantees:
      - Every day has a row (no gaps in timeline)
      - Every value is numeric (never None, never NaN)
      - Balance and price are aligned to the same daily resolution
      - Debug diagnostics logged on completion
    """
    # ═══════════════════════════════════════════════════════════════
    # 1. Fetch prices per token
    # ═══════════════════════════════════════════════════════════════
    price_result = await _fetch_prices_per_token(user_id, wallet_address)
    unmapped = set(u.lower() for u in price_result.get("unmapped", []))
    degraded = set(d.lower() for d in price_result.get("degraded", []))
    price_series = price_result.get("prices", {})

    # ═══════════════════════════════════════════════════════════════
    # 2. Preload all transactions, sorted by time
    # ═══════════════════════════════════════════════════════════════
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT token_symbol, amount, usd_price, direction, block_time, chain, contract_address "
            "FROM transactions WHERE user_id=? AND wallet_address=? "
            "ORDER BY block_time ASC",
            (user_id, wallet_address))
        txs = await cur.fetchall()

    if not txs:
        logger.info(f"[rebuild] user={user_id} wallet={wallet_address}: no transactions")
        return {"ok": True, "days": 0, "unmapped_tokens": list(unmapped)}

    # ═══════════════════════════════════════════════════════════════
    # 3. Token identity + price sources (contract-aware)
    # ═══════════════════════════════════════════════════════════════
    # A token's identity ("tid") is its CONTRACT ADDRESS (fallback:
    # chain:symbol). This prevents two different tokens sharing a symbol —
    # e.g. the real BOB (~$1) and a spam "bob" (millions of units) — from
    # being merged and mispriced together.
    def _tid(symbol, chain, contract):
        c = (contract or "").strip().lower()
        return c if c else f"{(chain or '').lower()}:{(symbol or '').lower()}"

    tid_sym: Dict[str, str] = {}     # tid -> display symbol (lower)
    tid_chain: Dict[str, str] = {}   # tid -> chain
    for tx in txs:
        tid = _tid(tx["token_symbol"], tx["chain"], tx["contract_address"])
        tid_sym.setdefault(tid, (tx["token_symbol"] or "").lower())
        tid_chain.setdefault(tid, tx["chain"])

    # Symbol-keyed DefiLlama/CoinGecko series (from price_service)
    sorted_prices_sym: Dict[str, List[Tuple[int, float]]] = {}
    for sym, p_dict in price_series.items():
        if p_dict:
            sorted_prices_sym[sym] = sorted(p_dict.items())

    # Per-tid fallback price (last known usd_price of THIS token) + dated points
    fallback_prices: Dict[str, float] = {}                           # tid -> price
    tx_price_points: Dict[str, Dict[int, float]] = defaultdict(dict)  # tid -> {ts_ms: price}
    for tx in txs:
        if not tx["usd_price"] or tx["usd_price"] <= 0:
            continue
        tid = _tid(tx["token_symbol"], tx["chain"], tx["contract_address"])
        fallback_prices[tid] = tx["usd_price"]
        try:
            dt = datetime.datetime.strptime(tx["block_time"][:19], "%Y-%m-%d %H:%M:%S")
            tx_price_points[tid][calendar.timegm(dt.timetuple()) * 1000] = tx["usd_price"]
        except Exception:
            pass

    # Per-tid dated price series: prefer the token's own tx-price series,
    # else fall back to its symbol's DefiLlama series.
    sorted_prices: Dict[str, List[Tuple[int, float]]] = {}   # keyed by tid
    for tid, sym in tid_sym.items():
        if tx_price_points.get(tid):
            sorted_prices[tid] = sorted(tx_price_points[tid].items())
        elif sym in sorted_prices_sym:
            sorted_prices[tid] = sorted_prices_sym[sym]

    # Fetch current portfolio prices/values as ultimate fallback (keyed by tid)
    current_prices: Dict[str, float] = {}   # tid -> price
    current_values: Dict[str, float] = {}   # tid -> usd_value
    if compute_portfolio:
        try:
            from_portfolio = await compute_portfolio(wallet_address)
            for t in from_portfolio.get("tokens", []):
                tid = _tid(t.get("symbol"), t.get("chain"), t.get("contract_address"))
                tid_sym.setdefault(tid, (t.get("symbol") or "").lower())
                tid_chain.setdefault(tid, t.get("chain"))
                price = t.get("usd_price", 0)
                total_val = t.get("usd_value", 0)
                if price > 0:
                    current_prices[tid] = price
                if total_val > 0:
                    current_values[tid] = total_val
        except Exception as e:
            logger.warning(f"[rebuild] portfolio fetch failed: {e}")

    # ═══════════════════════════════════════════════════════════════
    # 4. Build unified timeline
    # ═══════════════════════════════════════════════════════════════
    first_date = txs[0]["block_time"][:10]
    last_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    timeline = build_timeline(first_date, last_date)
    n_days = len(timeline)
    logger.info(
        f"[rebuild] user={user_id} wallet={wallet_address}: "
        f"timeline={first_date}→{last_date} ({n_days} days)"
    )

    # ═══════════════════════════════════════════════════════════════
    # 5. Compute balance deltas for each day (sparse)
    # ═══════════════════════════════════════════════════════════════
    daily_deltas: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    excluded_tids: Set[str] = set()

    tx_tids: Set[str] = {_tid(tx["token_symbol"], tx["chain"], tx["contract_address"]) for tx in txs}

    def _keep_tid(tid: str) -> bool:
        """Keep a token if its symbol is CoinGecko-mapped, or if THIS contract
        has its own price. Spam and price-less unmapped tokens are dropped —
        so a same-symbol spam token can't inherit another token's price."""
        sym = tid_sym.get(tid, "")
        if SYMBOL_TO_CG.get(sym):
            return True
        if _is_spam(sym):
            return False
        return tid in fallback_prices or current_prices.get(tid, 0) > 0

    # Detect orphan tokens (have portfolio value but no transactions)
    for tid, val in current_values.items():
        if tid not in tx_tids and _keep_tid(tid):
            price = current_prices.get(tid, 0)
            if price > 0:
                qty = val / price
                if qty > 0:
                    daily_deltas[first_date][tid] += qty
                    if tid not in fallback_prices:
                        fallback_prices[tid] = price

    for tx in txs:
        tid = _tid(tx["token_symbol"], tx["chain"], tx["contract_address"])
        date = tx["block_time"][:10]
        amount = tx["amount"] or 0

        if tid in excluded_tids:
            continue
        if not _keep_tid(tid):
            excluded_tids.add(tid)
            continue

        if tx["direction"] == "in":
            daily_deltas[date][tid] += amount
        else:
            daily_deltas[date][tid] -= amount

    # ═══════════════════════════════════════════════════════════════
    # 6. Collect all active token symbols (have balance or price)
    # ═══════════════════════════════════════════════════════════════
    active_tids: Set[str] = set()
    for tid in tx_tids:
        if tid not in excluded_tids:
            active_tids.add(tid)
    for tid in current_values:
        if tid not in excluded_tids:
            active_tids.add(tid)

    # ═══════════════════════════════════════════════════════════════
    # 7. Pre-normalize prices: for each active token, build a
    #    price array aligned with the timeline (forward-filled).
    # ═══════════════════════════════════════════════════════════════
    price_matrix: Dict[str, List[float]] = {}
    missing_price_count = 0
    for tid in active_tids:
        prices = normalize_prices_for_timeline(
            timeline, tid, sorted_prices, fallback_prices, current_prices
        )
        price_matrix[tid] = prices
        missing_price_count += sum(1 for p in prices if p == 0.0)

    logger.info(
        f"[rebuild] active_tokens={len(active_tids)}, "
        f"missing_price_slots={missing_price_count}/{max(1, len(active_tids)*n_days)}"
    )

    # ═══════════════════════════════════════════════════════════════
    # 8. Walk timeline: compute balances, costs, values for each day
    # ═══════════════════════════════════════════════════════════════
    balances: Dict[str, float] = defaultdict(float)  # per-token-id cumulative balance
    costs: Dict[str, float] = defaultdict(float)      # per-token-id cumulative cost basis
    daily_rows: list = []

    tokens_skipped: Set[str] = set()
    all_values: List[float] = []

    for day_idx, date_str in enumerate(timeline):
        day_deltas = daily_deltas.get(date_str, {})

        # --- 8a. Update per-token balances and costs ---
        for tid, delta in day_deltas.items():
            old_bal = balances[tid]
            new_bal = max(0.0, old_bal + delta)

            day_price = (
                price_matrix[tid][day_idx]
                if tid in price_matrix
                else _price_at(tid, 0, sorted_prices, fallback_prices, current_prices)
            )

            if delta > 0 and old_bal >= 0:
                costs[tid] += delta * max(0.0, day_price)          # incoming: add cost at day price
            elif delta < 0 and old_bal > 0:
                avg_cost = costs[tid] / old_bal if old_bal > 0 else 0.0
                costs[tid] = max(0.0, costs[tid] - abs(delta) * avg_cost)  # outgoing: at avg cost

            balances[tid] = new_bal
            if new_bal == 0:
                costs[tid] = 0.0

        # --- 8b. Compute daily value: sum(balance × price) ---
        value = 0.0
        per_token_values: Dict[str, float] = {}

        for tid in active_tids:
            bal = balances.get(tid, 0.0)
            if bal <= 0 or tid in excluded_tids:
                continue

            if tid in price_matrix:
                p = price_matrix[tid][day_idx]
            else:
                day_ts = calendar.timegm(datetime.datetime.strptime(date_str, "%Y-%m-%d").timetuple()) + 43200
                p = _price_at(tid, day_ts * 1000, sorted_prices, fallback_prices, current_prices)

            if p <= 0:
                continue

            token_val = bal * p
            if not math.isfinite(token_val):
                tokens_skipped.add(tid)
                continue

            token_val = round(token_val, 2)
            value += token_val
            per_token_values[tid] = token_val

        if not math.isfinite(value):
            value = 0.0

        all_values.append(value)

        # --- 8c. Net flows: sum(delta × price) ---
        net_flows = 0.0
        for tid, delta in day_deltas.items():
            if tid in price_matrix:
                p = price_matrix[tid][day_idx]
            else:
                p = _price_at(tid, 0, sorted_prices, fallback_prices, current_prices)
            flow = delta * p
            if math.isfinite(flow):
                net_flows += flow

        # --- 8d. Cost basis ---
        cost_basis = sum(costs.values())
        if not math.isfinite(cost_basis) or cost_basis < 0:
            cost_basis = 0.0

        # --- 8e. Aggregate row (token_symbol=NULL) ---
        daily_rows.append((
            user_id, wallet_address, date_str,
            round(value, 2), round(cost_basis, 2),
            round(net_flows, 2), None, None,
        ))

        # --- 8f. Per-token rows (stored by display symbol; several contracts
        #         of the same symbol are summed per date by the read queries) ---
        for tid, token_val in per_token_values.items():
            daily_rows.append((
                user_id, wallet_address, date_str,
                token_val, round(costs.get(tid, 0.0), 2), 0,
                tid_sym.get(tid), tid_chain.get(tid),
            ))

    # ═══════════════════════════════════════════════════════════════
    # 9. Write to daily_history (idempotent)
    # ═══════════════════════════════════════════════════════════════
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM daily_history WHERE user_id=? AND wallet_address=?",
            (user_id, wallet_address))
        for row in daily_rows:
            await db.execute(
                "INSERT INTO daily_history "
                "(user_id, wallet_address, date, value_usd, cost_basis_usd, "
                "net_flows_usd, token_symbol, chain) "
                "VALUES (?,?,?,?,?,?,?,?)",
                row)
        await db.commit()

    # ═══════════════════════════════════════════════════════════════
    # 10. Debug diagnostics
    # ═══════════════════════════════════════════════════════════════
    valid_values = [v for v in all_values if v > 0]
    min_val = round(min(valid_values), 2) if valid_values else 0.0
    max_val = round(max(valid_values), 2) if valid_values else 0.0

    logger.info(
        f"[rebuild] DONE user={user_id} wallet={wallet_address}: "
        f"days={n_days} tokens={len(active_tids)} "
        f"rows_written={len(daily_rows)} "
        f"skipped_tokens={len(tokens_skipped)} "
        f"missing_price_slots={missing_price_count} "
        f"value_range=[{min_val}, {max_val}]"
    )

    return {
        "ok": True,
        "days": n_days,
        "unmapped_tokens": sorted(unmapped),
        "degraded_tokens": sorted(degraded),
        "price_calls_ok": price_result.get("price_calls_ok", 0),
        "price_calls_failed": price_result.get("price_calls_failed", 0),
        "tokens_with_series": len(price_series),
        "tokens_skipped": sorted(tokens_skipped),
        "value_min": min_val,
        "value_max": max_val,
        "missing_price_slots": missing_price_count,
    }


# ═══════════════════════════════════════════════════════════════════
# PNL computation (pure, NaN-safe)
# ═══════════════════════════════════════════════════════════════════

def compute_pnl_from_rows(rows: list) -> list:
    """Pure PNL computation from daily_history rows.

    Takes list of rows with keys: date, value_usd, cost_basis_usd, net_flows_usd.
    Returns list of dicts with: date, value, cost_basis, pnl, pnl_pct, pnl_day.

    Hardening: NaN values are filtered to 0.0. Division by zero guarded.
    """
    result = []
    prev_value = None
    for r in rows:
        value = r["value_usd"] if math.isfinite(r["value_usd"]) else 0.0
        cost_basis = r["cost_basis_usd"] if math.isfinite(r["cost_basis_usd"]) else 0.0
        net_flows = r["net_flows_usd"] if math.isfinite(r["net_flows_usd"]) else 0.0

        pnl = value - cost_basis
        # Guard against division by zero
        if cost_basis > 0:
            pnl_pct = round(pnl / cost_basis * 100, 2)
        else:
            pnl_pct = 0.0

        if prev_value is not None:
            pnl_day = round(value - prev_value - net_flows, 2)
        else:
            pnl_day = 0.0
        prev_value = value

        # NaN filtering
        if math.isnan(pnl_day):
            pnl_day = 0.0
        if math.isnan(pnl):
            pnl = 0.0
        if math.isnan(pnl_pct):
            pnl_pct = 0.0

        result.append({
            "date": r["date"],
            "value": value,
            "cost_basis": cost_basis,
            "pnl": round(pnl, 2),
            "pnl_pct": pnl_pct,
            "pnl_day": pnl_day,
        })

    return result


def format_pnl_v2(result: list) -> dict:
    """Convert PNL array to standardized v2 format: {labels, values, meta}.

    Guarantees:
      - labels.length == values.length
      - No nulls, no NaN
      - meta always present with points/min/max
    """
    if not result:
        return {"labels": [], "values": [], "meta": {"points": 0, "min": 0, "max": 0}}

    labels = []
    values = []
    for r in result:
        lbl = r.get("date", "")
        val = r.get("pnl", 0.0)
        if not math.isfinite(val):
            val = 0.0
        labels.append(str(lbl))
        values.append(round(float(val), 2))

    # Safety: ensure lengths match
    assert len(labels) == len(values), f"labels={len(labels)} != values={len(values)}"

    return {
        "labels": labels,
        "values": values,
        "meta": {
            "points": len(result),
            "min": round(min(values), 2) if values else 0,
            "max": round(max(values), 2) if values else 0,
        },
    }
