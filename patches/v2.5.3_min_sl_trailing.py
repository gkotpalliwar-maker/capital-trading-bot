import re
import os

print("v2.5.3: Minimum SL Distance + Correct Trailing Stop")
print("=" * 50)

BOT_DIR = os.path.join(os.getcwd(), "bot")
exec_path = os.path.join(BOT_DIR, "execution.py")

with open(exec_path) as f:
    code = f.read()

changes = 0

# ── 1. Add minimum SL distance enforcement ──
if "MIN_SL_DISTANCE" not in code:
    # Add config dict after imports
    min_sl_config = """
# v2.5.3: Minimum SL distance per instrument (in price points)
MIN_SL_DISTANCE = {
    "EURUSD": 0.0015, "GBPUSD": 0.0020, "USDJPY": 0.150,
    "AUDUSD": 0.0015, "NZDUSD": 0.0015, "USDCAD": 0.0020, "USDCHF": 0.0015,
    "GOLD": 2.0, "SILVER": 0.15, "OIL_CRUDE": 0.50,
    "US100": 30.0, "US500": 8.0, "US30": 50.0,
    "BTCUSD": 200.0, "ETHUSD": 15.0,
}
"""
    # Insert after the last top-level import or config block
    # Find a good insertion point - after DEFAULT_SIZE or after imports
    for marker in ["DEFAULT_SIZE", "INSTRUMENTS", "logger = "]:
        idx = code.find(marker)
        if idx >= 0:
            line_end = code.index("\n", code.index("\n", idx) + 1)
            # Skip to end of dict if it's a dict
            if "{" in code[idx:line_end+50]:
                brace_count = 0
                for i in range(idx, len(code)):
                    if code[i] == "{": brace_count += 1
                    elif code[i] == "}": brace_count -= 1
                    if brace_count == 0 and i > idx:
                        line_end = code.index("\n", i)
                        break
            code = code[:line_end+1] + min_sl_config + code[line_end+1:]
            changes += 1
            print("  + Added MIN_SL_DISTANCE config")
            break

    # Add SL enforcement in order creation
    # Find where stopLevel is set: order["stopLevel"] = stop_loss
    sl_set = code.find('order["stopLevel"] = stop_loss')
    if sl_set < 0:
        sl_set = code.find("order[\'stopLevel\'] = stop_loss")
    if sl_set >= 0:
        line_end = code.index("\n", sl_set)
        enforce_code = """
    # v2.5.3: Enforce minimum SL distance
    min_dist = MIN_SL_DISTANCE.get(epic, 0)
    if min_dist > 0 and stop_loss is not None and current_price > 0:
        actual_dist = abs(current_price - stop_loss)
        if actual_dist < min_dist:
            if direction.upper() == "BUY":
                stop_loss = current_price - min_dist
            else:
                stop_loss = current_price + min_dist
            order["stopLevel"] = round(stop_loss, 5)
            logger.warning(f"SL too tight ({actual_dist:.5f}), enforced min {min_dist:.5f} -> SL={stop_loss:.5f}")"""
        code = code[:line_end] + enforce_code + code[line_end:]
        changes += 1
        print("  + Added minimum SL enforcement after stopLevel")

# ── 2. Add correct trailing stop (trailing_stop + sl_distance) ──
if "trailing_stop" not in code:
    # Find where order is posted: client.post("/api/v1/positions"
    post_idx = code.find('client.post("/api/v1/positions"')
    if post_idx < 0:
        post_idx = code.find("client.post(\'/api/v1/positions\'")
    if post_idx >= 0:
        # Insert trailing stop logic just before the post call
        # Find the line start
        line_start = code.rfind("\n", 0, post_idx) + 1
        indent = " " * (post_idx - line_start - len(code[line_start:post_idx].lstrip()) + len(code[line_start:post_idx]) - len(code[line_start:post_idx].lstrip()))
        # Get proper indent
        indent = code[line_start:post_idx].replace(code[line_start:post_idx].lstrip(), "")
        
        trailing_code = """
    # v2.5.3: Capital.com native trailing stop
    trailing_enabled = os.environ.get("TRAILING_STOP_ENABLED", "true").lower() == "true"
    if trailing_enabled and stop_loss is not None and current_price > 0:
        sl_dist = abs(current_price - stop_loss)
        if sl_dist > 0:
            order["trailing_stop"] = True
            order["sl_distance"] = round(sl_dist, 5)
            # Remove stopLevel when using sl_distance (they conflict)
            order.pop("stopLevel", None)
            logger.info(f"Trailing stop: sl_distance={sl_dist:.5f}")

"""
        code = code[:line_start] + trailing_code + code[line_start:]
        changes += 1
        print("  + Added trailing_stop + sl_distance before order post")

# Ensure os import exists
if "import os" not in code.split("from __future__")[0] if "from __future__" in code else code[:500]:
    # Add after __future__ import if exists
    future_idx = code.find("from __future__")
    if future_idx >= 0:
        line_end = code.index("\n", future_idx)
        code = code[:line_end+1] + "import os\n" + code[line_end+1:]
    else:
        code = "import os\n" + code
    print("  + Added os import")

with open(exec_path, "w") as f:
    f.write(code)
print(f"  execution.py: {changes} changes")

print()
print("=" * 50)
print("Done! Min SL + trailing stop applied.")
print("Config: TRAILING_STOP_ENABLED=true in .env")
print("Restart bot to apply.")
