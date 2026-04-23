import logging
import os
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("telegram")


async def trailing_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot-side trailing stop management.

    Capital.com API v1 does NOT support native trailing stops (returns 400).
    This command controls the bot-side TrailingManager which:
      - Moves SL to breakeven at 1.0R profit
      - Starts trailing SL at 1.5R profit (ratchets behind peak)
      - Persists state to data/trailing_state.json

    Usage:
      /trailing         — show trailing status of all positions
      /trailing on      — enable bot-side trailing
      /trailing off     — disable bot-side trailing
      /trailing now     — force immediate trailing update
    """
    try:
        import telegram_bot as _tb
        client = _tb._client
        if not client:
            await update.message.reply_text("\u274c Not connected to Capital.com")
            return

        import bot_trailing

        args = context.args

        # Fetch current positions for display
        resp = client.get("/api/v1/positions", {})
        positions = resp.get("positions", [])

        # If no args, show status
        if not args:
            enabled = bot_trailing.TRAILING_ENABLED
            header = (
                f"\U0001f4cf <b>Bot-Side Trailing Stop</b>\n"
                f"Status: {\'\u2705 ENABLED\' if enabled else \'\U0001f534 DISABLED\'}\n"
                f"Breakeven trigger: {bot_trailing.BREAKEVEN_TRIGGER_R}R\n"
                f"Trail start: {bot_trailing.TRAIL_START_R}R\n"
                f"Trail distance: {bot_trailing.TRAIL_DISTANCE_ATR}x risk\n"
            )

            if not positions:
                await update.message.reply_html(header + "\nNo open positions.")
                return

            # Get trailing state from the scanner's trailing_manager if available
            trailing_state = {}
            try:
                trailing_state = _get_trailing_state()
            except Exception:
                pass

            lines = [header, ""]
            for p in positions:
                pos = p.get("position", {})
                mkt = p.get("market", {})
                epic = mkt.get("epic", "?")
                direction = pos.get("direction", "?")
                deal_id = pos.get("dealId", "?")
                sl = float(pos.get("stopLevel", 0) or 0)
                entry = float(pos.get("level", 0) or 0)
                bid = float(mkt.get("bid", 0) or 0)
                ask = float(mkt.get("offer", 0) or mkt.get("ask", 0) or 0)
                current = bid if direction == "BUY" else ask

                de = "\U0001f7e2" if direction == "BUY" else "\U0001f534"

                # Calculate current R
                risk = abs(entry - sl) if sl > 0 else 0
                pnl = (current - entry) if direction == "BUY" else (entry - current)
                current_r = pnl / risk if risk > 0 else 0

                lines.append(f"{de} <b>{epic} {direction}</b>")
                lines.append(f"  Entry: {entry:.5f} | SL: {sl:.5f}")
                lines.append(f"  Current: {current:.5f} | P&L: {current_r:.2f}R")

                # Show trailing state
                state = trailing_state.get(deal_id, {})
                if state:
                    be = "\u2705" if state.get("breakeven_hit") else "\u23f3"
                    tr = "\u2705" if state.get("trailing_active") else "\u23f3"
                    lines.append(f"  Breakeven: {be} | Trail: {tr}")
                    if state.get("trailing_active"):
                        peak = state.get("highest") if direction == "BUY" else state.get("lowest")
                        if peak:
                            lines.append(f"  Peak: {peak:.5f}")
                else:
                    if current_r < bot_trailing.BREAKEVEN_TRIGGER_R:
                        lines.append(f"  \u23f3 Waiting for {bot_trailing.BREAKEVEN_TRIGGER_R}R for breakeven")
                    else:
                        lines.append(f"  \u2705 Above breakeven trigger")
                lines.append("")

            lines.append("\U0001f527 /trailing on — enable")
            lines.append("\U0001f527 /trailing off — disable")
            lines.append("\U0001f527 /trailing now — force update")
            await update.message.reply_html("\n".join(lines))
            return

        action = args[0].lower()

        # Enable/disable
        if action in ("on", "off"):
            enable = action == "on"
            bot_trailing.TRAILING_ENABLED = enable
            # Also update .env for persistence across restarts
            _update_env("TRAILING_STOP_ENABLED", "true" if enable else "false")

            msg = f"\U0001f4cf <b>Trailing {'ENABLED' if enable else 'DISABLED'}</b>\n"
            if enable and positions:
                # Run immediate update
                try:
                    tm = bot_trailing.TrailingManager(client)
                    updates = tm.update_all()
                    if updates:
                        msg += f"\n\u2705 Immediate update: {len(updates)} SL(s) moved\n"
                        for u in updates:
                            msg += f"  \u2022 {u['deal_id'][:12]}... SL\u2192{u['new_sl']:.5f} ({u['reason']})\n"
                    else:
                        msg += "\n\u2139\ufe0f No positions need SL updates yet"
                        if positions:
                            msg += f" ({len(positions)} open)"
                except Exception as e:
                    msg += f"\n\u26a0\ufe0f Update error: {e}"
            elif not positions:
                msg += "No open positions."

            msg += f"\n\nBreakeven at: {bot_trailing.BREAKEVEN_TRIGGER_R}R"
            msg += f"\nTrail start: {bot_trailing.TRAIL_START_R}R"
            await update.message.reply_html(msg)
            return

        # Force immediate update
        if action == "now":
            if not bot_trailing.TRAILING_ENABLED:
                await update.message.reply_text(
                    "\u274c Trailing is disabled. Use /trailing on first.")
                return

            try:
                tm = bot_trailing.TrailingManager(client)
                updates = tm.update_all()
                if updates:
                    lines = [f"\U0001f4cf <b>Trailing Update</b>\n"]
                    for u in updates:
                        lines.append(
                            f"\u2705 {u['deal_id'][:12]}... SL\u2192{u['new_sl']:.5f}"
                            f" ({u['reason']})")
                    await update.message.reply_html("\n".join(lines))
                else:
                    await update.message.reply_text(
                        f"\u2139\ufe0f No SL updates needed ({len(positions)} positions)")
            except Exception as e:
                await update.message.reply_text(f"\u274c Update error: {e}")
            return

        await update.message.reply_text(
            "Usage: /trailing [on|off|now]\n"
            "  on  — enable bot-side trailing\n"
            "  off — disable\n"
            "  now — force immediate SL update")

    except Exception as e:
        logger.error("Trailing cmd error: %s", e)
        await update.message.reply_text(f"\u274c Error: {e}")


def _get_trailing_state():
    """Load trailing state from JSON file."""
    import json
    from pathlib import Path
    state_file = Path(__file__).resolve().parent.parent / "data" / "trailing_state.json"
    if state_file.exists():
        with open(state_file) as f:
            return json.load(f)
    return {}


def _update_env(key: str, value: str):
    """Update a key in .env file (create if missing)."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        lines = []
        found = False
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.strip().startswith(f"{key}="):
                        lines.append(f"{key}={value}\n")
                        found = True
                    else:
                        lines.append(line)
        if not found:
            lines.append(f"{key}={value}\n")
        with open(env_path, "w") as f:
            f.writelines(lines)
    except Exception as e:
        logger.warning(f"Failed to update .env: {e}")
