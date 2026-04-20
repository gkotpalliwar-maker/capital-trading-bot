import re, sys

path = "bot/scanner.py"
with open(path) as f:
    code = f.read()

# Step 1: Strip ALL risk_report code
lines = code.split("\n")
clean = []
skip = False
for line in lines:
    if "from risk_report import" in line:
        continue
    if "Weekend Risk Report" in line:
        skip = True
        continue
    if skip:
        if "logger.warning" in line and "Weekend report" in line:
            skip = False
            continue
        elif line.strip().startswith("except") and "rpt_err" in line:
            continue
        elif skip:
            continue
    clean.append(line)
code = "\n".join(clean)

# Step 2: Find notify_scan_summary call using paren balancing
idx = code.find("telegram_bot.notify_scan_summary(")
if idx == -1:
    print("! notify_scan_summary not found")
    sys.exit(1)

# Find the balanced closing paren
depth = 0
i = code.index("(", idx)
for pos in range(i, len(code)):
    if code[pos] == "(":
        depth += 1
    elif code[pos] == ")":
        depth -= 1
        if depth == 0:
            insert_pos = pos + 1
            break

# Step 3: Insert risk report block after the balanced call
indent = "            "
risk_block = f"""

{indent}# -- Weekend Risk Report (Fri 21:50 UTC) --
{indent}try:
{indent}    if should_send_report():
{indent}        logger.info("Sending weekend risk report...")
{indent}        report_text = generate_weekend_report(client)
{indent}        telegram_bot.send_message(report_text, parse_mode="HTML")
{indent}        mark_report_sent()
{indent}        logger.info("Weekend risk report sent.")
{indent}except Exception as rpt_err:
{indent}    logger.warning("Weekend report failed: %s", rpt_err)
"""

code = code[:insert_pos] + risk_block + code[insert_pos:]

# Step 4: Add import
if "from risk_report import" not in code:
    code = code.replace(
        "from mtf_confluence import check_mtf_alignment, clear_cache as clear_mtf_cache",
        "from mtf_confluence import check_mtf_alignment, clear_cache as clear_mtf_cache\n"
        "from risk_report import should_send_report, generate_weekend_report, mark_report_sent"
    )
    print("+ Added risk_report import")

with open(path, "w") as f:
    f.write(code)
print("+ Inserted weekend report (paren-balanced)")

# Verify
import py_compile
try:
    py_compile.compile(path, doraise=True)
    print("\n✅ scanner.py syntax OK!")
except py_compile.PyCompileError as e:
    print(f"\n❌ Still broken: {e}")
