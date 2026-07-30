"""
Tax service — weighted-average cost basis and realized/unrealized PnL.

Computes per-asset (symbol+chain) cost basis using the weighted-average
method, walking transactions in chronological order.  Produces:

  * qty_held — current on-chain balance (from live portfolio)
  * cost_basis — remaining acquisition cost for held quantity
  * current_value — qty_held × current price
  * unrealized_pnl — current_value − cost_basis
  * realized_pnl — sum of (sale_proceeds − cost_of_sold_units) across sales
  * status — "ok" or "incomplete" (prices missing, non-EVM partial history)

Aggregates across all wallets for a user.  Never invents data —
assets with insufficient price history are flagged incomplete.
"""
import logging
import math
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import aiosqlite

from services.db import DB_PATH

logger = logging.getLogger("crypto.tax")


# ── Core computation ────────────────────────────────────────────────


async def compute_tax(
    user_id: int,
    wallet_address: str = "ALL",
    method: str = "avg",
    *,
    portfolio_fn=None,
    current_prices_fn=None,
) -> dict:
    """Compute tax / PnL per asset for a user.

    Args:
        user_id: the authenticated user id
        wallet_address: "ALL" or a specific wallet address
        method: cost-basis method; only "avg" supported
        portfolio_fn: async callable(address) → dict matching /api/portfolio
                      output (injectable for testing).  If None, the live
                      _compute_portfolio is used.
        current_prices_fn: async callable(addresses: list[str]) → dict of
                           {address: {asset_key: (price, balance_usd, balance_qty)}}

    Returns:
        {
            assets: [{symbol, chain, qty_held, cost_basis, current_value,
                      unrealized_pnl, realized_pnl, method, status}],
            totals: {total_cost_basis, total_current_value, total_unrealized,
                     total_realized, method},
        }
    """
    if method not in ("avg",):
        return {"assets": [], "totals": {"method": method, "error": "unsupported method"}}

    # ── Resolve wallets ──────────────────────────────────────────
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if wallet_address.upper() == "ALL":
            cur = await db.execute(
                "SELECT address FROM wallets WHERE user_id=?", (user_id,))
            addrs = [r["address"] for r in await cur.fetchall()]
        else:
            # Verify the address belongs to this user
            cur = await db.execute(
                "SELECT address FROM wallets WHERE user_id=? AND lower(address)=lower(?)",
                (user_id, wallet_address))
            row = await cur.fetchone()
            if not row:
                return {"assets": [], "totals": {"method": method, "error": "wallet not found"}}
            addrs = [row["address"]]

    if not addrs:
        return {"assets": [], "totals": {
            "total_cost_basis": 0.0, "total_current_value": 0.0,
            "total_unrealized": 0.0, "total_realized": 0.0, "method": "avg"}}

    if portfolio_fn is None:
        from services.portfolio_service import _compute_portfolio
        portfolio_fn = _compute_portfolio

    # ── Load transactions for all wallets ────────────────────────
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        placeholders = ",".join("?" for _ in addrs)
        cur = await db.execute(
            f"SELECT wallet_address, token_symbol, chain, contract_address, "
            f"amount, usd_price, usd_value, direction, block_time, tx_hash, log_index "
            f"FROM transactions "
            f"WHERE user_id=? AND lower(wallet_address) IN "
            f"({','.join('lower(?)' for _ in addrs)}) "
            f"ORDER BY block_time ASC",
            (user_id,) + tuple(a.lower() for a in addrs))
        txns = await cur.fetchall()

    # Pre-compute which wallets have substantial transaction history
    tx_wallets = set()
    for tx in txns:
        tx_wallets.add(tx["wallet_address"].lower())

    # ── Per-wallet portfolio data for current prices/balances ─────
    wallet_currencies: Dict[str, dict] = {}  # addr → {asset_key: {price, balance_qty}}
    for addr in addrs:
        try:
            pf = await portfolio_fn(addr)
            wallet_currencies[addr.lower()] = {}
            for t in pf.get("tokens", []):
                sym = (t.get("symbol") or "").upper()
                chain = t.get("chain", "")
                key = _asset_key(sym, chain)
                wallet_currencies[addr.lower()][key] = {
                    "price": t.get("usd_price", 0) or 0,
                    "balance_qty": t.get("balance", 0) or 0,
                    "usd_value": t.get("usd_value", 0) or 0,
                }
        except Exception:
            wallet_currencies[addr.lower()] = {}

    # ── Build per-asset transaction timeline ─────────────────────
    # asset_map: (sym, chain) → {txs: [...], current_price, current_balance, wallet_addresses}
    asset_map: Dict[Tuple[str, str], dict] = defaultdict(lambda: {
        "txs": [],  # ordered list of (direction, amount, price, value, wallet_addr)
        "wallet_addresses": set(),
    })

    for tx in txns:
        sym = (tx["token_symbol"] or "").upper()
        chain = tx["chain"] or ""
        if not sym:
            continue
        key = (sym, chain)
        amount = tx["amount"] or 0
        price = tx["usd_price"] or 0
        value = tx["usd_value"] or 0
        direction = tx["direction"] or "in"
        wallet = tx["wallet_address"] or ""
        if not (math.isfinite(amount) and math.isfinite(price) and math.isfinite(value)):
            continue
        asset_map[key]["txs"].append((direction, amount, price, value, wallet))
        asset_map[key]["wallet_addresses"].add(wallet.lower())

    # ── Inject portfolio-only assets (no transactions but current value > 0) ──
    for addr in addrs:
        wc = wallet_currencies.get(addr.lower(), {})
        for asset_key_str, asset_data in wc.items():
            parts = asset_key_str.split(":", 1)
            if len(parts) != 2:
                continue
            sym, chain = parts[0].upper(), parts[1]
            key = (sym, chain)
            price = asset_data.get("price", 0)
            val = asset_data.get("usd_value", 0)
            bal = asset_data.get("balance_qty", 0)
            if val > 0 and key not in asset_map:
                asset_map[key] = {
                    "txs": [],
                    "wallet_addresses": {addr.lower()},
                }
            elif key in asset_map:
                asset_map[key]["wallet_addresses"].add(addr.lower())

    # ── Compute per-asset cost basis + PnL ───────────────────────
    assets = []
    total_cost_basis = 0.0
    total_current_value = 0.0
    total_realized = 0.0
    total_unrealized = 0.0

    for (sym, chain), data in sorted(asset_map.items()):
        txs = data["txs"]
        has_tx_history = any(
            w.lower() in tx_wallets for w in data["wallet_addresses"])

        # Determine current price and balance by summing across wallets
        current_price = 0.0
        current_balance = 0.0
        current_val = 0.0
        has_current_data = False

        for addr in data["wallet_addresses"]:
            wc = wallet_currencies.get(addr, {})
            asset_data = wc.get(_asset_key(sym, chain))
            if asset_data and asset_data.get("price", 0) > 0:
                current_price = max(current_price, asset_data["price"])
                current_balance += asset_data.get("balance_qty", 0)
                current_val += asset_data.get("usd_value", 0)
                has_current_data = True

        # Walk transactions to compute weighted-average cost basis
        qty = 0.0
        cost = 0.0
        realized_pnl = 0.0
        any_price = False
        all_priced = True

        for direction, amount, price, value, wallet in txs:
            if not math.isfinite(amount):
                continue
            if direction == "in":
                qty += amount
                if price > 0 and math.isfinite(price):
                    cost += amount * price
                    any_price = True
                else:
                    all_priced = False
            elif direction == "out":
                if qty > 0 and cost > 0:
                    avg_cost = cost / qty
                    sold_cost = avg_cost * min(amount, qty)
                    realized_pnl += (value - sold_cost) if math.isfinite(value) else 0
                    cost -= sold_cost
                qty -= amount
                if qty < 0:
                    qty = 0.0
                if qty == 0:
                    cost = 0.0

        # Clamp
        if qty < 0:
            qty = 0.0
        if cost < 0:
            cost = 0.0

        cost_basis = round(cost, 2)
        current_value = round(current_val, 2)

        # Determine status
        if not has_tx_history or not has_current_data:
            status = "incomplete"
        elif not any_price:
            status = "incomplete"
        elif not all_priced:
            status = "incomplete"
        else:
            status = "ok"

        # If no price data at all, mark as incomplete
        if not has_tx_history and has_current_data and current_val > 0:
            # Asset exists in portfolio but has no transactions — Solana/non-EVM with
            # limited history.  Mark as incomplete.
            status = "incomplete"

        unrealized_pnl = round(current_val - cost_basis, 2) if status != "incomplete" or cost_basis > 0 else 0.0

        # Dust filter: skip sub-cent assets with no transactions
        if current_val < 0.01 and not any_price:
            continue

        assets.append({
            "symbol": sym,
            "chain": chain,
            "qty_held": round(current_balance, 8),
            "cost_basis": cost_basis,
            "current_value": current_value,
            "unrealized_pnl": round(current_value - cost_basis, 2),
            "realized_pnl": round(realized_pnl, 2),
            "method": "weighted-average",
            "status": status,
        })

        total_cost_basis += cost_basis
        total_current_value += current_value
        total_realized += realized_pnl
        total_unrealized += (current_value - cost_basis)

    # Sort by current value descending
    assets.sort(key=lambda a: a["current_value"], reverse=True)

    return {
        "assets": assets,
        "totals": {
            "total_cost_basis": round(total_cost_basis, 2),
            "total_current_value": round(total_current_value, 2),
            "total_unrealized": round(total_unrealized, 2),
            "total_realized": round(total_realized, 2),
            "method": "weighted-average",
        },
    }


def _asset_key(symbol: str, chain: str) -> str:
    """Normalized asset key — symbol uppercase + chain lowercase."""
    return f"{symbol.upper()}:{chain.lower()}"
