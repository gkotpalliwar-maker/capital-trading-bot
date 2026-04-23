
import os
import re

print("v2.6.0: SL Fix + Bot-Side Trailing Patcher")
print("=" * 55)

exec_path = os.path.join(os.getcwd(), "bot", "execution.py")
if not os.path.exists(exec_path):
    print(f"  ERROR: {exec_path} not found")
    exit(1)

with open(exec_path) as f:
    code = f.read()
orig = code
changes = []

# ── 1. Remove trailing_stop / sl_distance from order payload ──
# These patterns can appear in the order dict building
trailing_patterns = [
    (r'\s*["\']?trailing_stop["\']?\s*:\s*(?:True|False|true|false)[,\n]?', ''),
    (r'\s*["\']?sl_distance["\']?\s*:\s*[^,}\n]+[,\n]?', ''),
    (r'\s*["\']?sl_distance_price["\']?\s*:\s*[^,}\n]+[,\n]?', ''),
    (r'\s*["\']?trailingStop["\']?\s*:\s*(?:True|False|true|false)[,\n]?', ''),
    (r'\s*["\']?trailingStopDistance["\']?\s*:\s*[^,}\n]+[,\n]?', ''),
    (r'order\[["\']trailing_stop["\']\]\s*=\s*[^\n]+\n', ''),
    (r'order\[["\']sl_distance["\']\]\s*=\s*[^\n]+\n', ''),
]

for pattern, replacement in trailing_patterns:
    if re.search(pattern, code, re.IGNORECASE):
        code = re.sub(pattern, replacement, code, flags=re.IGNORECASE)
        changes.append(f"Removed trailing stop param matching: {pattern[:40]}...")

# ── 2. Remove TRAILING_STOP_ENABLED conditionals that add these params ──
# Pattern: if TRAILING_STOP_ENABLED: order["trailing_stop"] = True...
trailing_block = r'if\s+(?:TRAILING_STOP_ENABLED|os\.environ\.get\([^)]*TRAILING[^)]*\))[^:]*:[^\n]*\n(?:\s+order\[["\'][^"\']]+["\']\]\s*=\s*[^\n]+\n)+'
if re.search(trailing_block, code, re.IGNORECASE | re.DOTALL):
    code = re.sub(trailing_block, '', code, flags=re.IGNORECASE | re.DOTALL)
    changes.append("Removed TRAILING_STOP_ENABLED conditional block")

# ── 3. Ensure stopLevel is set cleanly ──
# Make sure the stop_loss -> stopLevel assignment is simple
# Pattern should be: if stop_loss: order["stopLevel"] = stop_loss
stop_pattern = r'if\s+stop_loss[^:]*:\s*\n\s*order\[["\']stopLevel["\']\]\s*=\s*stop_loss'
if not re.search(stop_pattern, code):
    # Look for the order dict building section and ensure stopLevel is clean
    # Find where order dict is built
    order_build = re.search(r'order\s*=\s*\{[^}]+\}', code, re.DOTALL)
    if order_build:
        order_text = order_build.group()
        if 'stopLevel' not in order_text:
            changes.append("Note: stopLevel not in initial order dict (added via conditional)")

# ── 4. Remove broken native trailing from open_position function ──
# If there's trailing logic inside open_position that calls the API with trailing params
trailing_in_open = r'# *(?:Native|Enable) trailing.*?\n(?:.*?(?:trailing_stop|sl_distance).*?\n)+'
if re.search(trailing_in_open, code, re.IGNORECASE):
    code = re.sub(trailing_in_open, '', code, flags=re.IGNORECASE)
    changes.append("Removed native trailing comment block from open_position")

# ── 5. Fix double-cleanup of trailing commas in dict ──
code = re.sub(r',\s*,', ',', code)  # Remove double commas
code = re.sub(r',\s*\}', '}', code)  # Remove trailing comma before }
code = re.sub(r',\s*\)', ')', code)  # Remove trailing comma before )

# ── 6. Ensure MIN_SL_DISTANCE doesn't interfere with stopLevel ──
# The MIN_SL check should modify stop_loss BEFORE it goes into order, not block it
min_sl_block = re.search(r'# *MIN_SL.*?(?=\ndef |\n#|$)', code, re.DOTALL)
if min_sl_block:
    block = min_sl_block.group()
    # Make sure it doesn't prevent SL from being set
    if 'stop_loss = None' in block or 'stopLevel.*= None' in block:
        changes.append("WARNING: MIN_SL block may be setting stop_loss to None")

if code != orig:
    with open(exec_path, 'w') as f:
        f.write(code)
    print(f"  ✅ execution.py patched ({len(changes)} changes)")
    for c in changes:
        print(f"     - {c}")
else:
    print("  ⏭️  No trailing stop params found to remove")

# ── Verify stopLevel is being set ──
with open(exec_path) as f:
    final = f.read()

if '"stopLevel"' in final or "'stopLevel'" in final:
    print("  ✅ stopLevel assignment present")
else:
    print("  ⚠️  WARNING: stopLevel not found in execution.py!")

if 'trailing_stop' in final.lower() or 'sl_distance' in final.lower():
    # Check if it's in comments only
    lines_with_trailing = [l for l in final.split('\n') 
                           if ('trailing_stop' in l.lower() or 'sl_distance' in l.lower())
                           and not l.strip().startswith('#')]
    if lines_with_trailing:
        print(f"  ⚠️  Still has trailing params in {len(lines_with_trailing)} lines (may need manual review)")
        for l in lines_with_trailing[:3]:
            print(f"       {l[:80]}...")
else:
    print("  ✅ No trailing stop API params in code")

print("\n" + "=" * 55)
print("v2.6.0 execution.py patch complete.")
