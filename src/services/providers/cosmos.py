"""
CosmosProvider — Cosmos/ATOM ecosystem via public LCD REST endpoints.

Implements ChainProvider for Cosmos bech32 addresses (cosmos1…, osmo1…,
celestia1…, juno1…, etc.).  Uses public LCD endpoints (Polkachu) for
balances, staking delegations, and rewards.  Prices via DefiLlama.

2026.07.24 — Phase 2 Cosmos/ATOM support.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from services.providers.base import ChainProvider, register_provider, logger

# ═══════════════════════════════════════════════════════════════════════
# bech32 detection (Cosmos-specific, conservative)
# ═══════════════════════════════════════════════════════════════════════

# bech32 charset used by Cosmos SDK
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_RE = re.compile(r"^([" + re.escape(_BECH32_CHARSET) + r"]+)$")

# Known Cosmos HRPs → LCD endpoint and native info
# EXCLUDES: "bc" (Bitcoin bech32 — never handled here)
_KNOWN_HRPS: dict[str, dict[str, str]] = {
    "cosmos": {
        "lcd": "https://cosmos-api.polkachu.com",
        "symbol": "ATOM",
        "name": "Cosmos Hub",
        "coingecko_id": "cosmos",
        "explorer_prefix": "cosmos",
    },
    "osmo": {
        "lcd": "https://osmosis-api.polkachu.com",
        "symbol": "OSMO",
        "name": "Osmosis",
        "coingecko_id": "osmosis",
        "explorer_prefix": "osmosis",
    },
    "celestia": {
        "lcd": "https://celestia-api.polkachu.com",
        "symbol": "TIA",
        "name": "Celestia",
        "coingecko_id": "celestia",
        "explorer_prefix": "celestia",
    },
    "juno": {
        "lcd": "https://juno-api.polkachu.com",
        "symbol": "JUNO",
        "name": "Juno",
        "coingecko_id": "juno-network",
        "explorer_prefix": "juno",
    },
    "stars": {
        "lcd": "https://stargaze-api.polkachu.com",
        "symbol": "STARS",
        "name": "Stargaze",
        "coingecko_id": "stargaze",
        "explorer_prefix": "stargaze",
    },
    "akash": {
        "lcd": "https://akash-api.polkachu.com",
        "symbol": "AKT",
        "name": "Akash",
        "coingecko_id": "akash-network",
        "explorer_prefix": "akash",
    },
    "inj": {
        "lcd": "https://injective-api.polkachu.com",
        "symbol": "INJ",
        "name": "Injective",
        "coingecko_id": "injective-protocol",
        "explorer_prefix": "injective",
    },
    "kujira": {
        "lcd": "https://kujira-api.polkachu.com",
        "symbol": "KUJI",
        "name": "Kujira",
        "coingecko_id": "kujira",
        "explorer_prefix": "kujira",
    },
    "stride": {
        "lcd": "https://stride-api.polkachu.com",
        "symbol": "STRD",
        "name": "Stride",
        "coingecko_id": "stride",
        "explorer_prefix": "stride",
    },
}

# Recognized HRPs (lowercase) — built from _KNOWN_HRPS
_RECOGNIZED_HRPS: set[str] = set(_KNOWN_HRPS.keys())

# Pattern: known HRP + "1" + 38 bech32 chars + optional longer = Cosmos address
# We match known HRP "1" then 38-52 bech32 chars (38 is the minimum Cosmos data part)
_COSMOS_RE = re.compile(
    r"^(cosmos|osmo|celestia|juno|stars|akash|inj|kujira|stride)"
    r"1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{38,}$",
    re.IGNORECASE,
)

# BTC bech32 MUST be rejected — bc1 prefix is NOT Cosmos
_BTC_BECH32_RE = re.compile(r"^bc1", re.IGNORECASE)
# EVM MUST be rejected
_EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def _identify_hrp(address: str) -> str | None:
    """Extract the HRP from a bech32 Cosmos address.

    Returns the lowercased HRP if recognized, or None.
    """
    a = address.strip()
    m = _COSMOS_RE.match(a)
    if not m:
        return None
    return m.group(1).lower()


def _hrp_info(hrp: str) -> dict[str, str] | None:
    """Return the chain info dict for a recognized HRP."""
    return _KNOWN_HRPS.get(hrp.lower())


# ═══════════════════════════════════════════════════════════════════════
# LCD REST helpers
# ═══════════════════════════════════════════════════════════════════════

REQUEST_TIMEOUT = 20.0


async def _lcd_get(lcd_base: str, path: str) -> dict | list | None:
    """GET from a Cosmos LCD endpoint, return parsed JSON or None on any error."""
    url = f"{lcd_base}{path}"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.get(url)
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 429:
            logger.debug("Cosmos LCD 429 rate-limited: %s", path[:60])
        else:
            logger.debug("Cosmos LCD HTTP %d for %s", r.status_code, path[:60])
    except Exception as e:
        logger.debug("Cosmos LCD error for %s: %s", path[:60], e)
    return None


async def _get_balances(lcd_base: str, address: str) -> list[dict]:
    """Return native coin balances via /cosmos/bank/v1beta1/balances/{addr}.

    Returns list of {denom, amount} where amount is in base units (e.g. uatom).
    """
    data = await _lcd_get(lcd_base, f"/cosmos/bank/v1beta1/balances/{address}")
    if not isinstance(data, dict):
        return []
    balances = data.get("balances", [])
    if not isinstance(balances, list):
        return []
    result: list[dict] = []
    for b in balances:
        denom = (b.get("denom") or "").strip()
        amt_str = (b.get("amount") or "0").strip()
        try:
            amt = float(amt_str)
        except (ValueError, TypeError):
            amt = 0.0
        if denom and amt > 0:
            result.append({"denom": denom, "amount": amt})
    return result


async def _get_delegations(lcd_base: str, address: str) -> list[dict]:
    """Return staking delegations via /cosmos/staking/v1beta1/delegations/{addr}.

    Returns list of {validator, denom, amount}.
    """
    data = await _lcd_get(
        lcd_base, f"/cosmos/staking/v1beta1/delegations/{address}"
    )
    if not isinstance(data, dict):
        return []
    delegations = data.get("delegation_responses", data.get("delegations", []))
    if not isinstance(delegations, list):
        return []
    result: list[dict] = []
    for d in delegations:
        # delegation_responses wrap: {delegation: {validator_address, shares}, balance: {denom, amount}}
        delegation = d.get("delegation", d)
        balance = d.get("balance", delegation)
        validator = delegation.get("validator_address", "")
        denom = (balance.get("denom") or "").strip()
        amt_str = (balance.get("amount") or "0").strip()
        try:
            amt = float(amt_str)
        except (ValueError, TypeError):
            amt = 0.0
        if denom and amt > 0:
            result.append({"validator": validator, "denom": denom, "amount": amt})
    return result


async def _get_rewards(lcd_base: str, address: str) -> list[dict]:
    """Return staking rewards via /cosmos/distribution/v1beta1/delegators/{addr}/rewards.

    Returns list of {denom, amount} totaling all pending rewards.
    """
    data = await _lcd_get(
        lcd_base,
        f"/cosmos/distribution/v1beta1/delegators/{address}/rewards",
    )
    if not isinstance(data, dict):
        return []
    total_list = data.get("total", [])
    if not isinstance(total_list, list):
        return []
    result: list[dict] = []
    for r in total_list:
        denom = (r.get("denom") or "").strip()
        amt_str = (r.get("amount") or "0").strip()
        # Truncate decimal part (rewards are integers in base units)
        if "." in amt_str:
            amt_str = amt_str.split(".")[0]
        try:
            amt = float(amt_str)
        except (ValueError, TypeError):
            amt = 0.0
        if denom and amt > 0:
            result.append({"denom": denom, "amount": amt})
    return result


# ═══════════════════════════════════════════════════════════════════════
# Price helpers (DefiLlama, free, no key)
# ═══════════════════════════════════════════════════════════════════════


async def _get_cosmos_price_usd(coingecko_id: str) -> float | None:
    """Get token price via DefiLlama (free, no key)."""
    url = f"https://coins.llama.fi/prices/current/coingecko:{coingecko_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url)
        if r.status_code == 200:
            data = r.json()
            coin_data = data.get("coins", {}).get(
                f"coingecko:{coingecko_id}", {}
            )
            price = coin_data.get("price")
            if price and float(price) > 0:
                return float(price)
    except Exception as e:
        logger.debug(
            "DefiLlama price error for %s: %s", coingecko_id, e
        )
    return None


def _denom_to_symbol(denom: str) -> tuple[str, int]:
    """Convert a Cosmos denom to (symbol, exponent).

    E.g. "uatom" → ("ATOM", 6), "uosmo" → ("OSMO", 6).
    For unknown denoms starting with "u", strip the "u" and use exponent 6.
    Otherwise return the denom as-is with exponent 0.
    """
    known: dict[str, tuple[str, int]] = {
        "uatom": ("ATOM", 6),
        "uosmo": ("OSMO", 6),
        "utia": ("TIA", 6),
        "ujuno": ("JUNO", 6),
        "ustars": ("STARS", 6),
        "uakt": ("AKT", 6),
        "uinj": ("INJ", 18),  # Injective uses 18 decimals
        "ukuji": ("KUJI", 6),
        "ustrd": ("STRD", 6),
    }
    if denom in known:
        return known[denom]
    if denom.startswith("u") and len(denom) > 1:
        return (denom[1:].upper(), 6)
    return (denom.upper(), 0)


# ═══════════════════════════════════════════════════════════════════════
# Transaction parsing helpers
# ═══════════════════════════════════════════════════════════════════════


def _parse_cosmos_tx(
    address: str,
    tx_data: dict,
    hrp: str,
    display_chain: str,
    native_symbol: str,
    explorer_prefix: str,
) -> dict[str, Any] | None:
    """Parse a single Cosmos LCD tx_response into a standard event dict.

    Returns None if the transaction has no meaningful transfers for this
    address (no MsgSend/MsgDelegate/MsgUndelegate involving this address).
    """
    from datetime import datetime, timezone

    txhash = tx_data.get("txhash", "")
    if not txhash:
        return None

    # Timestamp
    timestamp = tx_data.get("timestamp", "")
    block_time = ""
    if timestamp:
        if timestamp.endswith("Z"):
            block_time = timestamp
        else:
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                block_time = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except (ValueError, TypeError):
                block_time = timestamp[:19] + "Z" if len(timestamp) >= 19 else ""

    # ── Parse messages ──────────────────────────────────────────
    tx_body = tx_data.get("tx", {})
    body = tx_body.get("body", {})
    messages = body.get("messages", [])
    if not isinstance(messages, list):
        messages = []

    # ── Collect sent and received token legs ────────────────────
    sent_tokens: list[dict] = []
    recv_tokens: list[dict] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        msg_type = msg.get("@type", "")

        if "MsgSend" in msg_type:
            _from = msg.get("from_address", "")
            _to = msg.get("to_address", "")
            amounts = msg.get("amount", [])
            if not isinstance(amounts, list):
                amounts = [amounts] if isinstance(amounts, dict) else []

            for amt in amounts:
                if not isinstance(amt, dict):
                    continue
                denom = amt.get("denom", "")
                raw_amt_str = amt.get("amount", "0")
                try:
                    raw_amt = float(raw_amt_str)
                except (ValueError, TypeError):
                    raw_amt = 0.0
                if raw_amt <= 0:
                    continue

                sym, exp = _denom_to_symbol(denom)
                divisor = 10**exp
                human_amt = raw_amt / divisor

                leg = {
                    "symbol": sym,
                    "name": f"{sym} ({denom})",
                    "amount": round(human_amt, 6),
                    "usd_price": 0.0,
                    "usd_value": 0.0,
                    "contract": denom,
                }

                if _from == address:
                    sent_tokens.append(leg)
                elif _to == address:
                    recv_tokens.append(leg)

        elif "MsgDelegate" in msg_type:
            delegator = msg.get("delegator_address", "")
            amt = msg.get("amount", {})
            if isinstance(amt, dict) and delegator == address:
                denom = amt.get("denom", "")
                raw_amt_str = amt.get("amount", "0")
                try:
                    raw_amt = float(raw_amt_str)
                except (ValueError, TypeError):
                    raw_amt = 0.0
                if raw_amt > 0:
                    sym, exp = _denom_to_symbol(denom)
                    divisor = 10**exp
                    human_amt = raw_amt / divisor
                    sent_tokens.append({
                        "symbol": f"staked_{sym}",
                        "name": f"Delegate {sym}",
                        "amount": round(human_amt, 6),
                        "usd_price": 0.0,
                        "usd_value": 0.0,
                        "contract": denom,
                    })

        elif "MsgUndelegate" in msg_type:
            delegator = msg.get("delegator_address", "")
            amt = msg.get("amount", {})
            if isinstance(amt, dict) and delegator == address:
                denom = amt.get("denom", "")
                raw_amt_str = amt.get("amount", "0")
                try:
                    raw_amt = float(raw_amt_str)
                except (ValueError, TypeError):
                    raw_amt = 0.0
                if raw_amt > 0:
                    sym, exp = _denom_to_symbol(denom)
                    divisor = 10**exp
                    human_amt = raw_amt / divisor
                    recv_tokens.append({
                        "symbol": f"unstaked_{sym}",
                        "name": f"Undelegate {sym}",
                        "amount": round(human_amt, 6),
                        "usd_price": 0.0,
                        "usd_value": 0.0,
                        "contract": denom,
                    })

    # ── Determine event type ────────────────────────────────────
    has_out = len(sent_tokens) > 0
    has_in = len(recv_tokens) > 0

    if not has_out and not has_in:
        return None

    ev_type: str
    ev_direction: str
    if has_out and has_in:
        ev_type = "swap"
        ev_direction = "swap"
    elif has_out:
        ev_type = "send"
        ev_direction = "out"
    else:
        ev_type = "receive"
        ev_direction = "in"

    # ── Main legs (largest by amount) ───────────────────────────
    def _best_leg(legs: list[dict]) -> dict | None:
        best = None
        for leg in legs:
            if best is None or leg["amount"] > best["amount"]:
                best = leg
        return best

    main_sent = _best_leg(sent_tokens)
    main_recv = _best_leg(recv_tokens)

    sent_symbol = main_sent["symbol"] if main_sent else None
    sent_amount = round(main_sent["amount"], 6) if main_sent else 0.0
    recv_symbol = main_recv["symbol"] if main_recv else None
    recv_amount = round(main_recv["amount"], 6) if main_recv else 0.0

    # ── usd_value for the event ──────────────────────────────────
    if ev_type == "swap":
        out_usd = sum(t["usd_value"] for t in sent_tokens)
        in_usd = sum(t["usd_value"] for t in recv_tokens)
        ev_usd = round(max(out_usd, in_usd), 2)
    elif ev_type == "send":
        ev_usd = round(sum(t["usd_value"] for t in sent_tokens), 2)
    else:
        ev_usd = round(sum(t["usd_value"] for t in recv_tokens), 2)

    # ── Token symbol summary ─────────────────────────────────────
    if ev_type == "swap":
        ss = (main_sent["symbol"] if main_sent else "?")
        rs = (main_recv["symbol"] if main_recv else "?")
        token_symbol = f"{ss} → {rs}"
        usd_price = None
    else:
        main = main_sent or main_recv
        token_symbol = main["symbol"] if main else "?"
        usd_price = main["usd_price"] if main else 0.0

    # ── Gas fee USD (0 — no price lookup in tx parser) ─────────
    gas_fee_usd = 0.0

    # ── Sent/Received dicts (matching Solana provider shape) ────
    sent_dict: dict = (
        {
            "symbol": main_sent["symbol"],
            "name": main_sent["name"],
            "amount": main_sent["amount"],
            "usd_price": main_sent["usd_price"],
            "usd_value": main_sent["usd_value"],
            "contract": main_sent["contract"],
        }
        if main_sent
        else {"symbol": native_symbol, "name": native_symbol, "amount": 0.0, "usd_price": 0.0, "usd_value": 0.0, "contract": ""}
    )
    recv_dict: dict = (
        {
            "symbol": main_recv["symbol"],
            "name": main_recv["name"],
            "amount": main_recv["amount"],
            "usd_price": main_recv["usd_price"],
            "usd_value": main_recv["usd_value"],
            "contract": main_recv["contract"],
        }
        if main_recv
        else {"symbol": native_symbol, "name": native_symbol, "amount": 0.0, "usd_price": 0.0, "usd_value": 0.0, "contract": ""}
    )

    # ── Root-level amount for UI consistency ─────────────────────
    if ev_type == "send":
        amount = sent_amount
    elif ev_type == "receive":
        amount = recv_amount
    else:  # swap
        amount = sent_amount

    return {
        "type": ev_type,
        "direction": ev_direction,
        "tx_hash": txhash,
        "block_time": block_time,
        "token_symbol": token_symbol,
        "token_name": native_symbol,
        "chain": display_chain,
        "amount": amount,
        "usd_value": ev_usd,
        "usd_price": usd_price,
        "sent": sent_dict,
        "sent_symbol": sent_symbol,
        "sent_amount": sent_amount,
        "received": recv_dict,
        "recv_symbol": recv_symbol,
        "recv_amount": recv_amount,
        "legs": len(sent_tokens) + len(recv_tokens),
        "gas_fee_usd": gas_fee_usd,
        "wallet_address": address,
        "log_index": 0,
    }


# ═══════════════════════════════════════════════════════════════════════
# CosmosProvider
# ═══════════════════════════════════════════════════════════════════════


class CosmosProvider(ChainProvider):
    """Cosmos/ATOM ecosystem portfolio provider via public LCD REST."""

    chain_type = "cosmos"
    native_symbol = "ATOM"  # default; overridden per-address by HRP

    # ── Detection ────────────────────────────────────────────────

    def detect(self, address: str) -> bool:
        """Return True for recognized Cosmos bech32 addresses.

        Conservative: only matches known HRPs, rejects Bitcoin bc1 and EVM.
        """
        a = address.strip()
        if not a:
            return False
        # Reject EVM
        if _EVM_RE.match(a):
            return False
        # Reject BTC bech32 (bc1...)
        if _BTC_BECH32_RE.match(a):
            return False
        # Match known Cosmos HRPs
        return bool(_COSMOS_RE.match(a))

    # ── Portfolio ────────────────────────────────────────────────

    async def get_portfolio(self, address: str) -> dict:
        """Return Cosmos portfolio in the standard shape.

        Fetches available balance, staking delegations, and pending rewards
        from public LCD REST endpoints.  Prices via DefiLlama.
        Defensive: each LCD call is independent; one failing won't block others.
        """
        hrp = _identify_hrp(address)
        if hrp is None:
            return {
                "supported": False,
                "chain_type": self.chain_type,
                "message": "Adresse Cosmos non reconnue",
            }

        info = _hrp_info(hrp)
        if info is None:
            return {
                "supported": False,
                "chain_type": self.chain_type,
                "message": f"HRP {hrp} non supporte",
            }

        lcd = info["lcd"]
        native_sym = info["symbol"]
        native_name = info["name"]
        cg_id = info["coingecko_id"]

        # Fetch balance, delegations, rewards, and price in parallel
        balances, delegations, rewards, price = await asyncio.gather(
            _get_balances(lcd, address),
            _get_delegations(lcd, address),
            _get_rewards(lcd, address),
            _get_cosmos_price_usd(cg_id),
        )

        price = price or 0.0
        errors: list[str] = []

        # ── Aggregate by denom ──────────────────────────────────
        # balances: available
        # delegations: staked
        # rewards: pending

        denoms: dict[str, dict[str, float]] = {}  # denom → {avail, staked, reward}

        for b in balances:
            d = b["denom"]
            if d not in denoms:
                denoms[d] = {"avail": 0.0, "staked": 0.0, "reward": 0.0}
            denoms[d]["avail"] += b["amount"]

        for d in delegations:
            dn = d["denom"]
            if dn not in denoms:
                denoms[dn] = {"avail": 0.0, "staked": 0.0, "reward": 0.0}
            denoms[dn]["staked"] += d["amount"]

        for r in rewards:
            dn = r["denom"]
            if dn not in denoms:
                denoms[dn] = {"avail": 0.0, "staked": 0.0, "reward": 0.0}
            denoms[dn]["reward"] += r["amount"]

        # ── Build tokens ─────────────────────────────────────────
        tokens: list[dict[str, Any]] = []

        for denom, amts in denoms.items():
            sym, exp = _denom_to_symbol(denom)
            divisor = 10**exp
            avail = amts["avail"] / divisor
            staked = amts["staked"] / divisor
            reward = amts["reward"] / divisor

            # Use the same price for the native denom; unknown denoms get 0
            is_native = sym.upper() == native_sym.upper()
            token_price = price if is_native else 0.0

            # Available balance token
            avail_usd = round(avail * token_price, 2)
            if avail > 0:
                tokens.append({
                    "symbol": sym,
                    "name": native_name if is_native else sym,
                    "chain": f"cosmos-{hrp}",
                    "balance": round(avail, 6),
                    "usd_price": token_price,
                    "usd_value": avail_usd,
                    "category": "wallet",
                    "contract_address": "",
                    "enabled": True,
                    "price_unknown": token_price <= 0 and not is_native,
                })

            # Staked token — separate entry with category "staked"
            staked_usd_val = round(staked * token_price, 2)
            if staked > 0:
                tokens.append({
                    "symbol": f"staked_{sym}",
                    "name": f"Staked {native_name}" if is_native else f"Staked {sym}",
                    "chain": f"cosmos-{hrp}",
                    "balance": round(staked, 6),
                    "usd_price": token_price,
                    "usd_value": staked_usd_val,
                    "category": "staked",
                    "contract_address": "",
                    "enabled": True,
                    "price_unknown": token_price <= 0,
                })

            # Rewards token — small, separate entry
            reward_usd_val = round(reward * token_price, 2)
            if reward > 0:
                tokens.append({
                    "symbol": f"rewards_{sym}",
                    "name": f"Rewards {native_name}" if is_native else f"Rewards {sym}",
                    "chain": f"cosmos-{hrp}",
                    "balance": round(reward, 6),
                    "usd_price": token_price,
                    "usd_value": reward_usd_val,
                    "category": "rewards",
                    "contract_address": "",
                    "enabled": True,
                    "price_unknown": token_price <= 0,
                })

        # ── Compute aggregates ───────────────────────────────────
        total_usd = round(sum(t["usd_value"] for t in tokens), 2)
        staked_usd = round(
            sum(t["usd_value"] for t in tokens if t["category"] == "staked"), 2
        )
        rewards_usd = round(
            sum(t["usd_value"] for t in tokens if t["category"] == "rewards"), 2
        )
        active_tokens = [t for t in tokens if t["balance"] > 0]

        chains: dict[str, float] = {}
        if total_usd > 0:
            chains[f"cosmos-{hrp}"] = total_usd

        return {
            "address": address,
            "total_usd": total_usd,
            "token_count": len(active_tokens),
            "chain_count": 1 if total_usd > 0 else 0,
            "chains": chains,
            "tokens": active_tokens,
            "errors": errors,
            "defi_usd": 0,
            "staked_usd": staked_usd,
            "defi_breakdown": {},
            "active_count": len(active_tokens),
            "inactive_count": 0,
            "rewards_usd": rewards_usd,
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
        """Return Cosmos transaction events via public LCD REST.

        Fetches outgoing (message.sender) and incoming (transfer.recipient)
        transactions, parses MsgSend/MsgDelegate/MsgWithdrawDelegatorReward,
        and builds standard events. Best-effort: one failed API call or tx
        parse never blocks the others.
        """
        hrp = _identify_hrp(address)
        if hrp is None:
            return {"total": 0, "items": [], "counts": {"send": 0, "receive": 0, "swap": 0}}

        info = _hrp_info(hrp)
        if info is None:
            return {"total": 0, "items": [], "counts": {"send": 0, "receive": 0, "swap": 0}}

        lcd = info["lcd"]
        native_sym = info["symbol"]
        cg_id = info["coingecko_id"]
        explorer_prefix = info["explorer_prefix"]
        display_chain = f"cosmos:{hrp}"

        # ── Fetch outgoing + incoming tx hashes in parallel ───────
        import urllib.parse

        async def _fetch_tx_page(query: str, lim: int = 25) -> list[dict]:
            """Fetch one page of txs matching query, return tx_responses list."""
            q_enc = urllib.parse.quote(query, safe="")
            path = (
                f"/cosmos/tx/v1beta1/txs?"
                f"query={q_enc}&order_by=ORDER_BY_DESC&pagination.limit={lim}"
            )
            data = await _lcd_get(lcd, path)
            if not isinstance(data, dict):
                return []
            txns = data.get("tx_responses", data.get("txs", []))
            return txns if isinstance(txns, list) else []

        out_raw, in_raw = await asyncio.gather(
            _fetch_tx_page(f"message.sender='{address}'"),
            _fetch_tx_page(f"transfer.recipient='{address}'"),
        )

        # ── Dedup by txhash; tag each with its query source ───────
        seen: dict[str, dict] = {}
        for t in out_raw:
            h = t.get("txhash", "")
            if h:
                seen[h] = t
        for t in in_raw:
            h = t.get("txhash", "")
            if h and h not in seen:
                seen[h] = t

        all_txs = list(seen.values())

        # ── Parse each tx into events ──────────────────────────────
        events: list[dict[str, Any]] = []
        for tx_data in all_txs:
            try:
                ev = _parse_cosmos_tx(
                    address=address,
                    tx_data=tx_data,
                    hrp=hrp,
                    display_chain=display_chain,
                    native_symbol=native_sym,
                    explorer_prefix=explorer_prefix,
                )
                if ev is not None:
                    events.append(ev)
            except Exception:
                logger.debug("Failed to parse Cosmos tx for %s", address[:12])
                continue

        # ── Sort by block_time DESC (newest first) ─────────────────
        events.sort(key=lambda e: e.get("block_time", ""), reverse=True)

        # ── Apply filters ──────────────────────────────────────────
        if direction:
            events = [e for e in events if e.get("direction") == direction]
        if event_type:
            events = [e for e in events if e.get("type") == event_type]
        if token:
            tlow = token.lower()
            events = [e for e in events
                      if e.get("token_symbol", "").lower() == tlow
                      or tlow in (e.get("sent_symbol", "").lower(), e.get("recv_symbol", "").lower())]
        hrp_chain = f"cosmos:{hrp}"
        if chain and chain.lower() != hrp_chain:
            events = []

        # ── Counts ─────────────────────────────────────────────────
        counts: dict[str, int] = {"send": 0, "receive": 0, "swap": 0}
        for ev in events:
            t = ev.get("type", "")
            counts[t] = counts.get(t, 0) + 1

        total = len(events)
        page = events[offset : offset + limit]
        return {"total": total, "items": page, "counts": counts}

    # ── Explorer URLs ────────────────────────────────────────────

    def explorer_url(self, address: str) -> str | None:
        """Return Mintscan explorer URL for the address."""
        hrp = _identify_hrp(address)
        if hrp is None:
            return None
        info = _hrp_info(hrp)
        if info is None:
            return None
        prefix = info["explorer_prefix"]
        return f"https://www.mintscan.io/{prefix}/address/{address}"

    def explorer_tx_url(self, tx_hash: str) -> str | None:
        """Return Mintscan transaction URL."""
        # Try to resolve chain from tx hash via address HRP if known, else default
        return f"https://www.mintscan.io/cosmos/tx/{tx_hash}"


# ── Auto-register ──────────────────────────────────────────────────
cosmos_provider = CosmosProvider()
register_provider(cosmos_provider)
