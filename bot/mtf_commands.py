import logging
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("telegram")


async def mtf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show H4 bias for all scanning instruments.
    Usage: /mtf
    """
    try:
        import telegram_bot as _tb
        client = _tb._client
        if client is None:
            await update.message.reply_text("\u274c API client not ready")
            return

        from mtf_confluence import get_all_biases
        import json
        from pathlib import Path

        # Load instrument map from config
        data_dir = Path(__file__).resolve().parent.parent / "data"
        instruments_path = data_dir / "instruments.json"
        inst_map = {}
        if instruments_path.exists():
            with open(instruments_path) as f:
                instruments = json.load(f)
            # Handle both list and dict formats
            if isinstance(instruments, dict):
                for k, v in instruments.items():
                    if isinstance(v, dict):
                        if v.get("scan", True):
                            inst_map[k] = v.get("epic", k.upper())
                    elif isinstance(v, list):
                        # [epic, lot_size, pip_size, scan] format
                        epic = v[0] if v else k.upper()
                        scan = v[3] if len(v) > 3 else True
                        if scan:
                            inst_map[k] = epic
                    else:
                        inst_map[k] = str(v)
            elif isinstance(instruments, list):
                for item in instruments:
                    if isinstance(item, dict):
                        name = item.get("name", item.get("instrument", ""))
                        epic = item.get("epic", name.upper())
                        scan = item.get("scan", True)
                        if name and scan:
                            inst_map[name] = epic
        if not inst_map:
            inst_map = {
                "eurusd": "EURUSD", "gbpusd": "GBPUSD", "usdjpy": "USDJPY",
                "gold": "GOLD", "crude": "OIL_CRUDE", "btcusd": "BTCUSD",
                "ethusd": "ETHUSD", "nas100": "US100", "spx500": "US500",
            }

        biases = get_all_biases(inst_map, client)

        # Format output
        lines = ["\U0001f4ca <b>H4 Multi-Timeframe Bias</b>\n"]
        lines.append(f"{'Instrument':<12}{'Bias':<10}{'Structure':<8}{'Conf':<6}{'MSS'}")
        lines.append("\u2500" * 48)

        for name, bias in sorted(biases.items()):
            b = bias.get("bias", "neutral")
            emoji = "\U0001f7e2" if b == "bullish" else "\U0001f534" if b == "bearish" else "\u26aa"
            conf = f"{bias.get('confidence', 0):.0%}"
            struct = bias.get("structure", "?")
            mss = bias.get("last_mss", "none")
            mss_emoji = "\u2b06" if mss == "bullish" else "\u2b07" if mss == "bearish" else "\u2796"
            lines.append(f"{emoji} {name:<10}{b:<10}{struct:<8}{conf:<6}{mss_emoji} {mss}")

        text = "\n".join(lines)
        await update.message.reply_html(f"<pre>{text}</pre>")

    except Exception as e:
        logger.error("MTF error: %s", e)
        await update.message.reply_text(f"\u274c Error: {e}")
