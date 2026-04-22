import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger("telegram")


async def news_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show upcoming high-impact news events. Usage: /news [hours]"""
    try:
        from news_filter import get_upcoming_events, INSTRUMENT_CURRENCIES
        hours = 24
        if context.args:
            try:
                hours = int(context.args[0])
            except ValueError:
                pass
        hours = min(hours, 72)

        events = get_upcoming_events(hours=hours)
        if not events:
            await update.message.reply_text(f"No news events in the next {hours}h \u2705")
            return

        high = [e for e in events if e["impact"] == "High"]
        medium = [e for e in events if e["impact"] == "Medium"]
        low = [e for e in events if e["impact"] == "Low"]

        lines = ["\U0001f4f0 <b>Upcoming News Events</b>\n"]

        if high:
            lines.append("\U0001f534 <b>HIGH IMPACT</b>")
            for e in high:
                mins = e["minutes_away"]
                if mins > 0:
                    time_str = f"in {mins}m"
                elif mins < 0:
                    time_str = f"{abs(mins)}m ago"
                else:
                    time_str = "NOW"
                affected = []
                for epic, currs in INSTRUMENT_CURRENCIES.items():
                    if e["currency"] in currs:
                        affected.append(epic)
                aff_str = ", ".join(affected[:4]) if affected else e["currency"]
                title = e["title"]
                currency = e["currency"]
                lines.append(f"  \u26a0\ufe0f {title} ({currency})")
                lines.append(f"     {time_str} | Affects: {aff_str}")
                forecast = e.get("forecast", "")
                previous = e.get("previous", "-")
                if forecast:
                    lines.append(f"     Forecast: {forecast} | Prev: {previous}")
            lines.append("")

        if medium:
            lines.append("\U0001f7e1 <b>MEDIUM IMPACT</b>")
            for e in medium[:5]:
                mins = e["minutes_away"]
                if mins > 0:
                    time_str = f"in {mins}m"
                else:
                    time_str = f"{abs(mins)}m ago"
                title = e["title"]
                currency = e["currency"]
                lines.append(f"  {title} ({currency}) - {time_str}")
            lines.append("")

        if low:
            cnt = len(low)
            lines.append(f"\u26aa {cnt} low-impact events (hidden)")

        lines.append(f"\n\U0001f552 Showing next {hours}h")
        await update.message.reply_html("\n".join(lines))

    except Exception as e:
        logger.error("News error: %s", e)
        await update.message.reply_text(f"\u274c Error: {e}")


async def activate_guard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activate the news & volatility guard."""
    try:
        from news_filter import activate_guard, get_guard_status, get_upcoming_events
        activate_guard()
        status = get_guard_status()
        high_events = [e for e in get_upcoming_events(hours=8) if e["impact"] == "High"]

        lines = ["\U0001f6e1\ufe0f <b>News Guard ACTIVATED</b>\n"]
        ev_count = status["events_cached"]
        blk_before = status["block_before"]
        blk_after = status["block_after"]
        penalty = status["penalty"]
        is_required = status["required"]
        lines.append(f"Events cached: {ev_count}")
        lines.append(f"Block window: {blk_before}min before / {blk_after}min after")
        if is_required:
            lines.append("Mode: BLOCK signals")
        else:
            lines.append(f"Mode: Advisory (-{penalty} confluence)")

        if high_events:
            n_high = len(high_events)
            lines.append(f"\n\u26a0\ufe0f <b>{n_high} high-impact events in 8h:</b>")
            for e in high_events[:3]:
                title = e["title"]
                currency = e["currency"]
                mins = e["minutes_away"]
                lines.append(f"  \U0001f534 {title} ({currency}) - in {mins}m")
        else:
            lines.append("\n\u2705 No high-impact events in next 8h")

        # Check open positions for news risk
        try:
            import telegram_bot as _tb
            client = _tb._client
            if client:
                resp = client.get("/api/v1/positions", {})
                positions = resp.get("positions", [])
                if positions:
                    n_pos = len(positions)
                    lines.append(f"\n\U0001f4bc Open positions: {n_pos}")
                    from news_filter import check_news_risk
                    risk_emojis = {"blocked": "\U0001f534", "caution": "\U0001f7e1", "clear": "\U0001f7e2"}
                    for p in positions:
                        epic = p.get("market", {}).get("epic", "?")
                        risk, evts, reason = check_news_risk(epic)
                        emoji = risk_emojis.get(risk, "\u2753")
                        lines.append(f"  {emoji} {epic}: {reason}")
        except Exception:
            pass

        await update.message.reply_html("\n".join(lines))

    except Exception as e:
        logger.error("Guard activation error: %s", e)
        await update.message.reply_text(f"\u274c Error: {e}")


async def deactivate_guard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deactivate the news guard."""
    try:
        from news_filter import deactivate_guard
        deactivate_guard()
        await update.message.reply_text(
            "\U0001f6e1\ufe0f News Guard DEACTIVATED\n"
            "Signals will not be checked against news events."
        )
    except Exception as e:
        await update.message.reply_text(f"\u274c Error: {e}")


async def guard_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show news guard status."""
    try:
        from news_filter import get_guard_status, get_upcoming_events
        s = get_guard_status()
        high = [e for e in get_upcoming_events(hours=4) if e["impact"] == "High"]

        active = s["active"]
        emoji = "\U0001f7e2" if active else "\U0001f534"
        state = "ON" if active else "OFF"
        lines = [f"\U0001f6e1\ufe0f <b>News Guard: {emoji} {state}</b>\n"]

        ev_count = s["events_cached"]
        cache_age = s["cache_age_min"]
        blk_before = s["block_before"]
        blk_after = s["block_after"]
        penalty = s["penalty"]
        is_required = s["required"]

        lines.append(f"Events cached: {ev_count}")
        lines.append(f"Cache age: {cache_age}min")
        lines.append(f"Block: {blk_before}min before / {blk_after}min after")
        if is_required:
            lines.append("Mode: BLOCK")
        else:
            lines.append(f"Mode: Advisory (-{penalty} conf)")

        if high:
            lines.append("\n\u26a0\ufe0f Next high-impact:")
            for e in high[:3]:
                title = e["title"]
                currency = e["currency"]
                mins = e["minutes_away"]
                lines.append(f"  \U0001f534 {title} ({currency}) in {mins}m")
        else:
            lines.append("\n\u2705 No high-impact events in 4h")

        await update.message.reply_html("\n".join(lines))

    except Exception as e:
        await update.message.reply_text(f"\u274c Error: {e}")


async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show feature dashboard - all active/inactive features."""
    try:
        lines = ["\U0001f4cb <b>Bot Feature Status</b>\n"]

        # Scanner
        lines.append("\U0001f50d <b>Scanner</b>")
        try:
            import scanner
            scan_count = getattr(scanner, "scan_count", "?")
            lines.append(f"  \u2705 Running (scan #{scan_count})")
        except Exception:
            lines.append("  \u2705 Active")

        # MTF
        mtf_req = os.environ.get("MTF_REQUIRED", "false")
        lines.append("\n\U0001f4ca <b>MTF Filter</b>")
        if mtf_req == "true":
            lines.append("  \u2705 Required (blocks counter-trend)")
        else:
            lines.append("  \u2705 Advisory mode (+2/-1 conf)")

        # ML Scoring
        ml_min = int(os.environ.get("ML_MIN_TRADES", "30"))
        lines.append("\n\U0001f916 <b>ML Scoring</b>")
        try:
            import sqlite3
            from pathlib import Path
            db = Path(__file__).resolve().parent.parent / "data" / "bot.db"
            if db.exists():
                conn = sqlite3.connect(str(db))
                cnt = conn.execute("SELECT COUNT(*) FROM trades WHERE status='closed'").fetchone()[0]
                conn.close()
                if cnt >= ml_min:
                    lines.append(f"  \u2705 Active ({cnt} trades, threshold {ml_min})")
                else:
                    lines.append(f"  \u23f8\ufe0f Inactive ({cnt}/{ml_min} trades needed)")
            else:
                lines.append("  \u23f8\ufe0f Inactive (DB not found)")
        except Exception:
            lines.append("  \u2753 Unknown")

        # News Guard
        try:
            from news_filter import get_guard_status
            gs = get_guard_status()
            ev_count = gs["events_cached"]
            lines.append("\n\U0001f6e1\ufe0f <b>News Guard</b>")
            if gs["active"]:
                lines.append(f"  \U0001f7e2 ON ({ev_count} events cached)")
            else:
                lines.append("  \U0001f534 OFF - /activateguard to enable")
        except Exception:
            lines.append("\n\U0001f6e1\ufe0f <b>News Guard</b>")
            lines.append("  \u274c Not installed")

        # Breakeven
        be = os.environ.get("BREAKEVEN_TRIGGER_R", "1.0")
        lines.append(f"\n\U0001f4b9 <b>Breakeven</b>")
        lines.append(f"  \u2705 Active (trigger: {be}R)")

        # Partial TP
        pt = os.environ.get("PARTIAL_TP_ENABLED", "true")
        pt_r = os.environ.get("PARTIAL_TP_TARGET_R", "1.5")
        pt_pct = os.environ.get("PARTIAL_TP_RATIO", "0.5")
        pct_int = int(float(pt_pct) * 100)
        pt_status = "\u2705 Active" if pt == "true" else "\U0001f534 OFF"
        lines.append("\n\U0001f3af <b>Partial TP</b>")
        lines.append(f"  {pt_status} ({pct_int}% at {pt_r}R)")

        # Market Hours
        lines.append("\n\U0001f55b <b>Market Hours</b>")
        lines.append("  \u2705 Always active")

        # Correlation
        lines.append("\n\U0001f517 <b>Correlation Filter</b>")
        lines.append("  \U0001f534 Not built - planned v2.6.0")

        # Daily Loss
        dl = os.environ.get("DAILY_LOSS_LIMIT_PCT", "5")
        lines.append("\n\U0001f6a8 <b>Daily Loss Limit</b>")
        lines.append(f"  \u2705 -{dl}%")

        await update.message.reply_html("\n".join(lines))

    except Exception as e:
        logger.error("Summary error: %s", e)
        await update.message.reply_text(f"\u274c Error: {e}")
