#!/usr/bin/env python3
"""Dump scanner.py lines 145-210 (main() startup)."""
import os
with open(os.path.join(os.getcwd(), "bot", "scanner.py")) as f:
    lines = f.readlines()
print("=" * 70)
print("  LINES 145-210 (main() startup)")
print("=" * 70)
for i in range(144, min(210, len(lines))):
    print(f"L{i+1:>3}: {lines[i].rstrip()}")
