# bot/intel_commands.py - v2.8.0
# Telegram commands for market intelligence
from __future__ import annotations

import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

logger = logging.getLogger("intel_commands")


async def intel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /intel command - show market intelligence for an instrument."""
    try:
        from market_intelligence import MarketIntelligence

        intel = MarketIntelligence()
        args = context.args if context.args else []

        if not args:
            lines = ["<b>Market Intelligence Summary</b>", ""]
            instruments = ["gold", "crude", "eurusd", "gbpusd", "usdjpy"]
            last_date = None
            for inst in instruments:
                cot = intel.fetch_cot_data(inst)
                if cot:
                    bias = cot["bias"]
                    if "BULLISH" in bias:
                        tag = "[BULL]"
                    elif "BEARISH" in bias:
                        tag = "[BEAR]"
                    else:
                        tag = "[NEUT]"
                    spec_net = cot["large_spec_net"]
                    momentum = cot["spec_momentum"]
                    lines.append(f"{tag} <b>{inst.upper()}</b>: {bias}")
                    lines.append(f"    Specs {cot['spec_direction']} {spec_net:+,} ({momentum})")
                    last_date = cot.get("report_date", "")
                else:
                    lines.append(f"[--] <b>{inst.upper()}</b>: COT unavailable")

            fg = intel.fetch_fear_greed()
            if fg:
                lines.append("")
                val = fg["value"]
                cls = fg["classification"]
                lines.append(f"Fear and Greed: {val} ({cls})")

            if last_date:
                lines.append("")
                lines.append(f"COT report: {str(last_date)[:10]}")

            text = "\n".join(lines)
            await update.message.reply_text(text, parse_mode="HTML")
        else:
            inst = args[0].lower()
            tf = args[1].upper() if len(args) > 1 else "H4"
            report = intel.get_full_report(inst, tf)
            text = intel.format_telegram(report)
            await update.message.reply_text(text, parse_mode="HTML")

    except ImportError:
        await update.message.reply_text("Market intelligence module not installed.")
    except Exception as e:
        logger.error(f"Intel command error: {e}")
        await update.message.reply_text(f"Error: {e}")


def register_intel_commands(app):
    """Register /intel command handler."""
    app.add_handler(CommandHandler("intel", intel_cmd))
    logger.info("Registered /intel command")
