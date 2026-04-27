#!/usr/bin/env python3
"""v2.9.0 Patcher: Integrate retrace-entry into scanner.py"""
import os, sys, shutil
from datetime import datetime

print("v2.9.0 Retrace-Entry Patcher")
print("=" * 55)

scanner_path = os.path.join(os.getcwd(), "bot", "scanner.py")
if not os.path.exists(scanner_path):
    print(f"  ERROR: {scanner_path} not found")
    sys.exit(1)

with open(scanner_path) as f:
    content = f.read()
lines_list = content.split(chr(10))

if "retrace_entry" in content:
    print("  Already patched. Skipping.")
    sys.exit(0)

# Backup
bdir = os.path.join(os.getcwd(), "backups", datetime.now().strftime("%Y%m%d_%H%M%S"))
os.makedirs(bdir, exist_ok=True)
shutil.copy2(scanner_path, os.path.join(bdir, "scanner.py.bak"))
print(f"  Backup: {bdir}/scanner.py.bak")

changes = 0
new_lines = []

# We process line-by-line and inject code at the right spots
import_done = False
init_done = False
scan_done = False

for i, line in enumerate(lines_list):
    new_lines.append(line)

    # PATCH 1: Add import after guardrails import
    if not import_done and "_guardrails_available = False" in line:
        new_lines.append("")
        new_lines.append("try:")
        new_lines.append("    from retrace_entry import init_retrace_scanner, scan_retrace_entry")
        new_lines.append("    _retrace_available = True")
        new_lines.append("except ImportError:")
        new_lines.append("    _retrace_available = False")
        import_done = True
        changes += 1
        print("  [1/3] Added retrace_entry import")

    # Fallback: import after check_news_risk = None
    if not import_done and "check_news_risk = None" in line:
        new_lines.append("")
        new_lines.append("try:")
        new_lines.append("    from retrace_entry import init_retrace_scanner, scan_retrace_entry")
        new_lines.append("    _retrace_available = True")
        new_lines.append("except ImportError:")
        new_lines.append("    _retrace_available = False")
        import_done = True
        changes += 1
        print("  [1/3] Added retrace_entry import (fallback)")

    # PATCH 2: Init retrace scanner after guardrails init
    if not init_done and "Smart signal guardrails initialized" in line:
        # Skip ahead 3 lines (except block), then inject
        # We mark to inject after the except block ends
        init_done = True  # mark, inject below

    # Detect end of guardrails except block to inject init
    if init_done and not scan_done and changes == 1:
        # Look for the line that has _guardrails or _intel set to None (end of except)
        stripped = line.strip()
        if stripped.startswith("_guardrails") or stripped.startswith("_intel"):
            if "= None" in stripped:
                new_lines.append("")
                new_lines.append("# v2.9.0: Initialize retrace-entry scanner")
                new_lines.append("_retrace_scanner = None")
                new_lines.append("if _retrace_available:")
                new_lines.append("    try:")
                new_lines.append("        _retrace_scanner = init_retrace_scanner()")
                new_lines.append('        logger.info("Retrace-entry scanner initialized")')
                new_lines.append("    except Exception as e:")
                new_lines.append('        logger.warning(f"Retrace scanner init failed: {e}")')
                new_lines.append("        _retrace_scanner = None")
                changes += 1
                print("  [2/3] Added retrace scanner init")

    # PATCH 3: Add retrace scan before "for sig in signals:"
    if not scan_done and "for sig in signals:" in line:
        # Get the indentation of this line
        indent = line[:len(line) - len(line.lstrip())]
        # Insert retrace scan BEFORE this line (replace last appended line)
        new_lines.pop()  # remove the "for sig" line we just added
        new_lines.append(indent + "# v2.9.0: Add retrace-entry signals")
        new_lines.append(indent + "if _retrace_scanner is not None:")
        new_lines.append(indent + "    try:")
        new_lines.append(indent + "        retrace_sigs = scan_retrace_entry(df, inst, tf)")
        new_lines.append(indent + "        for rs in retrace_sigs:")
        new_lines.append(indent + "            sig_obj = type('Sig', (), {")
        new_lines.append(indent + "                'direction': rs['direction'],")
        new_lines.append(indent + "                'entry': rs['entry'],")
        new_lines.append(indent + "                'sl': rs['sl'],")
        new_lines.append(indent + "                'tp': rs['tp'],")
        new_lines.append(indent + "                'rr_ratio': rs['rr_ratio'],")
        new_lines.append(indent + "                'confluence': rs['confluence'],")
        new_lines.append(indent + "                'metadata': rs,")
        new_lines.append(indent + "            })()")
        new_lines.append(indent + "            signals.append(sig_obj)")
        new_lines.append(indent + "        if retrace_sigs:")
        new_lines.append(indent + "            logger.info(f'Retrace: {len(retrace_sigs)} signals for {inst} {tf}')")
        new_lines.append(indent + "    except Exception as e:")
        new_lines.append(indent + "        logger.warning(f'Retrace scan error {inst} {tf}: {e}')")
        new_lines.append("")
        new_lines.append(line)  # re-add the "for sig in signals:" line
        scan_done = True
        changes += 1
        print("  [3/3] Added retrace signal generation")

# Fallback for init if we never found the guardrails except block
if changes == 1 and init_done:
    # Insert init after the first import block we added
    for idx, ln in enumerate(new_lines):
        if "_retrace_available = False" in ln:
            insert_block = [
                "",
                "# v2.9.0: Initialize retrace-entry scanner",
                "_retrace_scanner = None",
                "if _retrace_available:",
                "    try:",
                "        _retrace_scanner = init_retrace_scanner()",
                '        logger.info("Retrace-entry scanner initialized")',
                "    except Exception as e:",
                '        logger.warning(f"Retrace scanner init failed: {e}")',
                "        _retrace_scanner = None",
            ]
            for j, block_line in enumerate(insert_block):
                new_lines.insert(idx + 1 + j, block_line)
            changes += 1
            print("  [2/3] Added retrace scanner init (fallback)")
            break

if changes >= 2:
    final = chr(10).join(new_lines)
    with open(scanner_path, "w") as f:
        f.write(final)
    print(f"  scanner.py patched ({changes}/3 changes)")
    try:
        compile(final, scanner_path, "exec")
        print("  Syntax check passed")
    except SyntaxError as e:
        print(f"  SYNTAX ERROR: {e}")
        print("  Restoring backup...")
        shutil.copy2(os.path.join(bdir, "scanner.py.bak"), scanner_path)
        print("  Backup restored")
        sys.exit(1)
else:
    print(f"  Only {changes}/3 patches. Not writing.")
    sys.exit(1)