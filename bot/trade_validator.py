import json, logging, sqlite3
from pathlib import Path
logger = logging.getLogger("validator")
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "bot.db"

def get_open_trades_for_validation():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    trades = [dict(r) for r in conn.execute(
        "SELECT * FROM trades WHERE status='open'"
    ).fetchall()]
    conn.close()
    return trades

def get_trade_health(trade, current_price):
    entry = float(trade.get("entry_price", 0) or 0)
    sl = float(trade.get("stop_loss", 0) or 0)
    tp = float(trade.get("take_profit", 0) or 0)
    d = trade.get("direction", "BUY")
    if entry == 0 or sl == 0: return {"status": "unknown", "pnl_r": 0, "sl_pct": 0, "tp_pct": 0}
    risk = abs(entry - sl)
    if risk == 0: return {"status": "unknown", "pnl_r": 0, "sl_pct": 0, "tp_pct": 0}
    if d == "BUY":
        pnl_dist = current_price - entry
        sl_dist = current_price - sl
        tp_dist = tp - current_price if tp > 0 else 0
    else:
        pnl_dist = entry - current_price
        sl_dist = sl - current_price
        tp_dist = current_price - tp if tp > 0 else 0
    pnl_r = round(pnl_dist / risk, 2)
    sl_pct = round(sl_dist / current_price * 100, 2) if current_price > 0 else 0
    tp_pct = round(tp_dist / current_price * 100, 2) if current_price > 0 else 0
    if pnl_r >= 1.5: status = "excellent"
    elif pnl_r >= 1.0: status = "good"
    elif pnl_r >= 0: status = "breakeven_zone"
    elif pnl_r >= -0.5: status = "drawdown"
    else: status = "danger"
    return {"status": status, "pnl_r": pnl_r, "sl_pct": sl_pct, "tp_pct": tp_pct}

def validate_trade(trade, close_price, mss_events=None):
    d = trade["direction"]
    inv = trade.get("invalidation_price")
    sl = float(trade.get("stop_loss", 0) or 0)
    if inv is not None:
        if d == "BUY" and close_price < inv: return False, f"Structure broken: {close_price:.5f} < inv {inv:.5f}"
        if d == "SELL" and close_price > inv: return False, f"Structure broken: {close_price:.5f} > inv {inv:.5f}"
    if sl > 0:
        if d == "BUY" and close_price < sl: return False, f"Price below SL"
        if d == "SELL" and close_price > sl: return False, f"Price above SL"
    if mss_events:
        counter = [e for e in mss_events if e.get("direction") != d and e.get("is_reversal")]
        if counter: return False, f"Counter MSS: {counter[-1].get('type', '?')}"
    return True, "Valid"

def store_pattern_context(deal_id, mss_type, pattern_tf, inv_price, ctx=None):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE trades SET mss_type=?,pattern_tf=?,invalidation_price=?,pattern_context=?,validation_status='valid' WHERE deal_id=?",
        (mss_type, pattern_tf, inv_price, json.dumps(ctx or {}), deal_id))
    conn.commit(); conn.close()

def mark_trade_invalidated(deal_id, reason):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE trades SET validation_status='invalidated' WHERE deal_id=?", (deal_id,))
    conn.commit(); conn.close()



def init_validation_schema():
    conn = sqlite3.connect(str(DB_PATH)); c = conn.cursor()
    c.execute("PRAGMA table_info(trades)"); cols = [r[1] for r in c.fetchall()]
    for col, tp in {"mss_type":"TEXT","pattern_tf":"TEXT","invalidation_price":"REAL","pattern_context":"TEXT","validation_status":"TEXT DEFAULT 'valid'"}.items():
        if col not in cols: c.execute(f"ALTER TABLE trades ADD COLUMN {col} {tp}"); logger.info(f"Added {col}")
    conn.commit(); conn.close()

def validate_all_open_trades(fetch_fn, indicator_fn, mss_fn, close_fn, notify_fn):
    trades = get_open_trades_for_validation()
    if not trades: return []
    closed = []
    for t in trades:
        inv = t.get("invalidation_price")
        if not inv: continue  # skip trades without invalidation_price for auto-close
        try:
            df = fetch_fn(t["epic"], t.get("pattern_tf") or "M15", 200)
            if df is None or df.empty: continue
            df = indicator_fn(df)
            ok, reason = validate_trade(t, df["close"].iloc[-1], mss_fn(df))
            if not ok:
                logger.warning(f"INVALID: {t['deal_id']} {t['epic']} - {reason}")
                try: close_fn(t["deal_id"])
                except Exception as e: logger.error(f"Close failed: {e}")
                mark_trade_invalidated(t["deal_id"], reason)
                try: notify_fn(f"PATTERN INVALIDATED\n{t['deal_id']}\n{t['epic']} ({t['direction']})\nReason: {reason}\nCLOSED automatically")
                except: pass
                closed.append({"deal_id": t["deal_id"], "reason": reason})
        except Exception as e: logger.error(f"Validate err {t.get('deal_id')}: {e}")
    return closed

def compute_invalidation_price(direction, mss_events):
    r = [e for e in mss_events if e.get("direction") == direction]
    if not r: return None
    return sorted(r, key=lambda e: (e.get("is_reversal", False), e.get("index", 0)), reverse=True)[0].get("break_level")
