#!/usr/bin/env python3
"""v2.9.1: Fix Sig object entry -> entry_price in scanner.py"""
import os, sys

print("v2.9.1 Sig attribute fix")
print("=" * 40)

path = os.path.join(os.getcwd(), "bot", "scanner.py")
with open(path) as f:
    content = f.read()

# Fix: entry -> entry_price in Sig object
old = "'entry': rs['entry'],"
new = "'entry_price': rs['entry'],"

if old in content:
    content = content.replace(old, new)
    with open(path, "w") as f:
        f.write(content)
    print("  Fixed: entry -> entry_price")
elif new in content:
    print("  Already fixed")
else:
    print("  WARNING: Could not find anchor string")