#!/usr/bin/env python3
"""Dump scanner.py lines 1-40 (imports) + 200-329 (main loop)."""
import os
SCANNER = os.path.join(os.getcwd(), "bot", "scanner.py")
with open(SCANNER) as f:
    lines = f.readlines()
print(f"scanner.py: {len(lines)} lines\n")
print("=" * 75)
print("  LINES 1-40 (imports + globals)")
print("=" * 75)
for i in range(0, min(40, len(lines))):
    print(f"L{i+1:>3}: {lines[i].rstrip()}")
print("\n" + "=" * 75)
print("  LINES 200-329 (main loop)")
print("=" * 75)
for i in range(199, min(329, len(lines))):
    print(f"L{i+1:>3}: {lines[i].rstrip()}")
