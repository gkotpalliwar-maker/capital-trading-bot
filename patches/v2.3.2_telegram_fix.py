import re, os

path = os.path.join(os.path.dirname(__file__), "..", "bot", "telegram_bot.py")
with open(path) as f:
    code = f.read()
orig = code

# Fix 1: Signal Revalidation before trade execution
old = '        size = sig.get("size", 1) * size_multiplier'
new = """        # v2.3.2: Revalidate entry zone
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
                    return None, f"Signal stale: price moved {_drift:.5f} from entry (max {_risk*2:.5f}). Now: {_cur}"
                sig["_revalidated_price"] = _cur
        except Exception as _e:
            logger.warning("Revalidation failed: %s", _e)

        size = sig.get("size", 1) * size_multiplier"""
if old in code:
    code = code.replace(old, new, 1)
    print("  1. Signal revalidation added")

# Fix 2: Add retry keyboard helper
retry_helper = """\ndef _rebuild_signal_keyboard(sig_id, sig):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    d = sig.get("direction", "?")
    sz = sig.get("size", 1)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"\U0001f680 Retry {d} x{sz}", callback_data=f"exec:{sig_id}"),
         InlineKeyboardButton("\u274c Skip", callback_data=f"skip:{sig_id}")],
        [InlineKeyboardButton("\U0001f680 Half Size", callback_data=f"half:{sig_id}"),
         InlineKeyboardButton("\U0001f680 Double Size", callback_data=f"dbl:{sig_id}")]
    ])

"""
if "_rebuild_signal_keyboard" not in code:
    idx = code.find("def _execute_signal_trade")
    if idx > 0:
        code = code[:idx] + retry_helper + code[idx:]
        print("  2a. Retry keyboard helper added")

# Fix 2b: Patch callback handler for retry buttons
for action, mult in [("exec:", "1.0"), ("half:", "0.5"), ("dbl:", "2.0")]:
    tag = action.rstrip(":")
    pattern = (
        rf'result, error = _execute_signal_trade\(sig_id, {re.escape(mult)}\)\n'
        r'(\s+)suffix = f"[^"]*" if not error else f"[^"]*"\n'
        r'\1_tg_edit_message\(chat_id, msg_id, original_text \+ suffix\)'
    )
    replacement = (
        f'result, error = _execute_signal_trade(sig_id, {mult})\n'
        f'            if not error:\n'
        f'                _tg_edit_message(chat_id, msg_id, original_text + f"\\n\\n\u2705 Trade opened! Deal: {{result.get(\'deal_id\',\'?\')}}")\n'
        f'            elif sig_id in _pending_signals:\n'
        f'                kb = _rebuild_signal_keyboard(sig_id, _pending_signals[sig_id])\n'
        f'                try:\n'
        f'                    context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id,\n'
        f'                        text=original_text + f"\\n\\n\u26a0\ufe0f {{error}}\\n\\nRetry or skip:",\n'
        f'                        reply_markup=kb)\n'
        f'                except: _tg_edit_message(chat_id, msg_id, original_text + f"\\n\\n\u274c {{error}}")\n'
        f'            else:\n'
        f'                _tg_edit_message(chat_id, msg_id, original_text + f"\\n\\n\u274c {{error}}")'
    )
    m = re.search(pattern, code)
    if m:
        code = code[:m.start()] + replacement + code[m.end():]
        print(f"  2b. Retry buttons for {tag}")

# Fix 3: Update /help with all commands
old_help = '        "<b>Info</b>\\n"\n        "/about - Bot info and strategies\\n"\n        "/help - This message")'
new_help = ('        "<b>Instruments (v2.2.9)</b>\\n"\n'
            '        "/instruments - List all instruments\\n"\n'
            '        "/add - Add instrument\\n"\n'
            '        "/remove - Remove instrument\\n"\n'
            '        "/lotsize - Change lot size\\n"\n'
            '        "/pip - Set pip size\\n\\n"\n'
            '        "<b>Trade Validation (v2.2.9)</b>\\n"\n'
            '        "/validate - Check open trade validity\\n\\n"\n'
            '        "<b>ML Signal Scoring (v2.3.0)</b>\\n"\n'
            '        "/mlstats - Model accuracy &amp; features\\n"\n'
            '        "/retrain - Force model retrain\\n"\n'
            '        "/mlthreshold - View/set ML threshold\\n\\n"\n'
            '        "<b>Trade Management (v2.3.0)</b>\\n"\n'
            '        "/breakeven - Breakeven status\\n"\n'
            '        "/partialtp - Partial TP on/off/status\\n"\n'
            '        "/trademanage &lt;id&gt; - Trade detail\\n\\n"\n'
            '        "<b>Info</b>\\n"\n'
            '        "/about - Bot info and strategies\\n"\n'
            '        "/help - This message")')
if old_help in code:
    code = code.replace(old_help, new_help)
    print("  3. /help updated")

if code != orig:
    with open(path, "w") as f:
        f.write(code)
    print("  telegram_bot.py patched!")
