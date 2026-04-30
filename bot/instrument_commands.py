import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from instrument_manager import get_merged_config, add_instrument, remove_instrument, set_lot_size, set_pip_size, list_instruments
logger = logging.getLogger("telegram")
async def instruments_cmd(update, context):
    try: await update.message.reply_text(list_instruments(), parse_mode="HTML")
    except Exception as e: await update.message.reply_text(f"Error: {e}")
async def add_instrument_cmd(update, context):
    a = context.args
    if not a or len(a)<4: await update.message.reply_text("Usage: /add name epic pip lot\nExample: /add usdchf USDCHF 0.0001 1000"); return
    try: await update.message.reply_text(add_instrument(a[0],a[1],float(a[2]),float(a[3])))
    except ValueError: await update.message.reply_text("pip/lot must be numbers.")
async def remove_instrument_cmd(update, context):
    a = context.args
    if not a: await update.message.reply_text("Usage: /remove name"); return
    n,cfg = a[0].lower(), get_merged_config()
    if n not in cfg["instrument_map"]: await update.message.reply_text(f"'{n}' not found."); return
    kb=[[InlineKeyboardButton("Yes",callback_data=f"confirm_remove:{n}"),InlineKeyboardButton("Cancel",callback_data="cancel_remove")]]
    await update.message.reply_text(f"Remove {n}?",reply_markup=InlineKeyboardMarkup(kb))
async def lotsize_cmd(update, context):
    a = context.args
    if not a or len(a)<2:
        cfg=get_merged_config(); lines=["Usage: /lotsize name size\n","Current:"]
        for n,e in sorted(cfg["instrument_map"].items()): lines.append(f"  {n:<10} > {cfg['default_size'].get(e,'?')}")
        await update.message.reply_text("\n".join(lines)); return
    try: await update.message.reply_text(set_lot_size(a[0],float(a[1])))
    except ValueError: await update.message.reply_text("Must be a number.")
async def pip_cmd(update, context):
    a = context.args
    if not a or len(a)<2:
        cfg=get_merged_config(); lines=["Usage: /pip name size\n","Current:"]
        for n,e in sorted(cfg["instrument_map"].items()): lines.append(f"  {n:<10} > {cfg['pip_size'].get(e,'?')}")
        await update.message.reply_text("\n".join(lines)); return
    try: await update.message.reply_text(set_pip_size(a[0],float(a[1])))
    except ValueError: await update.message.reply_text("Must be a number.")
def handle_instrument_callback(data):
    if data.startswith("confirm_remove:"): return True, remove_instrument(data.split(":",1)[1])
    if data=="cancel_remove": return True, "Cancelled."
    return False, None
