import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

logger = logging.getLogger("telegram")


def _get_positions(client):
    """Fetch open positions from Capital.com API."""
    try:
        resp = client.get("/api/v1/positions", {})
        positions = resp.get("positions", [])
        result = []
        for p in positions:
            pos = p.get("position", {})
            mkt = p.get("market", {})
            result.append({
                "deal_id": pos.get("dealId", "?"),
                "epic": mkt.get("epic", "?"),
                "instrument_name": mkt.get("instrumentName", mkt.get("epic", "?")),
                "direction": pos.get("direction", "?"),
                "entry_price": float(pos.get("level", 0)),
                "stop_loss": float(pos.get("stopLevel", 0) or 0),
                "take_profit": float(pos.get("limitLevel", 0) or 0),
                "size": float(pos.get("size", 0)),
                "upl": float(pos.get("upl", 0)),
                "current_bid": float(mkt.get("bid", 0)),
                "current_ask": float(mkt.get("offer", 0)),
            })
        return result
    except Exception as e:
        logger.warning("Failed to fetch positions: %s", e)
        return []


def _get_news_risk_line(epic):
    """Get a single-line news risk summary for an instrument."""
    try:
        from news_filter import (
            check_news_risk, get_upcoming_events, is_guard_active,
            NEWS_ENABLED, INSTRUMENT_CURRENCIES, INSTRUMENT_KEYWORDS
        )

        # Always check news for /positions (regardless of guard state)
        currencies = INSTRUMENT_CURRENCIES.get(epic, ["USD"])
        keywords = INSTRUMENT_KEYWORDS.get(epic, [])

        upcoming = get_upcoming_events(hours=24, impact_filter="Medium")
        instrument_events = []
        for ev in upcoming:
            is_relevant = ev["currency"] in currencies or ev["currency"] == "ALL"
            if not is_relevant:
                title_lower = ev["title"].lower()
                if any(kw in title_lower for kw in keywords):
                    is_relevant = True
            if is_relevant:
                instrument_events.append(ev)

        if not instrument_events:
            return "\U0001f7e2 News: Clear", "clear", []

        # Find highest impact event
        high = [e for e in instrument_events if e["impact"] == "High"]
        medium = [e for e in instrument_events if e["impact"] == "Medium"]

        if high:
            ev = high[0]
            mins = ev["minutes_away"]
            title = ev["title"]
            if mins > 0:
                if mins >= 60:
                    h = mins // 60
                    m = mins % 60
                    time_str = f"{h}h{m}m away" if m else f"{h}h away"
                else:
                    time_str = f"{mins}m away"
            elif mins < 0:
                time_str = f"{abs(mins)}m ago"
            else:
                time_str = "NOW"
            return f"\u26a0\ufe0f {title} \u2014 {time_str}", "high", instrument_events
        elif medium:
            ev = medium[0]
            mins = ev["minutes_away"]
            title = ev["title"]
            if mins > 0:
                if mins >= 60:
                    h = mins // 60
                    m = mins % 60
                    time_str = f"{h}h{m}m away" if m else f"{h}h away"
                else:
                    time_str = f"{mins}m away"
            else:
                time_str = f"{abs(mins)}m ago"
            return f"\U0001f7e1 {title} \u2014 {time_str}", "medium", instrument_events
        else:
            return "\U0001f7e2 News: Clear", "clear", []

    except ImportError:
        return "\u26a0\ufe0f News filter not installed", "unknown", []
    except Exception as e:
        logger.warning("News risk check failed for %s: %s", epic, e)
        return "\u26a0\ufe0f News check error", "unknown", []


async def positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show open positions with P&L and news risk."""
    try:
        import telegram_bot as _tb
        client = _tb._client
    except Exception:
        client = None

    if not client:
        await update.message.reply_text("\u274c Not connected to Capital.com")
        return

    positions = _get_positions(client)
    if not positions:
        await update.message.reply_text("No open positions.")
        return

    lines = ["\U0001f4ca <b>Open Positions</b>\n"]
    total_upl = 0
    has_news_risk = False
    affected_epics = []

    for p in positions:
        epic = p["epic"]
        d = p["direction"]
        entry = p["entry_price"]
        cur = (p["current_bid"] + p["current_ask"]) / 2 if p["current_bid"] else 0
        upl = p["upl"]
        total_upl += upl
        size = p["size"]

        de = "\U0001f7e2" if d == "BUY" else "\U0001f534"
        pnl_emoji = "\U0001f7e2" if upl >= 0 else "\U0001f534"

        lines.append(f"{de} <b>{epic} {d}</b> x{size}")
        if cur > 0:
            lines.append(f"  Entry: {entry} | Now: {cur:.5f} | {pnl_emoji} {upl:+.2f} SGD")
        else:
            lines.append(f"  Entry: {entry} | {pnl_emoji} P&L: {upl:+.2f} SGD")

        # News risk line
        news_line, risk_level, events = _get_news_risk_line(epic)
        lines.append(f"  {news_line}")

        if risk_level in ("high", "medium"):
            has_news_risk = True
            if epic not in affected_epics:
                affected_epics.append(epic)

        lines.append("")

    # Total
    total_emoji = "\U0001f7e2" if total_upl >= 0 else "\U0001f534"
    lines.append(f"\U0001f4b0 Total: {total_emoji} {total_upl:+.2f} SGD ({len(positions)} positions)")

    # If news risk detected and guard is active, show action buttons
    if has_news_risk:
        lines.append(f"\n\u26a0\ufe0f News risk detected for: {', '.join(affected_epics)}")

        try:
            from news_filter import is_guard_active
            if is_guard_active():
                lines.append("\nProtect profits?")
                keyboard = [
                    [
                        InlineKeyboardButton("\U0001f6d1 Close All Affected", callback_data="guard_close_all"),
                        InlineKeyboardButton("\U0001f6e1\ufe0f Tighten SL", callback_data="guard_tighten_sl"),
                    ],
                    [
                        InlineKeyboardButton("\u274c Dismiss", callback_data="guard_dismiss"),
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_html("\n".join(lines), reply_markup=reply_markup)
                return
        except ImportError:
            pass

    await update.message.reply_html("\n".join(lines))


async def guard_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle guard action button presses."""
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "guard_dismiss":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("\u2705 Dismissed. Monitor manually.")
        return

    if action == "guard_tighten_sl":
        try:
            import telegram_bot as _tb
            client = _tb._client
            if not client:
                await query.message.reply_text("\u274c Not connected")
                return

            positions = _get_positions(client)
            tightened = 0
            for p in positions:
                epic = p["epic"]
                deal_id = p["deal_id"]
                entry = p["entry_price"]
                sl = p["stop_loss"]
                cur = (p["current_bid"] + p["current_ask"]) / 2
                direction = p["direction"]

                # Check if this position has news risk
                _, risk_level, _ = _get_news_risk_line(epic)
                if risk_level not in ("high", "medium"):
                    continue

                # Calculate tighter SL: move 50% closer to current price
                if sl > 0 and cur > 0 and entry > 0:
                    if direction == "BUY":
                        new_sl = sl + (cur - sl) * 0.5
                        if new_sl > sl:
                            try:
                                client.put(f"/api/v1/positions/{deal_id}", {
                                    "stopLevel": round(new_sl, 5)
                                })
                                tightened += 1
                                await query.message.reply_text(
                                    f"\U0001f6e1\ufe0f {epic}: SL moved {sl:.5f} \u2192 {new_sl:.5f}"
                                )
                            except Exception as e:
                                await query.message.reply_text(f"\u274c {epic}: {e}")
                    else:  # SELL
                        new_sl = sl - (sl - cur) * 0.5
                        if new_sl < sl:
                            try:
                                client.put(f"/api/v1/positions/{deal_id}", {
                                    "stopLevel": round(new_sl, 5)
                                })
                                tightened += 1
                                await query.message.reply_text(
                                    f"\U0001f6e1\ufe0f {epic}: SL moved {sl:.5f} \u2192 {new_sl:.5f}"
                                )
                            except Exception as e:
                                await query.message.reply_text(f"\u274c {epic}: {e}")

            if tightened == 0:
                await query.message.reply_text("\u2139\ufe0f No positions needed SL tightening")
            await query.edit_message_reply_markup(reply_markup=None)

        except Exception as e:
            await query.message.reply_text(f"\u274c Error: {e}")
        return

    if action == "guard_close_all":
        try:
            import telegram_bot as _tb
            client = _tb._client
            if not client:
                await query.message.reply_text("\u274c Not connected")
                return

            positions = _get_positions(client)
            closed = 0
            for p in positions:
                epic = p["epic"]
                deal_id = p["deal_id"]

                _, risk_level, _ = _get_news_risk_line(epic)
                if risk_level not in ("high", "medium"):
                    continue

                try:
                    client.delete(f"/api/v1/positions/{deal_id}")
                    closed += 1
                    upl = p["upl"]
                    await query.message.reply_text(
                        f"\U0001f6d1 Closed {epic} {p['direction']} | P&L: {upl:+.2f} SGD"
                    )
                except Exception as e:
                    await query.message.reply_text(f"\u274c {epic}: {e}")

            if closed == 0:
                await query.message.reply_text("\u2139\ufe0f No affected positions to close")
            await query.edit_message_reply_markup(reply_markup=None)

        except Exception as e:
            await query.message.reply_text(f"\u274c Error: {e}")
