import logging
from trade_manager import get_trade_status, get_settings, toggle_partial_tp
logger = logging.getLogger("telegram")

async def breakeven_cmd(update, context):
    try:
        st = get_trade_status()
        if not st: await update.message.reply_text("ℹ️ No open trades."); return
        lines = ["🛡️ <b>Breakeven Status</b>\n"]
        for t in st:
            lines.append(f"{'✅' if t['breakeven_hit'] else '⏳'} <code>{t['deal_id'][:8]}...</code> {t['epic']} | SL: {t['stop_loss']}")
        lines.append(f"\nTrigger: {get_settings()['breakeven_trigger_r']}R")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e: await update.message.reply_text(f"❌ {e}")

async def partialtp_cmd(update, context):
    try:
        args = context.args
        s = get_settings()
        if args and args[0].lower() in ("on","off"):
            toggle_partial_tp(args[0].lower() == "on")
            await update.message.reply_text(f"✅ Partial TP {'on' if args[0].lower()=='on' else 'off'}")
            return
        st = get_trade_status()
        ph = sum(1 for t in st if t.get("partial_tp_hit"))
        text = (f"💰 <b>Partial TP</b>\n\nStatus: {'✅' if s['partial_tp_enabled'] else '❌'}\nTarget: {s['partial_tp_target_r']}R\n"
                f"Ratio: {s['partial_tp_ratio']:.0%}\nTrail: {s['trailing_after_partial_atr']} ATR\n\nOpen: {len(st)} | Partial: {ph}\n\n/partialtp on|off")
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e: await update.message.reply_text(f"❌ {e}")

async def trademanage_cmd(update, context):
    try:
        args = context.args
        if not args: await update.message.reply_text("ℹ️ /trademanage <deal_id>"); return
        st = get_trade_status(args[0])
        if not st: await update.message.reply_text(f"❌ Not found: {args[0]}"); return
        t, s = st[0], get_settings()
        be = "✅" if t["breakeven_hit"] else f"⏳ at {s['breakeven_trigger_r']}R"
        pt = "✅" if t["partial_tp_hit"] else f"⏳ at {s['partial_tp_target_r']}R"
        text = (f"📊 <b>Trade</b>\n<code>{t['deal_id']}</code>\n{t['epic']} {t['direction']}\n\n"
                f"Entry: {t['entry']}\nSL: {t['stop_loss']}\nML: {t.get('ml_score','N/A')}\n\nBE: {be}\nPTP: {pt}")
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e: await update.message.reply_text(f"❌ {e}")