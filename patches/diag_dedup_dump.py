#!/usr/bin/env python3
"""Dump scanner.py dedup section (lines 45-145) for analysis."""
import os
SCANNER = os.path.join(os.getcwd(), "bot", "scanner.py")
with open(SCANNER) as f:
    lines = f.readlines()
print(f"scanner.py: {len(lines)} lines total\n")
print("=" * 75)
print("  LINES 45-145 (dedup + signal persistence logic)")
print("=" * 75)
for i in range(44, min(145, len(lines))):
    print(f"L{i+1:>3}: {lines[i].rstrip()}")
print("\n" + "=" * 75)
print("  LINES 125-145 (signal data creation)")  
print("=" * 75)
for i in range(124, min(145, len(lines))):
    print(f"L{i+1:>3}: {lines[i].rstrip()}")
