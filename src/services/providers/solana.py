"""
SolanaProvider — Solana portfolio via public JSON-RPC (free, no key).

Implements ChainProvider for Solana addresses: base58-encoded 32-byte
public keys.  Uses api.mainnet-beta.solana.com for balance/token-accounts
and DefiLlama for SOL and SPL token prices.

2026.07.23 — Phase 2 Solana support.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from services.providers.base import ChainProvider, register_provider, logger

# ═══════════════════════════════════════════════════════════════════════
# Minimal Base58 decoder (stdlib only — no external dependency)
# ═══════════════════════════════════════════════════════════════════════

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX: dict[str, int] = {c: i for i, c in enumerate(_B58_ALPHABET)}

# Patterns that are definitely NOT Solana
_EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_BTC_BECH32_RE = re.compile(r"^bc1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{38,}$", re.IGNORECASE)
_COSMOS_LIKE_RE = re.compile(r"^(cosmos|osmosis|neutron|stargaze|juno|injective|terra|kava|secret|akash|shentu|cryptoorg|persistence|iris|regen|sentinel|sommelier|stride|evmos|axelar|noble|celestia|dydx|saga|initia|migaloo|omniflix|quicksilver|umee|gravitybridge|mars|comdex|chihuahua|cheqd|bitsong|likecoin|band|echelon|empower|gitopia|kyve|lum|pylons|tgrade|aura|beezee|bluzelle|c4e|carbon|cerberus|chronic|crescent|decentr|desmos|dig|emoney|fetchai|firma|galaxy|genesisl1|govgen|hedge|impacthub|imversed|jackal|kava|kichain|konstellation|lambda|logos|lorenzo|medibloc|meme|microtick|mises|neta|nibiru|noble|nolus|odin|onomy|panacea|passage|planq|pstake|realionetwork|rizon|sge|shareledger|sifchain|stafihub|starname|teritori|tgrade|umma|vidulum|wemix|zeta|kyve)\d[a-z0-9]{38,}$", re.IGNORECASE)


def _base58_decode(b58: str) -> bytes | None:
    """Decode a base58 string to bytes. Returns None on invalid input."""
    if not b58:
        return None
    # Count leading '1's (each represents a leading zero byte)
    leading_ones = 0
    for ch in b58:
        if ch == "1":
            leading_ones += 1
        else:
            break
    # Convert
    acc = 0
    for ch in b58:
        idx = _B58_INDEX.get(ch)
        if idx is None:
            return None  # Invalid character
        acc = acc * 58 + idx
    if acc == 0:
        return b"\x00" * leading_ones
    # Convert integer to bytes
    result = bytearray()
    while acc > 0:
        acc, mod = divmod(acc, 256)
        result.insert(0, mod)
    return b"\x00" * leading_ones + bytes(result)


# ═══════════════════════════════════════════════════════════════════════
# Detection
# ═══════════════════════════════════════════════════════════════════════

_SOLANA_B58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _is_solana_address(address: str) -> bool:
    """Return True if address looks like a Solana public key.

    Conservative: rejects EVM (0x...), BTC bech32 (bc1...),
    Cosmos-like (prefix + 1 + ...), and decodes base58 → exactly 32 bytes.
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
    # Reject Cosmos-like (cosmos1..., osmosis1..., etc.)
    if _COSMOS_LIKE_RE.match(a):
        return False
    # Must be base58 (32-44 chars is the valid Solana address range)
    if not (32 <= len(a) <= 44):
        return False
    if not _SOLANA_B58_RE.match(a):
        return False
    # Must decode to exactly 32 bytes
    try:
        decoded = _base58_decode(a)
        return decoded is not None and len(decoded) == 32
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════
# Solana JSON-RPC helpers
# ═══════════════════════════════════════════════════════════════════════

SOLANA_RPC = "https://api.mainnet-beta.solana.com"
REQUEST_TIMEOUT = 20.0
# Token Program ID (official SPL Token program)
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


async def _solana_rpc(method: str, params: list) -> dict | None:
    """Call Solana JSON-RPC, return result or None on any error."""
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.post(SOLANA_RPC, json=body)
        if r.status_code == 200:
            data = r.json()
            if "error" in data:
                logger.debug(
                    "Solana RPC error %s: %s", method, data["error"]
                )
                return None
            return data
        elif r.status_code == 429:
            logger.debug("Solana RPC 429 rate-limited for %s", method)
        else:
            logger.debug("Solana RPC HTTP %d for %s", r.status_code, method)
    except Exception as e:
        logger.debug("Solana RPC exception for %s: %s", method, e)
    return None


async def _get_sol_balance_lamports(address: str) -> int | None:
    """Return native SOL balance in lamports, or None on failure."""
    data = await _solana_rpc("getBalance", [address])
    if data is None:
        return None
    try:
        return data["result"]["value"]
    except (KeyError, TypeError):
        return None


async def _get_spl_accounts(address: str) -> list[dict]:
    """Return non-zero SPL token accounts for address.

    Returns list of {mint, amount, decimals, uiAmount} per account.
    """
    data = await _solana_rpc(
        "getTokenAccountsByOwner",
        [
            address,
            {"programId": TOKEN_PROGRAM_ID},
            {"encoding": "jsonParsed"},
        ],
    )
    if data is None:
        return []
    accounts = []
    try:
        for item in data.get("result", {}).get("value", []):
            info = (
                item.get("account", {})
                .get("data", {})
                .get("parsed", {})
                .get("info", {})
            )
            amount_raw = info.get("tokenAmount", {})
            ui_amount = amount_raw.get("uiAmount") or 0.0
            if ui_amount <= 0:
                continue
            accounts.append(
                {
                    "mint": info.get("mint", ""),
                    "amount": float(amount_raw.get("amount", "0")),
                    "decimals": amount_raw.get("decimals", 0),
                    "uiAmount": float(ui_amount),
                }
            )
    except Exception as e:
        logger.debug("Error parsing SPL accounts for %s: %s", address[:12], e)
    return accounts


async def _get_sol_price_usd() -> float | None:
    """Get SOL/USD price via DefiLlama (free, no key)."""
    url = "https://coins.llama.fi/prices/current/coingecko:solana"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url)
        if r.status_code == 200:
            data = r.json()
            sol_data = data.get("coins", {}).get("coingecko:solana", {})
            price = sol_data.get("price")
            if price:
                return float(price)
    except Exception as e:
        logger.debug("DefiLlama SOL price error: %s", e)
    return None


async def _get_spl_prices(mints: list[str]) -> dict[str, float]:
    """Batch-fetch SPL token prices via DefiLlama (best-effort).

    Returns dict mapping mint_address → usd_price.
    """
    if not mints:
        return {}
    # Batch up to 50 mints at a time (DefiLlama free tier)
    prices: dict[str, float] = {}
    chunk_size = 50
    for i in range(0, len(mints), chunk_size):
        batch = mints[i : i + chunk_size]
        addrs = ",".join(f"solana:{m.lower()}" for m in batch)
        url = f"https://coins.llama.fi/prices/current/{addrs}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                coins = data.get("coins", {})
                for mint in batch:
                    key = f"solana:{mint.lower()}"
                    coin = coins.get(key, {})
                    p = coin.get("price")
                    if p:
                        prices[mint] = float(p)
        except Exception as e:
            logger.debug("DefiLlama SPL price error: %s", e)
    return prices


# ═══════════════════════════════════════════════════════════════════════
# Known SPL token symbols (common tokens on Solana)
# ═══════════════════════════════════════════════════════════════════════

_KNOWN_SPL: dict[str, tuple[str, str]] = {
    # mint → (symbol, name)
    "So11111111111111111111111111111111111111112": ("SOL", "Wrapped SOL"),
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": ("USDC", "USD Coin"),
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": ("USDT", "Tether USD"),
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": ("BONK", "Bonk"),
    "7i5KKsX2weiTkry7jA4ZwSuXGhs5eJBEjY8vVxR4pfRx": ("JTO", "Jito"),
    "jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v": ("JupSOL", "Jupiter Staked SOL"),
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": ("mSOL", "Marinade Staked SOL"),
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1": ("bSOL", "Blaze Staked SOL"),
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": ("JitoSOL", "Jito Staked SOL"),
    "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj": ("stSOL", "Lido Staked SOL"),
    "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3": ("PYTH", "Pyth Network"),
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm": ("WIF", "Dogwifhat"),
    "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN": ("POPCAT", "Popcat"),
    "3S8qX1MsMqRbiwKg2cQyx7nis1oHMgaCuc9c4VfvVdPN": ("MOODENG", "Moo Deng"),
    "CzLSujWBLFsSjncfkh59rUFqvafWcY5tzedWJSuypump": ("GOAT", "Goatseus Maximus"),
    "2weMjPLLybRMMva1fM3U31goWWrCpF59CHWNhnCJ9Vyh": ("ORCA", "Orca"),
    "MELLd8PyFoeNW3D5VaUe7L96eZeihtrzPEfoq5V9DsR": ("MEOW", "Meow"),
    "hntyVP6YFm1Hg25TN9WGLqM12b8TQmcknHduZ7m5g5h": ("HNT", "Helium"),
    "iotEVVZLEywoTn1QdwNPddxPWszn3zFhEot3MfL9fns": ("IOT", "Helium IOT"),
    "DeFi6F9F9n6vNxNxNxNxNxNxNxNxNxNxNxNxNxNxNx": ("DEFI", "DeFi Land"),
    "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE": ("ORCA", "Orca Governance"),
    "mPLZo4XYdP9Tkn4Xmq8s8YcAu7L7Kzj7s6KZ5nbyLqW": ("MPL", "Maple"),
    "SRMuApVNdxXokk5GT7XD5cUUgXMBCoY2Xf4Dk6Krzh7": ("SRM", "Serum"),
    "cAMAQJDJ2gJwTJG6Ezr2kLFmBfcBTiz4jB15SwYiRzx": ("CAMA", "Camino"),
    "kinXdEcpDQeHPEuQnqmUgtYykqKGVFq6CeVX5iAHJq6": ("KIN", "Kin"),
    "MAPS41MDahZ9QdKXhVa4dWB9RuyfV4XqhyAZ8XcYepb": ("MAPS", "Maps.me"),
    "zvJJ1Y3Bcyff69Ej5agbzeJ4sDg7BXA4e9yfzBtSq1r": ("ZVE", "Zeta"),
    "SHDWyBxihqiCj6YekG2GUr7wqKLeLAMK1gHZck9pL6y": ("SHDW", "Shadow"),
    "ATLASXmbPQxbuYrgnZdPNB2kNBzutxYThbwFPhQrmm8c": ("ATLAS", "Star Atlas"),
    "PoLiSdcSByaRi3YFRv38sL7LQ4Yv9d6zPckmEBW8L29": ("POLIS", "Star Atlas DAO"),
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R": ("RAY", "Raydium"),
    "RLBxxFkseAZ4RgJH3Sqn8jXxhmGoz9jWxDNJMh8pL7a": ("RLB", "Rollbit"),
    "Gz7VkD4MacbEB6yC5XD3HcumEiYx2EtDYYrfikGsvopG": ("WEN", "Wen"),
    "9aL2PGL3eUyfUJkoBHJN8aXtBBkUHvFV8GrBawB88E5s": ("DUST", "Dust Protocol"),
    "mplTokenMetadata111111111111111111111111111111111": ("MPL", "Metaplex"),
}

# Known SPL token mints by symbol (for reverse lookup)
_SPL_SYMBOLS = {info[0].lower(): mint for mint, info in _KNOWN_SPL.items()}


def _get_token_info(mint: str) -> tuple[str, str, bool]:
    """Return (symbol, name, is_known) for a mint address."""
    info = _KNOWN_SPL.get(mint)
    if info:
        return info[0], info[1], True
    # Unknown token — use truncated mint as symbol
    return (mint[:8] + "...", "Unknown SPL Token", False)



# ═══════════════════════════════════════════════════════════════════════
# Transaction fetching helpers
# ═══════════════════════════════════════════════════════════════════════


async def _get_signatures(address: str, limit: int = 25) -> list[dict]:
    """Return list of {signature, blockTime} for address, newest first."""
    data = await _solana_rpc(
        "getSignaturesForAddress",
        [address, {"limit": limit}],
    )
    if data is None:
        return []
    try:
        return data.get("result", [])
    except Exception:
        return []


async def _get_parsed_tx(signature: str) -> dict | None:
    """Fetch a single parsed transaction. Returns None on failure."""
    data = await _solana_rpc(
        "getTransaction",
        [
            signature,
            {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"},
        ],
    )
    if data is None:
        return None
    result = data.get("result")
    if not result or not isinstance(result, dict):
        return None
    return result


def _parse_solana_tx(
    address: str,
    tx: dict,
    sol_price: float | None,
    spl_prices: dict[str, float],
) -> dict[str, Any] | None:
    """Parse a jsonParsed Solana tx into a standard event dict.

    Returns None if the transaction has no meaningful transfers for this address.
    """
    from datetime import datetime, timezone

    meta = tx.get("meta", {})
    if not meta:
        return None

    block_time_ts = tx.get("blockTime")
    block_time = ""
    if block_time_ts:
        block_time = datetime.fromtimestamp(
            block_time_ts, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

    signature = tx.get("transaction", {}).get("signatures", [""])[0] if tx.get("transaction") else ""

    # Find our index in accountKeys
    message = tx.get("transaction", {}).get("message", {})
    account_keys: list = message.get("accountKeys", [])
    our_idx: int | None = None
    is_fee_payer = False
    for i, ak in enumerate(account_keys):
        pubkey = ak.get("pubkey") if isinstance(ak, dict) else str(ak)
        if pubkey == address:
            our_idx = i
            if i == 0:
                is_fee_payer = True
            break

    if our_idx is None:
        return None  # Not our transaction

    pre_balances: list[int] = meta.get("preBalances", [])
    post_balances: list[int] = meta.get("postBalances", [])
    pre_tb: list[dict] = meta.get("preTokenBalances", [])
    post_tb: list[dict] = meta.get("postTokenBalances", [])

    # ── SOL change ──────────────────────────────────────────────
    sol_change_lamports = 0
    if our_idx < len(pre_balances) and our_idx < len(post_balances):
        sol_change_lamports = post_balances[our_idx] - pre_balances[our_idx]

    # Account for fee if we are fee-payer
    fee_lamports = meta.get("fee", 0)
    if is_fee_payer:
        sol_change_lamports += fee_lamports  # fee was already subtracted in post

    sol_change = sol_change_lamports / 1_000_000_000.0

    # ── SPL token changes ───────────────────────────────────────
    # Build lookup: {mint: (pre_uiAmount, post_uiAmount, decimals)}
    def _tb_lookup(tb_list: list[dict]) -> dict[str, tuple[float, int]]:
        out: dict[str, tuple[float, int]] = {}
        for entry in tb_list:
            if entry.get("accountIndex") != our_idx:
                continue
            mint = entry.get("mint", "")
            amt = entry.get("uiTokenAmount", {})
            if mint:
                out[mint] = (
                    float(amt.get("uiAmount") or 0),
                    amt.get("decimals", 0),
                )
        return out

    pre_spl = _tb_lookup(pre_tb)
    post_spl = _tb_lookup(post_tb)

    # Merge all mints
    all_mints = set(pre_spl.keys()) | set(post_spl.keys())

    # Collect sent and received SPL token changes
    sent_tokens: list[dict] = []    # outflows
    recv_tokens: list[dict] = []    # inflows

    for mint in all_mints:
        pre_amt, _ = pre_spl.get(mint, (0.0, 0))
        post_amt, _ = post_spl.get(mint, (0.0, 0))
        delta = post_amt - pre_amt
        if abs(delta) < 0.00000001:
            continue
        sym, name, _ = _get_token_info(mint)
        price = spl_prices.get(mint, 0.0)
        usd_val = round(abs(delta) * price, 2)
        token_data = {
            "symbol": sym,
            "name": name,
            "amount": round(abs(delta), 8),
            "usd_price": price,
            "usd_value": usd_val,
            "contract": mint,
        }
        if delta < 0:
            sent_tokens.append(token_data)
        else:
            recv_tokens.append(token_data)

    # ── SOL transfer as a token leg ──────────────────────────────
    sol_sent_amount = 0.0
    sol_recv_amount = 0.0
    if abs(sol_change) >= 0.00000001:
        sol_usd = round(abs(sol_change) * (sol_price or 0), 2)
        sol_leg = {
            "symbol": "SOL",
            "name": "Solana",
            "amount": round(abs(sol_change), 9),
            "usd_price": sol_price or 0,
            "usd_value": sol_usd,
            "contract": "",
        }
        if sol_change < 0:
            sent_tokens.append(sol_leg)
            sol_sent_amount = round(abs(sol_change), 9)
        else:
            recv_tokens.append(sol_leg)
            sol_recv_amount = round(abs(sol_change), 9)

    # ── Determine event type ────────────────────────────────────
    has_out = len(sent_tokens) > 0
    has_in = len(recv_tokens) > 0

    if not has_out and not has_in:
        return None  # No transfer detected for this address

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

    # ── Main legs (largest by usd_value) ─────────────────────────
    def _best_leg(legs: list[dict]) -> dict | None:
        best = None
        for leg in legs:
            if best is None or (leg["usd_value"], leg["amount"]) > (best["usd_value"], best["amount"]):
                best = leg
        return best

    main_sent = _best_leg(sent_tokens)
    main_recv = _best_leg(recv_tokens)

    sent_symbol = main_sent["symbol"] if main_sent else None
    sent_amount = main_sent["amount"] if main_sent else 0.0
    recv_symbol = main_recv["symbol"] if main_recv else None
    recv_amount = main_recv["amount"] if main_recv else 0.0

    # ── usd_value for the event ──────────────────────────────────
    if ev_type == "swap":
        # Max of sum(out) vs sum(in) — don't double-count
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

    # ── Gas fee ─────────────────────────────────────────────────
    gas_fee_sol = fee_lamports / 1_000_000_000.0
    gas_fee_usd = round(gas_fee_sol * (sol_price or 0), 4) if is_fee_payer else 0.0

    # ── Sent/Received dicts (matching Bitcoin provider shape) ────
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
        else {"symbol": "SOL", "name": "Solana", "amount": 0.0, "usd_price": sol_price or 0, "usd_value": 0.0, "contract": ""}
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
        else {"symbol": "SOL", "name": "Solana", "amount": 0.0, "usd_price": sol_price or 0, "usd_value": 0.0, "contract": ""}
    )

    return {
        "type": ev_type,
        "direction": ev_direction,
        "tx_hash": signature,
        "block_time": block_time,
        "token_symbol": token_symbol,
        "token_name": "Solana",
        "chain": "solana",
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
# SolanaProvider
# ═══════════════════════════════════════════════════════════════════════


class SolanaProvider(ChainProvider):
    """Solana portfolio provider via public JSON-RPC."""

    chain_type = "solana"
    native_symbol = "SOL"

    # ── Detection ────────────────────────────────────────────────

    def detect(self, address: str) -> bool:
        """Return True for valid Solana base58 public keys."""
        return _is_solana_address(address)

    # ── Portfolio ────────────────────────────────────────────────

    async def get_portfolio(self, address: str) -> dict:
        """Return Solana portfolio in the standard shape.

        Fetches native SOL balance + SPL token accounts in parallel,
        then fetches prices.  Defensive: if the RPC fails, returns
        the best available data without raising.
        """
        import asyncio

        # Fetch native balance + SPL accounts in parallel
        lamports, spl_accounts = await asyncio.gather(
            _get_sol_balance_lamports(address),
            _get_spl_accounts(address),
        )

        sol_balance = (lamports or 0) / 1_000_000_000.0  # lamports → SOL

        # Fetch prices
        sol_price, spl_prices = await asyncio.gather(
            _get_sol_price_usd(),
            _get_spl_prices([a["mint"] for a in spl_accounts]) if spl_accounts else asyncio.sleep(0, result={}),
        )

        # Build tokens list
        tokens: list[dict[str, Any]] = []

        # Native SOL
        sol_usd_value = round(sol_balance * (sol_price or 0), 2)
        tokens.append(
            {
                "symbol": "SOL",
                "name": "Solana",
                "chain": "solana",
                "balance": round(sol_balance, 6),
                "usd_price": sol_price or 0.0,
                "usd_value": sol_usd_value,
                "category": "wallet",
                "contract_address": "",
                "enabled": True,
                "price_unknown": sol_price is None,
            }
        )

        # SPL tokens
        for acc in spl_accounts:
            mint = acc["mint"]
            sym, name, known = _get_token_info(mint)
            price = spl_prices.get(mint, 0.0)
            usd_val = round(acc["uiAmount"] * price, 2)
            tokens.append(
                {
                    "symbol": sym,
                    "name": f"{name} ({mint[:6]}...)" if not known else name,
                    "chain": "solana",
                    "balance": acc["uiAmount"],
                    "usd_price": price,
                    "usd_value": usd_val,
                    "category": _token_category_sol(sym),
                    "contract_address": mint,
                    "enabled": True,
                    "price_unknown": price <= 0,
                }
            )

        total_usd = round(sum(t["usd_value"] for t in tokens), 2)
        active_tokens = [t for t in tokens if t["balance"] > 0]

        chains: dict[str, float] = {}
        if total_usd > 0:
            chains["solana"] = total_usd

        return {
            "address": address,
            "total_usd": total_usd,
            "token_count": len(active_tokens),
            "chain_count": 1 if total_usd > 0 else 0,
            "chains": chains,
            "tokens": active_tokens,
            "errors": [],
            "defi_usd": 0,
            "staked_usd": 0,
            "defi_breakdown": {},
            "active_count": len(active_tokens),
            "inactive_count": 0,
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
        """Return paginated Solana transaction events via public RPC.

        Fetches recent signatures via getSignaturesForAddress, then
        parses each via getTransaction (jsonParsed).  Derives
        send/receive/swap from pre/postBalances and pre/postTokenBalances.
        Best-effort: one failed tx parse never blocks the others.
        """
        # 1. Get recent signatures
        signatures = await _get_signatures(address, limit=25)
        if not signatures:
            return {"total": 0, "items": [], "counts": {"send": 0, "receive": 0, "swap": 0}}

        # 2. Get SOL price once (used as fallback for all events)
        sol_price = await _get_sol_price_usd()

        # 3. Fetch parsed transactions (sequential to avoid rate-limit, with small delay)
        import asyncio

        tx_datas: list[dict] = []
        all_mints: set[str] = set()
        for sig_info in signatures[:22]:  # cap at ~22 parsed txs
            sig = sig_info["signature"]
            tx = await _get_parsed_tx(sig)
            if tx is not None:
                # Collect all mints from pre/post token balances
                for tb in tx.get("meta", {}).get("preTokenBalances", []):
                    m = tb.get("mint", "")
                    if m:
                        all_mints.add(m)
                for tb in tx.get("meta", {}).get("postTokenBalances", []):
                    m = tb.get("mint", "")
                    if m:
                        all_mints.add(m)
                tx_datas.append(tx)
            await asyncio.sleep(0.05)  # gentle rate-limit

        # 4. Fetch SPL prices for all mints seen in transactions
        spl_prices: dict[str, float] = {}
        if all_mints:
            spl_prices = await _get_spl_prices(list(all_mints))

        # 5. Parse each transaction into events
        from datetime import datetime, timezone

        events: list[dict[str, Any]] = []
        for tx in tx_datas:
            try:
                ev = _parse_solana_tx(address, tx, sol_price, spl_prices)
                if ev is not None:
                    events.append(ev)
            except Exception:
                logger.debug("Failed to parse Solana tx for %s", address[:12])
                continue

        # 6. Sort by block_time DESC (newest first)
        events.sort(key=lambda e: e.get("block_time", ""), reverse=True)

        # 7. Apply filters
        if direction:
            events = [e for e in events if e["direction"] == direction]
        if event_type:
            events = [e for e in events if e["type"] == event_type]
        if token:
            tlow = token.lower()
            events = [e for e in events
                      if e.get("token_symbol", "").lower() == tlow
                      or tlow in (e.get("sent_symbol", "").lower(), e.get("recv_symbol", "").lower())]
        if chain and chain.lower() != "solana":
            events = []

        # 8. Counts
        counts: dict[str, int] = {"send": 0, "receive": 0, "swap": 0}
        for ev in events:
            counts[ev["type"]] = counts.get(ev["type"], 0) + 1

        total = len(events)
        page = events[offset : offset + limit]
        return {"total": total, "items": page, "counts": counts}

    # ── Explorer URLs ────────────────────────────────────────────

    def explorer_url(self, address: str) -> str:
        return f"https://solscan.io/account/{address}"

    def explorer_tx_url(self, tx_hash: str) -> str:
        return f"https://solscan.io/tx/{tx_hash}"


# ═══════════════════════════════════════════════════════════════════════
# Token category heuristic (Solana-specific, best-effort)
# ═══════════════════════════════════════════════════════════════════════

_STAKED_SOL_PATTERNS = {
    "jitosol", "msol", "bsol", "stsol", "jupsol", "lstsol", "dSOL",
    "scnsol", "compastsol", "ssol", "ksol", "daosol", "stsol",
    "bonksol", "vansol", "picosol", "inf", "haSOL", "edgeSOL",
    "hSOL", "cSOL", "laineSOL", "prismSOL", "strongSOL",
}


def _token_category_sol(symbol: str) -> str:
    """Best-effort token category for Solana SPL tokens."""
    s = (symbol or "").lower()
    if s in _STAKED_SOL_PATTERNS:
        return "staked"
    if s in ("usdc", "usdt", "usdc.e", "usdt.e"):
        return "wallet"  # stablecoin
    return "wallet"


# ── Auto-register ──────────────────────────────────────────────────
solana_provider = SolanaProvider()
register_provider(solana_provider)
