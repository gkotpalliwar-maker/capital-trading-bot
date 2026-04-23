
import os
import re
import shutil
from datetime import datetime

print("=" * 60)
print("  v2.6.1 EMERGENCY REPAIR")
print("  Fixing corrupted execution.py + scanner.py")
print("=" * 60)

BOT_DIR = os.path.join(os.getcwd(), "bot")
BACKUP_DIR = os.path.join(os.getcwd(), "backups", datetime.now().strftime("%Y%m%d_%H%M%S"))
os.makedirs(BACKUP_DIR, exist_ok=True)

# ============================================================
# PART 1: FIX execution.py
# ============================================================
print("\n" + "=" * 60)
print("  PART 1: Fixing execution.py")
print("=" * 60)

exec_path = os.path.join(BOT_DIR, "execution.py")
if not os.path.exists(exec_path):
    print(f"  ERROR: {exec_path} not found")
else:
    # Backup
    shutil.copy(exec_path, os.path.join(BACKUP_DIR, "execution.py.bak"))
    print(f"  Backed up to {BACKUP_DIR}/execution.py.bak")
    
    with open(exec_path) as f:
        lines = f.readlines()
    
    print(f"  Original: {len(lines)} lines")
    print(f"\n  Lines 55-70 (around error):")
    for i in range(max(0, 54), min(len(lines), 70)):
        marker = ">>>" if i == 58 else "   "  # line 59 is index 58
        print(f"  {marker} {i+1:4d}: {lines[i].rstrip()[:70]}")
    
    # Strategy: Remove ALL trailing stop related code
    # Find and remove: any line with trailing_stop, sl_distance, trailingStop
    # Also fix any malformed elif/if statements
    
    code = "".join(lines)
    orig = code
    
    # 1. Remove entire trailing stop conditional blocks
    # Pattern: if TRAILING_STOP_ENABLED: ... (multi-line block)
    code = re.sub(
        r"\n\s*# *(?:Native|Enable|Trailing).*trailing.*\n(?:.*\n)*?(?=\n\s*(?:def |#|if |$))",
        "\n", code, flags=re.IGNORECASE
    )
    
    # 2. Remove standalone trailing stop lines
    patterns_to_remove = [
        r".*trailing_stop.*=.*\n",
        r".*sl_distance.*=.*\n",
        r".*trailingStop.*=.*\n",
        r".*trailingStopDistance.*=.*\n",
        r".*TRAILING_STOP_ENABLED.*\n",
        r".*trailing_sl_distance.*\n",
        r".*trailing_sl_pct.*\n",
    ]
    for pat in patterns_to_remove:
        if re.search(pat, code, re.IGNORECASE):
            code = re.sub(pat, "", code, flags=re.IGNORECASE)
    
    # 3. Fix the specific corrupted line: elif trailing_ "distance": ...
    # This is garbage - remove the entire line
    code = re.sub(r".*elif trailing_.*\"distance\".*\n", "", code)
    code = re.sub(r".*elif trailing_[^:]*:\s*trailing.*\n", "", code)
    
    # 4. Remove orphaned elif without matching if
    # Find elif that comes after a line that doesn't end with :
    lines = code.split("\n")
    fixed_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        
        # Skip empty lines and comments
        if not stripped or stripped.startswith("#"):
            fixed_lines.append(line)
            i += 1
            continue
        
        # Check for orphaned elif
        if stripped.startswith("elif "):
            # Look back for matching if
            found_if = False
            for j in range(len(fixed_lines) - 1, -1, -1):
                prev = fixed_lines[j].strip()
                if prev.startswith("if ") and prev.endswith(":"):
                    found_if = True
                    break
                if prev.startswith("def ") or prev.startswith("class "):
                    break
            if not found_if:
                print(f"  Removing orphaned elif at line {i+1}: {stripped[:50]}...")
                i += 1
                continue
        
        fixed_lines.append(line)
        i += 1
    
    code = "\n".join(fixed_lines)
    
    # 5. Fix syntax: unmatched braces, commas
    code = re.sub(r",\s*}", "}", code)  # Remove trailing comma before }
    code = re.sub(r":\s*}", ": {}}", code)  # Fix empty dict value with extra }
    code = re.sub(r"}\s*}", "}", code)  # Remove double }}
    
    # 6. Ensure open_position function has clean SL/TP handling
    # Find the order dict building and ensure stopLevel is set simply
    if "stopLevel" not in code:
        # Need to add it back - find where order dict is built
        match = re.search(r"(order\s*=\s*\{[^}]+\})", code, re.DOTALL)
        if match:
            print("  WARNING: stopLevel not found, may need manual fix")
    
    # Write fixed file
    with open(exec_path, "w") as f:
        f.write(code)
    
    # Verify it compiles
    try:
        compile(code, exec_path, "exec")
        print(f"\n  \u2705 execution.py compiles successfully!")
    except SyntaxError as e:
        print(f"\n  \u274c Still has syntax error at line {e.lineno}: {e.msg}")
        print(f"     {e.text}")
        print(f"     Manual fix needed. Showing lines {max(1,e.lineno-3)}-{e.lineno+3}:")
        with open(exec_path) as f:
            lines = f.readlines()
        for i in range(max(0, e.lineno-4), min(len(lines), e.lineno+3)):
            marker = ">>>" if i == e.lineno-1 else "   "
            print(f"     {marker} {i+1:4d}: {lines[i].rstrip()}")

# ============================================================
# PART 2: FIX scanner.py
# ============================================================
print("\n" + "=" * 60)
print("  PART 2: Fixing scanner.py")
print("=" * 60)

scan_path = os.path.join(BOT_DIR, "scanner.py")
if not os.path.exists(scan_path):
    print(f"  ERROR: {scan_path} not found")
else:
    # Backup
    shutil.copy(scan_path, os.path.join(BACKUP_DIR, "scanner.py.bak"))
    print(f"  Backed up to {BACKUP_DIR}/scanner.py.bak")
    
    with open(scan_path) as f:
        lines = f.readlines()
    
    print(f"  Original: {len(lines)} lines")
    print(f"\n  Lines 178-190 (around error):")
    for i in range(max(0, 177), min(len(lines), 190)):
        marker = ">>>" if i == 181 else "   "  # line 182 is index 181
        print(f"  {marker} {i+1:4d}: {lines[i].rstrip()[:70]}")
    
    code = "".join(lines)
    orig = code
    
    # 1. Remove v2.6.0 injections (import block, trailing_manager, update calls)
    code = re.sub(
        r"\ntry:\n\s+from bot_trailing import TrailingManager\n\s+HAS_TRAILING = True\nexcept ImportError:\n\s+HAS_TRAILING = False\n*",
        "\n", code
    )
    code = re.sub(r"\n# Bot-side trailing manager\n.*?trailing_manager.*?\n*", "\n", code)
    code = re.sub(
        r"\n*\s+# Bot-side trailing stop updates\n(?:\s+.*?\n)*?\s+logger\.warning\(f\"Trailing update error:.*?\"\)\n*",
        "\n", code
    )
    
    # 2. Fix indentation - find lines with unexpected indent after non-block
    lines = code.split("\n")
    fixed_lines = []
    prev_indent = 0
    prev_ends_block = False
    
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped:  # empty line
            fixed_lines.append(line)
            continue
        
        curr_indent = len(line) - len(stripped)
        
        # If this line has way more indent than expected and prev didn't start a block
        if (curr_indent > prev_indent + 4 and 
            not prev_ends_block and 
            stripped and not stripped.startswith("#")):
            # Check if it's a control statement that shouldn't be indented
            if stripped.startswith(("if ", "for ", "while ", "try:", "def ", "class ", "return ", "elif ", "else:", "except", "finally:")):
                # Align with previous non-empty code line
                fixed_lines.append(" " * prev_indent + stripped)
                print(f"  Fixed indent at line {i+1}: {curr_indent} -> {prev_indent}")
                curr_indent = prev_indent
            else:
                fixed_lines.append(line)
        else:
            fixed_lines.append(line)
        
        # Track for next iteration
        if stripped and not stripped.startswith("#"):
            prev_indent = curr_indent
            prev_ends_block = stripped.rstrip().endswith(":")
    
    code = "\n".join(fixed_lines)
    
    # Write fixed file
    with open(scan_path, "w") as f:
        f.write(code)
    
    # Verify it compiles
    try:
        compile(code, scan_path, "exec")
        print(f"\n  \u2705 scanner.py compiles successfully!")
    except SyntaxError as e:
        print(f"\n  \u274c Still has syntax error at line {e.lineno}: {e.msg}")
        print(f"     {e.text}")
        print(f"     Manual fix needed.")

# ============================================================
# SUMMARY
# ============================================================
print("\n" + "=" * 60)
print("  REPAIR COMPLETE")
print("=" * 60)
print(f"\n  Backups saved to: {BACKUP_DIR}")
print("")
print("  If errors remain, you can restore with:")
print(f"    cp {BACKUP_DIR}/execution.py.bak bot/execution.py")
print(f"    cp {BACKUP_DIR}/scanner.py.bak bot/scanner.py")
print("")
print("  Next steps:")
print("    sudo systemctl start trading-bot")
print("    sudo journalctl -u trading-bot -f")
print("=" * 60)
