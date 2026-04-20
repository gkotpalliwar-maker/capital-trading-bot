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
            # instruments.json has structure:
            # {"scan_list": ["EURUSD", ...], "lot_overrides": {...}, ...}
            if isinstance(instruments, dict) and "scan_list" in instruments:
                scan_list = instruments["scan_list"]
                if isinstance(scan_list, list):
                    for epic in scan_list:
                        inst_map[epic.lower()] = epic
            elif isinstance(instruments, dict):
                # Maybe a flat {name: epic} or {name: {epic:..., scan:...}} format
                skip_keys = {"added", "removed", "lot_overrides", "pip_overrides",
                             "scan_list", "defaults", "config"}
                for k, v in instruments.items():
                    if k in skip_keys:
                        continue
                    if isinstance(v, dict):
                        if v.get("scan", True):
                            inst_map[k] = v.get("epic", k.upper())
                    elif isinstance(v, str):
                        inst_map[k] = v
            elif isinstance(instruments, list):
                for item in instruments:
                    if isinstance(item, str):
                        inst_map[item.lower()] = item
                    elif isinstance(item, dict):
                        name = item.get("name", item.get("epic", ""))
                        epic = item.get("epic", name.upper())
                        if name:
                            inst_map[name.lower()] = epic
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
