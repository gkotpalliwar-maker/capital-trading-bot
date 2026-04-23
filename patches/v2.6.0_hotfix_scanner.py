
import os
import re

print("v2.6.0 HOTFIX: Fix scanner.py IndentationError")
print("=" * 55)

path = os.path.join(os.getcwd(), "bot", "scanner.py")
if not os.path.exists(path):
    print(f"  ERROR: {path} not found")
    exit(1)

with open(path) as f:
    lines = f.readlines()

print(f"  scanner.py: {len(lines)} lines")
print(f"\n  Lines 175-195 (around the error):")
for i in range(max(0, 174), min(len(lines), 195)):
    marker = ">>>" if i == 181 else "   "
    print(f"  {marker} {i+1:4d}: {lines[i].rstrip()}")

# ── Strategy: Remove ALL v2.6.0 injections and re-add cleanly ──
code = "".join(lines)
orig = code

# 1. Remove the try/except import block for bot_trailing
code = re.sub(
    r"\ntry:\n\s+from bot_trailing import TrailingManager\n\s+HAS_TRAILING = True\nexcept ImportError:\n\s+HAS_TRAILING = False\n+",
    "\n", code
)

# 2. Remove the trailing_manager initialization
code = re.sub(
    r"\n# Bot-side trailing manager\n.*?trailing_manager.*?\n+",
    "\n", code
)

# 3. Remove the trailing update call block
code = re.sub(
    r"\n+\s+# Bot-side trailing stop updates\n(?:\s+.*?\n)*?\s+logger\.warning\(f\"Trailing update error:.*?\"\)\n",
    "\n", code
)

if code != orig:
    print(f"\n  Removed v2.6.0 scanner injections")
else:
    print(f"\n  No v2.6.0 injections found to remove")

# ── Now fix any remaining indentation issues ──
# Find lines with IndentationError pattern:
# A line that should be at one indent level but is at another
fixed_lines = code.split("\n")
indent_fixes = 0

for i in range(1, len(fixed_lines)):
    line = fixed_lines[i]
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#"):
        continue
    
    # Check for common indent issues: line has more indent than expected
    # after a blank line or comment that shouldn't increase indent
    prev_idx = i - 1
    while prev_idx >= 0 and (not fixed_lines[prev_idx].strip() or fixed_lines[prev_idx].strip().startswith("#")):
        prev_idx -= 1
    
    if prev_idx >= 0:
        prev = fixed_lines[prev_idx]
        prev_stripped = prev.lstrip()
        prev_indent = len(prev) - len(prev_stripped)
        curr_indent = len(line) - len(stripped)
        
        # If current line is indented MORE than prev, but prev doesn't end with :
        # and current is a statement (not continuation), it's likely wrong
        if (curr_indent > prev_indent + 4 and 
            not prev_stripped.rstrip().endswith(":") and
            not prev_stripped.rstrip().endswith(",") and
            not prev_stripped.rstrip().endswith("(") and
            not prev_stripped.rstrip().endswith("\\") and
            stripped.startswith(("if ", "for ", "while ", "try:", "except", "return ", "print(", "logger.")) and
            curr_indent - prev_indent > 4):
            # Fix: align with prev
            fixed_lines[i] = " " * prev_indent + stripped
            indent_fixes += 1
            print(f"  Fixed indent at line {i+1}: {curr_indent} -> {prev_indent} spaces")

code = "\n".join(fixed_lines)

# ── Write the fixed file ──
with open(path, "w") as f:
    f.write(code)

print(f"  Indent fixes applied: {indent_fixes}")

# ── Verify: try to compile ──
try:
    with open(path) as f:
        source = f.read()
    compile(source, path, "exec")
    print(f"\n  \u2705 scanner.py compiles successfully!")
except SyntaxError as e:
    print(f"\n  \u274c Still has syntax error: {e}")
    print(f"     Line {e.lineno}: {e.text}")
    print(f"     Manual fix needed.")

# ── Show the fixed area ──
with open(path) as f:
    lines2 = f.readlines()
print(f"\n  Lines 175-195 AFTER fix:")
for i in range(max(0, 174), min(len(lines2), 195)):
    print(f"     {i+1:4d}: {lines2[i].rstrip()}")

print("\n" + "=" * 55)
print("v2.6.0 HOTFIX complete.")
