import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger("telegram")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "bot.db"


def _get_live_price(epic: str, instrument: str = "") -> float:
    """Get current mid-price for an instrument. Tries multiple sources."""
    # Try execution module first (has simple get_current_price(epic) -> float)
    try:
        from execution import get_current_price
        p = get_current_price(epic)
        if p and p > 0:
            return float(p)
    except Exception:
        pass
    # Try data_fetcher with both args
    try:
        from data_fetcher import get_current_price as dfp
        result = dfp(epic, instrument) if instrument else dfp(epic)
        if isinstance(result, dict):
            bid = float(result.get("bid", 0) or 0)
            ask = float(result.get("ask", result.get("offer", 0)) or 0)
            return (bid + ask) / 2 if bid and ask else bid or ask
        return float(result) if result else 0
    except Exception:
        pass
    # Last resort: direct API call
    try:
        import requests, os
        from dotenv import load_dotenv
        load_dotenv()
        base = os.getenv("CAPITAL_API_URL", "https://api-capital.backend-capital.com")
        token = os.getenv("CST", "") or os.getenv("CAPITAL_API_KEY", "")
        headers = {"X-CAP-API-KEY": os.getenv("CAPITAL_API_KEY", "")}
        # Try session tokens if available
        import scanner
        if hasattr(scanner, "client") and hasattr(scanner.client, "session_headers"):
            headers = scanner.client.session_headers
        r = requests.get(f"{base}/api/v1/markets/{epic}", headers=headers, timeout=5)
        s = r.json().get("snapshot", {})
        bid = float(s.get("bid", 0))
        ask = float(s.get("offer", 0))
        return (bid + ask) / 2
    except Exception:
        return 0


async def recall_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recall past signals and show which are still valid for trading.
    Usage: /recall [hours]  or  /recall [X]d for days
    Examples: /recall 4  (last 4 hours), /recall 2d (last 2 days)
    """
    try:
        # Parse time argument
        hours = 4  # default
        if context.args:
            arg = context.args[0].lower()
            if arg.endswith("d"):
                hours = int(arg[:-1]) * 24
            else:
                hours = int(arg)
        hours = min(hours, 168)  # max 7 days

        # Query signals from the time window
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        signals = [dict(r) for r in conn.execute(
            "SELECT * FROM signals WHERE timestamp >= ? ORDER BY timestamp DESC, confluence DESC",
            (cutoff,)
        ).fetchall()]
        conn.close()

        if not signals:
            await update.message.reply_text(f"No signals in the last {hours}h.")
            return

        # Revalidate each signal
        from market_hours import is_market_open

        valid = []
        invalid = []
        seen_instruments = set()  # For conflict detection

        for sig in signals:
            epic = sig.get("epic", "")
            direction = sig.get("direction", "")
            entry = float(sig.get("entry_price", 0) or 0)
            sl = float(sig.get("stop_loss", 0) or 0)
            status = sig.get("status", "pending")
            regime = sig.get("regime", "")

            # Skip already executed/expired
            if status in ("executed", "expired", "skipped"):
                invalid.append((sig, f"{status}"))
                continue

            # Check market hours
            mkt_open, mkt_reason = is_market_open(epic)
            if not mkt_open:
                invalid.append((sig, mkt_reason))
                continue

            # Check regime (hard block)
            if "blocked" in regime.lower():
                invalid.append((sig, f"Regime blocked: {regime}"))
                continue

            # Check price drift
            try:
                cur_price = _get_live_price(epic, sig.get("instrument", ""))
                if entry > 0 and sl > 0:
                    risk = abs(entry - sl)
                    drift = abs(cur_price - entry)
                    if risk > 0 and drift > risk * 2:
                        invalid.append((sig, f"Price drifted {drift:.5f} (max {risk*2:.5f})"))
                        continue
                sig["_current_price"] = cur_price
            except Exception as e:
                invalid.append((sig, f"Price fetch failed: {e}"))
                continue

            # Conflict check: skip lower-confluence opposite direction
            key = epic
            if key in seen_instruments:
                invalid.append((sig, f"Conflict: higher-confluence {epic} already listed"))
                continue
            seen_instruments.add(key)

            valid.append(sig)

        # Build response
        period = f"{hours}h" if hours < 24 else f"{hours//24}d"
        header = f"\U0001f50d <b>Signal Recall ({period})</b>\n{len(signals)} signals found\n\n"

        # Show valid signals with trade buttons
        if valid:
            header += f"\u2705 <b>{len(valid)} Valid (tradeable):</b>\n"
            await update.message.reply_html(header)

            # Import the pending signals dict to register for execution
            import telegram_bot as tb

            for sig in valid[:10]:  # Limit to 10
                sig_id = f"recall_{sig['id']}"
                # Register in _pending_signals for execution
                sig_data = {
                    "instrument": sig.get("instrument", ""),
                    "epic": sig.get("epic", ""),
                    "direction": sig.get("direction", ""),
                    "entry_price": sig.get("entry_price"),
                    "entry": sig.get("entry_price"),
                    "sl": sig.get("stop_loss"),
                    "tp": sig.get("take_profit"),
                    "size": None,  # Let position sizer calculate
                    "confluence": sig.get("confluence", 0),
                    "zone_types": sig.get("zone_types", ""),
                    "mss_type": sig.get("mss_type", ""),
                    "tf": sig.get("timeframe", ""),
                    "regime": sig.get("regime", ""),
                    "session": sig.get("session", ""),
                    "rsi": sig.get("rsi", 50),
                    "_db_id": sig.get("id"),
                }
                tb._pending_signals[sig_id] = sig_data

                d_emoji = "\U0001f7e2" if sig["direction"] == "BUY" else "\U0001f534"
                cur = sig.get("_current_price", 0)
                sig_age = ""
                try:
                    ts = sig.get("timestamp", "")
                    if ts:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else ts
                        mins = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
                        sig_age = f" | {mins}m ago" if mins < 60 else f" | {mins//60}h{mins%60:02d}m ago"
                except Exception:
                    pass
                text = (
                    f"{d_emoji} <b>{sig['epic']} {sig['direction']}</b> [{sig['timeframe']}]{sig_age}\n"
                    f"Entry: {sig['entry_price']:.5f} | Now: {cur:.5f}\n"
                    f"SL: {sig['stop_loss']:.5f} | TP: {sig['take_profit']:.5f}\n"
                    f"Confluence: {sig['confluence']} | {sig.get('zone_types','')}\n"
                    f"RSI: {sig.get('rsi',0):.1f} | {sig.get('session','')}\n"
                )
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"\U0001f680 Execute {sig['direction']}", callback_data=f"exec:{sig_id}"),
                     InlineKeyboardButton("\u274c Skip", callback_data=f"skip:{sig_id}")],
                    [InlineKeyboardButton("\U0001f680 Half", callback_data=f"half:{sig_id}"),
                     InlineKeyboardButton("\U0001f680 Double", callback_data=f"dbl:{sig_id}")]
                ])
                await update.message.reply_html(text, reply_markup=keyboard)
        else:
            header += "\u2705 <b>0 Valid signals</b>\n"

        # Show invalid summary
        if invalid:
            inv_text = f"\n\u274c <b>{len(invalid)} Invalid:</b>\n"
            for sig, reason in invalid[:15]:
                age = ""
                try:
                    ts = sig.get("timestamp", "")
                    if ts:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else ts
                        mins = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
                        age = f" {mins}m ago" if mins < 60 else f" {mins//60}h{mins%60:02d}m ago"
                except Exception:
                    pass
                inv_text += f"  \u2022 {sig['epic']} {sig['direction']} [{sig.get('timeframe','')}] C={sig.get('confluence',0)}:{age} {reason}\n"
            if len(invalid) > 15:
                inv_text += f"  ... and {len(invalid)-15} more\n"
            await update.message.reply_html(inv_text)

    except Exception as e:
        logger.error("Recall error: %s", e)
        await update.message.reply_text(f"\u274c Error: {e}")
