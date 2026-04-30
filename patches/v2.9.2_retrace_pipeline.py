#!/usr/bin/env python3
"""
v2.9.2 Patcher: Enable retrace signals in processing pipeline
1. Adds "retrace+buy" and "retrace+sell" to WINNING_ZONE_COMBOS in config.py
2. Skips regime filter for retrace signals in scanner.py
"""
import os

BASE = os.getcwd()

def patch_file(filepath, desc, old, new):
    path = os.path.join(BASE, filepath)
    content = open(path).read()
    if new in content:
        print(f"  Already patched: {desc}")
        return True
    if old not in content:
        print(f"  ERROR: target not found in {filepath}")
        print(f"  Looking for: {repr(old[:80])}")
        return False
    content = content.replace(old, new, 1)
    open(path, "w").write(content)
    print(f"  Fixed: {desc}")
    return True

print("v2.9.2 Retrace Pipeline Fix")
print("=" * 40)

# Fix 1: Add retrace to WINNING_ZONE_COMBOS
ok1 = patch_file(
    "bot/config.py",
    "Add retrace+buy/sell to WINNING_ZONE_COMBOS",
    'WINNING_ZONE_COMBOS = {',
    'WINNING_ZONE_COMBOS = {"retrace+buy", "retrace+sell", '
)

# Fix 2: Skip regime filter for retrace signals
path = os.path.join(BASE, "bot/scanner.py")
scanner = open(path).read()

if '"retrace" not in zt' in scanner:
    print("  Already patched: Retrace regime bypass")
    ok2 = True
else:
    # Find the regime check block and wrap it
    old_block = '                    if is_top5:\n                        # Regime filter (advisory)\n                        regime_ok, regime_reason = regime_filter.is_setup_allowed(\n                            regime, zt, sig.direction)\n                        if not regime_ok:'
    new_block = '                    if is_top5:\n                        # Regime filter — skip for retrace (own quality scoring)\n                        if "retrace" not in zt:\n                          regime_ok, regime_reason = regime_filter.is_setup_allowed(\n                              regime, zt, sig.direction)\n                          if not regime_ok:'
    
    if old_block in scanner:
        # Also need to indent the REGIME BLOCKED log and continue
        old_after = '                            logger.info("  REGIME BLOCKED: %s %s [%s] - %s", inst_name, sig.direction, tf, regime_reason)\n                            # v2.3.4: Regime enforcement\n                            continue'
        new_after = '                              logger.info("  REGIME BLOCKED: %s %s [%s] - %s", inst_name, sig.direction, tf, regime_reason)\n                              # v2.3.4: Regime enforcement\n                              continue'
        scanner = scanner.replace(old_block, new_block, 1)
        scanner = scanner.replace(old_after, new_after, 1)
        open(path, "w").write(scanner)
        print("  Fixed: Retrace signals bypass regime filter")
        ok2 = True
    else:
        print("  ERROR: Could not find regime filter block")
        # Debug: show what's there
        lines = scanner.split("\n")
        for i, line in enumerate(lines):
            if "is_top5" in line:
                for j in range(i, min(i+8, len(lines))):
                    print(f"    {j+1}: {lines[j]}")
                break
        ok2 = False

if ok1 and ok2:
    print("\n✅ All patches applied. Restart: sudo systemctl restart trading-bot")
else:
    print("\n⚠️  Some patches failed")
