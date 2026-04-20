import logging
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("telegram")


async def risk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show weekend risk report on demand.
    Usage: /risk
    """
    try:
        import telegram_bot as _tb
        client = _tb._client
        if client is None:
            await update.message.reply_text("\u274c API client not ready")
            return

        from risk_report import generate_weekend_report
        report = generate_weekend_report(client)
        await update.message.reply_html(report)

    except Exception as e:
        logger.error("Risk report error: %s", e)
        await update.message.reply_text(f"\u274c Error: {e}")
