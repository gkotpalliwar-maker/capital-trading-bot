import re, os, logging
logger = logging.getLogger("pnl_fix")

path = os.path.join(os.path.dirname(__file__), "..", "bot", "execution.py")
with open(path) as f:
    code = f.read()
orig = code

helper = '''
def _fetch_close_details(client, deal_id, trade_info):
    """Fetch actual P&L from Capital.com transaction history."""
    import logging
    logger = logging.getLogger("execution")
    try:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        from_dt = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        to_dt = now.strftime("%Y-%m-%dT%H:%M:%S")
        # Close deal_id = open deal_id + 1 (Capital.com pattern)
        parts = deal_id.rsplit("-", 1)
        close_did = f"{parts[0]}-{int(parts[1], 16) + 1:012x}" if len(parts) == 2 else deal_id
        resp = client.get("/api/v1/history/transactions", {"from": from_dt, "to": to_dt})
        for tx in resp.get("transactions", []):
            if tx.get("dealId") == close_did and tx.get("transactionType") == "TRADE" and "closed" in tx.get("note", "").lower():
                pnl = float(tx.get("size", 0))
                logger.info(f"Tx match: {deal_id} -> {close_did}, PnL={pnl:.2f} {tx.get('currency','')}") 
                return 0, pnl
    except Exception as e:
        logger.warning(f"Transaction API failed: {e}")
    # Fallback: SL estimate
    entry = float(trade_info.get("entry_price", 0) or 0)
    sl = float(trade_info.get("stop_loss", 0) or 0)
    size = float(trade_info.get("size", 0) or 0)
    d = trade_info.get("direction", "BUY")
    if sl > 0 and entry > 0 and size > 0:
        raw = (sl - entry) if d == "BUY" else (entry - sl)
        logger.info(f"SL fallback: {deal_id}, PnL={raw*size:.2f}")
        return sl, raw * size
    return 0, 0
'''

if "_fetch_close_details" not in code:
    idx = code.find("def sync_positions_with_db")
    if idx > 0:
        code = code[:idx] + helper + "\n" + code[idx:]
        print("  Added _fetch_close_details()")

old_call = 'db.close_trade_record(t["deal_id"], reason="broker_closed")'
new_call = 'close_price, pnl = _fetch_close_details(client, t["deal_id"], t)\n            db.close_trade_record(t["deal_id"], close_price=close_price, pnl=pnl, reason="broker_closed")'
if old_call in code:
    code = code.replace(old_call, new_call)
    print("  Fixed close call")

if code != orig:
    with open(path, "w") as f:
        f.write(code)
    print("  execution.py patched!")
