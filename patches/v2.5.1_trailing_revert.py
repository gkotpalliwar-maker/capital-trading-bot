import re
import os

print("Reverting trailing stop from execution.py")
print("=" * 50)

BOT_DIR = os.path.join(os.getcwd(), "bot")
exec_path = os.path.join(BOT_DIR, "execution.py")

with open(exec_path) as f:
    code = f.read()

# Remove the trailing stop block we added
# Pattern: the block from "# v2.5.1: Enable" to the end of the if block
trailing_block = re.search(
    r'\n    # v2\.5\.1: Enable Capital\.com native trailing stop.*?logger\.info\(f"Trailing stop.*?\)',
    code,
    re.DOTALL
)

if trailing_block:
    code = code[:trailing_block.start()] + code[trailing_block.end():]
    with open(exec_path, "w") as f:
        f.write(code)
    print("  Removed trailing stop block from execution.py")
else:
    print("  Trailing stop block not found (may already be removed)")
    # Try simpler removal
    lines = code.split("\n")
    new_lines = []
    skip = False
    for line in lines:
        if "v2.5.1: Enable Capital.com native trailing stop" in line:
            skip = True
            continue
        if skip and ('order["trailingStop"]' in line or 'order["trailingStopDistance"]' in line or 'logger.info(f"Trailing stop' in line):
            continue
        if skip and line.strip() and not line.strip().startswith("#") and "trailing" not in line.lower():
            skip = False
        if not skip:
            new_lines.append(line)
    
    new_code = "\n".join(new_lines)
    if new_code != code:
        with open(exec_path, "w") as f:
            f.write(new_code)
        print("  Removed trailing stop lines (line-by-line)")
    else:
        print("  No changes needed")

print("\nDone! Trailing stop removed from order creation.")
print("The /trailing command still works for manually enabling on existing positions.")
