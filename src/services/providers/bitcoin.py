"""
BitcoinProvider — Bitcoin portfolio via mempool.space API (free, no key).

Implements ChainProvider for BTC addresses: bech32 (bc1...), legacy P2PKH (1...),
P2SH (3...).  Uses mempool.space for balance/transactions and DefiLlama for
BTC/USD price (same free source already in use for benchmarks).

2026.07.22 — Phase 2 Bitcoin support.
"""

from __future__ import annotations

import re
import time as _time
from typing import Any

import httpx

from services.providers.base import ChainProvider, register_provider, logger

# ═══════════════════════════════════════════════════════════════════════
# Detection helpers
# ═══════════════════════════════════════════════════════════════════════

# bech32 BTC addresses always start with bc1 (NOT cosmos1/osmo1/...)
_BECH32_BTC_RE = re.compile(r"^bc1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{38,}$", re.IGNORECASE)

# Legacy P2PKH: 1... (26-35 chars base58)
_LEGACY_RE = re.compile(r"^1[a-km-zA-HJ-NP-Z1-9]{25,34}$")

# P2SH: 3... (26-35 chars base58)
_P2SH_RE = re.compile(r"^3[a-km-zA-HJ-NP-Z1-9]{25,34}$")


def _is_btc_address(address: str) -> bool:
    """Return True if address looks like a Bitcoin address.

    Must NOT match EVM (0x...) or Cosmos bech32 (cosmos1.../osmo1...).
    """
    a = address.strip()
    if a.startswith("0x"):
        return False
    if a.startswith("bc1") and _BECH32_BTC_RE.match(a):
        return True
    if a.startswith("1") and _LEGACY_RE.match(a):
        return True
    if a.startswith("3") and _P2SH_RE.match(a):
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════
# Mempool.space helpers
# ═══════════════════════════════════════════════════════════════════════

MEMPOOL_BASE = "https://mempool.space/api"
REQUEST_TIMEOUT = 15.0  # generous; mempool is usually fast


async def _mempool_get(path: str) -> dict | None:
    """GET from mempool.space, return parsed JSON or None on any error."""
    url = f"{MEMPOOL_BASE}{path}"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.get(url)
        if r.status_code == 200:
            return r.json()
        logger.debug("mempool.space HTTP %d for %s", r.status_code, path)
    except Exception as e:
        logger.debug("mempool.space error for %s: %s", path, e)
    return None


async def _get_btc_balance_sats(address: str) -> int | None:
    """Return confirmed balance in satoshis, or None on failure.

    Uses chain_stats.funded_txo_sum - chain_stats.spent_txo_sum.
    """
    data = await _mempool_get(f"/address/{address}")
    if data is None:
        return None
    try:
        stats = data.get("chain_stats", {})
        funded = stats.get("funded_txo_sum", 0)
        spent = stats.get("spent_txo_sum", 0)
        return max(0, funded - spent)
    except (TypeError, AttributeError):
        return None


async def _get_btc_price_usd() -> float | None:
    """Get BTC/USD price via DefiLlama (free, no key)."""
    url = "https://coins.llama.fi/prices/current/coingecko:bitcoin"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url)
        if r.status_code == 200:
            data = r.json()
            coins = data.get("coins", {})
            btc_data = coins.get("coingecko:bitcoin", {})
            price = btc_data.get("price")
            if price:
                return float(price)
    except Exception as e:
        logger.debug("DefiLlama BTC price error: %s", e)
    return None


async def _get_btc_transactions_raw(address: str) -> list[dict] | None:
    """Return recent transactions from mempool.space /address/{addr}/txs."""
    data = await _mempool_get(f"/address/{address}/txs")
    if data is None:
        return None
    if isinstance(data, list):
        return data
    return None


# ═══════════════════════════════════════════════════════════════════════
# BitcoinProvider
# ═══════════════════════════════════════════════════════════════════════


class BitcoinProvider(ChainProvider):
    """Bitcoin portfolio provider via mempool.space public API."""

    chain_type = "bitcoin"
    native_symbol = "BTC"

    # ── Detection ────────────────────────────────────────────────

    def detect(self, address: str) -> bool:
        """Return True for bech32 bc1..., legacy 1..., and P2SH 3... addresses."""
        return _is_btc_address(address)

    # ── Portfolio ────────────────────────────────────────────────

    async def get_portfolio(self, address: str) -> dict:
        """Return BTC portfolio in the same shape as EVM.

        Contains a single BTC token entry with balance in BTC,
        USD price, and USD value.  total_usd = BTC value.
        """
        # Fetch balance (sats) and price in parallel
        import asyncio

        sats, price = await asyncio.gather(
            _get_btc_balance_sats(address),
            _get_btc_price_usd(),
        )

        btc_balance = (sats or 0) / 100_000_000.0  # sats → BTC
        usd_price = price or 0.0
        usd_value = round(btc_balance * usd_price, 2)

        token = {
            "symbol": "BTC",
            "name": "Bitcoin",
            "chain": "bitcoin",
            "balance": round(btc_balance, 8),
            "usd_price": usd_price,
            "usd_value": usd_value,
            "category": "wallet",
            "contract_address": "",
            "enabled": True,
        }

        # Include chain_stats for transparency
        chain_stats = {
            "funded_sats": sats or 0,
            "balance_btc": round(btc_balance, 8),
        }

        return {
            "address": address,
            "total_usd": usd_value,
            "token_count": 1 if btc_balance > 0 else 0,
            "chain_count": 1 if btc_balance > 0 else 0,
            "chains": {"bitcoin": usd_value} if usd_value > 0 else {},
            "tokens": [token] if btc_balance > 0 else [],
            "errors": [],
            "defi_usd": 0,
            "staked_usd": 0,
            "defi_breakdown": {},
            "active_count": 1 if btc_balance > 0 else 0,
            "inactive_count": 0,
            "chain_stats": chain_stats,
        }

    # ── Transactions ─────────────────────────────────────────────

    async def get_transactions(
        self,
        address: str,
        wallet: str | None = None,
        chain: str | None = None,
        token: str | None = None,
        direction: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """Return paginated BTC transaction events from mempool.space.

        Maps to the standard event shape: type (send/receive), direction,
        sent/received, tx_hash, block_time, usd_value.

        If mempool.space is unreachable, returns empty list gracefully.
        """
        raw_txs = await _get_btc_transactions_raw(address)
        if raw_txs is None:
            return {"total": 0, "items": [], "counts": {}}

        # Get current BTC price for approximate USD values
        price = await _get_btc_price_usd()

        events: list[dict[str, Any]] = []
        for tx in raw_txs:
            try:
                txid = tx.get("txid", "")
                status = tx.get("status", {})
                block_time = ""
                if status.get("block_time"):
                    from datetime import datetime, timezone
                    block_time = datetime.fromtimestamp(
                        status["block_time"], tz=timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")

                # Determine direction: check if our address is in vin or vout
                vin = tx.get("vin", [])
                vout = tx.get("vout", [])

                # Check if any vin.prevout.scriptpubkey_address matches our address
                is_sender = any(
                    inv.get("prevout", {})
                    .get("scriptpubkey_address", "")
                    == address
                    for inv in vin
                )

                # Check if any vout.scriptpubkey_address matches our address
                total_received_sats = 0
                total_sent_sats = 0
                for v in vout:
                    val = v.get("value", 0)
                    if v.get("scriptpubkey_address", "") == address:
                        total_received_sats += val
                    else:
                        total_sent_sats += val

                fee_sats = tx.get("fee", 0)
                ev_type: str
                ev_direction: str
                btc_amount: float  # positive float
                usd_value: float

                if is_sender:
                    ev_type = "send"
                    ev_direction = "out"
                    # Amount sent = all vout values to non-self addrs + fee
                    btc_amount = total_sent_sats / 100_000_000.0
                    usd_value = round(btc_amount * (price or 0), 2)
                else:
                    ev_type = "receive"
                    ev_direction = "in"
                    btc_amount = total_received_sats / 100_000_000.0
                    usd_value = round(btc_amount * (price or 0), 2)

                ev = {
                    "type": ev_type,
                    "direction": ev_direction,
                    "tx_hash": txid,
                    "block_time": block_time,
                    "token_symbol": "BTC",
                    "token_name": "Bitcoin",
                    "chain": "bitcoin",
                    "usd_value": usd_value,
                    "usd_price": price or 0,
                    "sent": {
                        "symbol": "BTC",
                        "name": "Bitcoin",
                        "amount": (
                            round(btc_amount, 8) if ev_type == "send" else 0.0
                        ),
                        "usd_price": price or 0,
                        "usd_value": usd_value if ev_type == "send" else 0.0,
                        "contract": "",
                    },
                    "sent_symbol": "BTC",
                    "sent_amount": round(btc_amount, 8) if ev_type == "send" else 0.0,
                    "received": {
                        "symbol": "BTC",
                        "name": "Bitcoin",
                        "amount": (
                            round(btc_amount, 8) if ev_type == "receive" else 0.0
                        ),
                        "usd_price": price or 0,
                        "usd_value": usd_value if ev_type == "receive" else 0.0,
                        "contract": "",
                    },
                    "recv_symbol": "BTC",
                    "recv_amount": round(btc_amount, 8) if ev_type == "receive" else 0.0,
                    "legs": 1,
                    "gas_fee_usd": round((fee_sats / 100_000_000.0) * (price or 0), 2),
                    "wallet_address": address,
                    "log_index": 0,
                }
                events.append(ev)
            except Exception:
                logger.debug("Skipping malformed BTC tx for %s", address[:20])
                continue

        # Apply direction filter
        if direction:
            events = [e for e in events if e["direction"] == direction]

        # Apply type filter
        if event_type:
            events = [e for e in events if e["type"] == event_type]

        # Apply token filter (only BTC, trivial)
        if token and token.lower() not in ("btc", "bitcoin", ""):
            events = []

        # Counts
        counts = {"send": 0, "receive": 0, "swap": 0, "approve": 0, "contract": 0, "native": 0}
        for ev in events:
            counts[ev["type"]] = counts.get(ev["type"], 0) + 1

        total = len(events)
        page = events[offset : offset + limit]
        return {"total": total, "items": page, "counts": counts}

    # ── Explorer URLs ────────────────────────────────────────────

    def explorer_url(self, address: str) -> str:
        return f"https://mempool.space/address/{address}"

    def explorer_tx_url(self, tx_hash: str) -> str:
        return f"https://mempool.space/tx/{tx_hash}"


# ── Auto-register ──────────────────────────────────────────────────
bitcoin_provider = BitcoinProvider()
register_provider(bitcoin_provider)
