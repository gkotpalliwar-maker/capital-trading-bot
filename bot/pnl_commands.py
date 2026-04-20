import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("telegram")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "bot.db"


def _increment_deal_id(deal_id: str) -> str:
    """Compute close transaction dealId (last hex segment + 1)."""
    try:
        parts = deal_id.rsplit("-", 1)
        if len(parts) == 2:
            last_hex = int(parts[1], 16)
            return f"{parts[0]}-{(last_hex + 1):012x}"
    except Exception:
        pass
    return ""


async def fixpnl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-fetch P&L from Capital.com transaction API for trades with suspect values.
    Usage: /fixpnl        - Fix all suspect trades
           /fixpnl <id>   - Fix specific trade by DB id
    """
    try:
        # Get the API client from execution module
        try:
            import telegram_bot as _tb
            client = _tb._client
            if client is None:
                await update.message.reply_text("\u274c API client not initialized yet (wait for first scan)")
                return
        except Exception as e:
            await update.message.reply_text(f"\u274c Cannot access API client: {e}")
            return

        # Parse argument
        specific_id = None
        if context.args:
            try:
                specific_id = int(context.args[0])
            except ValueError:
                pass

        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        if specific_id:
            trades = [dict(r) for r in conn.execute(
                "SELECT * FROM trades WHERE id = ?", (specific_id,)
            ).fetchall()]
        else:
            # Find trades with suspect P&L: closed trades where |pnl| > 50 or pnl = 0
            trades = [dict(r) for r in conn.execute(
                "SELECT * FROM trades WHERE status = 'closed' AND (pnl = 0 OR pnl IS NULL OR abs(pnl) > 50)"
            ).fetchall()]

        if not trades:
            await update.message.reply_text("\u2705 No trades with suspect P&L found.")
            conn.close()
            return

        await update.message.reply_text(f"\U0001f50d Checking {len(trades)} trade(s)...")

        # Fetch transactions from Capital.com
        now = datetime.now(timezone.utc)
        from_dt = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
        to_dt = now.strftime("%Y-%m-%dT%H:%M:%S")

        try:
            import re
            resp = client.get("/api/v1/history/transactions", {
                "from": from_dt, "to": to_dt, "type": "ALL"
            })
            transactions = resp.get("transactions", [])
        except Exception as e:
            await update.message.reply_text(f"\u274c API error: {e}")
            conn.close()
            return

        fixed = 0
        results = []

        for t in trades:
            deal_id = t.get("deal_id", "")
            close_deal_id = _increment_deal_id(deal_id)
            old_pnl = t.get("pnl", 0) or 0
            entry = float(t.get("entry_price", 0) or 0)
            direction = t.get("direction", "BUY")
            size = float(t.get("size", 0) or 0)

            # Search transactions
            matched_tx = None
            for tx in transactions:
                tx_deal_id = str(tx.get("dealId", ""))
                tx_ref = str(tx.get("reference", ""))
                tx_type = str(tx.get("transactionType", ""))

                if tx_type != "TRADE":
                    continue

                if (close_deal_id and tx_deal_id == close_deal_id) or \
                   deal_id == tx_deal_id or deal_id in tx_ref:
                    matched_tx = tx
                    break

            if not matched_tx:
                results.append(f"  \u2022 #{t['id']} {t['epic']}: no transaction found")
                continue

            # Extract P&L from 'size' field (account currency SGD)
            pnl_raw = str(matched_tx.get("size", "0")).replace(",", "")
            new_pnl = float(pnl_raw) if pnl_raw else 0

            if new_pnl == 0:
                pnl_alt = str(matched_tx.get("profitAndLoss", "0"))
                pnl_alt = re.sub(r"[A-Z]{3}\s*", "", pnl_alt).replace(",", "").strip()
                try:
                    new_pnl = float(pnl_alt) if pnl_alt else 0
                except ValueError:
                    pass

            # Get close price
            close_price = 0.0
            for field in ["closeLevel", "openLevel", "level"]:
                val = matched_tx.get(field, 0)
                if val:
                    close_price = float(val)
                    break

            if close_price == 0 and new_pnl != 0 and entry > 0 and size > 0:
                if direction == "BUY":
                    close_price = entry + (new_pnl / size)
                else:
                    close_price = entry - (new_pnl / size)

            # Calculate P&L in R
            sl = float(t.get("stop_loss", 0) or 0)
            risk = abs(entry - sl) if sl > 0 and entry > 0 else 0
            pnl_r = 0
            if risk > 0 and close_price > 0:
                if direction == "BUY":
                    pnl_r = (close_price - entry) / risk
                else:
                    pnl_r = (entry - close_price) / risk

            # Update if changed
            if new_pnl != old_pnl or (close_price > 0 and close_price != float(t.get("close_price", 0) or 0)):
                conn.execute(
                    "UPDATE trades SET pnl = ?, pnl_r = ?, close_price = ? WHERE id = ?",
                    (new_pnl, round(pnl_r, 4), close_price, t["id"])
                )
                fixed += 1
                results.append(f"  \u2705 #{t['id']} {t['epic']} {direction}: {old_pnl:.2f} \u2192 {new_pnl:.2f} SGD (R={pnl_r:.2f})")
            else:
                results.append(f"  \u23ed\ufe0f #{t['id']} {t['epic']}: P&L unchanged ({old_pnl:.2f})")

        conn.commit()
        conn.close()

        text = f"\U0001f4b0 <b>P&L Fix Results</b>\n\nFixed {fixed}/{len(trades)} trades:\n"
        text += "\n".join(results[:20])
        if len(results) > 20:
            text += f"\n  ... and {len(results)-20} more"

        await update.message.reply_html(text)

    except Exception as e:
        logger.error("fixpnl error: %s", e)
        await update.message.reply_text(f"\u274c Error: {e}")
