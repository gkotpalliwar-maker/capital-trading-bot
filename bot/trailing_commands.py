import logging
import os
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("telegram")


async def trailing_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable/check trailing stops on open positions.
    Usage:
      /trailing         — show trailing status of all positions
      /trailing on      — enable trailing on ALL open positions
      /trailing off     — disable trailing on ALL open positions
      /trailing <dealId> — toggle trailing on specific position
    """
    try:
        import telegram_bot as _tb
        client = _tb._client
        if not client:
            await update.message.reply_text("\u274c Not connected to Capital.com")
            return

        args = context.args

        # Fetch current positions
        resp = client.get("/api/v1/positions", {})
        positions = resp.get("positions", [])
        if not positions:
            await update.message.reply_text("No open positions.")
            return

        # If no args, show status
        if not args:
            lines = ["\U0001f4cf <b>Trailing Stop Status</b>\n"]
            for p in positions:
                pos = p.get("position", {})
                mkt = p.get("market", {})
                epic = mkt.get("epic", "?")
                direction = pos.get("direction", "?")
                deal_id = pos.get("dealId", "?")
                trailing = pos.get("trailingStop", False)
                trail_dist = pos.get("trailingStopDistance", 0)
                sl = float(pos.get("stopLevel", 0) or 0)
                entry = float(pos.get("level", 0) or 0)

                de = "\U0001f7e2" if direction == "BUY" else "\U0001f534"
                ts_emoji = "\u2705" if trailing else "\U0001f534"

                lines.append(f"{de} <b>{epic} {direction}</b>")
                lines.append(f"  Entry: {entry} | SL: {sl}")
                if trailing:
                    lines.append(f"  {ts_emoji} Trailing: ON (distance: {trail_dist})")
                else:
                    lines.append(f"  {ts_emoji} Trailing: OFF")
                lines.append("")

            lines.append("\U0001f527 /trailing on — enable all")
            lines.append("\U0001f527 /trailing off — disable all")
            await update.message.reply_html("\n".join(lines))
            return

        # Enable/disable trailing on all positions
        action = args[0].lower()

        if action in ("on", "off"):
            enable = action == "on"
            updated = 0
            errors = 0
            lines = []

            for p in positions:
                pos = p.get("position", {})
                mkt = p.get("market", {})
                epic = mkt.get("epic", "?")
                deal_id = pos.get("dealId", "?")
                entry = float(pos.get("level", 0) or 0)
                sl = float(pos.get("stopLevel", 0) or 0)
                current_trailing = pos.get("trailingStop", False)

                if enable == current_trailing:
                    lines.append(f"\u23ed\ufe0f {epic}: already {'ON' if enable else 'OFF'}")
                    continue

                try:
                    update_data = {"trailingStop": enable}
                    if enable and sl > 0 and entry > 0:
                        distance = abs(entry - sl)
                        update_data["trailingStopDistance"] = round(distance, 5)

                    client.put(f"/api/v1/positions/{deal_id}", update_data)
                    updated += 1
                    if enable:
                        lines.append(f"\u2705 {epic}: Trailing ON (dist: {abs(entry - sl):.5f})")
                    else:
                        lines.append(f"\u2705 {epic}: Trailing OFF")
                except Exception as e:
                    errors += 1
                    lines.append(f"\u274c {epic}: {e}")

            header = f"\U0001f4cf <b>Trailing {'Enabled' if enable else 'Disabled'}</b>\n"
            summary = f"\nUpdated: {updated} | Errors: {errors}"
            await update.message.reply_html(header + "\n".join(lines) + summary)
            return

        # Toggle specific deal
        deal_prefix = args[0]
        target = None
        for p in positions:
            pos = p.get("position", {})
            if pos.get("dealId", "").startswith(deal_prefix):
                target = p
                break

        if not target:
            await update.message.reply_text(f"\u274c Position {deal_prefix} not found")
            return

        pos = target.get("position", {})
        mkt = target.get("market", {})
        deal_id = pos.get("dealId")
        epic = mkt.get("epic", "?")
        entry = float(pos.get("level", 0) or 0)
        sl = float(pos.get("stopLevel", 0) or 0)
        current = pos.get("trailingStop", False)
        new_state = not current

        try:
            update_data = {"trailingStop": new_state}
            if new_state and sl > 0 and entry > 0:
                update_data["trailingStopDistance"] = round(abs(entry - sl), 5)
            client.put(f"/api/v1/positions/{deal_id}", update_data)
            state_str = "ON" if new_state else "OFF"
            await update.message.reply_html(
                f"\U0001f4cf <b>{epic}</b>: Trailing {state_str}"
                + (f"\nDistance: {abs(entry - sl):.5f}" if new_state else "")
            )
        except Exception as e:
            await update.message.reply_text(f"\u274c Error: {e}")

    except Exception as e:
        logger.error("Trailing cmd error: %s", e)
        await update.message.reply_text(f"\u274c Error: {e}")
