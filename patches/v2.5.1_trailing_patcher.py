import re
import os

print("v2.5.1: Capital.com Native Trailing Stop")
print("=" * 50)

BOT_DIR = os.path.join(os.getcwd(), "bot")

# ── 1. Patch execution.py — add trailingStop to order creation ──
exec_path = os.path.join(BOT_DIR, "execution.py")
with open(exec_path) as f:
    code = f.read()

changes = 0

# Find the order dict and add trailingStop after it
# Pattern: the order = {...} block followed by if stop_loss / if take_profit
if "trailingStop" not in code:
    # Find where stopLevel is set
    sl_line = "order[\"stopLevel\"] = stop_loss"
    if sl_line in code:
        idx = code.index(sl_line) + len(sl_line)
        # Find the next newline
        nl = code.index("\n", idx)
        # Insert trailing stop logic after the SL line
        trailing_code = """
    # v2.5.1: Enable Capital.com native trailing stop
    trailing_enabled = os.environ.get("TRAILING_STOP_ENABLED", "true").lower() == "true"
    if trailing_enabled and stop_loss is not None:
        trail_distance = abs(current_price - stop_loss)
        if trail_distance > 0:
            order["trailingStop"] = True
            order["trailingStopDistance"] = round(trail_distance, 5)
            logger.info(f"Trailing stop: distance={trail_distance:.5f}")"""
        code = code[:nl] + trailing_code + code[nl:]
        changes += 1
        print("  + Added trailingStop to order creation")
    else:
        # Try alternate pattern
        alt = 'order["stopLevel"]'
        if alt in code:
            idx = code.index(alt)
            line_end = code.index("\n", idx)
            trailing_code = """
    # v2.5.1: Enable Capital.com native trailing stop
    trailing_enabled = os.environ.get("TRAILING_STOP_ENABLED", "true").lower() == "true"
    if trailing_enabled and stop_loss is not None:
        trail_distance = abs(current_price - stop_loss)
        if trail_distance > 0:
            order["trailingStop"] = True
            order["trailingStopDistance"] = round(trail_distance, 5)"""
            code = code[:line_end] + trailing_code + code[line_end:]
            changes += 1
            print("  + Added trailingStop (alt pattern)")
        else:
            print("  ! Could not find stopLevel pattern in execution.py")

    # Add os import if missing
    if "import os" not in code:
        code = "import os\n" + code
        print("  + Added os import")

    if changes > 0:
        with open(exec_path, "w") as f:
            f.write(code)
        print(f"  execution.py: {changes} changes")
else:
    print("  execution.py: trailingStop already present")

print()
print("=" * 50)
print("Done! Native trailing stops enabled on new positions.")
print("Config: TRAILING_STOP_ENABLED=true (default) in .env")
print("Distance = SL distance from entry (same as risk)")
