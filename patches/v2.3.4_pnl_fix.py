import re, os

# ============================================================
# v2.3.4 Patcher: P&L Currency Conversion Fix
# 
# Bug: _fetch_close_details uses wrong API field matching:
#   - Matches by 'reference' field (doesn't work)
#   - Uses 'profitAndLoss' field (not always present)
#   - Fallback gives raw quote currency (e.g. JPY) not SGD
#
# Fix: 
#   - Match by dealId using deal_id+1 hex increment pattern
#   - Use 'size' field = actual P&L in account currency (SGD)
#   - Fallback returns P&L=0 with warning instead of wrong value
# ============================================================

path = os.path.join("bot", "execution.py")
if not os.path.exists(path):
    print(f"  \u26a0\ufe0f {path} not found")
    exit(0)

with open(path) as f:
    code = f.read()
orig = code

NEW_FETCH = 'def _fetch_close_details(client, deal_id, trade_info):\n    """Fetch actual close price & P&L for a broker-closed position.\n    \n    Capital.com API patterns:\n    - Close transaction dealId = open dealId with last hex segment + 1\n    - Transaction API \'size\' field = P&L in account currency (SGD)\n    """\n    import logging\n    from datetime import datetime, timedelta, timezone\n    logger = logging.getLogger("execution")\n    entry = float(trade_info.get("entry_price", 0) or 0)\n    direction = trade_info.get("direction", "BUY")\n    size = float(trade_info.get("size", 0) or 0)\n    sl = float(trade_info.get("stop_loss", 0) or 0)\n    tp = float(trade_info.get("take_profit", 0) or 0)\n\n    # Calculate expected close deal_id (last hex segment + 1)\n    close_deal_id = None\n    try:\n        parts = deal_id.rsplit("-", 1)\n        if len(parts) == 2:\n            last_hex = int(parts[1], 16)\n            close_deal_id = f"{parts[0]}-{(last_hex + 1):012x}"\n    except Exception as e:\n        logger.warning(f"Could not compute close deal_id from {deal_id}: {e}")\n\n    # Try transaction history API\n    try:\n        now = datetime.now(timezone.utc)\n        from_dt = (now - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S")\n        to_dt = now.strftime("%Y-%m-%dT%H:%M:%S")\n\n        resp = client.get("/api/v1/history/transactions", {\n            "from": from_dt, "to": to_dt, "type": "ALL"\n        })\n        transactions = resp.get("transactions", [])\n        logger.info(f"P&L lookup: {len(transactions)} txns, looking for {close_deal_id or deal_id}")\n\n        for tx in transactions:\n            tx_deal_id = str(tx.get("dealId", ""))\n            tx_ref = str(tx.get("reference", ""))\n            tx_type = str(tx.get("transactionType", ""))\n\n            # Primary: close_deal_id (hex +1), Secondary: deal_id match\n            matched = False\n            if close_deal_id and tx_deal_id == close_deal_id:\n                matched = True\n            elif deal_id == tx_deal_id:\n                matched = True\n            elif deal_id in tx_ref:\n                matched = True\n\n            if matched and tx_type == "TRADE":\n                # \'size\' field = P&L in account currency (SGD)\n                pnl_raw = str(tx.get("size", "0")).replace(",", "")\n                pnl = float(pnl_raw) if pnl_raw else 0\n\n                # Also check profitAndLoss as secondary\n                if pnl == 0:\n                    pnl_alt = str(tx.get("profitAndLoss", "0"))\n                    pnl_alt = re.sub(r"[A-Z]{3}\\\\s*", "", pnl_alt).replace(",", "").strip()\n                    try:\n                        pnl = float(pnl_alt) if pnl_alt else 0\n                    except ValueError:\n                        pass\n\n                # Close price from available fields\n                close_price = 0.0\n                for field in ["closeLevel", "openLevel", "level"]:\n                    val = tx.get(field, 0)\n                    if val:\n                        close_price = float(val)\n                        break\n\n                # Estimate close price from P&L if not available\n                if close_price == 0 and pnl != 0 and entry > 0 and size > 0:\n                    if direction == "BUY":\n                        close_price = entry + (pnl / size)\n                    else:\n                        close_price = entry - (pnl / size)\n\n                if close_price == 0 and sl > 0:\n                    close_price = sl\n\n                logger.info(f"P&L found: {deal_id} close={close_price:.5f}, pnl={pnl:.2f} SGD")\n                return close_price, pnl\n\n        logger.warning(f"No matching transaction for {deal_id} (close_id={close_deal_id})")\n\n    except Exception as e:\n        logger.warning(f"Transaction API failed for {deal_id}: {e}")\n\n    # Fallback: SL as close price, P&L = 0 (unknown, will backfill later)\n    if sl > 0:\n        logger.warning(f"P&L UNKNOWN for {deal_id} - using SL as close, P&L=0")\n        return sl, 0.0\n\n    logger.warning(f"No close details for {deal_id}")\n    return 0, 0\n'


# Replace existing _fetch_close_details using regex
pattern = r'def _fetch_close_details\(.*?\n(?=\ndef |\nclass |\Z)'
m = re.search(pattern, code, re.DOTALL)
if m:
    code = code[:m.start()] + NEW_FETCH.strip() + "\n\n\n" + code[m.end():]
    print("  \u2705 Replaced _fetch_close_details with v2.3.4 version")
elif "_fetch_close_details" in code:
    # Alt: function at end of file or before another function
    start = code.find("def _fetch_close_details(")
    if start >= 0:
        next_def = code.find("\ndef ", start + 1)
        if next_def == -1:
            next_def = len(code)
        code = code[:start] + NEW_FETCH.strip() + "\n\n\n" + code[next_def:]
        print("  \u2705 Replaced _fetch_close_details (alt pattern)")
    else:
        print("  \u26a0\ufe0f Could not locate _fetch_close_details boundaries")
else:
    # Function doesn't exist yet - add before get_current_price
    idx = code.find("def get_current_price")
    if idx > 0:
        code = code[:idx] + NEW_FETCH.strip() + "\n\n\n" + code[idx:]
    else:
        code += "\n\n" + NEW_FETCH.strip() + "\n"
    print("  \u2705 Added _fetch_close_details v2.3.4 (was missing)")

# Ensure 'import re' is available in execution.py
lines_20 = code.split("\n")[:20]
if not any("import re" in l for l in lines_20):
    first_import = code.find("import ")
    if first_import >= 0:
        code = code[:first_import] + "import re\n" + code[first_import:]
        print("  \u2705 Added 'import re' to execution.py")

if code != orig:
    with open(path, "w") as f:
        f.write(code)
    print("\n\u2705 execution.py patched with v2.3.4 P&L fix!")
else:
    print("\n\u23ed\ufe0f No changes needed")
