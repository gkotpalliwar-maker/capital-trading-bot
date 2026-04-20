import sys

path = "bot/scanner.py"
with open(path) as f:
    lines = f.readlines()

# === STEP 1: Remove ALL risk_report related lines ===
clean = []
skip = False
for line in lines:
    # Remove risk_report import
    if "from risk_report import" in line:
        continue
    # Remove weekend risk report block start
    if "Weekend Risk Report" in line:
        skip = True
        continue
    if skip:
        # End of block: the logger.warning line about Weekend report
        if "Weekend report" in line:
            skip = False
            continue
        # Also catch the except line
        if "rpt_err" in line:
            continue
        continue
    clean.append(line)

# === STEP 2: Find and fix the notify_scan_summary area ===
# The problem: the call may have been split with orphaned lines
# Strategy: find the notify_scan_summary( line, then find its TRUE end

code = "".join(clean)
lines = code.split("\n")

# Find the line with notify_scan_summary(
nsn_idx = None
for i, line in enumerate(lines):
    if "notify_scan_summary(" in line:
        nsn_idx = i
        break

if nsn_idx is None:
    print("! notify_scan_summary not found - skipping scanner patch")
    print("  /risk command will still work (manual trigger only)")
    # Just write the cleaned code
    with open(path, "w") as f:
        f.write(code)
    sys.exit(0)

# Find the balanced end of this function call
# Start from nsn_idx, track parens
depth = 0
end_idx = nsn_idx
found_end = False
for i in range(nsn_idx, min(nsn_idx + 20, len(lines))):
    for ch in lines[i]:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end_idx = i
                found_end = True
                break
    if found_end:
        break

# Check if there are orphaned lines after end_idx that look like
# leftover function args (e.g., "                top5_count, risk_status)")
# These would be lines with unmatched ) that aren't part of any statement
orphan_indices = []
for i in range(end_idx + 1, min(end_idx + 5, len(lines))):
    stripped = lines[i].strip()
    if not stripped:
        continue
    # Orphaned if it's just args with a closing paren and no statement keyword
    if stripped.endswith(")") and not any(kw in stripped for kw in ["try:", "except", "if ", "for ", "def ", "class ", "return", "print("]):
        # Check if it has unmatched )
        opens = stripped.count("(")
        closes = stripped.count(")")
        if closes > opens:
            orphan_indices.append(i)
            # This was part of the original notify_scan_summary call
            # We need to incorporate it back
            break

# If there are orphans, the notify_scan_summary call was split
# Rebuild: remove the orphan line and incorporate its content into the call
if orphan_indices:
    # Get the orphaned content
    orphan_content = lines[orphan_indices[0]].strip()
    # Remove trailing ) from the current end_idx line if it has one that closes the call
    # and append the orphan content before the final )
    
    # Actually simpler: remove any standalone ) between nsn_idx and the orphan,
    # then let the orphan's ) be the real end of the call
    
    # Remove lines that are just "            )" between end_idx and orphan
    removal_indices = set()
    for i in range(end_idx, orphan_indices[0]):
        if lines[i].strip() == ")":
            removal_indices.add(i)
    
    # Also check: if the end_idx line already has content + ), we need to
    # add a comma and merge with orphan
    end_line = lines[end_idx].rstrip()
    if end_line.endswith(")"):
        # Remove the ) from this line - the orphan has the real closing )
        lines[end_idx] = end_line[:-1].rstrip() + ",\n"
    
    # Remove the standalone ) lines
    lines = [l for i, l in enumerate(lines) if i not in removal_indices]
    
    # Recalculate orphan index after removals
    # Just rebuild and find the end again
    code = "\n".join(lines)
    lines = code.split("\n")
    
    # Re-find the balanced end
    for i, line in enumerate(lines):
        if "notify_scan_summary(" in line:
            nsn_idx = i
            break
    
    depth = 0
    for i in range(nsn_idx, min(nsn_idx + 20, len(lines))):
        for ch in lines[i]:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end_idx = i
                    found_end = True
                    break
        if found_end:
            break

# === STEP 3: Insert risk report AFTER the complete call ===
indent = "            "
risk_block = [
    "",
    f"{indent}# -- Weekend Risk Report (Fri 21:50 UTC) --",
    f"{indent}try:",
    f"{indent}    if should_send_report():",
    f'{indent}        logger.info("Sending weekend risk report...")',
    f"{indent}        report_text = generate_weekend_report(client)",
    f'{indent}        telegram_bot.send_message(report_text, parse_mode="HTML")',
    f"{indent}        mark_report_sent()",
    f'{indent}        logger.info("Weekend risk report sent.")',
    f"{indent}except Exception as rpt_err:",
    f'{indent}    logger.warning("Weekend report failed: %s", rpt_err)',
    "",
]

# Insert after end_idx
lines = lines[:end_idx + 1] + risk_block + lines[end_idx + 1:]
code = "\n".join(lines)

# === STEP 4: Add import if needed ===
if "from risk_report import" not in code:
    code = code.replace(
        "from mtf_confluence import check_mtf_alignment, clear_cache as clear_mtf_cache",
        "from mtf_confluence import check_mtf_alignment, clear_cache as clear_mtf_cache\n"
        "from risk_report import should_send_report, generate_weekend_report, mark_report_sent"
    )
    print("+ Added risk_report import")

with open(path, "w") as f:
    f.write(code)
print("+ Rebuilt notify_scan_summary area + inserted risk report")

# === VERIFY ===
import py_compile
try:
    py_compile.compile(path, doraise=True)
    print("\n✅ scanner.py syntax OK!")
except py_compile.PyCompileError as e:
    print(f"\n❌ Still broken: {e}")
    print("\nFallback: removing risk report from scanner (manual /risk only)")
    # Nuclear fallback: just remove all risk_report code
    with open(path) as f:
        code = f.read()
    code2 = []
    skip2 = False
    for line in code.split("\n"):
        if "from risk_report import" in line:
            continue
        if "Weekend Risk Report" in line:
            skip2 = True
            continue
        if skip2:
            if "Weekend report" in line or "rpt_err" in line:
                skip2 = False
                continue
            continue
        code2.append(line)
    with open(path, "w") as f:
        f.write("\n".join(code2))
    # Final verify
    try:
        py_compile.compile(path, doraise=True)
        print("✅ Fallback OK - /risk works manually, no auto-trigger")
    except py_compile.PyCompileError as e2:
        print(f"❌ CRITICAL: {e2}")
        print("   Please share: sed -n \'290,330p\' bot/scanner.py")
