
import os
import shutil
import glob

print("=" * 60)
print("  v2.6.2: TARGETED execution.py Fix")
print("  No regex. Line-by-line exact fixes.")
print("=" * 60)

TARGET = os.path.join(os.getcwd(), "bot", "execution.py")

# Step 1: Find and restore from backup
backup_dir = os.path.join(os.getcwd(), "backups")
BACKUP = None
if os.path.exists(backup_dir):
    for d in sorted(os.listdir(backup_dir), reverse=True):  # newest first
        bak = os.path.join(backup_dir, d, "execution.py.bak")
        if os.path.exists(bak):
            BACKUP = bak
            break

if BACKUP:
    shutil.copy(BACKUP, TARGET)
    print(f"\n  1. Restored from backup: {BACKUP}")
else:
    print(f"\n  1. No backup found, working with current file")

with open(TARGET) as f:
    lines = f.readlines()

lines = [l.rstrip("\n") for l in lines]
print(f"     {len(lines)} lines loaded")
changes = []

# ============================================================
# Fix A: Add MIN_SL_DISTANCE = { declaration
# ============================================================
for i in range(len(lines)):
    if "Minimum SL distance per instrument" in lines[i] and lines[i].strip().startswith("#"):
        # Check next non-empty line
        j = i + 1
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        if j < len(lines) and lines[j].strip().startswith('"') and "MIN_SL_DISTANCE" not in lines[j]:
            lines.insert(i + 1, "MIN_SL_DISTANCE = {")
            changes.append(f"A. Added 'MIN_SL_DISTANCE = {{' after line {i+1}")
        break

# ============================================================
# Fix B: Fix garbled elif trailing line
# ============================================================
for i in range(len(lines)):
    s = lines[i].strip()
    if s.startswith("elif trailing_") and '"distance"' in s:
        # Get the indentation of the original line
        indent = len(lines[i]) - len(lines[i].lstrip())
        sp = " " * indent
        lines[i] = sp + "elif trailing_sl_distance:"
        # Check if next non-empty line has trail_config assignment
        j = i + 1
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        if j < len(lines) and "trail_config" not in lines[j]:
            lines.insert(i + 1, sp + '    trail_config = {"type": "fixed", "distance": trailing_sl_distance}')
            changes.append(f"B. Fixed garbled elif + added trail_config at line {i+1}")
        else:
            changes.append(f"B. Fixed garbled elif at line {i+1}")
        break

# ============================================================
# Fix C: Fix truncated logger.info line
# ============================================================
for i in range(len(lines)):
    if 'logger.info("Opening %s' in lines[i] and lines[i].rstrip().endswith("direction,"):
        indent = len(lines[i]) - len(lines[i].lstrip())
        sp = " " * indent
        lines[i] = sp + 'logger.info("Opening %s on %s, size=%s, SL=%s, TP=%s", direction, epic, size, stop_loss, take_profit)'
        changes.append(f"C. Fixed truncated logger.info at line {i+1}")
        break

# ============================================================
# Fix D: Remove entire v2.5.3 native trailing stop block
# This block adds trailing_stop/sl_distance to the order and
# REMOVES stopLevel — which is the ROOT CAUSE of SL not being set.
# ============================================================
i = 0
removed_count = 0
new_lines = []
removing = False

while i < len(lines):
    s = lines[i].strip()
    
    # Detect start of v2.5.3 trailing block
    if not removing and "v2.5.3" in lines[i] and "trailing" in lines[i].lower():
        removing = True
        i += 1
        removed_count += 1
        continue
    
    if removing:
        # Keep removing trailing-related lines
        if (s == "" or
            "trailing" in s.lower() or
            "sl_dist" in s or
            "order.pop" in s or
            ("order[" in s and "distance" in s.lower()) or
            s.startswith("if trailing") or
            s.startswith("if sl_dist")):
            i += 1
            removed_count += 1
            continue
        else:
            # Hit a non-trailing line, stop removing
            removing = False
            new_lines.append(lines[i])
            i += 1
            continue
    
    new_lines.append(lines[i])
    i += 1

lines = new_lines
if removed_count > 0:
    changes.append(f"D. Removed v2.5.3 native trailing block ({removed_count} lines)")

# ============================================================
# Fix E: Clean up any double blank lines
# ============================================================
final_lines = []
for i, line in enumerate(lines):
    if line.strip() == "" and i > 0 and final_lines and final_lines[-1].strip() == "":
        continue  # skip consecutive blank lines
    final_lines.append(line)
lines = final_lines

# ============================================================
# Write and verify
# ============================================================
code = "\n".join(lines) + "\n"

print(f"\n  Changes applied:")
for c in changes:
    print(f"    - {c}")

try:
    compile(code, TARGET, "exec")
    with open(TARGET, "w") as f:
        f.write(code)
    print(f"\n  \u2705 execution.py COMPILES! ({len(lines)} lines)")
    print("  \u2705 Safe to start the bot.")
except SyntaxError as e:
    with open(TARGET, "w") as f:
        f.write(code)
    print(f"\n  \u274c Still has error at line {e.lineno}: {e.msg}")
    if e.text:
        print(f"     {e.text.rstrip()}")
    print(f"\n  Context around error:")
    for j in range(max(0, e.lineno-5), min(len(lines), e.lineno+4)):
        marker = ">>>" if j == e.lineno-1 else "   "
        print(f"  {marker} {j+1:4d}: {lines[j]}")
    print(f"\n  Run: cat -n bot/execution.py | head -{e.lineno+5} | tail -15")
    print(f"  Paste the output here so I can write the exact fix.")

print("\n" + "=" * 60)
print("  v2.6.2 complete.")
print("=" * 60)
