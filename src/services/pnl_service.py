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
    excluded = unmapped  # only unmapped tokens fully excluded

    # ═══════════════════════════════════════════════════════════════
    # 2. Preload all transactions, sorted by time
    # ═══════════════════════════════════════════════════════════════
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT token_symbol, amount, usd_price, direction, block_time, chain "
            "FROM transactions WHERE user_id=? AND wallet_address=? "
            "ORDER BY block_time ASC",
            (user_id, wallet_address))
        txs = await cur.fetchall()

    if not txs:
        logger.info(f"[rebuild] user={user_id} wallet={wallet_address}: no transactions")
        return {"ok": True, "days": 0, "unmapped_tokens": list(unmapped)}

    # ═══════════════════════════════════════════════════════════════
    # 3. Build fallback prices from transactions and portfolio
    # ═══════════════════════════════════════════════════════════════
    # Pre-sort price series for each token
    sorted_prices: Dict[str, List[Tuple[int, float]]] = {}
    for sym, p_dict in price_series.items():
        if p_dict:
            sorted_prices[sym] = sorted(p_dict.items())  # [(ts_ms, price), ...]

    # Build fallback prices from transactions (last known usd_price per token)
    fallback_prices: Dict[str, float] = {}
    for tx in txs:
        sym = tx["token_symbol"].lower()
        if tx["usd_price"] > 0:
            fallback_prices[sym] = tx["usd_price"]

    # v2.11.13: build dated price series from transaction prices for tokens
    # NOT covered by DefiLlama series (unmapped but priced tokens such as
    # STETH/HEX enriched at import time). normalize_prices_for_timeline can
    # then forward-fill a real per-date price instead of one static value.
    tx_price_points: Dict[str, Dict[int, float]] = defaultdict(dict)
    for tx in txs:
        sym = tx["token_symbol"].lower()
        if sym in sorted_prices:
            continue
        price = tx["usd_price"] or 0
        if price <= 0:
            continue
        try:
            dt = datetime.datetime.strptime(
                tx["block_time"][:19], "%Y-%m-%d %H:%M:%S")
            ts_ms = calendar.timegm(dt.timetuple()) * 1000
        except Exception:
            continue
        tx_price_points[sym][ts_ms] = price
    for sym, points in tx_price_points.items():
        sorted_prices[sym] = sorted(points.items())  # [(ts_ms, price), ...]

    # v2.11.13: unmapped tokens that carry at least one transaction price are
    # NOT excluded anymore — their tx prices provide real dated series above.
    # (excluded aliases unmapped, so these tokens also stop being reported as
    # excluded to the frontend.) Spam and truly price-less unmapped tokens
    # remain excluded (see tx loop below).
    excluded.difference_update(fallback_prices.keys())

    # Fetch current portfolio prices as ultimate fallback
    current_prices: Dict[str, float] = {}
    current_values: Dict[str, float] = {}
    if compute_portfolio:
        try:
            from_portfolio = await compute_portfolio(wallet_address)
            for t in from_portfolio.get("tokens", []):
                sym = t["symbol"].lower()
                price = t.get("usd_price", 0)
                total_val = t.get("usd_value", 0)
                if price > 0:
                    current_prices[sym] = price
                if total_val > 0:
                    current_values[sym] = total_val
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

    # Detect orphan tokens (have portfolio value but no transactions)
    tx_syms: Set[str] = {tx["token_symbol"].lower() for tx in txs}
    for sym in current_values:
        if sym not in tx_syms and sym not in excluded:
            if SYMBOL_TO_CG.get(sym) or sym in fallback_prices:
                price = current_prices.get(sym, 0)
                if price > 0:
                    qty = current_values[sym] / price
                    if qty > 0:
                        daily_deltas[first_date][sym] += qty
                        if sym not in fallback_prices:
                            fallback_prices[sym] = price

    for tx in txs:
        sym = tx["token_symbol"].lower()
        date = tx["block_time"][:10]
        amount = tx["amount"] or 0
        price = tx["usd_price"] or 0

        if sym in excluded:
            continue
        if not SYMBOL_TO_CG.get(sym):
            # v2.11.13: keep spam and truly price-less unmapped tokens out;
            # unmapped tokens with at least one priced transaction stay in
            # (their tx-price series feeds normalize_prices_for_timeline).
            if _is_spam(sym) or sym not in fallback_prices:
                excluded.add(sym)
                continue

        if tx["direction"] == "in":
            daily_deltas[date][sym] += amount
        else:
            daily_deltas[date][sym] -= amount

    # ═══════════════════════════════════════════════════════════════
    # 6. Collect all active token symbols (have balance or price)
    # ═══════════════════════════════════════════════════════════════
    active_syms: Set[str] = set()
    for sym in tx_syms:
        if sym not in excluded:
            active_syms.add(sym)
    for sym in current_values:
        if sym not in excluded:
            active_syms.add(sym)

    # ═══════════════════════════════════════════════════════════════
    # 7. Pre-normalize prices: for each active token, build a
    #    price array aligned with the timeline (forward-filled).
    # ═══════════════════════════════════════════════════════════════
    price_matrix: Dict[str, List[float]] = {}
    missing_price_count = 0
    for sym in active_syms:
        prices = normalize_prices_for_timeline(
            timeline, sym, sorted_prices, fallback_prices, current_prices
        )
        price_matrix[sym] = prices
        zero_count = sum(1 for p in prices if p == 0.0)
        if zero_count > 0:
            missing_price_count += zero_count
            logger.debug(
                f"[rebuild] token={sym}: {zero_count}/{n_days} days with price=0"
            )

    logger.info(
        f"[rebuild] active_tokens={len(active_syms)}, "
        f"missing_price_slots={missing_price_count}/{len(active_syms)*n_days}"
    )

    # ═══════════════════════════════════════════════════════════════
    # 8. Walk timeline: compute balances, costs, values for each day
    # ═══════════════════════════════════════════════════════════════
    balances: Dict[str, float] = defaultdict(float)  # per-token cumulative balance
    costs: Dict[str, float] = defaultdict(float)      # per-token cumulative cost basis
    token_chain: Dict[str, str] = {}
    daily_rows: list = []

    tokens_skipped: Set[str] = set()
    all_values: List[float] = []

    for day_idx, date_str in enumerate(timeline):
        day_deltas = daily_deltas.get(date_str, {})

        # --- 8a. Update per-token balances and costs ---
        for sym, delta in day_deltas.items():
            old_bal = balances[sym]
            new_bal = max(0.0, old_bal + delta)

            # Get price for this day from the normalized price matrix
            day_price = (
                price_matrix[sym][day_idx]
                if sym in price_matrix
                else _price_at(sym, 0, sorted_prices, fallback_prices, current_prices)
            )

            if delta > 0 and old_bal >= 0:
                # Incoming: add cost at day's price
                costs[sym] += delta * max(0.0, day_price)
            elif delta < 0 and old_bal > 0:
                # Outgoing: remove at average cost
                avg_cost = costs[sym] / old_bal if old_bal > 0 else 0.0
                costs[sym] = max(0.0, costs[sym] - abs(delta) * avg_cost)

            balances[sym] = new_bal
            if new_bal == 0:
                costs[sym] = 0.0

        # --- 8b. Compute daily value: sum(balance × price) ---
        value = 0.0
        per_token_values: Dict[str, float] = {}

        for sym in active_syms:
            bal = balances.get(sym, 0.0)
            if bal <= 0 or sym in excluded:
                continue

            # Get pre-normalized price for this day
            if sym in price_matrix:
                p = price_matrix[sym][day_idx]
            else:
                # Fallback for tokens not in price_matrix (shouldn't happen)
                day_ts = calendar.timegm(datetime.datetime.strptime(date_str, "%Y-%m-%d").timetuple()) + 43200
                day_ts_ms = day_ts * 1000
                p = _price_at(sym, day_ts_ms, sorted_prices, fallback_prices, current_prices)

            if p <= 0:
                continue

            token_val = bal * p
            if not math.isfinite(token_val):
                tokens_skipped.add(sym)
                continue

            token_val = round(token_val, 2)
            value += token_val
            per_token_values[sym] = token_val

        if not math.isfinite(value):
            value = 0.0

        all_values.append(value)

        # --- 8c. Net flows: sum(delta × price) ---
        net_flows = 0.0
        for sym, delta in day_deltas.items():
            if sym in price_matrix:
                p = price_matrix[sym][day_idx]
            else:
                p = _price_at(sym, 0, sorted_prices, fallback_prices, current_prices)
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

        # --- 8f. Per-token rows ---
        for sym, token_val in per_token_values.items():
            daily_rows.append((
                user_id, wallet_address, date_str,
                token_val, round(costs.get(sym, 0.0), 2), 0, sym, token_chain.get(sym),
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
        f"days={n_days} tokens={len(active_syms)} "
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
