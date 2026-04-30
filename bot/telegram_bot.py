"""
Capital.com Trading Bot v2.1 - Telegram Interface
Signal alerts, trade execution, scanner control, risk status, analytics.
"""
import asyncio
import logging
import time
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from instrument_commands import instruments_cmd, add_instrument_cmd, remove_instrument_cmd, lotsize_cmd, pip_cmd, handle_instrument_callback
from trade_validator_commands import validate_cmd, validity_cmd

from signal_scorer_commands import mlstats_cmd, retrain_cmd, mlthreshold_cmd
from pnl_commands import fixpnl_cmd
from mtf_commands import mtf_cmd
from risk_report_commands import risk_cmd
from recall_commands import recall_cmd
from trade_manager_commands import breakeven_cmd, partialtp_cmd, trademanage_cmd
from news_commands import news_cmd, activate_guard_cmd, deactivate_guard_cmd, guard_status_cmd, summary_cmd
from positions_commands import positions_cmd, guard_button_callback
from trailing_commands import trailing_cmd
from trade_validator import store_pattern_context
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DEFAULT_SIZE,
    DEFAULT_INSTRUMENTS, DEFAULT_TIMEFRAMES,
    resolve_instrument, PIP_SIZE, WINNING_ZONE_COMBOS,
    SIGNAL_EXPIRY_SEC
)

logger = logging.getLogger(__name__)

BOT_VERSION = "2.2.0"

# Global references
_app = None
_client = None
_pending_signals = {}

# Scanner control flags (shared with scanner.py)
scanner_active = True          # False = scanning paused
manual_scan_requested = False  # True = trigger immediate scan
manual_scan_timeframes = None  # Custom TFs for manual scan e.g. ["M1","M5"]


# ================================================================
# CORE MESSAGING
# ================================================================

async def send_message(text, parse_mode="HTML", reply_markup=None):
    if not _app or not TELEGRAM_CHAT_ID:
        return False
    try:
        await _app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID, text=text,
            parse_mode=parse_mode, reply_markup=reply_markup)
        return True
    except Exception as e:
        logger.error("Telegram send error: %s", e)
        return False


def send_message_sync(text, parse_mode="HTML", reply_markup=None):
    """Send Telegram message via direct HTTP POST (bypasses asyncio issues)."""
    import requests as _tg_req
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup.to_json() if hasattr(reply_markup, 'to_json') else json.dumps(reply_markup)
    try:
        resp = _tg_req.post(url, json=payload, timeout=15)
        return resp.ok
    except Exception as e:
        logger.error("Telegram send error: %s", e)
        return False


def _tg_edit_message(chat_id, message_id, text, parse_mode="HTML"):
    """Edit Telegram message via direct HTTP POST."""
    import requests as _tg_req
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": parse_mode}
    try:
        _tg_req.post(url, json=payload, timeout=15)
    except Exception as e:
        logger.error("Telegram edit error: %s", e)


# ================================================================
# SIGNAL NOTIFICATIONS (with Execute button)
# ================================================================

def notify_signal(signal_data):
    direction = signal_data.get("direction", "")
    instrument = signal_data.get("inst_name", signal_data.get("instrument", "?"))
    epic = resolve_instrument(signal_data.get("instrument", ""))
    tf = signal_data.get("tf", "?")
    emoji = "\U0001f7e2" if direction == "BUY" else "\U0001f534"
    entry = signal_data.get("entry", 0)
    sl = signal_data.get("sl", 0)
    tp = signal_data.get("tp", 0)
    rr = signal_data.get("rr", 0)
    risk_pct = signal_data.get("risk_pct", 0)
    size = DEFAULT_SIZE.get(epic, 1)
    expiry_min = SIGNAL_EXPIRY_SEC // 60

    sig_id = f"{epic}_{direction}_{tf}_{int(time.time())}"
    _pending_signals[sig_id] = {
        **signal_data, "epic": epic, "size": size, "sig_id": sig_id,
        "_created_at": time.time()
    }
    if len(_pending_signals) > 50:
        oldest = list(_pending_signals.keys())[0]
        del _pending_signals[oldest]

    text = (
        f"{emoji} <b>{instrument} {direction}</b> [{tf}]\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\u23f0 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"\U0001f4ca Entry: <code>{entry:.5f}</code>\n"
        f"\U0001f6d1 SL: <code>{sl:.5f}</code>\n"
        f"\U0001f3af TP: <code>{tp:.5f}</code>\n"
        f"\U0001f4d0 R:R: {rr:.1f} | Risk: {risk_pct:.2f}%\n"
        f"\U0001f4e6 Size: {size}\n"
        f"\U0001f4c8 RSI: {signal_data.get('rsi', 0):.1f}\n"
        f"\U0001f517 Zones: {signal_data.get('zone_types', 'N/A')}\n"
        f"\U0001f504 MSS: {signal_data.get('mss_type', 'N/A')}\n"
        f"\u26a1 Confluence: {signal_data.get('confluence', 'N/A')}\n"
        f"\u23f3 Expires in {expiry_min}m"
        + (f"\n\u26a0\ufe0f Regime: {signal_data.get('regime_warning', '')}" if signal_data.get("regime_warning") else ""))

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"\U0001f680 Execute {direction} x{size}", callback_data=f"exec:{sig_id}"),
         InlineKeyboardButton("\u274c Skip", callback_data=f"skip:{sig_id}")],
        [InlineKeyboardButton("\U0001f680 Half Size", callback_data=f"half:{sig_id}"),
         InlineKeyboardButton("\U0001f680 Double Size", callback_data=f"dbl:{sig_id}")]
    ])
    return send_message_sync(text, reply_markup=keyboard)


# ================================================================
# TRADE & STATUS NOTIFICATIONS
# ================================================================

def notify_trade_opened(deal_id, epic, direction, entry_price, sl, tp, size):
    emoji = "\U0001f4c8" if direction == "BUY" else "\U0001f4c9"
    text = (
        f"{emoji} <b>Trade Opened</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001f4b1 {epic} {direction} x{size}\n"
        f"\U0001f4ca Entry: <code>{entry_price}</code>\n"
        f"\U0001f6d1 SL: <code>{sl}</code> | \U0001f3af TP: <code>{tp}</code>\n"
        f"\U0001f4cb Deal: <code>{deal_id}</code>")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("\u274c Close Trade", callback_data=f"close:{deal_id}")]
    ])
    return send_message_sync(text, reply_markup=keyboard)


def notify_account_status(client):
    try:
        accs = client.get_accounts()
        acc = accs.get("accounts", [{}])[0]
        b = acc.get("balance", {})
        text = (
            "\U0001f4b0 <b>Account Status</b>\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"\U0001f4b5 Balance: ${float(b.get('balance',0)):,.2f} {acc.get('currency','SGD')}\n"
            f"\U0001f4ca P&L: ${float(b.get('profitLoss',0)):+.2f}\n"
            f"\u23f0 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        return send_message_sync(text)
    except Exception as e:
        logger.error("Account status error: %s", e); return False


def notify_positions(client, get_open_positions_fn):
    try:
        positions = get_open_positions_fn(client)
        if not positions:
            return send_message_sync("\U0001f4cb No open positions")
        text = f"\U0001f4cb <b>Open Positions ({len(positions)})</b>\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        total_upl = 0
        buttons = []
        for pos in positions:
            d = pos["direction"]
            emoji = "\U0001f7e2" if d == "BUY" else "\U0001f534"
            upl = float(pos.get("upl", 0))
            total_upl += upl
            text += (f"\n{emoji} {pos['epic']} {d} x{pos['size']}\n"
                     f"   Entry: {pos['entry_price']} | P&L: {upl:+.2f}\n"
                     f"   SL: {pos.get('stop_loss', '\u2014')} | TP: {pos.get('take_profit', '\u2014')}\n")
            buttons.append([InlineKeyboardButton(
                f"\u274c Close {pos['epic']} {d}", callback_data=f"close:{pos['deal_id']}")])
        text += f"\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\U0001f4b0 Total P&L: ${total_upl:+.2f}"
        keyboard = InlineKeyboardMarkup(buttons) if buttons else None
        return send_message_sync(text, reply_markup=keyboard)
    except Exception as e:
        logger.error("Positions error: %s", e); return False


def notify_scan_summary(scan_num, max_rounds, signals_count, top5_count,
                        positions_count, balance, pnl, sessions, risk_status=None):
    risk_line = ""
    if risk_status:
        risk_line = (
            f"\n\U0001f6e1 <b>Risk</b>: {risk_status['open_trades']}/{risk_status['max_open']} trades"
            f" | Day P&L: {risk_status['daily_pnl']:+.2f}"
            f" | Losses: {risk_status['consecutive_losses']}/{risk_status['cooldown_threshold']}")
    text = (
        f"\U0001f50d <b>Scan #{scan_num}/{max_rounds}</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\u23f0 {datetime.now(timezone.utc).strftime('%H:%M UTC')} | {sessions}\n"
        f"\U0001f4ca Signals: {signals_count} scanned, {top5_count} top-5\n"
        f"\U0001f4cb Positions: {positions_count}\n"
        f"\U0001f4b0 Balance: {balance} | P&L: {pnl}"
        f"{risk_line}")
    return send_message_sync(text)


# ================================================================
# TRADE EXECUTION (with risk checks)
# ================================================================


def _rebuild_signal_keyboard(sig_id, sig):
    """Rebuild trade buttons for retry after error."""
    d = sig.get("direction", "?")
    sz = sig.get("size", 1)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"\U0001f680 Retry {d} x{sz}", callback_data=f"exec:{sig_id}"),
         InlineKeyboardButton("\u274c Skip", callback_data=f"skip:{sig_id}")],
        [InlineKeyboardButton("\U0001f680 Half Size", callback_data=f"half:{sig_id}"),
         InlineKeyboardButton("\U0001f680 Double Size", callback_data=f"dbl:{sig_id}")]
    ])

def _execute_signal_trade(sig_id, size_multiplier=1.0):
    import risk_manager
    import persistence as db
    from execution import open_trade

    # Callback lock
    lock_ok, lock_msg = risk_manager.check_callback_lock(sig_id)
    if not lock_ok:
        return None, lock_msg

    try:
        if sig_id not in _pending_signals:
            return None, "Signal expired or not found"
        sig = _pending_signals[sig_id]

        # Stale signal check
        fresh_ok, fresh_msg = risk_manager.check_signal_fresh(sig)
        if not fresh_ok:
            db_id = sig.get("_db_id")
            if db_id:
                db.mark_signal(db_id, "expired")
            del _pending_signals[sig_id]
            return None, fresh_msg

        # Risk check
        epic = sig.get("epic", "")
        risk_ok, risk_msg = risk_manager.check_risk_allowed(
            _client, sig.get("instrument", ""), sig.get("direction", ""), epic)
        if not risk_ok:
            return None, f"Risk blocked: {risk_msg}"

        # Execution validation
        exec_ok, exec_msg = risk_manager.check_execution_valid(_client, sig)
        if not exec_ok:
            return None, f"Validation failed: {exec_msg}"

        # v2.3.2: Revalidate entry zone (price may have drifted)
        try:
            from data_fetcher import get_current_price as _gcp
            _pi = _gcp(_client, sig.get("instrument", sig.get("epic", "")))
            _cur = _pi["ask"] if sig["direction"] == "BUY" else _pi["bid"]
            _entry = float(sig.get("entry_price", sig.get("entry", 0)) or 0)
            _sl = float(sig.get("sl", 0) or 0)
            if _entry > 0 and _sl > 0:
                _risk = abs(_entry - _sl)
                _drift = abs(_cur - _entry)
                if _risk > 0 and _drift > _risk * 2:
                    return None, f"\u26a0\ufe0f Signal stale: price moved {_drift:.5f} from entry (max {_risk*2:.5f}). Now: {_cur}"
                sig["_revalidated_price"] = _cur
        except Exception as _e:
            logger.warning("Revalidation failed: %s", _e)

        size = sig.get("size", 1) * size_multiplier
        result = open_trade(
            _client, instrument=sig.get("instrument", sig.get("epic", "")),
            direction=sig["direction"], stop_loss=sig.get("sl"),
            take_profit=sig.get("tp"), size=size,
            signal_id=sig.get("_db_id"), signal_data=sig)

        if "error" in result:
            return None, result["error"]

        # Mark signal executed in DB
        db_id = sig.get("_db_id")
        if db_id:
            db.mark_signal(db_id, "executed")

        notify_trade_opened(
            deal_id=result.get("deal_id", "?"), epic=sig.get("epic", "?"),
            direction=sig["direction"], entry_price=result.get("entry_price", "?"),
            sl=sig.get("sl", "?"), tp=sig.get("tp", "?"), size=size)
        del _pending_signals[sig_id]
        return result, None
    except Exception as e:
        logger.error("Trade execution failed: %s", e)
        return None, str(e)
    finally:
        risk_manager.release_callback_lock(sig_id)


def _close_trade_by_id(deal_id):
    from execution import close_trade
    try:
        result = close_trade(_client, deal_id)
        if "error" in result:
            return False, result["error"]
        return True, None
    except Exception as e:
        return False, str(e)


# ================================================================
# COMMAND HANDLERS
# ================================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global scanner_active
    if scanner_active:
        await update.message.reply_html("\U0001f7e2 Scanner is already running.\nUse /stop to pause.")
    else:
        scanner_active = True
        await update.message.reply_html(
            "\U0001f7e2 <b>Scanner Resumed</b>\n"
            "Scanning will continue on the next cycle.\n"
            f"Timeframes: {', '.join(DEFAULT_TIMEFRAMES)}")
        logger.info("Scanner resumed via /start command")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global scanner_active
    if not scanner_active:
        await update.message.reply_html("\U0001f534 Scanner is already paused.\nUse /start to resume.")
    else:
        scanner_active = False
        await update.message.reply_html(
            "\U0001f534 <b>Scanner Paused</b>\n"
            "The bot is still running but will skip scans.\n"
            "Trailing SL and position monitoring continue.\n"
            "Use /start to resume scanning.")
        logger.info("Scanner paused via /stop command")


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global manual_scan_requested, manual_scan_timeframes
    args = context.args if context.args else []
    valid_tfs = {"M1", "M5", "M15", "M30", "H1", "H4", "D", "W"}
    if args:
        requested_tfs = [tf.upper() for tf in args]
        invalid = [tf for tf in requested_tfs if tf not in valid_tfs]
        if invalid:
            await update.message.reply_html(
                f"\u274c Invalid timeframes: {', '.join(invalid)}\n"
                f"Valid: {', '.join(sorted(valid_tfs))}")
            return
        manual_scan_timeframes = requested_tfs
        tf_str = ", ".join(requested_tfs)
    else:
        manual_scan_timeframes = None
        tf_str = ", ".join(DEFAULT_TIMEFRAMES)
    manual_scan_requested = True
    await update.message.reply_html(
        f"\U0001f50d <b>Manual Scan Triggered</b>\n"
        f"Timeframes: {tf_str}\n"
        f"Instruments: {len(DEFAULT_INSTRUMENTS)}\n"
        "Results will appear shortly...")
    logger.info("Manual scan requested: TFs=%s", tf_str)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import risk_manager
    status = "\U0001f7e2 SCANNING" if scanner_active else "\U0001f534 PAUSED"
    pending = len(_pending_signals)
    rs = risk_manager.get_risk_status()
    text = (
        "\U0001f916 <b>Bot Status</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"Scanner: {status}\n"
        f"Pending signals: {pending}\n"
        f"Default TFs: {', '.join(DEFAULT_TIMEFRAMES)}\n"
        f"Instruments: {len(DEFAULT_INSTRUMENTS)}\n"
        f"Version: {BOT_VERSION}\n"
        f"\u23f0 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        "\U0001f6e1 <b>Risk Status</b>\n"
        f"Open trades: {rs['open_trades']}/{rs['max_open']}\n"
        f"Daily P&L: {rs['daily_pnl']:+.2f} (limit: -{rs['max_daily_loss']:.0f})\n"
        f"Trades today: {rs['trades_today']} ({rs['closed_today']} closed)\n"
        f"Consec. losses: {rs['consecutive_losses']}/{rs['cooldown_threshold']}")
    await update.message.reply_html(text)


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _client:
        from execution import get_open_positions
        notify_positions(_client, get_open_positions)
    else:
        await update.message.reply_text("Bot not connected to Capital.com")


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _client:
        notify_account_status(_client)
    else:
        await update.message.reply_text("Bot not connected to Capital.com")


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _pending_signals:
        await update.message.reply_text("No pending signals")
        return
    now = time.time()
    text = f"\U0001f4cb <b>Pending Signals ({len(_pending_signals)})</b>\n\n"
    for sid, sig in list(_pending_signals.items())[-10:]:
        age = int(now - sig.get("_created_at", now))
        remaining = max(0, SIGNAL_EXPIRY_SEC - age)
        text += (f"\u2022 {sig.get('epic','?')} {sig.get('direction','?')} "
                 f"[{sig.get('tf','?')}] @ {sig.get('entry',0):.5f} "
                 f"({remaining}s left)\n")
    await update.message.reply_html(text)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show trading statistics from the journal."""
    try:
        import persistence as db
        args = context.args if context.args else []
        days = int(args[0]) if args else 30
        stats = db.get_trade_stats(days)

        if stats["total"] == 0:
            await update.message.reply_html(f"\U0001f4ca No closed trades in the last {days} days")
            return

        text = (
            f"\U0001f4ca <b>Trade Stats ({days}d)</b>\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"Trades: {stats['total']} ({stats['wins']}W / {stats['losses']}L)\n"
            f"Win Rate: {stats['win_rate']:.1f}%\n"
            f"Total P&L: {stats['total_pnl']:+.2f}\n"
            f"Avg R: {stats['avg_r']:+.2f} | Best: {stats['best_r']:+.2f} | Worst: {stats['worst_r']:+.2f}\n")

        if stats["by_combo"]:
            text += "\n<b>By Combo:</b>\n"
            for combo, d in sorted(stats["by_combo"].items(), key=lambda x: -x[1]["pnl"]):
                wr = d["wins"] / d["count"] * 100 if d["count"] else 0
                text += f"  {combo}: {d['count']}T {wr:.0f}%WR {d['pnl']:+.2f}\n"

        if stats["by_instrument"]:
            text += "\n<b>By Instrument:</b>\n"
            for inst, d in sorted(stats["by_instrument"].items(), key=lambda x: -x[1]["pnl"]):
                wr = d["wins"] / d["count"] * 100 if d["count"] else 0
                text += f"  {inst}: {d['count']}T {wr:.0f}%WR {d['pnl']:+.2f}\n"

        if stats["by_session"]:
            text += "\n<b>By Session:</b>\n"
            for sess, d in sorted(stats["by_session"].items(), key=lambda x: -x[1]["pnl"]):
                wr = d["wins"] / d["count"] * 100 if d["count"] else 0
                text += f"  {sess}: {d['count']}T {wr:.0f}%WR {d['pnl']:+.2f}\n"

        await update.message.reply_html(text)


    except Exception as e:
        logger.error("/stats error: %s", e, exc_info=True)
        await update.message.reply_html("\u274c /stats error: " + str(e))


async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current risk controls and their state."""
    try:
        import risk_manager
        import persistence as db
        rs = risk_manager.get_risk_status()
        errors = db.get_recent_errors(hours=24, limit=5)

        text = (
            "\U0001f6e1 <b>Risk Controls</b>\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"Open trades: {rs['open_trades']}/{rs['max_open']}\n"
            f"Daily P&L: {rs['daily_pnl']:+.2f} (limit: -{rs['max_daily_loss']:.0f})\n"
            f"Trades today: {rs['trades_today']}\n"
            f"Consec. losses: {rs['consecutive_losses']}/{rs['cooldown_threshold']}\n")

        if errors:
            text += "\n<b>Recent Errors:</b>\n"
            for e in errors[:5]:
                text += f"  [{e['category']}] {e['message'][:60]}\n"

        await update.message.reply_html(text)


    except Exception as e:
        logger.error("/risk error: %s", e, exc_info=True)
        await update.message.reply_html("\u274c /risk error: " + str(e))


async def cmd_journal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent signal journal."""
    try:
        import persistence as db
        args = context.args if context.args else []
        hours = float(args[0]) if args else 24
        signals = db.get_recent_signals(hours=hours, limit=20)

        if not signals:
            await update.message.reply_html(f"\U0001f4d3 No signals in the last {hours:.0f}h")
            return

        text = f"\U0001f4d3 <b>Signal Journal ({hours:.0f}h)</b>\n\n"
        for s in signals[:15]:
            top5 = "\u2b50" if s.get("is_top5") else "\u25cb"
            status_emoji = {"pending": "\u23f3", "executed": "\u2705",
                            "skipped": "\u274c", "expired": "\u23f0"}.get(s.get("status", ""), "?")
            text += (f"{top5}{status_emoji} {s.get('epic','')} {s.get('direction','')} "
                     f"[{s.get('timeframe','')}] C{s.get('confluence',0)} "
                     f"{s.get('zone_types','')} | {s.get('status','')}\n")

        await update.message.reply_html(text)


    except Exception as e:
        logger.error("/journal error: %s", e, exc_info=True)
        await update.message.reply_html("\u274c /journal error: " + str(e))



async def cmd_regime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current market regime for all instruments."""
    try:
        if not _client:
            await update.message.reply_text("Bot not connected")
            return
        from data_fetcher import fetch_candles, add_technical_indicators
        import regime_filter
        from config import DEFAULT_INSTRUMENTS, resolve_instrument

        text = "\U0001f30d <b>Market Regimes (H1)</b>\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        for inst in DEFAULT_INSTRUMENTS:
            epic = resolve_instrument(inst)
            try:
                df = fetch_candles(_client, inst, "H1", count=200)
                if df.empty or len(df) < 60:
                    text += f"\n{epic}: insufficient data\n"
                    continue
                df = add_technical_indicators(df)
                regime = regime_filter.detect_regime(df)
                trend_e = {"trending": "\U0001f4c8", "ranging": "\u2194", "weak_trend": "\u27a1"}.get(regime["trend"], "?")
                vol_e = {"high": "\U0001f525", "low": "\u2744", "normal": "\u2696"}.get(regime["volatility"], "?")
                text += f"\n{trend_e} <b>{epic}</b> {regime['trend']} {vol_e}{regime['volatility']}\n   ADX={regime['adx']} BB={regime['bb_width']:.2f}% VR={regime['vol_ratio']}\n"
            except Exception as e:
                text += f"\n{epic}: error ({e})\n"
        await update.message.reply_html(text)
    except Exception as e:
        logger.error("/regime error: %s", e, exc_info=True)
        await update.message.reply_html("\u274c Regime error: " + str(e))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "\U0001f916 <b>Capital.com Trading Bot v" + BOT_VERSION + "</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        "<b>Scanner Control</b>\n"
        "/start - Resume scanning\n"
        "/stop - Pause scanning\n"
        "/scan - Manual scan (default TFs)\n"
        "/scan M1 M5 - Scan custom timeframes\n"
        "/status - Bot + risk status\n\n"
        "<b>Trading</b>\n"
        "/positions - Open positions (with close buttons)\n"
        "/balance - Account summary\n"
        "/pending - Pending signals (with expiry)\n\n"
        "<b>Analytics</b>\n"
        "/stats - Trade statistics (30d)\n"
        "/stats 7 - Stats for last 7 days\n"
        "/journal - Signal journal (24h)\n"
        "/journal 4 - Journal for last 4 hours\n"
        "/risk - Risk controls status\n""/regime - Market regime for all instruments\n\n"
        "<b>Instruments (v2.2.9)</b>\n"
        "/instruments - List all instruments\n"
        "/add - Add instrument\n"
        "/remove - Remove instrument\n"
        "/lotsize - Change lot size\n"
        "/pip - Set pip size\n\n"
        "<b>Trade Validation (v2.2.9)</b>\n"
        "/validate - Check open trade validity\n\n"
        "<b>Signal Recall (v2.3.3)</b>\n"
        "/recall - Recall signals (last 4h)\n"
        "/recall 8 - Last 8 hours\n"
        "/recall 2d - Last 2 days\n\n"
        "<b>ML Signal Scoring (v2.3.0)</b>\n"
        "/mlstats - Model accuracy & features\n"
        "/retrain - Force model retrain\n"
        "/mlthreshold - View/set ML threshold\n\n"
        "<b>Trade Management (v2.3.0)</b>\n"
        "/breakeven - Breakeven status\n"
        "/partialtp - Partial TP on/off/status\n"
        "/trademanage &lt;id&gt; - Trade detail\n\n"
        "<b>Info</b>\n"
        "/about - Bot info and strategies\n"
        "/help - This message")
    await update.message.reply_html(text)


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    combos = "\n".join([f"  \u2022 {c}" for c in sorted(WINNING_ZONE_COMBOS)])
    text = (
        "\U0001f916 <b>Capital.com SMC/ICT Trading Bot</b>\n"
        f"Version: {BOT_VERSION}\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        "<b>\U0001f3af What It Does</b>\n"
        "Automated signal scanner for Capital.com CFD trading. "
        "Scans 8 instruments across 3 timeframes every 15 minutes. "
        "Sends Telegram alerts with one-tap trade execution.\n\n"
        "<b>\U0001f4ca Strategies</b>\n"
        "\u2022 <b>SMC/ICT</b> - Order Blocks, Fair Value Gaps, "
        "Breaker Blocks, Mitigation Blocks, Inversion FVGs\n"
        "\u2022 <b>MSS/BOS</b> - Market Structure Shift (reversal) "
        "and Break of Structure (continuation) confirmation\n"
        "\u2022 Confluence scoring: OB(3) + FVG(3) + BB(2) + MB(2) + IFVG(1)\n"
        "\u2022 MSS adds +3, BOS adds +2 confluence\n"
        "\u2022 Minimum confluence threshold: 3\n\n"
        "<b>\U0001f3c6 Top-5 Winning Zone Combos</b>\n"
        "(From OANDA backtest: 47 trades, 37.5% WR, +6.11% P&L)\n"
        f"{combos}\n\n"
        "<b>\U0001f6e1 v2.1 Risk Controls</b>\n"
        "\u2022 Max daily loss limit\n"
        "\u2022 Max concurrent open trades\n"
        "\u2022 Duplicate signal suppression\n"
        "\u2022 Signal expiry + stale rejection\n"
        "\u2022 Price drift + spread validation\n"
        "\u2022 Cooldown after consecutive losses\n"
        "\u2022 Double-tap callback lock\n\n"
        "<b>\U0001f4be v2.1 Persistence</b>\n"
        "\u2022 SQLite signal & trade journal\n"
        "\u2022 Restart recovery for positions\n"
        "\u2022 Trailing SL config survives restart\n"
        "\u2022 Error classification & logging\n\n"
        "<b>\U0001f4b1 Instruments</b>\n"
        "Gold, Crude Oil, EUR/USD, GBP/USD, USD/JPY, "
        "BTC/USD, Nasdaq 100, S&P 500\n\n"
        "<b>\U0001f4bb Platform</b>\n"
        "\u2022 Capital.com Live API (REST)\n"
        "\u2022 Python 3.12 on Ubuntu 24.04 VPS\n"
        "\u2022 Runs 24/7 with systemd auto-restart")
    await update.message.reply_html(text)


# ================================================================
# CALLBACK HANDLER (button presses)
# ================================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    data = query.data
    chat_id = query.message.chat_id
    msg_id = query.message.message_id
    original_text = query.message.text or ""

    try:
        if data.startswith("exec:"):
            sig_id = data[5:]
            _tg_edit_message(chat_id, msg_id, original_text + "\n\n\u23f3 Executing trade...")
            result, error = _execute_signal_trade(sig_id, 1.0)
            if not error:
                _tg_edit_message(chat_id, msg_id, original_text + f"\n\n\u2705 Trade opened! Deal: {result.get('deal_id','?')}")
            elif sig_id in _pending_signals:
                kb = _rebuild_signal_keyboard(sig_id, _pending_signals[sig_id])
                try:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id,
                        text=original_text + f"\n\n\u26a0\ufe0f {error}\n\nRetry or skip:",
                        reply_markup=kb)
                except: _tg_edit_message(chat_id, msg_id, original_text + f"\n\n\u274c {error}")
            else:
                _tg_edit_message(chat_id, msg_id, original_text + f"\n\n\u274c {error}")

        elif data.startswith("half:"):
            sig_id = data[5:]
            _tg_edit_message(chat_id, msg_id, original_text + "\n\n\u23f3 Executing half size...")
            result, error = _execute_signal_trade(sig_id, 0.5)
            if not error:
                _tg_edit_message(chat_id, msg_id, original_text + f"\n\n\u2705 Trade opened! Deal: {result.get('deal_id','?')}")
            elif sig_id in _pending_signals:
                kb = _rebuild_signal_keyboard(sig_id, _pending_signals[sig_id])
                try:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id,
                        text=original_text + f"\n\n\u26a0\ufe0f {error}\n\nRetry or skip:",
                        reply_markup=kb)
                except: _tg_edit_message(chat_id, msg_id, original_text + f"\n\n\u274c {error}")
            else:
                _tg_edit_message(chat_id, msg_id, original_text + f"\n\n\u274c {error}")

        elif data.startswith("dbl:"):
            sig_id = data[4:]
            _tg_edit_message(chat_id, msg_id, original_text + "\n\n\u23f3 Executing double size...")
            result, error = _execute_signal_trade(sig_id, 2.0)
            if not error:
                _tg_edit_message(chat_id, msg_id, original_text + f"\n\n\u2705 Trade opened! Deal: {result.get('deal_id','?')}")
            elif sig_id in _pending_signals:
                kb = _rebuild_signal_keyboard(sig_id, _pending_signals[sig_id])
                try:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id,
                        text=original_text + f"\n\n\u26a0\ufe0f {error}\n\nRetry or skip:",
                        reply_markup=kb)
                except: _tg_edit_message(chat_id, msg_id, original_text + f"\n\n\u274c {error}")
            else:
                _tg_edit_message(chat_id, msg_id, original_text + f"\n\n\u274c {error}")

        elif data.startswith("close:"):
            deal_id = data[6:]
            _tg_edit_message(chat_id, msg_id, original_text + "\n\n\u23f3 Closing trade...")
            success, error = _close_trade_by_id(deal_id)
            suffix = "\n\n\u2705 Trade closed!" if success else f"\n\n\u274c Close failed: {error}"
            _tg_edit_message(chat_id, msg_id, original_text + suffix)

        elif data.startswith("skip:"):
            sig_id = data[5:]
            sig = _pending_signals.get(sig_id)
            if sig:
                import persistence as db_mod
                db_id = sig.get("_db_id")
                if db_id:
                    db_mod.mark_signal(db_id, "skipped")
                del _pending_signals[sig_id]
            _tg_edit_message(chat_id, msg_id, original_text + "\n\n\u274c Signal skipped")

    except Exception as e:
        logger.error("Callback error: %s", e)
        send_message_sync(f"\u26a0 Button error: {e}")

# ================================================================
# SETUP & BACKGROUND POLLING
# ================================================================

_polling_thread = None

def start_polling_background():
    import threading
    if not _app: return

    def _run_polling():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_app.initialize())
            loop.run_until_complete(_app.start())
            loop.run_until_complete(_app.updater.start_polling(drop_pending_updates=True))
            logger.info("Telegram polling started in background")
            loop.run_forever()
        except Exception as e:
            logger.error("Polling error: %s", e)
        finally:
            loop.close()

    global _polling_thread
    _polling_thread = threading.Thread(target=_run_polling, daemon=True)
    _polling_thread.start()
    logger.info("Telegram polling thread started")


def stop_polling():
    if _app and _app.updater:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_app.updater.stop())
            loop.run_until_complete(_app.stop())
            loop.run_until_complete(_app.shutdown())
            loop.close()
        except Exception:
            pass


# v2.8.0: Market intelligence command
try:
    from intel_commands import intel_cmd
    HAS_INTEL = True
except ImportError:
    HAS_INTEL = False

def setup_telegram_app():
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set"); return None
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("instruments", instruments_cmd))
    app.add_handler(CommandHandler("add", add_instrument_cmd))
    app.add_handler(CommandHandler("remove", remove_instrument_cmd))
    app.add_handler(CommandHandler("lotsize", lotsize_cmd))
    app.add_handler(CommandHandler("pip", pip_cmd))
    app.add_handler(CommandHandler("validate", validate_cmd))
    app.add_handler(CommandHandler("validity", validity_cmd))
    # v2.3.0
    app.add_handler(CommandHandler("mlstats", mlstats_cmd))
    app.add_handler(CommandHandler("retrain", retrain_cmd))
    app.add_handler(CommandHandler("mlthreshold", mlthreshold_cmd))
    app.add_handler(CommandHandler("breakeven", breakeven_cmd))
    app.add_handler(CommandHandler("partialtp", partialtp_cmd))
    app.add_handler(CommandHandler("trademanage", trademanage_cmd))
    app.add_handler(CommandHandler("fixpnl", fixpnl_cmd))
    app.add_handler(CommandHandler("mtf", mtf_cmd))
    app.add_handler(CommandHandler("risk", risk_cmd))

    app.add_handler(CommandHandler("recall", recall_cmd))


    app.add_handler(CommandHandler("instruments", instruments_cmd))
    app.add_handler(CommandHandler("add", add_instrument_cmd))
    app.add_handler(CommandHandler("remove", remove_instrument_cmd))
    app.add_handler(CommandHandler("lotsize", lotsize_cmd))
    app.add_handler(CommandHandler("pip", pip_cmd))
    app.add_handler(CommandHandler("validate", validate_cmd))
    app.add_handler(CommandHandler("validity", validity_cmd))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("positions", positions_cmd))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("journal", cmd_journal))
    app.add_handler(CommandHandler("risk", cmd_risk))
    app.add_handler(CommandHandler("regime", cmd_regime))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("news", news_cmd))
    app.add_handler(CommandHandler("activateguard", activate_guard_cmd))
    app.add_handler(CommandHandler("deactivateguard", deactivate_guard_cmd))
    app.add_handler(CommandHandler("guardstatus", guard_status_cmd))
    app.add_handler(CommandHandler("summary", summary_cmd))
    app.add_handler(CommandHandler("trailing", trailing_cmd))

    # v2.8.0: Market intelligence
    if HAS_INTEL:
        app.add_handler(CommandHandler("intel", intel_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))

    async def _error_handler(update, context):
        logger.error("Telegram handler error: %s", context.error, exc_info=True)

    app.add_error_handler(_error_handler)
    global _app
    _app = app
    logger.info("Telegram bot v" + BOT_VERSION + " initialized")
    return app
