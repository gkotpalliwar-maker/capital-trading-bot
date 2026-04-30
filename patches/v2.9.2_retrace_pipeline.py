#!/usr/bin/env python3
"""
v2.9.2 Patcher: Enable retrace signals in processing pipeline
1. Adds "retrace+buy" and "retrace+sell" to WINNING_ZONE_COMBOS in config.py
2. Skips regime filter for retrace signals in scanner.py (they have own quality scoring)
"""
import os
import re

BASE = os.getcwd()  # /opt/trading-bot

def patch_file(filepath, description, old, new):
    path = os.path.join(BASE, filepath)
    content = open(path, "r").read()
    if new in content:
        print(f"  Already patched: {description}")
        return True
    if old not in content:
        print(f"  ERROR: Could not find target in {filepath}")
        print(f"  Looking for: {repr(old[:80])}")
        return False
    content = content.replace(old, new, 1)
    open(path, "w").write(content)
    print(f"  Fixed: {description}")
    return True

print("v2.9.2 Retrace Pipeline Fix")
print("=" * 40)

# === Fix 1: Add retrace to WINNING_ZONE_COMBOS ===
ok1 = patch_file(
    "bot/config.py",
    "Add retrace+buy/sell to WINNING_ZONE_COMBOS",
    'WINNING_ZONE_COMBOS = {',
    'WINNING_ZONE_COMBOS = {"retrace+buy", "retrace+sell", '
)

# === Fix 2: Skip regime filter for retrace signals ===
# The scanner code has:
#   if is_top5:
#       regime_ok, regime_reason = regime_filter.is_setup_allowed(
#           regime, zt, sig.direction)
#       if not regime_ok:
#           ...
#           continue
#
# We need to wrap the regime check so retrace signals bypass it.
# Find the regime filter block and add a retrace bypass.

scanner_path = os.path.join(BASE, "bot/scanner.py")
scanner = open(scanner_path, "r").read()

# Check if already patched
if "retrace" in scanner and "regime" in scanner and "skip regime" in scanner.lower():
    print("  Already patched: Retrace regime bypass")
    ok2 = True
else:
    # Find the regime filter call pattern
    old_regime = """                    if is_top5:
                        # Regime filter (advisory)
                        regime_ok, regime_reason = regime_filter.is_setup_allowed(
                            regime, zt, sig.direction)
                        if not regime_ok:
                            logger.info("  REGIME BLOCKED: %s %s [%s] - %s", inst_name, sig.direction, tf, regime_reason)
                            # v2.3.4: Regime enforcement
                            continue"""

    new_regime = """                    if is_top5:
                        # Regime filter — skip for retrace signals (own quality scoring)
                        if "retrace" not in zt:
                            regime_ok, regime_reason = regime_filter.is_setup_allowed(
                                regime, zt, sig.direction)
                            if not regime_ok:
                                logger.info("  REGIME BLOCKED: %s %s [%s] - %s", inst_name, sig.direction, tf, regime_reason)
                                # v2.3.4: Regime enforcement
                                continue"""

    if old_regime in scanner:
        scanner = scanner.replace(old_regime, new_regime, 1)
        open(scanner_path, "w").write(scanner)
        print("  Fixed: Retrace signals bypass regime filter")
        ok2 = True
    else:
        print("  ERROR: Could not find regime filter block in scanner.py")
        print("  Searching for pattern...")
        # Try to find what's actually there
        lines = scanner.split("\n")
        for i, line in enumerate(lines):
            if "is_top5" in line and "regime" in lines[i+1] if i+1 < len(lines) else False:
                print(f"    Line {i+1}: {line.strip()}")
                for j in range(i+1, min(i+8, len(lines))):
                    print(f"    Line {j+1}: {lines[j].strip()}")
                break
        ok2 = False

if ok1 and ok2:
    print("\n✅ All patches applied successfully")
    print("   Restart bot: sudo systemctl restart trading-bot")
else:
    print("\n⚠️  Some patches failed — check output above")
