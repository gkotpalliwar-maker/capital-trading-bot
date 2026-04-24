# bot/intel_commands.py — v2.8.0
# Telegram commands for market intelligence
from __future__ import annotations

import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

logger = logging.getLogger("intel_commands")


async def intel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /intel command — show market intelligence for an instrument."""
    try:
        from bot.market_intelligence import MarketIntelligence

        intel = MarketIntelligence()
        args = context.args if context.args else []

        if not args:
            # Show all instruments summary
            lines = ["📊 <b>Market Intelligence Summary</b>
"]
            instruments = ["gold", "crude", "eurusd", "gbpusd", "usdjpy"]
            for inst in instruments:
                cot = intel.fetch_cot_data(inst)
                if cot:
                    emoji = "🟢" if "BULLISH" in cot["bias"] else "🔴" if "BEARISH" in cot["bias"] else "⚪"
                    lines.append(f"{emoji} <b>{inst.upper()}</b>: {cot['bias']}")
                    lines.append(f"   Specs {cot['spec_direction']} {cot['large_spec_net']:+,} ({cot['spec_momentum']})")
                else:
                    lines.append(f"⚪ <b>{inst.upper()}</b>: COT unavailable")

            fg = intel.fetch_fear_greed()
            if fg:
                lines.append(f"
🎭 Fear & Greed: {fg['value']} ({fg['classification']})")

            lines.append(f"
📅 COT report: {cot['report_date'][:10] if cot else 'N/A'}")
            await update.message.reply_text("
".join(lines), parse_mode="HTML")
        else:
            # Show full report for specific instrument
            inst = args[0].lower()
            tf = args[1].upper() if len(args) > 1 else "H4"
            report = intel.get_full_report(inst, tf)
            text = intel.format_telegram(report)
            await update.message.reply_text(text, parse_mode="HTML")

    except ImportError:
        await update.message.reply_text("⚠️ Market intelligence not installed.")
    except Exception as e:
        logger.error(f"Intel command error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


def register_intel_commands(app):
    """Register /intel command handler."""
    app.add_handler(CommandHandler("intel", intel_cmd))
    logger.info("Registered /intel command")
