import logging
from trade_validator import get_open_trades_for_validation, get_trade_health
logger = logging.getLogger("telegram")

async def validate_cmd(update, context):
    trades = get_open_trades_for_validation()
    if not trades:
        await update.message.reply_text("No open trades to validate.")
        return
    try:
        import telegram_bot as _tb
        client = _tb._client
    except Exception:
        client = None

    lines = ["\U0001f50d <b>Trade Validation</b>\n"]
    for t in trades:
        epic = t.get("epic", "?")
        d = t.get("direction", "?")
        entry = float(t.get("entry_price", 0) or 0)
        sl = float(t.get("stop_loss", 0) or 0)
        tp = float(t.get("take_profit", 0) or 0)
        conf = t.get("confluence", "?")
        mss = t.get("mss_type", "?")
        tf = t.get("timeframe", "?")
        inv = t.get("invalidation_price")
        cur = None
        if client:
            try:
                resp = client.get(f"/api/v1/markets/{epic}", {})
                bid = float(resp.get("snapshot", {}).get("bid", 0))
                ask = float(resp.get("snapshot", {}).get("offer", 0))
                cur = (bid + ask) / 2 if bid and ask else None
            except Exception:
                pass
        de = "\U0001f7e2" if d == "BUY" else "\U0001f534"
        lines.append(f"{de} <b>{epic} {d}</b> [{tf}]")
        lines.append(f"  Entry: {entry} | SL: {sl} | TP: {tp}")
        if cur:
            h = get_trade_health(t, cur)
            r = h["pnl_r"]
            he = {"excellent":"\U0001f31f","good":"\U0001f7e2","breakeven_zone":"\U0001f7e1","drawdown":"\U0001f7e0","danger":"\U0001f534"}.get(h["status"],"\u2753")
            lines.append(f"  Price: {cur:.5f} | {he} {r:+.2f}R ({h['status']})")
            lines.append(f"  SL dist: {h['sl_pct']:.2f}% | TP dist: {h['tp_pct']:.2f}%")
        else:
            lines.append("  Price: unavailable")
        if inv: lines.append(f"  Inv: {inv}")
        lines.append(f"  Conf: {conf} | MSS: {mss}")
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
        f"Inv: {t.get('invalidation_price','not set')} | Status: {t.get('validation_status','?')}")
