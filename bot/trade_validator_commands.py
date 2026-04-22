import logging
from trade_validator import get_open_trades_for_validation, get_trade_health
logger = logging.getLogger("telegram")

def _get_positions_from_api(client):
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
        logger.warning("Failed to fetch positions from API: %s", e)
        return []


def _get_news_section(epic):
    """Get news risk section for an instrument."""
    lines = []
    try:
        from news_filter import (
            check_news_risk, get_upcoming_events,
            INSTRUMENT_CURRENCIES, INSTRUMENT_KEYWORDS
        )
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
            lines.append("  \u2500\u2500\u2500 News Risk \u2500\u2500\u2500")
            lines.append("  \U0001f7e2 No upcoming events")
            return lines

        lines.append("  \u2500\u2500\u2500 News Risk \u2500\u2500\u2500")
        for ev in instrument_events[:3]:
            mins = ev["minutes_away"]
            impact = ev["impact"]
            title = ev["title"]
            currency = ev["currency"]
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
            emoji = "\U0001f534" if impact == "High" else "\U0001f7e1" if impact == "Medium" else "\u26aa"
            lines.append(f"  {emoji} {title} ({currency}) \u2014 {time_str}")

        risk_level, _, reason = check_news_risk(epic)
        risk_emojis = {"blocked": "\U0001f534", "caution": "\U0001f7e1", "clear": "\U0001f7e2"}
        risk_labels = {"blocked": "HIGH", "caution": "MEDIUM", "clear": "LOW"}
        risk_emoji = risk_emojis.get(risk_level, "\U0001f7e2")
        risk_label = risk_labels.get(risk_level, "LOW")
        advice = ""
        if risk_level == "blocked":
            advice = " \u2014 consider closing or tightening SL"
        elif risk_level == "caution":
            advice = " \u2014 monitor closely"
        lines.append(f"  \U0001f4ca News Risk: {risk_emoji} {risk_label}{advice}")

    except ImportError:
        lines.append("  \u2500\u2500\u2500 News Risk \u2500\u2500\u2500")
        lines.append("  \u26a0\ufe0f News filter not installed")
    except Exception as e:
        logger.warning("News section error for %s: %s", epic, e)
    return lines


def _get_structure_section(client, epic, direction, entry_price):
    """Get market structure validity section."""
    try:
        from structure_checker import get_structure_status_for_validate
        return get_structure_status_for_validate(client, epic, direction, entry_price)
    except ImportError:
        return ["  \u2500\u2500\u2500 Structure \u2500\u2500\u2500", "  \u26a0\ufe0f Structure checker not installed"]
    except Exception as e:
        logger.warning("Structure check error for %s: %s", epic, e)
        return ["  \u2500\u2500\u2500 Structure \u2500\u2500\u2500", f"  \u26a0\ufe0f Error: {e}"]


async def validate_cmd(update, context):
    """Validate open trades - health + news risk + structure check."""
    try:
        import telegram_bot as _tb
        client = _tb._client
    except Exception:
        client = None

    trades = get_open_trades_for_validation()
    api_positions = []
    if client:
        api_positions = _get_positions_from_api(client)

    if not trades and not api_positions:
        await update.message.reply_text("No open trades to validate.")
        return

    lines = ["\U0001f50d <b>Trade Validation</b>\n"]

    for p in api_positions:
        epic = p["epic"]
        d = p["direction"]
        entry = p["entry_price"]
        sl = p["stop_loss"]
        tp = p["take_profit"]
        upl = p["upl"]
        cur = (p["current_bid"] + p["current_ask"]) / 2 if p["current_bid"] else 0

        de = "\U0001f7e2" if d == "BUY" else "\U0001f534"
        lines.append(f"{de} <b>{epic} {d}</b> x{p['size']}")
        lines.append(f"  Entry: {entry} | SL: {sl} | TP: {tp}")

        if cur and entry and sl:
            h = get_trade_health(p, cur)
            r = h["pnl_r"]
            he = {
                "excellent": "\U0001f31f", "good": "\U0001f7e2",
                "breakeven_zone": "\U0001f7e1", "drawdown": "\U0001f7e0",
                "danger": "\U0001f534", "unknown": "\u2753"
            }.get(h["status"], "\u2753")
            lines.append(f"  Now: {cur:.5f} | {he} {r:+.2f}R ({h['status']})")
            lines.append(f"  P&L: {upl:+.2f} SGD | SL: {h['sl_pct']:.2f}% | TP: {h['tp_pct']:.2f}%")
        else:
            lines.append(f"  P&L: {upl:+.2f} SGD")

        # DB match info
        db_match = next((t for t in trades if str(t.get("deal_id", "")) == p["deal_id"]), None)
        if db_match:
            inv = db_match.get("invalidation_price")
            mss = db_match.get("mss_type", "?")
            conf = db_match.get("confluence", "?")
            lines.append(f"  Conf: {conf} | MSS: {mss}" + (f" | Inv: {inv}" if inv else ""))

        # Structure check (re-analyzes chart)
        if client:
            struct_lines = _get_structure_section(client, epic, d, entry)
            lines.extend(struct_lines)

        # News risk section
        news_lines = _get_news_section(epic)
        lines.extend(news_lines)
        lines.append("")

    # DB-only trades
    api_deals = {p["deal_id"] for p in api_positions}
    db_only = [t for t in trades if str(t.get("deal_id", "")) not in api_deals]
    if db_only:
        lines.append("<b>DB Only (not in API):</b>")
        for t in db_only:
            lines.append(f"  {t.get('epic', '?')} {t.get('direction', '?')} | deal: {str(t.get('deal_id', '?'))[:8]}...")
        lines.append("")

    await update.message.reply_html("\n".join(lines))


async def validity_cmd(update, context):
    a = context.args
    if not a:
        await update.message.reply_text("Usage: /validity <deal_id>")
        return
    trades = get_open_trades_for_validation()
    t = next((t for t in trades if str(t.get("deal_id", "")).startswith(a[0])), None)
    if not t:
        await update.message.reply_text(f"{a[0]} not found.")
        return
    await update.message.reply_text(
        f"{t.get('deal_id', '?')}\n{t.get('epic', '?')} {t.get('direction', '?')}\n"
        f"Entry: {t.get('entry_price', '?')} | SL: {t.get('stop_loss', '?')} | TP: {t.get('take_profit', '?')}\n"
        f"Conf: {t.get('confluence', '?')} | MSS: {t.get('mss_type', '?')}\n"
        f"Status: {t.get('validation_status', '?')}")
