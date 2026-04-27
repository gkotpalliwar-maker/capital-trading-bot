#!/usr/bin/env python3
"""v2.9.0 Patcher: Integrate retrace-entry strategy into scanner.py

This patcher:
1. Adds import for retrace_entry module
2. Initializes retrace scanner after guardrails init
3. Adds retrace signal generation into the scan loop

Run from /opt/trading-bot:
  venv/bin/python3 /tmp/v290/patches/v2.9.0_retrace_entry_patcher.py
"""
import os
import sys
import shutil
from datetime import datetime

print("v2.9.0 Retrace-Entry Patcher")
print("=" * 55)

scanner_path = os.path.join(os.getcwd(), "bot", "scanner.py")
if not os.path.exists(scanner_path):
    print(f"  ERROR: {scanner_path} not found")
    sys.exit(1)

with open(scanner_path) as f:
    content = f.read()

# Check if already patched
if "retrace_entry" in content:
    print("  Already patched (retrace_entry found). Skipping.")
    sys.exit(0)

# Backup
backup_dir = os.path.join(os.getcwd(), "backups", datetime.now().strftime("%Y%m%d_%H%M%S"))
os.makedirs(backup_dir, exist_ok=True)
shutil.copy2(scanner_path, os.path.join(backup_dir, "scanner.py.bak"))
print(f"  Backup: {backup_dir}/scanner.py.bak")

changes = 0

# ============================================================
# PATCH 1: Add import
# ============================================================

IMPORT_BLOCK = """
try:
    from retrace_entry import init_retrace_scanner, scan_retrace_entry
    _retrace_available = True
except ImportError:
    _retrace_available = False
"""

# Anchor: after guardrails import (v2.8.0)
anchor1 = "_guardrails_available = False"
anchor2 = "check_news_risk = None"

if anchor1 in content:
    idx = content.index(anchor1) + len(anchor1)
    nl = content.index("
", idx)
    content = content[:nl + 1] + IMPORT_BLOCK + content[nl + 1:]
    changes += 1
    print("  [1/3] Added retrace_entry import (after guardrails)")
elif anchor2 in content:
    idx = content.index(anchor2) + len(anchor2)
    nl = content.index("
", idx)
    content = content[:nl + 1] + IMPORT_BLOCK + content[nl + 1:]
    changes += 1
    print("  [1/3] Added retrace_entry import (after check_news_risk)")
else:
    print("  [1/3] FAILED: Could not find import anchor")

# ============================================================
# PATCH 2: Init retrace scanner
# ============================================================

INIT_BLOCK = """
# v2.9.0: Initialize retrace-entry scanner
_retrace_scanner = None
if _retrace_available:
    try:
        _retrace_scanner = init_retrace_scanner()
        logger.info("Retrace-entry scanner initialized")
    except Exception as e:
        logger.warning(f"Retrace scanner init failed: {e}")
        _retrace_scanner = None
"""

# Anchor: after guardrails init log message
init_anchor1 = "Smart signal guardrails initialized"
init_anchor2 = "Database initialized"

if init_anchor1 in content:
    # Find end of the guardrails init block (skip past except clause)
    idx = content.index(init_anchor1)
    # Move to end of this line
    nl = content.index("
", idx)
    # Skip 3 more lines (except, _guardrails/intel, pass)
    for _ in range(3):
        next_nl = content.find("
", nl + 1)
        if next_nl == -1:
            break
        nl = next_nl
    content = content[:nl + 1] + INIT_BLOCK + content[nl + 1:]
    changes += 1
    print("  [2/3] Added retrace scanner init (after guardrails init)")
elif init_anchor2 in content:
    idx = content.index(init_anchor2)
    nl = content.index("
", idx)
    content = content[:nl + 1] + INIT_BLOCK + content[nl + 1:]
    changes += 1
    print("  [2/3] Added retrace scanner init (after DB init)")
else:
    print("  [2/3] FAILED: Could not find init anchor")

# ============================================================
# PATCH 3: Add retrace signal generation before signal loop
# ============================================================

RETRACE_SCAN = """
            # v2.9.0: Add retrace-entry signals
            if _retrace_scanner is not None:
                try:
                    retrace_sigs = scan_retrace_entry(df, inst, tf)
                    for rs in retrace_sigs:
                        sig_obj = type("Sig", (), {
                            "direction": rs["direction"],
                            "entry": rs["entry"],
                            "sl": rs["sl"],
                            "tp": rs["tp"],
                            "rr_ratio": rs["rr_ratio"],
                            "confluence": rs["confluence"],
                            "metadata": rs,
                        })()
                        signals.append(sig_obj)
                    if retrace_sigs:
                        logger.info(f"Retrace: {len(retrace_sigs)} signals for {inst} {tf}")
                except Exception as e:
                    logger.warning(f"Retrace scan error {inst} {tf}: {e}")
"""

loop_anchor = "for sig in signals:"
if loop_anchor in content:
    idx = content.index(loop_anchor)
    # Find start of the line containing the for loop
    line_start = content.rfind("
", 0, idx) + 1
    content = content[:line_start] + RETRACE_SCAN + content[line_start:]
    changes += 1
    print("  [3/3] Added retrace signal generation before signal loop")
else:
    print("  [3/3] FAILED: Could not find signal loop anchor")

# ============================================================
# WRITE AND VERIFY
# ============================================================
if changes >= 2:
    with open(scanner_path, "w") as f:
        f.write(content)
    print(f"
  scanner.py patched ({changes}/3 changes)")
    try:
        compile(content, scanner_path, "exec")
        print("  Syntax check passed")
    except SyntaxError as e:
        print(f"  SYNTAX ERROR: {e}")
        print("  Restoring backup...")
        shutil.copy2(os.path.join(backup_dir, "scanner.py.bak"), scanner_path)
        print("  Backup restored")
        sys.exit(1)
else:
    print(f"
  Only {changes}/3 patches applied. Not writing.")
    sys.exit(1)
