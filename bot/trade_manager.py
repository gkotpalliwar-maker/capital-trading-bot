import os, logging, sqlite3
from pathlib import Path
logger = logging.getLogger("trade_manager")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "bot.db"
BREAKEVEN_TRIGGER_R = float(os.getenv("BREAKEVEN_TRIGGER_R", "1.0"))
PARTIAL_TP_ENABLED = os.getenv("PARTIAL_TP_ENABLED", "true").lower() == "true"
PARTIAL_TP_TARGET_R = float(os.getenv("PARTIAL_TP_TARGET_R", "1.5"))
PARTIAL_TP_RATIO = float(os.getenv("PARTIAL_TP_RATIO", "0.5"))
TRAILING_AFTER_PARTIAL_ATR = float(os.getenv("TRAILING_AFTER_PARTIAL_ATR", "1.5"))

def init_trade_manager_schema():
    if not DB_PATH.exists(): return
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("PRAGMA table_info(trades)")
    cols = [r[1] for r in c.fetchall()]
    for col, td in {"breakeven_hit":"INTEGER DEFAULT 0","partial_tp_hit":"INTEGER DEFAULT 0","partial_close_deal_id":"TEXT","ml_score":"REAL","original_size":"REAL"}.items():
        if col not in cols:
            c.execute(f"ALTER TABLE trades ADD COLUMN {col} {td}")
            logger.info(f"Added: {col}")
    conn.commit()
    conn.close()

def get_open_trades_for_management():
    if not DB_PATH.exists(): return []
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    trades = [dict(r) for r in conn.cursor().execute("SELECT * FROM trades WHERE status='open'").fetchall()]
    conn.close()
    return trades

def _calc_r(t, price):
    entry, sl, d = float(t.get("entry_price",0)), float(t.get("stop_loss",0)), t.get("direction","BUY")
    if entry == 0 or sl == 0: return 0
    risk = abs(entry - sl)
    if risk == 0: return 0
    return ((price - entry) if d == "BUY" else (entry - price)) / risk

def check_breakeven_triggers(trades, prices, update_sl_fn, notify_fn=None):
    triggered = []
    for t in trades:
        if t.get("breakeven_hit"): continue
        did, epic, entry = t.get("deal_id"), t.get("epic"), float(t.get("entry_price",0))
        if epic not in prices: continue
        r = _calc_r(t, prices[epic])
        if r >= BREAKEVEN_TRIGGER_R:
            logger.info(f"BE: {did} at {r:.2f}R")
            try:
                update_sl_fn(did, entry)
                conn = sqlite3.connect(str(DB_PATH))
                conn.cursor().execute("UPDATE trades SET breakeven_hit=1 WHERE deal_id=?",(did,))
                conn.commit()
                conn.close()
                triggered.append({"deal_id":did,"epic":epic,"r":r,"sl":entry})
                if notify_fn: notify_fn(f"🛡️ <b>BREAKEVEN</b>\n<code>{did[:8]}...</code> {epic}\nR: {r:.2f}\nSL→entry")
            except Exception as e: logger.error(f"BE fail: {e}")
    return triggered

def check_partial_tp_triggers(trades, prices, partial_fn, update_sl_fn, atr_fn, notify_fn=None):
    if not PARTIAL_TP_ENABLED: return []
    triggered = []
    for t in trades:
        if t.get("partial_tp_hit"): continue
        did, epic, entry, d = t.get("deal_id"), t.get("epic"), float(t.get("entry_price",0)), t.get("direction")
        size = float(t.get("size",0)) or float(t.get("original_size",0))
        if epic not in prices or size == 0: continue
        cur = prices[epic]
        r = _calc_r(t, cur)
        if r >= PARTIAL_TP_TARGET_R:
            logger.info(f"PTP: {did} at {r:.2f}R")
            try:
                ps = size * PARTIAL_TP_RATIO
                pdid = partial_fn(did, ps)
                atr = atr_fn(epic) or abs(entry - float(t.get("stop_loss",entry)))
                td = atr * TRAILING_AFTER_PARTIAL_ATR
                new_sl = (cur - td) if d == "BUY" else (cur + td)
                update_sl_fn(did, new_sl)
                conn = sqlite3.connect(str(DB_PATH))
                conn.cursor().execute("UPDATE trades SET partial_tp_hit=1, partial_close_deal_id=? WHERE deal_id=?",(pdid,did))
                conn.commit()
                conn.close()
                triggered.append({"deal_id":did,"epic":epic,"r":r,"closed":ps,"sl":new_sl})
                if notify_fn: notify_fn(f"💰 <b>PARTIAL TP</b>\n<code>{did[:8]}...</code> {epic}\nR: {r:.2f}\nClosed: {PARTIAL_TP_RATIO:.0%}\nTrailing SL: {new_sl:.5f}")
            except Exception as e: logger.error(f"PTP fail: {e}")
    return triggered

def manage_trades(trades, prices, update_sl_fn, partial_fn, atr_fn, notify_fn=None):
    return {"breakeven": check_breakeven_triggers(trades, prices, update_sl_fn, notify_fn),
            "partial_tp": check_partial_tp_triggers(trades, prices, partial_fn, update_sl_fn, atr_fn, notify_fn)}

def get_trade_status(did=None):
    trades = get_open_trades_for_management()
    if did: trades = [t for t in trades if t.get("deal_id","").startswith(did)]
    return [{"deal_id":t.get("deal_id"),"epic":t.get("epic"),"direction":t.get("direction"),"entry_price":t.get("entry_price"),
             "stop_loss":t.get("stop_loss"),"breakeven_hit":bool(t.get("breakeven_hit")),"partial_tp_hit":bool(t.get("partial_tp_hit")),
             "ml_score":t.get("ml_score")} for t in trades]

def toggle_partial_tp(e=None):
    global PARTIAL_TP_ENABLED
    PARTIAL_TP_ENABLED = (not PARTIAL_TP_ENABLED) if e is None else e
    return PARTIAL_TP_ENABLED

def get_settings():
    return {"breakeven_trigger_r":BREAKEVEN_TRIGGER_R,"partial_tp_enabled":PARTIAL_TP_ENABLED,"partial_tp_target_r":PARTIAL_TP_TARGET_R,"partial_tp_ratio":PARTIAL_TP_RATIO,"trailing_after_partial_atr":TRAILING_AFTER_PARTIAL_ATR}