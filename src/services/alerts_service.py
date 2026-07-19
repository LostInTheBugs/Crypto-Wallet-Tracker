"""
Alert evaluator, notification channels, and digest sender.
Pure service module — no FastAPI imports (stdlib + httpx + aiosqlite only).
"""
import asyncio, httpx, aiosqlite, os, datetime, time as _time, json, math, logging
from services.db import write_locked

_log = logging.getLogger("crypto.alerts")

DB_PATH = os.environ.get("DB_PATH", "/data/wallets.db")

# ── Alert evaluator ─────────────────────────────────────────────

def _check_price_alert(params: dict, current_prices: dict[str, float]) -> bool:
    """Check a price alert against current prices. Returns True if triggered."""
    symbol = (params.get("symbol") or "").lower()
    direction = params.get("direction", "above")
    threshold = float(params.get("threshold_usd") or 0)
    if not symbol or threshold <= 0:
        return False
    price = current_prices.get(symbol, 0)
    if price <= 0:
        return False
    if direction == "above":
        return price >= threshold
    else:  # below
        return price <= threshold


def _check_portfolio_alert(params: dict, total_usd: float) -> bool:
    """Check a portfolio value alert."""
    direction = params.get("direction", "above")
    threshold = float(params.get("threshold_usd") or 0)
    if threshold <= 0:
        return False
    if direction == "above":
        return total_usd >= threshold
    else:
        return total_usd <= threshold


def _check_move_alert(params: dict, change_pct: float) -> bool:
    """Check a movement alert. change_pct is the % change over the window."""
    threshold_pct = float(params.get("pct") or 0)
    return abs(change_pct) >= threshold_pct


async def _compute_current_prices_for_user(user_id: int) -> dict[str, float]:
    """Returns a dict of symbol→price for all tokens held by this user."""
    import sys, os as _os
    _abs = _os.path.dirname(_os.path.abspath(__file__))
    if _abs not in sys.path:
        sys.path.insert(0, _abs)
    from portfolio_service import _compute_portfolio

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT DISTINCT address FROM wallets WHERE user_id=?", (user_id,))
        wallets = await cur.fetchall()

    prices: dict[str, float] = {}
    for w in wallets:
        try:
            data = await _compute_portfolio(w["address"])
            for t in data.get("tokens", []):
                sym = (t.get("symbol") or "").lower()
                price = t.get("usd_price", 0) or 0
                if sym and price > 0:
                    prices[sym] = max(prices.get(sym, 0), price)
        except Exception:
            continue
    return prices


async def _compute_total_portfolio_value(user_id: int) -> float:
    """Returns total USD value across all wallets for a user."""
    import sys, os as _os
    _abs = _os.path.dirname(_os.path.abspath(__file__))
    if _abs not in sys.path:
        sys.path.insert(0, _abs)
    from portfolio_service import _compute_portfolio

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT DISTINCT address FROM wallets WHERE user_id=?", (user_id,))
        wallets = await cur.fetchall()

    total = 0.0
    for w in wallets:
        try:
            data = await _compute_portfolio(w["address"])
            total += data.get("total_usd", 0) or 0
        except Exception:
            continue
    return total


async def _compute_portfolio_change(user_id: int, window_hours: int = 24) -> float:
    """Returns % change in portfolio value over the given window in hours."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT DISTINCT address FROM wallets WHERE user_id=?", (user_id,))
        wallets = await cur.fetchall()

    now = datetime.datetime.utcnow()
    window_start = now - datetime.timedelta(hours=window_hours)
    window_str = window_start.strftime("%Y-%m-%d")

    current_total = 0.0
    prev_total = 0.0

    for w in wallets:
        addr = w["address"]
        # Current value
        cur_row = await db.execute(
            "SELECT value_usd FROM daily_history WHERE user_id=? AND lower(wallet_address)=lower(?) "
            "AND token_symbol IS NULL AND date <= ? ORDER BY date DESC LIMIT 1",
            (user_id, addr, now.strftime("%Y-%m-%d")))
        row = await cur_row.fetchone()
        if row:
            current_total += row["value_usd"] or 0

        # Previous value
        prev_row = await db.execute(
            "SELECT value_usd FROM daily_history WHERE user_id=? AND lower(wallet_address)=lower(?) "
            "AND token_symbol IS NULL AND date <= ? ORDER BY date DESC LIMIT 1",
            (user_id, addr, window_str))
        row = await prev_row.fetchone()
        if row:
            prev_total += row["value_usd"] or 0

    if prev_total <= 0:
        return 0.0
    return ((current_total - prev_total) / prev_total) * 100


# ── Notification channels ─────────────────────────────────────

async def _send_webhook(url: str, title: str, body: str) -> bool:
    """Send a webhook POST with JSON payload. Returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={"title": title, "body": body, "timestamp": datetime.datetime.utcnow().isoformat()})
            return resp.status_code < 500
    except Exception as e:
        _log.warning("Webhook send failed to %s: %s", url[:60], e)
        return False


async def _send_telegram(bot_token: str, chat_id: str, title: str, body: str) -> bool:
    """Send a Telegram message. Returns True on success."""
    try:
        text = f"*{title}*\n{body}"
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown"
            })
            return resp.status_code == 200
    except Exception as e:
        _log.warning("Telegram send failed: %s", e)
        return False


async def _send_email(
    smtp_host: str, smtp_port: int, user: str, password: str,
    from_addr: str, to_addr: str, title: str, body: str
) -> bool:
    """Send an email via SMTP (plain text). Returns True on success.
    Email is OPTIONAL — if any config field is empty, silently skip."""
    if not all([smtp_host, user, password, from_addr, to_addr]):
        return False
    try:
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg.set_content(body)
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as e:
        _log.warning("Email send failed: %s", e)
        return False


async def send_alert_notification(user_id: int, alert_id: int, title: str, body: str) -> dict:
    """Send a notification for a triggered alert. Creates in-app notification
    and dispatches to all active channels. Returns {in_app: bool, channels: {webhook: bool, ...}}"""
    import sys, os as _os
    _abs = _os.path.dirname(_os.path.abspath(__file__))
    if _abs not in sys.path:
        sys.path.insert(0, _abs)

    result = {"in_app": False, "channels": {}}

    async with aiosqlite.connect(DB_PATH) as db:
        # Create in-app notification
        async with write_locked():
            await db.execute(
                "INSERT INTO notifications (user_id, alert_id, title, body) VALUES (?, ?, ?, ?)",
                (user_id, alert_id, title, body))
            await db.commit()
        result["in_app"] = True

        # Load active channels
        cur = await db.execute(
            "SELECT channel, config_json FROM notif_channels WHERE user_id=? AND enabled=1",
            (user_id,))
        channels = await cur.fetchall()

    for ch in channels:
        channel = ch["channel"]
        try:
            config = json.loads(ch["config_json"] or "{}")
        except Exception:
            config = {}
        ok = False
        if channel == "webhook":
            url = config.get("url", "")
            if url:
                ok = await _send_webhook(url, title, body)
        elif channel == "telegram":
            bot_token = config.get("bot_token", "")
            chat_id = config.get("chat_id", "")
            if bot_token and chat_id:
                ok = await _send_telegram(bot_token, chat_id, title, body)
        elif channel == "email":
            ok = await _send_email(
                config.get("smtp_host", ""),
                int(config.get("smtp_port") or 587),
                config.get("user", ""),
                config.get("password", ""),
                config.get("from", ""),
                config.get("to", ""),
                title, body
            )
        result["channels"][channel] = ok

    return result


# ── Alert evaluator loop ──────────────────────────────────────

_evaluator_running = False
_last_evaluation_run = 0.0
_EVAL_INTERVAL = 600  # 10 minutes


async def evaluate_alerts_for_user(user_id: int) -> int:
    """Evaluate all active alerts for a single user. Returns count of triggered alerts."""
    import sys, os as _os
    _abs = _os.path.dirname(_os.path.abspath(__file__))
    if _abs not in sys.path:
        sys.path.insert(0, _abs)

    triggered = 0
    now = datetime.datetime.utcnow()
    now_ts = now.timestamp()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, type, params_json, cooldown_min, last_triggered_at FROM alerts "
            "WHERE user_id=? AND enabled=1", (user_id,))
        alerts = await cur.fetchall()

    if not alerts:
        return 0

    # Collect data needed for evaluation
    current_prices = None
    total_value = None
    move_change = None
    health_positions = None
    health_moralis_checked = False

    for al in alerts:
        alert_id = al["id"]
        alert_type = al["type"]
        cooldown = al["cooldown_min"] or 60
        last_triggered = al["last_triggered_at"] or ""

        # Check cooldown
        if last_triggered:
            try:
                last_ts = datetime.datetime.fromisoformat(last_triggered).timestamp()
                if now_ts - last_ts < cooldown * 60:
                    continue
            except Exception:
                pass

        try:
            params = json.loads(al["params_json"] or "{}")
        except Exception:
            params = {}

        triggered_now = False
        title = ""
        body = ""

        if alert_type == "price":
            if current_prices is None:
                current_prices = await _compute_current_prices_for_user(user_id)
            if _check_price_alert(params, current_prices):
                sym = (params.get("symbol") or "?").upper()
                direction = params.get("direction", "above")
                threshold = params.get("threshold_usd", 0)
                price = current_prices.get((params.get("symbol") or "").lower(), 0)
                triggered_now = True
                title = f"🚨 Alerte prix: {sym}"
                body = f"{sym} est {direction} {threshold:,.2f} USD (actuel: {price:,.2f} USD)"

        elif alert_type == "portfolio":
            if total_value is None:
                total_value = await _compute_total_portfolio_value(user_id)
            if _check_portfolio_alert(params, total_value):
                direction = params.get("direction", "above")
                threshold = params.get("threshold_usd", 0)
                triggered_now = True
                title = "🚨 Alerte portefeuille"
                body = f"Valeur du portefeuille {direction} {threshold:,.2f} USD (actuel: {total_value:,.2f} USD)"

        elif alert_type == "move":
            if move_change is None:
                move_change = await _compute_portfolio_change(user_id, 24)
            if _check_move_alert(params, move_change):
                pct = params.get("pct", 0)
                triggered_now = True
                direction_str = "hausse" if move_change > 0 else "baisse"
                title = f"🚨 Mouvement de portefeuille"
                body = f"{direction_str} de {abs(move_change):.1f}% sur 24h (seuil: {pct}%)"

        elif alert_type == "health":
            if not health_moralis_checked:
                # Check Moralis key once for all health alerts of this user
                moralis_key = await _get_moralis_key_for_user(user_id)
                if moralis_key:
                    health_positions = await _fetch_health_factors_for_user(moralis_key, user_id)
                else:
                    health_positions = []
                health_moralis_checked = True
            # Only evaluate if Moralis key is available (health_positions is non-empty list from API)
            if health_positions:
                threshold = float(params.get("threshold") or 1.2)
                scope = (params.get("scope") or "any").strip().lower()
                for pos in health_positions:
                    hf = pos.get("health_factor")
                    if hf is not None and isinstance(hf, (int, float)) and hf < threshold:
                        if scope == "any" or scope == pos.get("protocol_id", "") or scope == pos.get("protocol", "").lower():
                            triggered_now = True
                            title = "⚠️ Risque de liquidation"
                            body = (
                                f"Position {pos.get('protocol', '')} ({pos.get('chain', '')}) "
                                f"health factor {hf:.2f} < seuil {threshold}\n"
                                f"Fourni: ${pos.get('supplied_usd', 0):,.2f} "
                                f"Emprunté: ${pos.get('borrowed_usd', 0):,.2f}"
                            )
                            break

        if triggered_now:
            triggered += 1
            try:
                await send_alert_notification(user_id, alert_id, title, body)
            except Exception as e:
                _log.warning("Failed to send notification for alert %d: %s", alert_id, e)

            # Update last_triggered_at
            async with aiosqlite.connect(DB_PATH) as db:
                async with write_locked():
                    await db.execute(
                        "UPDATE alerts SET last_triggered_at=? WHERE id=?",
                        (now.isoformat(), alert_id))
                    await db.commit()

    return triggered


async def run_evaluator():
    """Background loop: evaluate alerts for all users every EVAL_INTERVAL seconds."""
    global _evaluator_running, _last_evaluation_run
    if _evaluator_running:
        return
    _evaluator_running = True
    _log.info("Alert evaluator started (interval=%ds)", _EVAL_INTERVAL)

    while True:
        try:
            _last_evaluation_run = _time.time()
            async with aiosqlite.connect(DB_PATH) as db:
                cb = db.cursor()
                await cb.execute("SELECT DISTINCT user_id FROM alerts WHERE enabled=1")
                rows = await cb.fetchall()
                user_ids = [r[0] for r in rows]

            total_triggered = 0
            for uid in user_ids:
                try:
                    n = await evaluate_alerts_for_user(uid)
                    total_triggered += n
                except Exception as e:
                    _log.warning("Alert evaluation failed for user %d: %s", uid, e)

            if total_triggered > 0:
                _log.info("Alert evaluator: %d alerts triggered across %d users", total_triggered, len(user_ids))

        except Exception as e:
            _log.warning("Alert evaluator loop error: %s", e)

        # Run digest checks
        try:
            await _run_digest_checks()
        except Exception as e:
            _log.warning("Digest check error: %s", e)

        await asyncio.sleep(_EVAL_INTERVAL)


# ── Digest ───────────────────────────────────────────────────

_last_digest_dates: dict[str, str] = {}  # user_id:frequency → "YYYY-MM-DD"


async def _run_digest_checks():
    """Check if any user needs a digest sent."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT user_id, frequency, channel FROM digest_prefs WHERE frequency != 'off'")
        prefs = await cur.fetchall()

    if not prefs:
        return

    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    today_dt = datetime.datetime.utcnow()

    for p in prefs:
        uid = p["user_id"]
        freq = p["frequency"]  # "daily" or "weekly"
        channel = p["channel"] or ""

        key = f"{uid}:{freq}"
        last_date = _last_digest_dates.get(key, "")

        should_send = False
        if freq == "daily":
            should_send = last_date != today
        elif freq == "weekly":
            # Monday (weekday=0 in Python)
            if today_dt.weekday() == 0:
                should_send = last_date != today

        if not should_send:
            continue

        _last_digest_dates[key] = today

        try:
            await _send_digest(uid, freq, channel)
        except Exception as e:
            _log.warning("Digest send failed for user %d: %s", uid, e)


async def _send_digest(user_id: int, frequency: str, channel: str):
    """Build and send a digest for a user."""
    total_value = await _compute_total_portfolio_value(user_id)
    change_24h = await _compute_portfolio_change(user_id, 24)
    change_7d = await _compute_portfolio_change(user_id, 168)

    title = f"📊 Digest {'quotidien' if frequency == 'daily' else 'hebdomadaire'}"
    body_lines = [
        f"Valeur du portefeuille: ${total_value:,.2f}",
        f"Variation 24h: {change_24h:+.2f}%",
        f"Variation 7j: {change_7d:+.2f}%",
        "",
        f"Généré le {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
    ]
    body = "\n".join(body_lines)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Send via selected channel
        cur = await db.execute(
            "SELECT channel, config_json FROM notif_channels WHERE user_id=? AND enabled=1 AND channel=?",
            (user_id, channel))
        ch_row = await cur.fetchone()

    if ch_row:
        try:
            config = json.loads(ch_row["config_json"] or "{}")
        except Exception:
            config = {}
        if channel == "webhook":
            url = config.get("url", "")
            if url:
                await _send_webhook(url, title, body)
        elif channel == "telegram":
            bot_token = config.get("bot_token", "")
            chat_id = config.get("chat_id", "")
            if bot_token and chat_id:
                await _send_telegram(bot_token, chat_id, title, body)
        elif channel == "email":
            await _send_email(
                config.get("smtp_host", ""),
                int(config.get("smtp_port") or 587),
                config.get("user", ""),
                config.get("password", ""),
                config.get("from", ""),
                config.get("to", ""),
                title, body
            )

    # Also create an in-app notification
    await send_alert_notification(user_id, 0, title, body)


# ── Health alert helpers ──────────────────────────────────────


async def _get_moralis_key_for_user(user_id: int) -> str:
    """Get Moralis API key for user, fallback to env var."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT api_key FROM user_api_keys WHERE user_id=? AND provider='moralis'",
            (user_id,))
        row = await cur.fetchone()
    if row:
        return row["api_key"] or ""
    return os.environ.get("MORALIS_API_KEY", "")


async def _fetch_health_factors_for_user(api_key: str, user_id: int) -> list[dict]:
    """Fetch DeFi positions with health_factor across all user wallets via Moralis.

    Returns list of {protocol, protocol_id, chain, health_factor, supplied_usd, borrowed_usd}.
    Empty list if no positions have health_factor or API fails.
    """
    import sys as _sys, os as _os
    _abs = _os.path.dirname(_os.path.abspath(__file__))
    if _abs not in _sys.path:
        _sys.path.insert(0, _abs)
    from defi_service import MORALIS_DEFI_CHAINS, normalize_defi_positions

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT address FROM wallets WHERE user_id=?", (user_id,))
        wallets = await cur.fetchall()

    if not wallets:
        return []

    base = "https://deep-index.moralis.io/api/v2.2"
    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    all_positions = []

    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            for w in wallets:
                addr = w["address"]
                for chain in MORALIS_DEFI_CHAINS:
                    try:
                        r = await client.get(
                            f"{base}/wallets/{addr}/defi/positions",
                            params={"chain": chain}, headers=headers)
                        if r.status_code != 200:
                            continue
                        payload = r.json()
                    except Exception:
                        continue
                    try:
                        positions = normalize_defi_positions(payload, chain=chain)
                    except Exception:
                        continue
                    for pos in positions:
                        if isinstance(pos, dict) and pos.get("health_factor") is not None:
                            all_positions.append({
                                "protocol": pos.get("protocol", ""),
                                "protocol_id": pos.get("protocol_id", ""),
                                "chain": pos.get("chain", ""),
                                "health_factor": pos["health_factor"],
                                "supplied_usd": pos.get("supplied_usd", 0),
                                "borrowed_usd": pos.get("borrowed_usd", 0),
                            })
    except Exception as e:
        _log.warning("Health factor fetch failed for user %d: %s", user_id, e)

    return all_positions
