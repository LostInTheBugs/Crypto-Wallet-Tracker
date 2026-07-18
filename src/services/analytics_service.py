"""Analytics service — pure computation helpers for /api/analytics (2026.07.3).

Stdlib only (math, datetime) so it stays testable without FastAPI:
    python3 tests/test_analytics_service.py

Design rules (same spirit as tx_events / defi_service):
  • DEFENSIVE: bad/missing/NaN input → None or empty lists, never an exception.
  • pct values are % of the ACTIVE total (enabled tokens only — the caller
    pre-filters on enabled != False).
  • "OTHERS" is the aggregated tail of the by-asset allocation (top 12 kept);
    the frontend translates it (Autres / Others).
"""

import datetime
import math

# Category vocabulary of portfolio_service._token_category (+ "wallet")
CATEGORY_ORDER = ["wallet", "lending", "staked", "lp", "vault", "synthetic"]

TOP_ASSETS = 12                 # by-asset entries kept before "OTHERS"
MIN_PERFORMER_USD = 1.0         # ignore dust in the performers ranking

PERIODS = {"24h": 1, "7d": 7, "30d": 30}
# Max distance (days) between the wanted date and the closest history row.
# Beyond that the history is considered insufficient → None ("—" in the UI).
TOLERANCE_DAYS = {"24h": 1, "7d": 2, "30d": 5}


def _finite(v, default: float = 0.0) -> float:
    """float(v) if finite, else `default`. Accepts None/str/garbage."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _finite_or_none(v) -> "float | None":
    """float(v) if finite, else None. Accepts None/str/garbage."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def filter_active_tokens(tokens):
    """Keep only ACTIVE tokens (enabled != False; absent flag → active)."""
    out = []
    for tk in tokens or []:
        if not isinstance(tk, dict):
            continue
        if not tk.get("enabled", True):
            continue
        out.append(tk)
    return out


def build_allocation(tokens):
    """Allocation of ACTIVE tokens by chain / category / asset.

    Returns {"total_usd", "by_chain": [{key, usd_value, pct}],
             "by_category": [{key, usd_value, pct}],
             "by_asset": [{symbol, usd_value, pct}]} — lists sorted by
    usd_value desc, pct = share of the active total. Top 12 assets kept,
    the rest aggregated under symbol "OTHERS".
    """
    total = 0.0
    by_chain = {}
    by_cat = {}
    by_asset = {}
    for tk in tokens or []:
        if not isinstance(tk, dict):
            continue
        v = _finite(tk.get("usd_value") or 0)
        if v <= 0:
            continue
        total += v
        ch = tk.get("chain") or "?"
        by_chain[ch] = by_chain.get(ch, 0.0) + v
        cat = tk.get("category") or "wallet"
        if cat not in CATEGORY_ORDER:
            cat = "wallet"
        by_cat[cat] = by_cat.get(cat, 0.0) + v
        sym = str(tk.get("symbol") or "?").upper()
        by_asset[sym] = by_asset.get(sym, 0.0) + v

    def _fmt(dd, key_name):
        items = sorted(dd.items(), key=lambda x: x[1], reverse=True)
        out = []
        for k, v in items:
            pct = round(v / total * 100, 2) if total > 0 else 0.0
            out.append({key_name: k, "usd_value": round(v, 2), "pct": pct})
        return out

    assets = _fmt(by_asset, "symbol")
    if len(assets) > TOP_ASSETS:
        head = assets[:TOP_ASSETS]
        tail = assets[TOP_ASSETS:]
        head.append({
            "symbol": "OTHERS",
            "usd_value": round(sum(a["usd_value"] for a in tail), 2),
            "pct": round(sum(a["pct"] for a in tail), 2),
        })
        assets = head

    return {
        "total_usd": round(total, 2),
        "by_chain": _fmt(by_chain, "key"),
        "by_category": _fmt(by_cat, "key"),
        "by_asset": assets,
    }


def pick_closest(rows, target_date, tolerance_days):
    """Value at the date closest to `target_date` among `rows`.

    rows: iterable of (date_str "YYYY-MM-DD", value). Returns the value of the
    row minimizing |date - target| if that distance <= tolerance_days, else
    None (insufficient history). Non-finite winning value → None.
    """
    try:
        tgt = datetime.datetime.strptime(str(target_date)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    best_val = None
    best_diff = None
    for row in rows or []:
        try:
            d, v = row[0], row[1]
            dd = datetime.datetime.strptime(str(d)[:10], "%Y-%m-%d")
        except (TypeError, ValueError, IndexError):
            continue
        diff = abs((dd - tgt).days)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_val = v
    if best_diff is None or best_diff > int(tolerance_days):
        return None
    return _finite_or_none(best_val)


def compute_change_periods(agg_rows, current_value, today=None):
    """Total-value change over 24h / 7d / 30d.

    agg_rows: [(date_str, value_usd)] — ONE aggregate value per date (the
    caller SUMs daily_history aggregate rows across wallets). current_value:
    live portfolio total. Returns {"24h": {"abs_usd", "pct"} | None, ...} —
    None when the history is insufficient for that period.
    """
    out: dict = {label: None for label in PERIODS}
    cur = _finite_or_none(current_value)
    if cur is None:
        return out
    try:
        base_dt = datetime.datetime.strptime(
            (today or datetime.datetime.utcnow().strftime("%Y-%m-%d"))[:10],
            "%Y-%m-%d")
    except (TypeError, ValueError):
        return out
    for label, days in PERIODS.items():
        target = (base_dt - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        past = pick_closest(agg_rows, target, TOLERANCE_DAYS[label])
        if past is None or past <= 0:
            continue
        diff = cur - past
        pct = diff / past * 100.0
        if not (math.isfinite(diff) and math.isfinite(pct)):
            continue
        out[label] = {"abs_usd": round(diff, 2), "pct": round(pct, 2)}
    return out


def compute_performers(tokens, past_prices, top_n=5):
    """Best / worst tokens by PRICE change over the requested range.

    Price change (past cached price vs live portfolio price) neutralizes
    deposits/withdrawals by construction. tokens: ACTIVE portfolio tokens
    (spam already filtered upstream); past_prices: {symbol_lower: price_usd}.
    Dust (< MIN_PERFORMER_USD) and unpriced tokens are ignored; a symbol held
    on several chains is aggregated once (same price series).

    Returns {"best": [{symbol, usd_value, pct}]  — pct > 0, desc, top 5,
             "worst": [...]                      — pct < 0, asc,  top 5}.
    """
    agg = {}
    for tk in tokens or []:
        if not isinstance(tk, dict):
            continue
        sym = str(tk.get("symbol") or "").strip()
        if not sym:
            continue
        v = _finite(tk.get("usd_value") or 0)
        p = _finite(tk.get("usd_price") or 0)
        if v < MIN_PERFORMER_USD or p <= 0:
            continue
        low = sym.lower()
        entry = agg.setdefault(low, {"symbol": sym.upper(), "usd_value": 0.0, "price": p})
        entry["usd_value"] += v

    perf = []
    for low, entry in agg.items():
        past = _finite_or_none((past_prices or {}).get(low))
        if past is None or past <= 0:
            continue
        pct = (entry["price"] - past) / past * 100.0
        if not math.isfinite(pct):
            continue
        perf.append({
            "symbol": entry["symbol"],
            "usd_value": round(entry["usd_value"], 2),
            "pct": round(pct, 2),
        })

    best = sorted([p for p in perf if p["pct"] > 0],
                  key=lambda x: x["pct"], reverse=True)[:top_n]
    worst = sorted([p for p in perf if p["pct"] < 0],
                   key=lambda x: x["pct"])[:top_n]
    return {"best": best, "worst": worst}


def pct_from_price_points(points):
    """% change first→last of a DefiLlama price series.

    points: [{"timestamp": ..., "price": ...}] (chronological). Returns a
    rounded pct or None when the series is unusable.
    """
    if not points or len(points) < 2:
        return None
    try:
        first = _finite_or_none(points[0].get("price"))
        last = _finite_or_none(points[-1].get("price"))
    except AttributeError:
        return None
    if first is None or last is None or first <= 0:
        return None
    pct = (last - first) / first * 100.0
    return round(pct, 2) if math.isfinite(pct) else None
