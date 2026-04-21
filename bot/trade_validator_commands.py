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
                "source": "api",
            })
        return result
    except Exception as e:
        logger.warning("Failed to fetch positions from API: %s", e)
        return []

async def validate_cmd(update, context):
    """Validate open trades - checks API positions + DB trades."""
    try:
        import telegram_bot as _tb
        client = _tb._client
    except Exception:
        client = None

    # Try DB first, fall back to API
    trades = get_open_trades_for_validation()
    api_positions = []
    if client:
        api_positions = _get_positions_from_api(client)

    # Merge: use API positions if DB has none
    if not trades and not api_positions:
        await update.message.reply_text("No open trades to validate.")
        return

    lines = ["\U0001f50d <b>Trade Validation</b>\n"]

    # Show API positions
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

        # Check if this trade is also in DB
        db_match = next((t for t in trades if str(t.get("deal_id","")) == p["deal_id"]), None)
        if db_match:
            inv = db_match.get("invalidation_price")
            mss = db_match.get("mss_type", "?")
            conf = db_match.get("confluence", "?")
            lines.append(f"  Conf: {conf} | MSS: {mss}" + (f" | Inv: {inv}" if inv else ""))
        lines.append("")

    # Show DB-only trades (not in API - might be pending)
    api_deals = {p["deal_id"] for p in api_positions}
    db_only = [t for t in trades if str(t.get("deal_id","")) not in api_deals]
    if db_only:
        lines.append("<b>DB Only (not in API):</b>")
        for t in db_only:
            lines.append(f"  {t.get('epic','?')} {t.get('direction','?')} | deal: {str(t.get('deal_id','?'))[:8]}...")
        lines.append("")

    await update.message.reply_html("\n".join(lines))

async def validity_cmd(update, context):
    a = context.args
    if not a: await update.message.reply_text("Usage: /validity <deal_id>"); return
    trades = get_open_trades_for_validation()
    t = next((t for t in trades if str(t.get("deal_id","")).startswith(a[0])), None)
    if not t: await update.message.reply_text(f"{a[0]} not found."); return
    await update.message.reply_text(
        f"{t.get('deal_id','?')}\n{t.get('epic','?')} {t.get('direction','?')}\n"
        f"Entry: {t.get('entry_price','?')} | SL: {t.get('stop_loss','?')} | TP: {t.get('take_profit','?')}\n"
        f"Conf: {t.get('confluence','?')} | MSS: {t.get('mss_type','?')}\n"
        f"Status: {t.get('validation_status','?')}")
