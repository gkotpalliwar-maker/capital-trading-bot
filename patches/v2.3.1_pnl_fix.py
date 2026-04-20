import re, os

path = os.path.join("bot", "execution.py")
with open(path) as f:
    code = f.read()

orig = code

# ── Add _fetch_close_details() before sync_positions_with_db ──
helper = """
def _fetch_close_details(client, deal_id, trade_info):
    \"\"\"Fetch actual close price & P&L for a broker-closed position.\"\"\"
    import logging
    logger = logging.getLogger("execution")
    entry = float(trade_info.get("entry_price", 0) or 0)
    direction = trade_info.get("direction", "BUY")
    size = float(trade_info.get("size", 0) or 0)
    sl = float(trade_info.get("stop_loss", 0) or 0)
    tp = float(trade_info.get("take_profit", 0) or 0)

    # Try Capital.com activity history API
    try:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        from_dt = (now - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S")
        to_dt = now.strftime("%Y-%m-%dT%H:%M:%S")

        resp = client.get("/api/v1/history/activity", {
            "from": from_dt, "to": to_dt, "detailed": "true"
        })
        for act in resp.get("activities", []):
            details = act.get("details", {})
            for action in details.get("actions", []):
                if action.get("dealId") == deal_id:
                    level = float(action.get("level", 0) or 0)
                    if level > 0 and entry > 0 and size > 0:
                        raw = (level - entry) if direction == "BUY" else (entry - level)
                        pnl = raw * size
                        logger.info(f"API close: {deal_id} @ {level}, PnL={pnl:.2f}")
                        return level, pnl
    except Exception as e:
        logger.warning(f"Activity API failed for {deal_id}: {e}")

    # Try transaction history
    try:
        resp2 = client.get("/api/v1/history/transactions", {
            "from": from_dt, "to": to_dt, "type": "ALL"
        })
        for tx in resp2.get("transactions", []):
            ref = str(tx.get("reference", ""))
            if deal_id in ref or ref == str(trade_info.get("deal_ref", "")):
                pnl_s = str(tx.get("profitAndLoss", "0")).replace(",", "")
                pnl = float(pnl_s) if pnl_s else 0
                cl = float(tx.get("closeLevel", 0) or 0)
                if cl > 0 or pnl != 0:
                    logger.info(f"Tx close: {deal_id} @ {cl}, PnL={pnl:.2f}")
                    return cl, pnl
    except Exception as e:
        logger.warning(f"Transaction API failed for {deal_id}: {e}")

    # Fallback: estimate from SL/TP
    if sl > 0 and entry > 0 and size > 0:
        raw = (sl - entry) if direction == "BUY" else (entry - sl)
        pnl = raw * size
        logger.info(f"SL estimate: {deal_id} @ {sl}, PnL={pnl:.2f}")
        return sl, pnl

    logger.warning(f"No close details for {deal_id}")
    return 0, 0

"""

if "_fetch_close_details" not in code:
    idx = code.find("def sync_positions_with_db")
    if idx > 0:
        code = code[:idx] + helper + "\n" + code[idx:]
        print("  \u2705 Added _fetch_close_details()")
    else:
        print("  \u26a0\ufe0f sync_positions_with_db not found")
else:
    print("  \u23ed\ufe0f _fetch_close_details already exists")

# ── Fix the close call to pass close_price and pnl ──
old_call = 'db.close_trade_record(t["deal_id"], reason="broker_closed")'
new_call = ('close_price, pnl = _fetch_close_details(client, t["deal_id"], t)\n'
            '            db.close_trade_record(t["deal_id"], close_price=close_price, pnl=pnl, reason="broker_closed")')

if old_call in code:
    code = code.replace(old_call, new_call)
    print("  \u2705 Fixed close call with close_price & pnl")
else:
    print("  \u23ed\ufe0f Close call already fixed")

if code != orig:
    with open(path, "w") as f:
        f.write(code)
    print("\n\u2705 execution.py patched!")
else:
    print("\n\u23ed\ufe0f No changes needed")
